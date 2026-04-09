import logging
import time
import json
import threading
from pathlib import Path
from datetime import datetime
from collections import deque
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)
LOG_FILE = Path("logs/scalping_trades.json")

WHITELIST = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "DOGEUSDT","AVAXUSDT","LINKUSDT","LTCUSDT","DOTUSDT",
    "ADAUSDT","MATICUSDT","NEARUSDT","ATOMUSDT","UNIUSDT",
    "AAVEUSDT","INJUSDT","SUIUSDT","APTUSDT","ARBUSDT"
]

def load_state():
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE) as f: return json.load(f)
        except: pass
    return {"total_pnl":0,"trades":[],"open_positions":[],"session_pnl":0,"win_count":0,"loss_count":0}

def save_state(s):
    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE,"w") as f: json.dump(s,f)


class KlineBuffer:
    """Rolling buffer of klines per symbol — updated by WS events."""
    def __init__(self, maxlen=100):
        self._data: dict[str, deque] = {}
        self._lock = threading.Lock()
        for s in WHITELIST:
            self._data[s] = deque(maxlen=maxlen)

    def push(self, symbol: str, kline: dict):
        with self._lock:
            buf = self._data.get(symbol)
            if buf is not None:
                if buf and buf[-1]["t"] == kline["t"]:
                    buf[-1] = kline          # update current candle
                else:
                    buf.append(kline)        # new candle

    def get(self, symbol: str) -> list:
        with self._lock:
            return list(self._data.get(symbol, []))


