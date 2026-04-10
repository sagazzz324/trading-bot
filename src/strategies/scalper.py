"""
scalper.py — event-driven WS scalper with:
- Fee-adjusted PnL
- HTF trend filter
- Order book imbalance
- Liquidity zones
- Microstructure filter
- Correlation cap
- Circuit breaker
- Async state saves
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

# Whitelist — quality assets only
WHITELIST = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","AVAXUSDT","LINKUSDT","LTCUSDT","DOTUSDT",
    "ADAUSDT","MATICUSDT","NEARUSDT","ATOMUSDT","UNIUSDT",
    "AAVEUSDT","INJUSDT","SUIUSDT","APTUSDT","ARBUSDT"
]

# Correlation groups — max 1 from each group simultaneously
CORRELATION_GROUPS = {
    "btc_beta":  {"SOLUSDT","AVAXUSDT","NEARUSDT","ATOMUSDT","DOTUSDT",
                  "LINKUSDT","AAVEUSDT","UNIUSDT","ARBUSDT","APTUSDT","INJUSDT"},
    "eth_beta":  {"MATICUSDT","SUIUSDT"},
    "meme":      {"DOGEUSDT"},
}

# Execution constants
FEE_RT         = 0.002   # 0.1% taker × 2
SPREAD_ASSUME  = 0.0005  # 0.05% simulated spread
MAX_DRAWDOWN   = 0.15    # 15% circuit breaker
DAILY_LOSS_LIM = -50.0   # $50 max daily loss
MAX_POSITIONS  = 3


def load_state():
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE) as f:
                return json.load(f)
        except:
            pass
    return {"total_pnl": 0.0, "trades": [], "open_positions": [],
            "session_pnl": 0.0, "win_count": 0, "loss_count": 0}


def save_state(s):
    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE, "w") as f:
        json.dump(s, f)


def save_state_async(s):
    """Non-blocking disk write."""
    import copy
    snap = copy.deepcopy(s)
    threading.Thread(target=save_state, args=(snap,), daemon=True).start()


# ── KLINE BUFFER ─────────────────────────────────────────────────────────────

class KlineBuffer:
    def __init__(self, maxlen=120):
        self._data: dict[str, deque] = {s: deque(maxlen=maxlen) for s in WHITELIST}
        self._lock = threading.Lock()

    def push(self, symbol: str, kline: dict):
        with self._lock:
            buf = self._data.get(symbol)
            if buf is None:
                return
            if buf and buf[-1].get("t") == kline.get("t"):
                buf[-1] = kline      # update open candle
            else:
                buf.append(kline)    # new candle

    def get(self, symbol: str) -> list:
        with self._lock:
            return list(self._data.get(symbol, []))

    def latest_price(self, symbol: str) -> float:
        with self._lock:
            buf = self._data.get(symbol, [])
            return buf[-1]["close"] if buf else 0.0


# ── MAIN BOT ──────────────────────────────────────────────────────────────────

class ScalpingBot:
    def __init__(self, max_positions=MAX_POSITIONS, risk_per_trade=0.01, capital=1000.0):
        from src.exchanges.binance_client import BinanceClient
        self.client         = BinanceClient()
        self.max_positions  = max_positions
        self.risk_per_trade = risk_per_trade
        self.capital        = capital
        self.initial_cap    = capital
        self.state          = load_state()
        self.buffer         = KlineBuffer()
        self._exec_pool     = ThreadPoolExecutor(max_workers=4, thread_name_prefix="exec")
        self._anal_pool     = ThreadPoolExecutor(max_workers=8, thread_name_prefix="anal")
        self._ws            = None
        self._running       = False
        self._cooldown: dict[str, float] = {}   # symbol → last signal ts
        self._cooldown_sec  = 60                 # min 60s between signals per symbol
        self._htf_cache: dict[str, tuple] = {}  # symbol → (trend, ts)
        self._htf_ttl       = 300                # refresh HTF every 5min
        self._state_lock    = threading.Lock()

    # ── CIRCUIT BREAKERS ─────────────────────────────────────────────────────

    def _check_circuit_breakers(self) -> tuple[bool, str]:
        """Returns (ok, reason). Call before any new entry."""
        drawdown = (self.initial_cap - self.capital) / self.initial_cap
        if drawdown >= MAX_DRAWDOWN:
            return False, f"Drawdown {drawdown:.1%} ≥ {MAX_DRAWDOWN:.0%} — HALTED"
        if self.state["session_pnl"] <= DAILY_LOSS_LIM:
            return False, f"Daily loss ${self.state['session_pnl']:.2f} ≤ ${DAILY_LOSS_LIM} — HALTED"
        return True, ""

    # ── CORRELATION GUARD ────────────────────────────────────────────────────

    def _correlation_allowed(self, symbol: str) -> bool:
        """Max 1 position per correlation group."""
        for group_syms in CORRELATION_GROUPS.values():
            if symbol in group_syms:
                group_open = [p for p in self.state["open_positions"] if p["symbol"] in group_syms]
                if len(group_open) >= 1:
                    return False
        return True

    # ── HTF TREND (cached) ───────────────────────────────────────────────────

    def _get_htf_trend(self, symbol: str) -> str:
        now = time.time()
        cached = self._htf_cache.get(symbol)
        if cached and (now - cached[1]) < self._htf_ttl:
            return cached[0]
        trend = get_htf_trend(self.client, symbol)
        self._htf_cache[symbol] = (trend, now)
        return trend

    # ── WS ────────────────────────────────────────────────────────────────────

    def _seed_buffers(self):
        """Pre-fill buffers with REST klines (runs once)."""
        def fetch(sym):
            klines = self.client.get_klines(sym, interval="1m", limit=120)
            for k in klines:
                self.buffer.push(sym, {"t": 0, **k})
        with ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(fetch, WHITELIST))
        print(f"✅ Buffers seeded — {len(WHITELIST)} symbols")

    def start_websocket(self):
        try:
            from binance import ThreadedWebsocketManager
        except ImportError:
            logger.warning("ThreadedWebsocketManager not available — polling fallback")
            self._start_polling_fallback()
            return

        self._seed_buffers()
        twm = ThreadedWebsocketManager(
            api_key=self.client.client.API_KEY,
            api_secret=self.client.client.API_SECRET
        )
        twm.start()
        self._ws = twm
        streams = [f"{s.lower()}@kline_1m" for s in WHITELIST]
        twm.start_multiplex_socket(callback=self._on_ws_message, streams=streams)
        self._running = True
        print(f"⚡ WS connected — {len(streams)} streams")

    def _on_ws_message(self, msg):
        try:
            data   = msg.get("data", msg)
            k      = data.get("k", {})
            symbol = k.get("s", "")
            if symbol not in WHITELIST:
                return
            kline = {
                "t": k["t"], "open": float(k["o"]), "high": float(k["h"]),
                "low": float(k["l"]), "close": float(k["c"]), "volume": float(k["v"])
            }
            self.buffer.push(symbol, kline)
            if k.get("x", False):   # candle closed
                self._anal_pool.submit(self._on_candle_close, symbol)
        except Exception as e:
            logger.debug(f"WS msg error: {e}")

    # ── EVENT-DRIVEN ANALYSIS ────────────────────────────────────────────────

    def _on_candle_close(self, symbol: str):
        now = time.time()
        if now - self._cooldown.get(symbol, 0) < self._cooldown_sec:
            return
        ok, reason = self._check_circuit_breakers()
        if not ok:
            print(f"⛔ {reason}")
            self._running = False
            return
        if not self._correlation_allowed(symbol):
            return
        with self._state_lock:
            open_syms = {p["symbol"] for p in self.state["open_positions"]}
            n_open    = len(self.state["open_positions"])
        if symbol in open_syms or n_open >= self.max_positions:
            return

        klines = self.buffer.get(symbol)
        if len(klines) < 35:
            return

        closes  = [k["close"]  for k in klines]
        highs   = [k["high"]   for k in klines]
        lows    = [k["low"]    for k in klines]
        volumes = [k["volume"] for k in klines]

        # Microstructure filter first (cheap, no API call)
        ms = microstructure_score(volumes, closes, highs, lows)
        if ms["quality"] == "low":
            return

        # Liquidity zones (no API call)
        lz = find_liquidity_zones(highs, lows, closes)

        # Base signal
        signal = get_signal_strength(klines, lz=lz, ms=ms)
        if signal["direction"] == "none":
            return

        # HTF trend filter
        htf = self._get_htf_trend(symbol)
        if htf == "down" and signal["direction"] == "long":
            return
        if htf == "up"   and signal["direction"] == "short":
            return

        # Order book imbalance (1 API call — justified by signal quality)
        ob = get_order_book_imbalance(self.client, symbol, depth=10)
        signal_final = get_signal_strength(klines, ob=ob, lz=lz, ms=ms)
        if signal_final["direction"] == "none":
            return

        signal_final["symbol"] = symbol
        signal_final["price"]  = closes[-1]
        signal_final["htf"]    = htf
        signal_final["ob"]     = ob

        self._cooldown[symbol] = now
        self._exec_pool.submit(self._execute_entry, signal_final)

    # ── EXECUTION ────────────────────────────────────────────────────────────

    def _execute_entry(self, signal: dict):
        pos = self._calc_position(signal)
        if not pos:
            return

        position = {
            "id":            len(self.state["trades"]) + 1,
            "symbol":        signal["symbol"],
            "direction":     signal["direction"],
            "entry_price":   pos["fill_price"],    # realistic fill
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

        with self._state_lock:
            self.state["open_positions"].append(position)
        save_state_async(self.state)

        icon = "🟢" if signal["direction"] == "long" else "🔴"
        print(f"{icon} {signal['symbol']} {signal['direction'].upper()} "
              f"@ ${pos['fill_price']:.4f} | str:{signal['strength']} "
              f"| HTF:{signal.get('htf','?')} | OB:{signal.get('ob',{}).get('imbalance',0.5):.2f} "
              f"| SL${pos['sl']:.4f} TP${pos['tp']:.4f} | fee${pos['fee_usdt']:.4f}")

    def _calc_position(self, signal: dict) -> dict | None:
        price = signal["price"]
        if price <= 0:
            return None

        atr_pct = signal.get("atr_pct", 0.1)

        # Realistic fill price (simulate spread)
        if signal["direction"] == "long":
            fill_price = price * (1 + SPREAD_ASSUME)
        else:
            fill_price = price * (1 - SPREAD_ASSUME)

        # Position size
        size = self.capital * self.risk_per_trade * (signal["strength"] / 100)
        size = max(10.0, min(size, self.capital * 0.05))

        # SL/TP must account for fees
        sl_pct = max(atr_pct * 1.5, 0.3) / 100
        tp_pct = max(sl_pct * 2.5, sl_pct + FEE_RT + 0.001)   # TP > SL + fees

        if signal["direction"] == "long":
            sl = round(fill_price * (1 - sl_pct), 6)
            tp = round(fill_price * (1 + tp_pct), 6)
        else:
            sl = round(fill_price * (1 + sl_pct), 6)
            tp = round(fill_price * (1 - tp_pct), 6)

        fee_usdt = size * FEE_RT   # deducted at open

        return {
            "fill_price": round(fill_price, 6),
            "size":       round(size - fee_usdt, 2),   # deduct entry fee
            "qty":        round((size - fee_usdt) / fill_price, 6),
            "sl":         sl,
            "tp":         tp,
            "sl_pct":     round(sl_pct * 100, 3),
            "tp_pct":     round(tp_pct * 100, 3),
            "fee_usdt":   round(fee_usdt, 4),
        }

    # ── POSITION MONITOR ────────────────────────────────────────────────────

    def _monitor_positions(self):
        """Runs in dedicated thread — checks every 2s using WS buffer prices."""
        while self._running:
            try:
                self._check_positions_fast()
            except Exception as e:
                logger.debug(f"Monitor error: {e}")
            time.sleep(2)

    def _check_positions_fast(self):
        with self._state_lock:
            positions = list(self.state["open_positions"])
        if not positions:
            return

        closed = []
        for pos in positions:
            current = self.buffer.latest_price(pos["symbol"])
            if current <= 0:
                # Fallback REST price
                current = self.client.get_price(pos["symbol"])
            if current <= 0:
                continue

            entry     = pos["entry_price"]
            direction = pos["direction"]

            if direction == "long":
                pnl_pct = (current - entry) / entry * 100
                hit_sl  = current <= pos["sl_price"]
                hit_tp  = current >= pos["tp_price"]
            else:
                pnl_pct = (entry - current) / entry * 100
                hit_sl  = current >= pos["sl_price"]
                hit_tp  = current <= pos["tp_price"]

            # Fee-adjusted PnL
            fee_pct = FEE_RT * 100
            pnl_net_pct = pnl_pct - fee_pct
            pnl_usdt    = pos["position_usdt"] * pnl_net_pct / 100

            if hit_tp:
                print(f"🎯 TP {pos['symbol']} +${pnl_usdt:.4f} (net after fees)")
                self._close_position(pos, current, "tp", pnl_usdt)
                closed.append(pos["id"])
            elif hit_sl:
                print(f"🛑 SL {pos['symbol']} -${abs(pnl_usdt):.4f} (net after fees)")
                self._close_position(pos, current, "sl", pnl_usdt)
                closed.append(pos["id"])
            elif pnl_pct > 1.0:
                # Trailing stop
                if direction == "long":
                    new_sl = round(current * 0.997, 6)
                    if new_sl > pos["sl_price"]:
                        with self._state_lock:
                            pos["sl_price"] = new_sl
                        print(f"📐 Trailing SL {pos['symbol']} → ${new_sl:.4f}")
                else:
                    new_sl = round(current * 1.003, 6)
                    if new_sl < pos["sl_price"]:
                        with self._state_lock:
                            pos["sl_price"] = new_sl
                        print(f"📐 Trailing SL {pos['symbol']} → ${new_sl:.4f}")

        if closed:
            with self._state_lock:
                self.state["open_positions"] = [
                    p for p in self.state["open_positions"] if p["id"] not in closed
                ]
            save_state_async(self.state)

    def _close_position(self, pos, exit_price, reason, pnl_usdt):
        trade = {
            **pos,
            "exit_price":  exit_price,
            "exit_reason": reason,
            "pnl_usdt":    round(pnl_usdt, 4),
            "pnl_pct":     round((exit_price - pos["entry_price"]) / pos["entry_price"] * 100, 4),
            "closed_at":   datetime.now().isoformat(),
            "status":      "closed"
        }
        with self._state_lock:
            self.state["trades"].append(trade)
            self.state["total_pnl"]   = round(self.state["total_pnl"] + pnl_usdt, 4)
            self.state["session_pnl"] = round(self.state["session_pnl"] + pnl_usdt, 4)
            if pnl_usdt > 0:
                self.state["win_count"] += 1
            else:
                self.state["loss_count"] += 1
        self.capital += pnl_usdt
        save_state_async(self.state)

    # ── POLLING FALLBACK ─────────────────────────────────────────────────────

    def _start_polling_fallback(self):
        self._running = True
        print("⚠️  Polling fallback — 10s cycle")

        def poll():
            self._seed_buffers()
            while self._running:
                ok, reason = self._check_circuit_breakers()
                if not ok:
                    print(f"⛔ {reason}")
                    self._running = False
                    break

                with self._state_lock:
                    open_syms = {p["symbol"] for p in self.state["open_positions"]}
                    n_open    = len(self.state["open_positions"])

                targets = [s for s in WHITELIST
                           if s not in open_syms
                           and self._correlation_allowed(s)][:10]

                futures = {self._anal_pool.submit(self._analyze_rest, s): s for s in targets}
                for f in as_completed(futures):
                    _, sig = f.result()
                    if sig and n_open < self.max_positions:
                        self._exec_pool.submit(self._execute_entry, sig)
                        n_open += 1

                self._check_positions_fast()
                time.sleep(10)

        threading.Thread(target=poll, daemon=True).start()

    def _analyze_rest(self, symbol: str):
        try:
            klines = self.client.get_klines(symbol, interval="1m", limit=120)
            if len(klines) < 35:
                return symbol, None

            for k in klines:
                self.buffer.push(symbol, {"t": 0, **k})

            closes  = [k["close"]  for k in klines]
            highs   = [k["high"]   for k in klines]
            lows    = [k["low"]    for k in klines]
            volumes = [k["volume"] for k in klines]

            ms = microstructure_score(volumes, closes, highs, lows)
            if ms["quality"] == "low":
                return symbol, None

            lz  = find_liquidity_zones(highs, lows, closes)
            htf = self._get_htf_trend(symbol)
            ob  = get_order_book_imbalance(self.client, symbol, depth=10)
            sig = get_signal_strength(klines, ob=ob, lz=lz, ms=ms)

            if sig["direction"] == "none":
                return symbol, None
            if htf == "down" and sig["direction"] == "long":
                return symbol, None
            if htf == "up"   and sig["direction"] == "short":
                return symbol, None

            sig["symbol"] = symbol
            sig["price"]  = closes[-1]
            sig["htf"]    = htf
            sig["ob"]     = ob
            return symbol, sig

        except Exception as e:
            logger.debug(f"REST analyze {symbol}: {e}")
            return symbol, None

    # ── PUBLIC ───────────────────────────────────────────────────────────────

    def run(self):
        print("⚡ ScalpingBot — event-driven WS mode")
        self.start_websocket()
        threading.Thread(target=self._monitor_positions, daemon=True).start()
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def run_once(self):
        """Compat shim for bot_controller."""
        if not self._running:
            self._seed_buffers()
            self._running = True
            threading.Thread(target=self._monitor_positions, daemon=True).start()

        ok, reason = self._check_circuit_breakers()
        if not ok:
            print(f"⛔ {reason}")
            return

        with self._state_lock:
            open_syms = {p["symbol"] for p in self.state["open_positions"]}
            n_open    = len(self.state["open_positions"])

        targets = [s for s in WHITELIST
                   if s not in open_syms
                   and self._correlation_allowed(s)][: self.max_positions * 3]

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
            try: self._ws.stop()
            except: pass
        self._exec_pool.shutdown(wait=False)
        self._anal_pool.shutdown(wait=False)
        print("⛔ ScalpingBot stopped")

    def _print_stats(self):
        t  = self.state["win_count"] + self.state["loss_count"]
        wr = self.state["win_count"] / t * 100 if t else 0.0
        dd = (self.initial_cap - self.capital) / self.initial_cap * 100
        print(f"📈 ${self.capital:.2f} | PnL ${self.state['total_pnl']:+.4f} | "
              f"WR {wr:.1f}% ({self.state['win_count']}/{t}) | "
              f"DD {dd:.1f}% | {len(self.state['open_positions'])} open")

    def print_stats(self):
        self._print_stats()

    # Legacy compat
    def open_position(self, signal):
        self._execute_entry(signal)
        with self._state_lock:
            return self.state["open_positions"][-1] if self.state["open_positions"] else None
