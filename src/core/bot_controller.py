import threading
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

MAX_DRAWDOWN   = 0.15
DAILY_LOSS_LIM = -50.0


class BotState:
    def __init__(self):
        self.poly_running     = False
        self.binance_running  = False
        self.poly_thread      = None
        self.binance_thread   = None
        self.poly_interval    = 15
        self.binance_interval = 1       # minutes between cycles
        self.binance_strategy = "Scalping"
        self.binance_profile  = "Moderado"
        self.last_cycle       = None
        self.cycle_count      = 0
        self._lock            = threading.Lock()
        self._stop_event      = threading.Event()

        # In-memory state — no file dependency in Railway
        self.logs           = []
        self.open_positions = []
        self.closed_trades  = []
        self.balance        = 1000.0
        self.initial_balance = 1000.0
        self.win_count      = 0
        self.loss_count     = 0
        self.session_pnl    = 0.0

    def add_log(self, msg, color="#ffffff"):
        with self._lock:
            self.logs.insert(0, {
                "time":  datetime.now().strftime("%H:%M:%S"),
                "msg":   msg,
                "color": color
            })
            if len(self.logs) > 120:
                self.logs.pop()

    def add_position(self, pos):
        with self._lock:
            self.open_positions.append(pos)

    def close_position(self, pos_id, exit_price, reason, pnl):
        with self._lock:
            pos = next((p for p in self.open_positions if p["id"] == pos_id), None)
            if not pos:
                return
            self.open_positions = [p for p in self.open_positions if p["id"] != pos_id]
            trade = {**pos, "exit_price": exit_price, "exit_reason": reason,
                     "pnl_usdt": round(pnl, 4), "closed_at": datetime.now().isoformat(),
                     "status": "closed"}
            self.closed_trades.insert(0, trade)
            if len(self.closed_trades) > 50:
                self.closed_trades.pop()
            self.balance     = round(self.balance + pnl, 2)
            self.session_pnl = round(self.session_pnl + pnl, 4)
            if pnl > 0: self.win_count += 1
            else:       self.loss_count += 1

    def get_stats(self):
        total = self.win_count + self.loss_count
        return {
            "balance":      self.balance,
            "session_pnl":  self.session_pnl,
            "win_rate":     round(self.win_count / total * 100, 1) if total else 0.0,
            "total_trades": total,
            "open":         len(self.open_positions),
            "drawdown":     round((self.initial_balance - self.balance) / self.initial_balance * 100, 2),
        }


state = BotState()


# ── CIRCUIT BREAKER CHECK ─────────────────────────────────────────────────────

def _circuit_ok(st: BotState) -> bool:
    dd = (st.initial_balance - st.balance) / st.initial_balance
    if dd >= MAX_DRAWDOWN:
        st.add_log(f"⛔ Circuit breaker: drawdown {dd:.1%} — bot halted", "#FF5050")
        return False
    if st.session_pnl <= DAILY_LOSS_LIM:
        st.add_log(f"⛔ Daily loss limit ${st.session_pnl:.2f} — bot halted", "#FF5050")
        return False
    return True


# ── POLYMARKET BOT ────────────────────────────────────────────────────────────

def run_poly_bot(st: BotState):
    from src.core.bot import TradingBot
    from src.core.resolver import auto_resolve_trades
    bot = TradingBot()
    st.add_log("Bot Polymarket iniciado", "#00E887")

    while st.poly_running:
        try:
            st.cycle_count += 1
            st.last_cycle   = datetime.now().strftime("%H:%M:%S")
            st.add_log(f"Ciclo #{st.cycle_count} · Polymarket", "#ffffff40")
            auto_resolve_trades()
            bot.run_once()
            st._stop_event.wait(timeout=st.poly_interval * 60)
            st._stop_event.clear()
        except Exception as e:
            st.add_log(f"Error Poly: {str(e)[:60]}", "#FF5050")
            logger.error(f"Poly error: {e}")
            time.sleep(60)

    st.add_log("Bot Polymarket detenido", "#FF5050")


# ── BINANCE BOT ───────────────────────────────────────────────────────────────

