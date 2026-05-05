"""
scalper.py — event-driven scalper con Bybit + verbose error logging
"""
import logging
import traceback
import time
import json
import threading
from collections import Counter
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.strategies.scalping_engine import (
    get_signal_strength, get_order_book_imbalance,
    find_liquidity_zones, microstructure_score, get_htf_trend
)

logger = logging.getLogger(__name__)
LOG_FILE = Path("logs/scalping_trades.json")
AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

WHITELIST = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","AVAXUSDT","LINKUSDT","LTCUSDT","DOTUSDT",
    "ADAUSDT","MATICUSDT","NEARUSDT","ATOMUSDT","UNIUSDT",
    "AAVEUSDT","INJUSDT","SUIUSDT","APTUSDT","ARBUSDT"
]

CORRELATION_GROUPS = {
    "btc_beta": {"SOLUSDT","AVAXUSDT","NEARUSDT","ATOMUSDT","DOTUSDT",
                 "LINKUSDT","AAVEUSDT","UNIUSDT","ARBUSDT","APTUSDT","INJUSDT"},
    "eth_beta": {"MATICUSDT","SUIUSDT"},
    "meme":     {"DOGEUSDT"},
}

FEE_RT         = 0.002
SPREAD_ASSUME  = 0.0005
MAX_DRAWDOWN   = 0.15
DAILY_LOSS_LIM = -50.0
MAX_POSITIONS  = 3
SCAN_MULTIPLIER = 5


def load_state():
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"load_state error: {e}")
    return {"total_pnl":0.0,"trades":[],"open_positions":[],
            "session_pnl":0.0,"win_count":0,"loss_count":0}


def save_state(s):
    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE,"w") as f:
        json.dump(s, f)


def save_state_async(s):
    import copy
    snap = copy.deepcopy(s)
    threading.Thread(target=save_state, args=(snap,), daemon=True).start()


# ── KLINE BUFFER ──────────────────────────────────────────────────────────────

class KlineBuffer:
    def __init__(self, maxlen=120):
        self._data = {s: deque(maxlen=maxlen) for s in WHITELIST}
        self._lock = threading.Lock()

    def push(self, symbol, kline):
        with self._lock:
            buf = self._data.get(symbol)
            if buf is None:
                return
            if buf and buf[-1].get("t") == kline.get("t"):
                buf[-1] = kline
            else:
                buf.append(kline)

    def get(self, symbol) -> list:
        with self._lock:
            return list(self._data.get(symbol, []))

    def latest_price(self, symbol) -> float:
        with self._lock:
            buf = self._data.get(symbol, [])
            return buf[-1]["close"] if buf else 0.0


# ── MAIN BOT ──────────────────────────────────────────────────────────────────