class ScalpingBot:
    def __init__(self, max_positions=3, risk_per_trade=0.01, capital=1000.0):
        from src.exchanges.binance_client import BinanceClient
        from src.strategies.scalping_engine import get_signal_strength
        self.client            = BinanceClient()
        self.get_signal        = get_signal_strength
        self.max_positions     = max_positions
        self.risk_per_trade    = risk_per_trade
        self.capital           = capital
        self.state             = load_state()
        self.buffer            = KlineBuffer()
        self._exec_pool        = ThreadPoolExecutor(max_workers=4)
        self._analysis_pool    = ThreadPoolExecutor(max_workers=8)
        self._ws               = None
        self._running          = False
        self._last_signal: dict[str, float] = {}   # symbol → last signal ts
        self._signal_cooldown  = 30                 # seconds between signals per symbol

    # ── WS BOOTSTRAP ─────────────────────────────────────────────────────────

    def _seed_buffers(self):
        """Pre-fill buffers with REST klines before WS connects."""
        def fetch(sym):
            klines = self.client.get_klines(sym, interval="1m", limit=100)
            for k in klines:
                self.buffer.push(sym, {"t": k.get("t", 0), **k})
        with ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(fetch, WHITELIST))

    def start_websocket(self):
        """Connect to Binance multi-stream kline WS."""
        try:
            from binance import ThreadedWebsocketManager
        except ImportError:
            logger.warning("ThreadedWebsocketManager not available — falling back to polling")
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
        logger.info(f"WS connected — {len(streams)} streams")

    def _on_ws_message(self, msg):
        """Called on every kline tick — ~every 250ms per symbol."""
        try:
            data   = msg.get("data", msg)
            k      = data.get("k", {})
            symbol = k.get("s", "")
            if symbol not in WHITELIST:
                return

            kline = {
                "t":      k["t"],
                "open":   float(k["o"]),
                "high":   float(k["h"]),
                "low":    float(k["l"]),
                "close":  float(k["c"]),
                "volume": float(k["v"]),
            }
            self.buffer.push(symbol, kline)

            # Only trigger analysis on candle CLOSE
            if k.get("x", False):
                self._analysis_pool.submit(self._on_candle_close, symbol)

        except Exception as e:
            logger.debug(f"WS msg error: {e}")

    def _on_candle_close(self, symbol: str):
        """Event-driven analysis — fires only on closed candle."""
        now = time.time()
        if now - self._last_signal.get(symbol, 0) < self._signal_cooldown:
            return

        open_syms = {p["symbol"] for p in self.state["open_positions"]}
        if symbol in open_syms:
            return

        if len(self.state["open_positions"]) >= self.max_positions:
            return

        klines = self.buffer.get(symbol)
        if len(klines) < 30:
            return

        signal = self.get_signal(klines)
        if signal["direction"] == "none" or signal["strength"] < 55:
            return

        atr_pct = signal.get("atr_pct", 0)
        if atr_pct > 2.0:
            return

        self._last_signal[symbol] = now
        signal["symbol"] = symbol
        signal["price"]  = klines[-1]["close"]

        # Non-blocking execution
        self._exec_pool.submit(self._execute_entry, signal)

    # ── EXECUTION ────────────────────────────────────────────────────────────

    def _execute_entry(self, signal: dict):
        pos = self._calc_position(signal)
        if not pos:
            return
        position = {
            "id":            len(self.state["trades"]) + 1,
            "symbol":        signal["symbol"],
            "direction":     signal["direction"],
            "entry_price":   signal["price"],
            "sl_price":      pos["sl"],
            "tp_price":      pos["tp"],
            "position_usdt": pos["size"],
            "quantity":      pos["qty"],
            "sl_pct":        pos["sl_pct"],
            "tp_pct":        pos["tp_pct"],
            "reasons":       signal.get("reasons", []),
            "strength":      signal["strength"],
            "rsi":           signal.get("rsi", 50),
            "timestamp":     datetime.now().isoformat(),
            "status":        "open"
        }
        self.state["open_positions"].append(position)
        save_state(self.state)
        icon = "🟢" if signal["direction"] == "long" else "🔴"
        print(f"{icon} {signal['symbol']} {signal['direction'].upper()} @ ${signal['price']:.4f} | {signal['strength']}/100 | SL${pos['sl']:.4f} TP${pos['tp']:.4f}")

    def _calc_position(self, signal: dict) -> dict | None:
        price   = signal["price"]
        if price <= 0:
            return None
        atr     = signal.get("atr_pct", 0.1)
        size    = max(10.0, min(
            self.capital * self.risk_per_trade * signal["strength"] / 100,
            self.capital * 0.05
        ))
        sl_pct  = max(atr * 1.5, 0.3) / 100
        tp_pct  = sl_pct * 2.5
        if signal["direction"] == "long":
            sl = round(price * (1 - sl_pct), 6)
            tp = round(price * (1 + tp_pct), 6)
        else:
            sl = round(price * (1 + sl_pct), 6)
            tp = round(price * (1 - tp_pct), 6)
        return {"size": round(size,2), "qty": round(size/price,6),
                "sl": sl, "tp": tp,
                "sl_pct": round(sl_pct*100,3), "tp_pct": round(tp_pct*100,3)}

    # ── POSITION MONITOR ─────────────────────────────────────────────────────

    def _monitor_positions(self):
        """Separate thread — checks open positions every 2s via WS buffer."""
        while self._running:
            try:
                self._check_positions_fast()
            except Exception as e:
                logger.debug(f"Monitor error: {e}")
            time.sleep(2)

    def _check_positions_fast(self):
        if not self.state["open_positions"]:
            return

        closed = []
        for pos in list(self.state["open_positions"]):
            sym    = pos["symbol"]
            klines = self.buffer.get(sym)
            if not klines:
                continue
            current   = klines[-1]["close"]
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

            pnl = pos["position_usdt"] * pnl_pct / 100

            if hit_tp:
                print(f"🎯 TP {sym} +${pnl:.2f}")
                self._close_position(pos, current, "tp", pnl)
                closed.append(pos["id"])
            elif hit_sl:
                print(f"🛑 SL {sym} -${abs(pnl):.2f}")
                self._close_position(pos, current, "sl", pnl)
                closed.append(pos["id"])
            elif pnl_pct > 1.0:
                # trailing
                if direction == "long":
                    new_sl = round(current * 0.997, 6)
                    if new_sl > pos["sl_price"]:
                        pos["sl_price"] = new_sl
                else:
                    new_sl = round(current * 1.003, 6)
                    if new_sl < pos["sl_price"]:
                        pos["sl_price"] = new_sl

        if closed:
            self.state["open_positions"] = [
                p for p in self.state["open_positions"] if p["id"] not in closed
            ]
            save_state(self.state)

    def _close_position(self, pos, exit_price, reason, pnl):
        trade = {**pos, "exit_price": exit_price, "exit_reason": reason,
                 "pnl_usdt": round(pnl,4),
                 "pnl_pct":  round((exit_price-pos["entry_price"])/pos["entry_price"]*100,3),
                 "closed_at": datetime.now().isoformat(), "status": "closed"}
        self.state["trades"].append(trade)
        self.state["total_pnl"]   = round(self.state["total_pnl"] + pnl, 4)
        self.state["session_pnl"] = round(self.state["session_pnl"] + pnl, 4)
        self.capital += pnl
        if pnl > 0: self.state["win_count"] += 1
        else:        self.state["loss_count"] += 1
        save_state(self.state)

    # ── POLLING FALLBACK ─────────────────────────────────────────────────────

    def _start_polling_fallback(self):
        """Used when WS unavailable — fast polling at 10s."""
        self._running = True
        logger.info("Polling fallback mode — 10s cycle")

        def poll():
            from concurrent.futures import as_completed
            self._seed_buffers()
            while self._running:
                open_syms = {p["symbol"] for p in self.state["open_positions"]}
                targets   = [s for s in WHITELIST if s not in open_syms][:10]
                futures   = {
                    self._analysis_pool.submit(
                        self._analyze_rest, s
                    ): s for s in targets
                }
                for f in as_completed(futures):
                    sym, signal = f.result()
                    if signal:
                        self._exec_pool.submit(self._execute_entry, signal)
                self._check_positions_fast()
                time.sleep(10)

        threading.Thread(target=poll, daemon=True).start()

    def _analyze_rest(self, symbol: str):
        try:
            klines = self.client.get_klines(symbol, interval="1m", limit=60)
            if len(klines) < 30:
                return symbol, None
            for k in klines:
                self.buffer.push(symbol, {"t": 0, **k})
            sig = self.get_signal(klines)
            if sig["direction"] == "none" or sig["strength"] < 55:
                return symbol, None
            if sig.get("atr_pct", 0) > 2.0:
                return symbol, None
            sig["symbol"] = symbol
            sig["price"]  = klines[-1]["close"]
            return symbol, sig
        except Exception as e:
            logger.debug(f"REST analyze {symbol}: {e}")
            return symbol, None

    # ── PUBLIC ───────────────────────────────────────────────────────────────

    def run(self):
        """Start event-driven bot — blocks until stopped."""
        print(f"⚡ ScalpingBot starting — WS event-driven mode")
        self.start_websocket()
        threading.Thread(target=self._monitor_positions, daemon=True).start()
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def run_once(self):
        """Compat shim for bot_controller polling mode."""
        if not self._running:
            self._seed_buffers()
            self._running = True
            threading.Thread(target=self._monitor_positions, daemon=True).start()

        open_syms = {p["symbol"] for p in self.state["open_positions"]}
        targets   = [s for s in WHITELIST if s not in open_syms][: self.max_positions * 2]

        from concurrent.futures import as_completed
        futures = {self._analysis_pool.submit(self._analyze_rest, s): s for s in targets}
        for f in as_completed(futures):
            sym, signal = f.result()
            if signal and len(self.state["open_positions"]) < self.max_positions:
                self._execute_entry(signal)

        self._check_positions_fast()
        t = self.state["win_count"] + self.state["loss_count"]
        wr = self.state["win_count"]/t*100 if t else 0
        print(f"📈 ${self.capital:.2f} | PnL ${self.state['total_pnl']:+.4f} | WR {wr:.1f}% | {len(self.state['open_positions'])} open")

    def stop(self):
        self._running = False
        if self._ws:
            try: self._ws.stop()
            except: pass
        self._exec_pool.shutdown(wait=False)
        self._analysis_pool.shutdown(wait=False)
        print("⛔ Bot stopped")

    def print_stats(self):
        self.run_once.__doc__  # just to have something callable
        t  = self.state["win_count"] + self.state["loss_count"]
        wr = self.state["win_count"]/t*100 if t else 0
        print(f"📈 ${self.capital:.2f} | PnL ${self.state['total_pnl']:+.4f} | WR {wr:.1f}% | {len(self.state['open_positions'])} open")

    # legacy open_position for bot_controller compatibility
    def open_position(self, signal):
        self._execute_entry(signal)
        return self.state["open_positions"][-1] if self.state["open_positions"] else None