def run_binance_bot(st: BotState):
    st.add_log(f"Bot Binance iniciado · {st.binance_strategy}", "#00E887")

    # Test conexión antes de arrancar
    try:
        from src.exchanges.binance_client import BinanceClient
        BinanceClient().get_price("BTCUSDT")
    except Exception as e:
        if "restricted location" in str(e).lower():
            st.add_log("⚠️ Binance bloqueado en esta región — activá testnet en .env", "#F5A623")
        else:
            st.add_log(f"⚠️ Error conectando a Binance: {str(e)[:60]}", "#FF5050")
        st.binance_running = False
        return

    while st.binance_running:
        if not _circuit_ok(st):
            st.binance_running = False
            break
        try:
            strategy = st.binance_strategy
            st.cycle_count += 1
            st.last_cycle   = datetime.now().strftime("%H:%M:%S")
            st.add_log(f"Ciclo #{st.cycle_count} · {strategy}", "#ffffff30")

            if strategy in ("Scalping", "Scalping Momentum"):
                _run_scalping_cycle(st)
            elif strategy == "Market Making":
                _run_mm_cycle(st)
            elif strategy == "Mean Reversion":
                _run_mr_cycle(st)

            # Non-blocking sleep
            st._stop_event.wait(timeout=st.binance_interval * 60)
            st._stop_event.clear()

        except Exception as e:
            st.add_log(f"Error Binance: {str(e)[:80]}", "#FF5050")
            logger.error(f"Binance error: {e}", exc_info=True)
            time.sleep(30)

    st.add_log("Bot Binance detenido", "#FF5050")


def _run_scalping_cycle(st: BotState):
    """Intercept all bot prints → dashboard logs. Sync positions to memory."""
    import builtins
    from src.strategies.scalper import ScalpingBot

    bot = ScalpingBot(
        max_positions=3,
        risk_per_trade=0.01,
        capital=st.balance
    )

    # Sync open positions from memory state
    with st._lock:
        bot.state["open_positions"] = list(st.open_positions)
    bot.capital      = st.balance
    bot.initial_cap  = st.initial_balance

    # Route all print() → dashboard log
    orig_print = builtins.print

    def log_print(*args, **kwargs):
        msg = " ".join(str(a) for a in args).strip()
        if not msg or set(msg) <= {"=", "-", " "}:
            return
        color = (
            "#00E887" if any(x in msg for x in ["✅","🟢","🎯","LONG","TP","seeded"]) else
            "#FF5050" if any(x in msg for x in ["🛑","🔴","SL","SHORT","⛔","Error"]) else
            "#41d6fc" if any(x in msg for x in ["📡","📈","⚡","Ciclo","Scan","📐","Trailing"]) else
            "#ffffff60"
        )
        st.add_log(msg, color)
        orig_print(*args, **kwargs)

    builtins.print = log_print

    try:
        # Patch close → sync to memory
        orig_close = bot._close_position
        def close_sync(pos, exit_price, reason, pnl):
            orig_close(pos, exit_price, reason, pnl)
            st.close_position(pos["id"], exit_price, reason, pnl)
        bot._close_position = close_sync

        # Patch open → sync to memory
        orig_open = bot.open_position
        def open_sync(signal):
            pos = orig_open(signal)
            if pos:
                st.add_position(pos)
            return pos
        bot.open_position = open_sync

        bot.run_once()

        # Sync final state
        with st._lock:
            st.balance = bot.capital

        stats = st.get_stats()
        st.add_log(
            f"Scalping · ${stats['balance']:.2f} · WR {stats['win_rate']:.0f}% · "
            f"DD {stats['drawdown']:.1f}% · {stats['open']} abiertas",
            "#41d6fc"
        )

    finally:
        builtins.print = orig_print


def _run_mm_cycle(st: BotState):
    from src.strategies.market_making import MarketMaker
    from src.strategies.market_making_profiles import PROFILES
    profile_key = next(
        (k for k, v in PROFILES.items() if v["name"].split(" ", 1)[-1] == st.binance_profile), "2"
    )
    bot = MarketMaker(symbol="BTCUSDT", params=PROFILES[profile_key]["params"])
    bot.run()
    st.add_log("Market Making · ciclo completado", "#41d6fc")


def _run_mr_cycle(st: BotState):
    from src.strategies.mean_reversion import MeanReversionStrategy
    bot = MeanReversionStrategy(symbol="BTCUSDT")
    bot.run(cycles=2)
    st.add_log("Mean Reversion · ciclo completado", "#41d6fc")


# ── CONTROLS ─────────────────────────────────────────────────────────────────

def start_poly(st: BotState) -> bool:
    if st.poly_running:
        return False
    st.poly_running = True
    st._stop_event.clear()
    st.poly_thread = threading.Thread(target=run_poly_bot, args=(st,), daemon=True)
    st.poly_thread.start()
    return True

def stop_poly(st: BotState) -> bool:
    st.poly_running = False
    st._stop_event.set()
    return True

def start_binance(st: BotState) -> bool:
    if st.binance_running:
        return False
    st.binance_running = True
    st._stop_event.clear()
    st.binance_thread = threading.Thread(target=run_binance_bot, args=(st,), daemon=True)
    st.binance_thread.start()
    return True

def stop_binance(st: BotState) -> bool:
    st.binance_running = False
    st._stop_event.set()
    return True