class ScalpingBot:
    def __init__(self, max_positions=MAX_POSITIONS, risk_per_trade=0.01, capital=1000.0):
        from src.exchanges.bybit_client import BybitClient
        self.client         = BybitClient()
        self.mode           = self.client.get_execution_mode()
        self.max_positions  = max_positions
        self.risk_per_trade = risk_per_trade
        self.capital        = capital
        self.initial_cap    = capital
        self.state          = load_state()
        self.buffer         = KlineBuffer()
        self._exec_pool     = ThreadPoolExecutor(max_workers=4)
        self._anal_pool     = ThreadPoolExecutor(max_workers=8)
        self._ws            = None
        self._running       = False
        self._cooldown: dict[str,float] = {}
        self._cooldown_sec  = 60
        self._htf_cache: dict[str,tuple] = {}
        self._htf_ttl       = 300
        self._lock          = threading.Lock()
        self._sync_capital_from_exchange(initial=True)

    def _sync_capital_from_exchange(self, initial: bool = False):
        if self.mode != "live":
            return
        try:
            snapshot = self.client.get_account_snapshot("USDT")
            equity = float(snapshot.get("equity", 0) or 0)
            free_balance = float(snapshot.get("free_balance", 0) or 0)
            if equity > 0:
                self.capital = equity
                if initial:
                    self.initial_cap = equity
                logger.info(
                    f"Live capital sync: equity={equity:.2f} free={free_balance:.2f}"
                )
        except Exception:
            logger.error(f"_sync_capital_from_exchange:\n{traceback.format_exc()}")

    def _summarize_rejections(self, rejections: list[str]):
        if not rejections:
            return
        top = Counter(rejections).most_common(3)
        parts = [f"{reason} x{count}" for reason, count in top]
        print(f"⚠ Rechazos: {' | '.join(parts)}")

    # ── CIRCUIT BREAKERS ──────────────────────────────────────────────────────

    def _circuit_ok(self) -> tuple[bool,str]:
        dd = (self.initial_cap - self.capital) / self.initial_cap
        if dd >= MAX_DRAWDOWN:
            return False, f"Drawdown {dd:.1%} — HALTED"
        if self.state["session_pnl"] <= DAILY_LOSS_LIM:
            return False, f"Daily loss ${self.state['session_pnl']:.2f} — HALTED"
        return True, ""

    # ── CORRELATION GUARD ─────────────────────────────────────────────────────

    def _correlation_allowed(self, symbol) -> bool:
        for grp in CORRELATION_GROUPS.values():
            if symbol in grp:
                if any(p["symbol"] in grp for p in self.state["open_positions"]):
                    return False
        return True

    # ── HTF TREND (cached) ────────────────────────────────────────────────────

    def _get_htf_trend(self, symbol) -> str:
        now = time.time()
        cached = self._htf_cache.get(symbol)
        if cached and (now - cached[1]) < self._htf_ttl:
            return cached[0]
        try:
            from src.strategies.scalping_engine import calculate_ema
            klines = self.client.get_klines(symbol, interval="60", limit=25)
            if len(klines) < 20:
                logger.debug(f"HTF {symbol}: solo {len(klines)} velas")
                return "neutral"
            closes = [k["close"] for k in klines]
            ema20  = calculate_ema(closes, 20)
            trend  = "up" if closes[-1] > ema20*1.002 else "down" if closes[-1] < ema20*0.998 else "neutral"
        except Exception as e:
            logger.error(f"HTF trend {symbol}:\n{traceback.format_exc()}")
            trend = "neutral"
        self._htf_cache[symbol] = (trend, now)
        return trend

    # ── BUFFER SEED ───────────────────────────────────────────────────────────

    def _seed_buffers(self):
        def fetch(sym):
            try:
                klines = self.client.get_klines(sym, interval="1", limit=120)
                if not klines:
                    logger.error(f"seed_buffers {sym}: 0 klines recibidas")
                    return
                for k in klines:
                    self.buffer.push(sym, k)
                logger.debug(f"seed_buffers {sym}: {len(klines)} velas, close={klines[-1]['close']}")
            except Exception as e:
                logger.error(f"seed_buffers {sym}:\n{traceback.format_exc()}")

        with ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(fetch, WHITELIST))

        # Verificar cuántos símbolos tienen datos válidos
        valid = sum(1 for s in WHITELIST if len(self.buffer.get(s)) >= 35)
        print(f"✅ Buffers seeded — {valid}/{len(WHITELIST)} símbolos con datos válidos")
        if valid == 0:
            logger.error("CRÍTICO: ningún símbolo tiene datos — verificar API key y permisos")

    # ── WS ────────────────────────────────────────────────────────────────────

    def start_websocket(self):
        self._seed_buffers()
        try:
            self._ws = self.client.start_kline_ws(
                symbols=WHITELIST,
                interval="1",
                callback=self._on_kline_update
            )
            self._running = True
            print(f"⚡ Bybit WS conectado — {len(WHITELIST)} streams")
        except Exception as e:
            logger.error(f"WS start failed:\n{traceback.format_exc()}")
            print(f"⚠️ WS falló — polling fallback")
            self._start_polling_fallback()

    def _on_kline_update(self, symbol: str, kline: dict):
        try:
            self.buffer.push(symbol, kline)
            if kline.get("x", False):
                self._anal_pool.submit(self._on_candle_close, symbol)
        except Exception as e:
            logger.error(f"_on_kline_update {symbol}:\n{traceback.format_exc()}")

    # ── EVENT-DRIVEN ANALYSIS ─────────────────────────────────────────────────

    def _on_candle_close(self, symbol: str):
        try:
            now = time.time()
            if now - self._cooldown.get(symbol, 0) < self._cooldown_sec:
                return
            ok, reason = self._circuit_ok()
            if not ok:
                print(f"⛔ {reason}")
                self._running = False
                return
            if not self._correlation_allowed(symbol):
                return
            with self._lock:
                open_syms = {p["symbol"] for p in self.state["open_positions"]}
                n_open    = len(self.state["open_positions"])
            if symbol in open_syms or n_open >= self.max_positions:
                return

            klines = self.buffer.get(symbol)
            if len(klines) < 35:
                logger.debug(f"_on_candle_close {symbol}: solo {len(klines)} velas")
                return

            closes  = [k["close"]  for k in klines]
            highs   = [k["high"]   for k in klines]
            lows    = [k["low"]    for k in klines]
            volumes = [k["volume"] for k in klines]

            ms = microstructure_score(volumes, closes, highs, lows)
            if ms["quality"] == "low":
                logger.debug(f"{symbol}: microstructure low — skip")
                return

            lz  = find_liquidity_zones(highs, lows, closes)
            htf = self._get_htf_trend(symbol)
            ob  = get_order_book_imbalance(self.client, symbol)
            sig = get_signal_strength(klines, ob=ob, lz=lz, ms=ms)

            logger.debug(f"{symbol}: dir={sig['direction']} str={sig['strength']} htf={htf} reasons={sig.get('reasons',[])}")

            if sig["direction"] == "none":
                return
            if htf == "down" and sig["direction"] == "long":
                logger.debug(f"{symbol}: HTF down — skip long")
                return
            if htf == "up" and sig["direction"] == "short":
                logger.debug(f"{symbol}: HTF up — skip short")
                return

            sig.update({"symbol": symbol, "price": closes[-1], "htf": htf, "ob": ob})
            self._cooldown[symbol] = now
            self._exec_pool.submit(self._execute_entry, sig)

        except Exception as e:
            logger.error(f"_on_candle_close {symbol}:\n{traceback.format_exc()}")

    # ── EXECUTION ─────────────────────────────────────────────────────────────

    def _execute_entry(self, signal: dict):
        try:
            pos = self._calc_position(signal)
            if not pos:
                return
            if self.mode == "live":
                position = self._execute_live_entry(signal, pos)
            else:
                position = self._execute_simulated_entry(signal, pos)
            if not position:
                return
            with self._lock:
                self.state["open_positions"].append(position)
            save_state_async(self.state)
        except Exception as e:
            logger.error(f"_execute_entry {signal.get('symbol')}:\n{traceback.format_exc()}")

    def _build_position_record(self, signal: dict, pos: dict, *,
                               entry_price: float, qty: float, fee_paid: float,
                               order_meta: dict | None = None) -> dict:
        return {
            "id":            len(self.state["trades"]) + 1,
            "symbol":        signal["symbol"],
            "direction":     signal["direction"],
            "entry_price":   entry_price,
            "sl_price":      pos["sl"],
            "tp_price":      pos["tp"],
            "position_usdt": pos["size"],
            "quantity":      qty,
            "sl_pct":        pos["sl_pct"],
            "tp_pct":        pos["tp_pct"],
            "fee_paid":      fee_paid,
            "reasons":       signal.get("reasons", []),
            "strength":      signal["strength"],
            "rsi":           signal.get("rsi", 50),
            "htf_trend":     signal.get("htf", "neutral"),
            "ob_imbalance":  signal.get("ob", {}).get("imbalance", 0.5),
            "timestamp":     datetime.now(AR_TZ).isoformat(),
            "status":        "open",
            "mode":          self.mode,
            "order_meta":    order_meta or {},
        }

    def _execute_simulated_entry(self, signal: dict, pos: dict) -> dict:
        order_meta = {"simulated": True, "mode": self.mode}
        if self.mode == "shadow":
            side = "Buy" if signal["direction"] == "long" else "Sell"
            preview = self.client.build_order_preview(signal["symbol"], side, pos["qty"])
            order_meta["preview"] = preview
            print(f"[SHADOW] preview {preview}")
        position = self._build_position_record(
            signal,
            pos,
            entry_price=pos["fill_price"],
            qty=pos["qty"],
            fee_paid=pos["fee_usdt"],
            order_meta=order_meta,
        )
        icon = "🟢" if signal["direction"] == "long" else "🔴"
        print(f"{icon} {signal['symbol']} {signal['direction'].upper()} "
              f"@ ${pos['fill_price']:.4f} | {signal['strength']}/100 "
              f"| HTF:{signal.get('htf','?')} "
              f"| OB:{signal.get('ob',{}).get('imbalance',0.5):.2f} "
              f"| SL${pos['sl']:.4f} TP${pos['tp']:.4f}")
        return position

    def _fetch_execution_summary(self, symbol: str, order_id: str, retries: int = 4,
                                 delay: float = 0.8) -> dict | None:
        for attempt in range(retries):
            executions = self.client.get_order_executions(symbol, order_id)
            if executions:
                total_qty = sum(x["qty"] for x in executions)
                total_value = sum(x["value"] for x in executions)
                total_fee = sum(x["fee"] for x in executions)
                avg_price = (total_value / total_qty) if total_qty > 0 else 0.0
                return {
                    "qty": total_qty,
                    "value": total_value,
                    "avg_price": avg_price,
                    "fee": total_fee,
                    "executions": executions,
                }
            if attempt < retries - 1:
                time.sleep(delay)
        return None

    def _execute_live_entry(self, signal: dict, pos: dict) -> dict | None:
        side = "Buy" if signal["direction"] == "long" else "Sell"
        order_resp = self.client.place_market_order(signal["symbol"], side, pos["qty"])
        if not order_resp or not order_resp.get("order_id"):
            logger.error(f"live entry {signal['symbol']}: respuesta inválida {order_resp}")
            return None
        fill = self._fetch_execution_summary(signal["symbol"], order_resp["order_id"])
        if not fill or fill["qty"] <= 0 or fill["avg_price"] <= 0:
            logger.error(
                f"live entry {signal['symbol']}: sin fills para orden {order_resp['order_id']}"
            )
            return None

        entry_price = round(fill["avg_price"], 6)
        qty = round(fill["qty"], 6)
        fee_paid = round(fill["fee"], 6)
        direction = signal["direction"]
        sl = round(entry_price * (1 - pos["sl_pct"] / 100), 6) if direction == "long" else round(entry_price * (1 + pos["sl_pct"] / 100), 6)
        tp = round(entry_price * (1 + pos["tp_pct"] / 100), 6) if direction == "long" else round(entry_price * (1 - pos["tp_pct"] / 100), 6)
        live_pos = dict(pos)
        live_pos["sl"] = sl
        live_pos["tp"] = tp
        live_pos["qty"] = qty
        live_pos["fill_price"] = entry_price
        position = self._build_position_record(
            signal,
            live_pos,
            entry_price=entry_price,
            qty=qty,
            fee_paid=fee_paid,
            order_meta={
                "simulated": False,
                "mode": self.mode,
                "entry_order_id": order_resp["order_id"],
                "entry_preview": self.client.build_order_preview(signal["symbol"], side, pos["qty"]),
                "entry_executions": fill["executions"],
                "entry_value": round(fill["value"], 6),
            },
        )
        self._sync_capital_from_exchange()
        icon = "🟢" if signal["direction"] == "long" else "🔴"
        print(f"{icon} LIVE {signal['symbol']} {signal['direction'].upper()} "
              f"@ ${entry_price:.4f} qty={qty:.6f} fee=${fee_paid:.4f} "
              f"| SL${sl:.4f} TP${tp:.4f}")
        return position

    def _calc_position(self, signal) -> dict | None:
        try:
            price = signal["price"]
            if price <= 0:
                logger.error(f"_calc_position: precio={price} inválido")
                return None
            atr_pct = signal.get("atr_pct", 0.1)
            fill    = price*(1+SPREAD_ASSUME) if signal["direction"]=="long" else price*(1-SPREAD_ASSUME)
            size    = max(10.0, min(
                self.capital * self.risk_per_trade * signal["strength"] / 100,
                self.capital * 0.05
            ))
            sl_pct = max(atr_pct * 1.5, 0.3) / 100
            tp_pct = max(sl_pct * 2.5, sl_pct + FEE_RT + 0.001)
            sl = round(fill*(1-sl_pct),6) if signal["direction"]=="long" else round(fill*(1+sl_pct),6)
            tp = round(fill*(1+tp_pct),6) if signal["direction"]=="long" else round(fill*(1-tp_pct),6)
            fee = size * FEE_RT
            return {
                "fill_price": round(fill,6),
                "size":       round(size-fee,2),
                "qty":        round((size-fee)/fill,6),
                "sl":         sl,
                "tp":         tp,
                "sl_pct":     round(sl_pct*100,3),
                "tp_pct":     round(tp_pct*100,3),
                "fee_usdt":   round(fee,4),
            }
        except Exception as e:
            logger.error(f"_calc_position:\n{traceback.format_exc()}")
            return None

    # ── POSITION MONITOR ──────────────────────────────────────────────────────

    def _monitor_positions(self):
        while self._running:
            try:
                self._check_positions_fast()
            except Exception as e:
                logger.error(f"_monitor_positions:\n{traceback.format_exc()}")
            time.sleep(2)

    def _check_positions_fast(self):
        with self._lock:
            positions = list(self.state["open_positions"])
        if not positions:
            return
        closed = []
        for pos in positions:
            try:
                current = self.buffer.latest_price(pos["symbol"])
                if current <= 0:
                    current = self.client.get_price(pos["symbol"])
                if current <= 0:
                    logger.error(f"check_positions {pos['symbol']}: precio 0 — skip")
                    continue
                entry = pos["entry_price"]
                d     = pos["direction"]
                pnl_pct  = (current-entry)/entry*100 if d=="long" else (entry-current)/entry*100
                hit_sl   = current<=pos["sl_price"] if d=="long" else current>=pos["sl_price"]
                hit_tp   = current>=pos["tp_price"] if d=="long" else current<=pos["tp_price"]
                net_pnl  = pos["position_usdt"] * (pnl_pct - FEE_RT*100) / 100
                if hit_tp:
                    print(f"🎯 TP {pos['symbol']} +${net_pnl:.4f}")
                    if self._close_position(pos, current, "tp", net_pnl):
                        closed.append(pos["id"])
                elif hit_sl:
                    print(f"🛑 SL {pos['symbol']} -${abs(net_pnl):.4f}")
                    if self._close_position(pos, current, "sl", net_pnl):
                        closed.append(pos["id"])
                elif pnl_pct > 1.0:
                    new_sl  = round(current*0.997,6) if d=="long" else round(current*1.003,6)
                    better  = new_sl > pos["sl_price"] if d=="long" else new_sl < pos["sl_price"]
                    if better:
                        with self._lock:
                            pos["sl_price"] = new_sl
                        print(f"📐 Trailing {pos['symbol']} → ${new_sl:.4f}")
            except Exception as e:
                logger.error(f"check_pos {pos.get('symbol')}:\n{traceback.format_exc()}")

        if closed:
            with self._lock:
                self.state["open_positions"] = [
                    p for p in self.state["open_positions"] if p["id"] not in closed
                ]
            save_state_async(self.state)

    def _close_position(self, pos, exit_price, reason, pnl) -> bool:
        if self.mode == "live":
            return self._close_live_position(pos, reason)
        return self._close_simulated_position(pos, exit_price, reason, pnl)

    def _close_simulated_position(self, pos, exit_price, reason, pnl) -> bool:
        trade = {
            **pos,
            "exit_price":  exit_price,
            "exit_reason": reason,
            "pnl_usdt":    round(pnl,4),
            "pnl_pct":     round((exit_price-pos["entry_price"])/pos["entry_price"]*100,4),
            "closed_at":   datetime.now(AR_TZ).isoformat(),
            "status":      "closed"
        }
        with self._lock:
            self.state["trades"].append(trade)
            self.state["total_pnl"]   = round(self.state["total_pnl"]+pnl,4)
            self.state["session_pnl"] = round(self.state["session_pnl"]+pnl,4)
            if pnl > 0: self.state["win_count"] += 1
            else:       self.state["loss_count"] += 1
        self.capital += pnl
        save_state_async(self.state)
        return True

    def _close_live_position(self, pos, reason: str) -> bool:
        try:
            side = "Sell" if pos["direction"] == "long" else "Buy"
            qty = float(pos.get("quantity", 0) or 0)
            if qty <= 0:
                logger.error(f"live close {pos.get('symbol')}: quantity inválida {qty}")
                return False
            order_resp = self.client.place_market_order(
                pos["symbol"], side, qty, reduce_only=True
            )
            if not order_resp or not order_resp.get("order_id"):
                logger.error(f"live close {pos['symbol']}: respuesta inválida {order_resp}")
                return False
            fill = self._fetch_execution_summary(pos["symbol"], order_resp["order_id"])
            if not fill or fill["qty"] <= 0 or fill["avg_price"] <= 0:
                logger.error(
                    f"live close {pos['symbol']}: sin fills para orden {order_resp['order_id']}"
                )
                return False
            exit_price = round(fill["avg_price"], 6)
            gross = (
                fill["value"] - pos["quantity"] * pos["entry_price"]
                if pos["direction"] == "long"
                else pos["quantity"] * pos["entry_price"] - fill["value"]
            )
            pnl = round(gross - pos.get("fee_paid", 0) - fill["fee"], 4)
            trade = {
                **pos,
                "exit_price": exit_price,
                "exit_reason": reason,
                "pnl_usdt": pnl,
                "pnl_pct": round((exit_price-pos["entry_price"])/pos["entry_price"]*100,4),
                "closed_at": datetime.now(AR_TZ).isoformat(),
                "status": "closed",
                "order_meta": {
                    **(pos.get("order_meta") or {}),
                    "exit_order_id": order_resp["order_id"],
                    "exit_executions": fill["executions"],
                    "exit_value": round(fill["value"], 6),
                },
            }
            with self._lock:
                self.state["trades"].append(trade)
                self.state["total_pnl"] = round(self.state["total_pnl"] + pnl, 4)
                self.state["session_pnl"] = round(self.state["session_pnl"] + pnl, 4)
                if pnl > 0:
                    self.state["win_count"] += 1
                else:
                    self.state["loss_count"] += 1
            self._sync_capital_from_exchange()
            save_state_async(self.state)
            print(f"✅ LIVE CLOSE {pos['symbol']} @ ${exit_price:.4f} pnl=${pnl:.4f}")
            return True
        except Exception:
            logger.error(f"_close_live_position {pos.get('symbol')}:\n{traceback.format_exc()}")
            return False

    # ── POLLING FALLBACK ──────────────────────────────────────────────────────

    def _start_polling_fallback(self):
        self._running = True
        def poll():
            self._seed_buffers()
            while self._running:
                try:
                    ok, reason = self._circuit_ok()
                    if not ok:
                        print(f"⛔ {reason}")
                        self._running = False
                        break
                    with self._lock:
                        open_syms = {p["symbol"] for p in self.state["open_positions"]}
                        n_open    = len(self.state["open_positions"])
                    targets = [s for s in WHITELIST
                               if s not in open_syms
                               and self._correlation_allowed(s)][: max(12, self.max_positions * SCAN_MULTIPLIER)]
                    futures = {self._anal_pool.submit(self._analyze_rest, s): s
                               for s in targets}
                    rejections = []
                    for f in as_completed(futures):
                        _, sig, reject_reason = f.result()
                        if sig and n_open < self.max_positions:
                            self._exec_pool.submit(self._execute_entry, sig)
                            n_open += 1
                        elif reject_reason:
                            rejections.append(reject_reason)
                    self._summarize_rejections(rejections)
                    self._check_positions_fast()
                except Exception as e:
                    logger.error(f"poll loop:\n{traceback.format_exc()}")
                time.sleep(10)
        threading.Thread(target=poll, daemon=True).start()

    def _analyze_rest(self, symbol: str):
        try:
            klines = self.client.get_klines(symbol, interval="1", limit=120)

            if not klines:
                logger.error(f"_analyze_rest {symbol}: klines vacíos")
                return symbol, None, "sin klines"

            if len(klines) < 35:
                logger.error(f"_analyze_rest {symbol}: solo {len(klines)} velas")
                return symbol, None, "pocas velas"

            sample = klines[-1]
            if sample.get("close", 0) <= 0:
                logger.error(f"_analyze_rest {symbol}: close={sample.get('close')} inválido")
                return symbol, None, "close inválido"

            for k in klines:
                self.buffer.push(symbol, k)

            closes  = [k["close"]  for k in klines]
            highs   = [k["high"]   for k in klines]
            lows    = [k["low"]    for k in klines]
            volumes = [k["volume"] for k in klines]

            ms = microstructure_score(volumes, closes, highs, lows)
            if ms["quality"] == "low":
                logger.debug(f"_analyze_rest {symbol}: microstructure low")
                return symbol, None, "microstructure low"

            lz  = find_liquidity_zones(highs, lows, closes)
            htf = self._get_htf_trend(symbol)
            ob  = get_order_book_imbalance(self.client, symbol)
            sig = get_signal_strength(klines, ob=ob, lz=lz, ms=ms)

            logger.debug(f"_analyze_rest {symbol}: dir={sig['direction']} "
                        f"str={sig['strength']} htf={htf} "
                        f"reasons={sig.get('reasons',[])}")

            if sig["direction"] == "none":
                reason = sig.get("reasons", ["sin señal"])[0]
                return symbol, None, reason
            if htf == "down" and sig["direction"] == "long":
                logger.debug(f"_analyze_rest {symbol}: HTF down — skip long")
                return symbol, None, "htf down bloquea long"
            if htf == "up" and sig["direction"] == "short":
                logger.debug(f"_analyze_rest {symbol}: HTF up — skip short")
                return symbol, None, "htf up bloquea short"

            sig.update({"symbol": symbol, "price": closes[-1], "htf": htf, "ob": ob})
            print(f"📡 Señal: {symbol} {sig['direction'].upper()} "
                  f"str={sig['strength']} htf={htf} "
                  f"rsi={sig.get('rsi',0):.1f}")
            return symbol, sig, None

        except Exception as e:
            logger.error(f"_analyze_rest {symbol}:\n{traceback.format_exc()}")
            return symbol, None, "error análisis"

    # ── PUBLIC ────────────────────────────────────────────────────────────────

    def run(self):
        print("⚡ ScalpingBot — Bybit event-driven")
        self.start_websocket()
        threading.Thread(target=self._monitor_positions, daemon=True).start()
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def run_once(self):
        if not self._running:
            self._seed_buffers()
            self._running = True
            threading.Thread(target=self._monitor_positions, daemon=True).start()

        ok, reason = self._circuit_ok()
        if not ok:
            print(f"⛔ {reason}")
            return

        with self._lock:
            open_syms = {p["symbol"] for p in self.state["open_positions"]}
            n_open    = len(self.state["open_positions"])

        targets = [s for s in WHITELIST
                   if s not in open_syms
                   and self._correlation_allowed(s)][: max(12, self.max_positions * SCAN_MULTIPLIER)]

        print(f"🔍 Analizando {len(targets)} símbolos...")

        futures = {self._anal_pool.submit(self._analyze_rest, s): s for s in targets}
        signals_found = 0
        rejections = []
        for f in as_completed(futures):
            _, sig, reject_reason = f.result()
            if sig:
                signals_found += 1
                if n_open < self.max_positions:
                    self._execute_entry(sig)
                    n_open += 1
            elif reject_reason:
                rejections.append(reject_reason)

        print(f"🔍 Scan completo — {signals_found} señales de {len(targets)} símbolos")
        self._summarize_rejections(rejections)
        self._check_positions_fast()
        self._print_stats()

    def stop(self):
        self._running = False
        if self._ws:
            try: self._ws.exit()
            except: pass
        self._exec_pool.shutdown(wait=False)
        self._anal_pool.shutdown(wait=False)
        print("⛔ ScalpingBot detenido")

    def _print_stats(self):
        t  = self.state["win_count"] + self.state["loss_count"]
        wr = self.state["win_count"]/t*100 if t else 0.0
        dd = (self.initial_cap - self.capital) / self.initial_cap * 100
        print(f"📈 ${self.capital:.2f} | PnL ${self.state['total_pnl']:+.4f} | "
              f"WR {wr:.1f}% | DD {dd:.1f}% | {len(self.state['open_positions'])} open")

    def print_stats(self):
        self._print_stats()

    def open_position(self, signal):
        self._execute_entry(signal)
        with self._lock:
            return self.state["open_positions"][-1] if self.state["open_positions"] else None
