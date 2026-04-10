"""
scalper.py — event-driven scalper usando Bybit (sin restricción geográfica)
Drop-in replacement del scalper de Binance.
"""
import logging
import time
import json
import threading
from pathlib import Path
from datetime import datetime
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.strategies.scalping_engine import (
    get_signal_strength, get_order_book_imbalance,
    find_liquidity_zones, microstructure_score, get_htf_trend
)

logger = logging.getLogger(__name__)
LOG_FILE = Path("logs/scalping_trades.json")

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


def load_state():
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE) as f: return json.load(f)
        except: pass
    return {"total_pnl":0.0,"trades":[],"open_positions":[],
            "session_pnl":0.0,"win_count":0,"loss_count":0}

def save_state(s):
    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE,"w") as f: json.dump(s,f)

def save_state_async(s):
    import copy
    snap = copy.deepcopy(s)
    threading.Thread(target=save_state, args=(snap,), daemon=True).start()


class KlineBuffer:
    def __init__(self, maxlen=120):
        self._data = {s: deque(maxlen=maxlen) for s in WHITELIST}
        self._lock = threading.Lock()

    def push(self, symbol, kline):
        with self._lock:
            buf = self._data.get(symbol)
            if buf is None: return
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


class ScalpingBot:
    def __init__(self, max_positions=MAX_POSITIONS, risk_per_trade=0.01, capital=1000.0):
        from src.exchanges.bybit_client import BybitClient
        self.client         = BybitClient()
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

    def _circuit_ok(self) -> tuple[bool,str]:
        dd = (self.initial_cap - self.capital) / self.initial_cap
        if dd >= MAX_DRAWDOWN:
            return False, f"Drawdown {dd:.1%} — HALTED"
        if self.state["session_pnl"] <= DAILY_LOSS_LIM:
            return False, f"Daily loss ${self.state['session_pnl']:.2f} — HALTED"
        return True, ""

    def _correlation_allowed(self, symbol) -> bool:
        for grp in CORRELATION_GROUPS.values():
            if symbol in grp:
                if any(p["symbol"] in grp for p in self.state["open_positions"]):
                    return False
        return True

    def _get_htf_trend(self, symbol) -> str:
        now = time.time()
        cached = self._htf_cache.get(symbol)
        if cached and (now - cached[1]) < self._htf_ttl:
            return cached[0]
        from src.strategies.scalping_engine import calculate_ema
        try:
            klines = self.client.get_klines(symbol, interval="60", limit=25)
            if len(klines) < 20: return "neutral"
            closes = [k["close"] for k in klines]
            ema20  = calculate_ema(closes, 20)
            trend  = "up" if closes[-1] > ema20*1.002 else "down" if closes[-1] < ema20*0.998 else "neutral"
        except:
            trend = "neutral"
        self._htf_cache[symbol] = (trend, now)
        return trend

    # ── WS (Bybit native) ────────────────────────────────────────────────────

    def _seed_buffers(self):
        def fetch(sym):
            klines = self.client.get_klines(sym, interval="1", limit=120)
            for k in klines:
                self.buffer.push(sym, k)
        with ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(fetch, WHITELIST))
        print(f"✅ Buffers seeded — {len(WHITELIST)} symbols")

    def start_websocket(self):
        self._seed_buffers()
        try:
            self._ws = self.client.start_kline_ws(
                symbols=WHITELIST,
                interval="1",
                callback=self._on_kline_update
            )
            self._running = True
            print(f"⚡ Bybit WS connected — {len(WHITELIST)} streams")
        except Exception as e:
            logger.warning(f"WS failed: {e} — polling fallback")
            self._start_polling_fallback()

    def _on_kline_update(self, symbol: str, kline: dict):
        self.buffer.push(symbol, kline)
        if kline.get("x", False):  # candle closed
            self._anal_pool.submit(self._on_candle_close, symbol)

    def _on_candle_close(self, symbol: str):
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
        if len(klines) < 35: return

        closes  = [k["close"]  for k in klines]
        highs   = [k["high"]   for k in klines]
        lows    = [k["low"]    for k in klines]
        volumes = [k["volume"] for k in klines]

        ms = microstructure_score(volumes, closes, highs, lows)
        if ms["quality"] == "low": return

        lz  = find_liquidity_zones(highs, lows, closes)
        htf = self._get_htf_trend(symbol)
        ob  = get_order_book_imbalance(self.client, symbol)
        sig = get_signal_strength(klines, ob=ob, lz=lz, ms=ms)

        if sig["direction"] == "none": return
        if htf == "down" and sig["direction"] == "long":  return
        if htf == "up"   and sig["direction"] == "short": return

        sig.update({"symbol": symbol, "price": closes[-1], "htf": htf, "ob": ob})
        self._cooldown[symbol] = now
        self._exec_pool.submit(self._execute_entry, sig)

    # ── EXECUTION ────────────────────────────────────────────────────────────

    def _execute_entry(self, signal: dict):
        pos = self._calc_position(signal)
        if not pos: return
        position = {
            "id":            len(self.state["trades"]) + 1,
            "symbol":        signal["symbol"],
            "direction":     signal["direction"],
            "entry_price":   pos["fill_price"],
            "sl_price":      pos["sl"],
            "tp_price":      pos["tp"],
            "position_usdt": pos["size"],
            "quantity":      pos["qty"],
            "sl_pct":        pos["sl_pct"],
            "tp_pct":        pos["tp_pct"],
            "fee_paid":      pos["fee_usdt"],
            "reasons":       signal.get("reasons", []),
            "strength":      signal["strength"],
            "rsi":           signal.get("rsi", 50),
            "htf_trend":     signal.get("htf", "neutral"),
            "ob_imbalance":  signal.get("ob", {}).get("imbalance", 0.5),
            "timestamp":     datetime.now().isoformat(),
            "status":        "open"
        }
        with self._lock:
            self.state["open_positions"].append(position)
        save_state_async(self.state)
        icon = "🟢" if signal["direction"] == "long" else "🔴"
        print(f"{icon} {signal['symbol']} {signal['direction'].upper()} @ ${pos['fill_price']:.4f} "
              f"| {signal['strength']}/100 | HTF:{signal.get('htf','?')} "
              f"| OB:{signal.get('ob',{}).get('imbalance',0.5):.2f} "
              f"| SL${pos['sl']:.4f} TP${pos['tp']:.4f}")

    def _calc_position(self, signal) -> dict | None:
        price = signal["price"]
        if price <= 0: return None
        atr_pct = signal.get("atr_pct", 0.1)
        fill = price * (1 + SPREAD_ASSUME) if signal["direction"] == "long" else price * (1 - SPREAD_ASSUME)
        size = max(10.0, min(self.capital * self.risk_per_trade * signal["strength"] / 100, self.capital * 0.05))
        sl_pct = max(atr_pct * 1.5, 0.3) / 100
        tp_pct = max(sl_pct * 2.5, sl_pct + FEE_RT + 0.001)
        sl = round(fill * (1-sl_pct), 6) if signal["direction"] == "long" else round(fill * (1+sl_pct), 6)
        tp = round(fill * (1+tp_pct), 6) if signal["direction"] == "long" else round(fill * (1-tp_pct), 6)
        fee = size * FEE_RT
        return {"fill_price": round(fill,6), "size": round(size-fee,2),
                "qty": round((size-fee)/fill,6), "sl": sl, "tp": tp,
                "sl_pct": round(sl_pct*100,3), "tp_pct": round(tp_pct*100,3), "fee_usdt": round(fee,4)}

    # ── MONITOR ──────────────────────────────────────────────────────────────

    def _monitor_positions(self):
        while self._running:
            try: self._check_positions_fast()
            except Exception as e: logger.debug(f"Monitor: {e}")
            time.sleep(2)

    def _check_positions_fast(self):
        with self._lock:
            positions = list(self.state["open_positions"])
        if not positions: return
        closed = []
        for pos in positions:
            current = self.buffer.latest_price(pos["symbol"])
            if current <= 0:
                current = self.client.get_price(pos["symbol"])
            if current <= 0: continue
            entry = pos["entry_price"]
            d     = pos["direction"]
            pnl_pct  = (current-entry)/entry*100 if d=="long" else (entry-current)/entry*100
            hit_sl   = current<=pos["sl_price"] if d=="long" else current>=pos["sl_price"]
            hit_tp   = current>=pos["tp_price"] if d=="long" else current<=pos["tp_price"]
            net_pnl  = pos["position_usdt"] * (pnl_pct - FEE_RT*100) / 100
            if hit_tp:
                print(f"🎯 TP {pos['symbol']} +${net_pnl:.4f}")
                self._close_position(pos, current, "tp", net_pnl)
                closed.append(pos["id"])
            elif hit_sl:
                print(f"🛑 SL {pos['symbol']} -${abs(net_pnl):.4f}")
                self._close_position(pos, current, "sl", net_pnl)
                closed.append(pos["id"])
            elif pnl_pct > 1.0:
                new_sl = round(current*0.997,6) if d=="long" else round(current*1.003,6)
                better = new_sl > pos["sl_price"] if d=="long" else new_sl < pos["sl_price"]
                if better:
                    with self._lock: pos["sl_price"] = new_sl
                    print(f"📐 Trailing {pos['symbol']} → ${new_sl:.4f}")
        if closed:
            with self._lock:
                self.state["open_positions"] = [p for p in self.state["open_positions"] if p["id"] not in closed]
            save_state_async(self.state)

    def _close_position(self, pos, exit_price, reason, pnl):
        trade = {**pos, "exit_price": exit_price, "exit_reason": reason,
                 "pnl_usdt": round(pnl,4),
                 "pnl_pct": round((exit_price-pos["entry_price"])/pos["entry_price"]*100,4),
                 "closed_at": datetime.now().isoformat(), "status": "closed"}
        with self._lock:
            self.state["trades"].append(trade)
            self.state["total_pnl"]   = round(self.state["total_pnl"]+pnl,4)
            self.state["session_pnl"] = round(self.state["session_pnl"]+pnl,4)
            if pnl > 0: self.state["win_count"] += 1
            else:       self.state["loss_count"] += 1
        self.capital += pnl
        save_state_async(self.state)

    # ── POLLING FALLBACK ─────────────────────────────────────────────────────

    def _start_polling_fallback(self):
        self._running = True
        def poll():
            self._seed_buffers()
            while self._running:
                ok, reason = self._circuit_ok()
                if not ok:
                    print(f"⛔ {reason}")
                    self._running = False
                    break
                with self._lock:
                    open_syms = {p["symbol"] for p in self.state["open_positions"]}
                    n_open    = len(self.state["open_positions"])
                targets = [s for s in WHITELIST
                           if s not in open_syms and self._correlation_allowed(s)][:10]
                futures = {self._anal_pool.submit(self._analyze_rest, s): s for s in targets}
                for f in as_completed(futures):
                    _, sig = f.result()
                    if sig and n_open < self.max_positions:
                        self._exec_pool.submit(self._execute_entry, sig)
                        n_open += 1
                self._check_positions_fast()
                time.sleep(10)
        threading.Thread(target=poll, daemon=True).start()

    def _analyze_rest(self, symbol):
        try:
            klines = self.client.get_klines(symbol, interval="1", limit=120)
            if len(klines) < 35: return symbol, None
            for k in klines: self.buffer.push(symbol, k)
            closes  = [k["close"]  for k in klines]
            highs   = [k["high"]   for k in klines]
            lows    = [k["low"]    for k in klines]
            volumes = [k["volume"] for k in klines]
            ms  = microstructure_score(volumes, closes, highs, lows)
            if ms["quality"] == "low": return symbol, None
            lz  = find_liquidity_zones(highs, lows, closes)
            htf = self._get_htf_trend(symbol)
            ob  = get_order_book_imbalance(self.client, symbol)
            sig = get_signal_strength(klines, ob=ob, lz=lz, ms=ms)
            if sig["direction"] == "none": return symbol, None
            if htf == "down" and sig["direction"] == "long":  return symbol, None
            if htf == "up"   and sig["direction"] == "short": return symbol, None
            sig.update({"symbol": symbol, "price": closes[-1], "htf": htf, "ob": ob})
            return symbol, sig
        except Exception as e:
            logger.debug(f"analyze_rest {symbol}: {e}")
            return symbol, None

    # ── PUBLIC ───────────────────────────────────────────────────────────────

    def run(self):
        print("⚡ ScalpingBot — Bybit event-driven")
        self.start_websocket()
        threading.Thread(target=self._monitor_positions, daemon=True).start()
        try:
            while self._running: time.sleep(1)
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
                   if s not in open_syms and self._correlation_allowed(s)][: self.max_positions * 3]
        futures = {self._anal_pool.submit(self._analyze_rest, s): s for s in targets}
        for f in as_completed(futures):
            _, sig = f.result()
            if sig and n_open < self.max_positions:
                self._execute_entry(sig)
                n_open += 1
        self._check_positions_fast()
        self._print_stats()

    def stop(self):
        self._running = False
        if self._ws:
            try: self._ws.exit()
            except: pass
        self._exec_pool.shutdown(wait=False)
        self._anal_pool.shutdown(wait=False)

    def _print_stats(self):
        t  = self.state["win_count"] + self.state["loss_count"]
        wr = self.state["win_count"]/t*100 if t else 0.0
        dd = (self.initial_cap - self.capital) / self.initial_cap * 100
        print(f"📈 ${self.capital:.2f} | PnL ${self.state['total_pnl']:+.4f} | "
              f"WR {wr:.1f}% | DD {dd:.1f}% | {len(self.state['open_positions'])} open")

    def print_stats(self): self._print_stats()
    def open_position(self, signal):
        self._execute_entry(signal)
        with self._lock:
            return self.state["open_positions"][-1] if self.state["open_positions"] else None
