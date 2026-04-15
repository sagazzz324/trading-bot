import threading
import traceback
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

MAX_DRAWDOWN   = 0.15
DAILY_LOSS_LIM = -50.0


class BybitState:
    def __init__(self):
        self.running          = False
        self.strategy         = "Auto"      # lo decide el orquestador
        self.active_strategy  = "—"         # estrategia actual corriendo
        self.regime           = "—"
        self.orch_reason      = "—"
        self.thread           = None
        self._lock            = threading.Lock()
        self._stop_event      = threading.Event()
        self.last_cycle       = None
        self.cycle_count      = 0
        self.logs             = []
        self.open_positions   = []
        self.closed_trades    = []
        self.balance          = 1000.0
        self.initial_balance  = 1000.0
        self.win_count        = 0
        self.loss_count       = 0
        self.session_pnl      = 0.0

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
            trade = {
                **pos,
                "exit_price":  exit_price,
                "exit_reason": reason,
                "pnl_usdt":    round(pnl, 4),
                "closed_at":   datetime.now().isoformat(),
                "status":      "closed"
            }
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
            "balance":         self.balance,
            "session_pnl":     self.session_pnl,
            "win_rate":        round(self.win_count / total * 100, 1) if total else 0.0,
            "total_trades":    total,
            "open":            len(self.open_positions),
            "drawdown":        round((self.initial_balance - self.balance) / self.initial_balance * 100, 2),
            "active_strategy": self.active_strategy,
            "regime":          self.regime,
            "orch_reason":     self.orch_reason,
        }


bybit_state = BybitState()


def _circuit_ok(st: BybitState) -> bool:
    dd = (st.initial_balance - st.balance) / st.initial_balance
    if dd >= MAX_DRAWDOWN:
        st.add_log(f"⛔ Drawdown {dd:.1%} — halted", "#FF5050")
        return False
    if st.session_pnl <= DAILY_LOSS_LIM:
        st.add_log(f"⛔ Daily loss ${st.session_pnl:.2f} — halted", "#FF5050")
        return False
    return True


def _run_bot(st: BybitState):
    st.add_log("Bot Bybit iniciado · modo Auto", "#00E887")

    # Test conexión + crear orquestador
    try:
        from src.exchanges.bybit_client import BybitClient
        from src.strategies.strategy_orchestrator import StrategyOrchestrator
        client = BybitClient()
        price  = client.get_price("BTCUSDT")
        st.add_log(f"✅ Bybit conectado · BTC ${price:,.2f}", "#00E887")
        orchestrator = StrategyOrchestrator(client)
    except Exception as e:
        st.add_log(f"❌ Error Bybit: {str(e)[:60]}", "#FF5050")
        logger.error(traceback.format_exc())
        st.running = False
        return

    while st.running:
        if not _circuit_ok(st):
            st.running = False
            break
        try:
            st.cycle_count += 1
            st.last_cycle   = datetime.now().strftime("%H:%M:%S")

            # ── Orquestador decide estrategia ──
            decision = orchestrator.decide()
            strategy = decision["strategy"]
            st.active_strategy = strategy
            st.regime          = decision["regime"]
            st.orch_reason     = decision["reason"]

            st.add_log(
                f"Ciclo #{st.cycle_count} · {strategy} · {decision['regime']} · "
                f"slope={decision['slope']:.5f} ATR={decision['atr_pct']:.2f}%",
                "#ffffff40"
            )

            if strategy == "Pause":
                st.add_log(f"⏸️  Pausado — {decision['reason']}", "#F5A623")

            elif strategy == "Scalping":
                _run_scalping_cycle(st)

            elif strategy == "Grid":
                _run_grid_cycle(st)

            # Intervalo más corto en scalping, más largo en grid
            wait = 60 if strategy == "Scalping" else 120
            st._stop_event.wait(timeout=wait)
            st._stop_event.clear()

        except Exception as e:
            tb    = traceback.format_exc()
            lines = [l.strip() for l in tb.strip().split("\n") if l.strip()]
            st.add_log(f"❌ {str(e)[:80]}", "#FF5050")
            for line in lines[-3:]:
                st.add_log(f"  {line[:90]}", "#FF5050")
            logger.error(f"Bybit loop:\n{tb}")
            time.sleep(30)

    st.add_log("Bot Bybit detenido", "#FF5050")


def _run_scalping_cycle(st: BybitState):
    import builtins
    from src.strategies.scalper import ScalpingBot

    bot = ScalpingBot(max_positions=3, risk_per_trade=0.01, capital=st.balance)
    with st._lock:
        bot.state["open_positions"] = list(st.open_positions)
    bot.capital     = st.balance
    bot.initial_cap = st.initial_balance

    orig_print = builtins.print

    def log_print(*args, **kwargs):
        msg = " ".join(str(a) for a in args).strip()
        if not msg or set(msg) <= {"=", "-", " "}:
            return
        color = (
            "#00E887" if any(x in msg for x in ["✅","🟢","🎯","TP","seeded","conectado"]) else
            "#FF5050" if any(x in msg for x in ["🛑","⛔","❌","SL","Error"]) else
            "#41d6fc" if any(x in msg for x in ["📡","📈","⚡","Ciclo","📐","Scan"]) else
            "#ffffff60"
        )
        st.add_log(msg, color)
        orig_print(*args, **kwargs)

    builtins.print = log_print

    try:
        orig_close = bot._close_position
        def close_sync(pos, exit_price, reason, pnl):
            orig_close(pos, exit_price, reason, pnl)
            st.close_position(pos["id"], exit_price, reason, pnl)
        bot._close_position = close_sync

        orig_open = bot.open_position
        def open_sync(signal):
            pos = orig_open(signal)
            if pos:
                st.add_position(pos)
            return pos
        bot.open_position = open_sync

        bot.run_once()
        with st._lock:
            st.balance = bot.capital

        s = st.get_stats()
        st.add_log(
            f"Scalping · ${s['balance']:.2f} · "
            f"WR {s['win_rate']:.0f}% · DD {s['drawdown']:.1f}%",
            "#41d6fc"
        )

    except Exception as e:
        tb    = traceback.format_exc()
        lines = [l.strip() for l in tb.strip().split("\n") if l.strip()]
        st.add_log(f"❌ Scalping: {str(e)[:80]}", "#FF5050")
        for line in lines[-3:]:
            st.add_log(f"  {line[:90]}", "#FF5050")
        logger.error(f"Scalping cycle:\n{tb}")
    finally:
        builtins.print = orig_print


def _run_grid_cycle(st: BybitState):
    try:
        from src.strategies.grid.main import run_once as grid_run_once
        grid_run_once(st)
    except Exception as e:
        tb = traceback.format_exc()
        st.add_log(f"❌ Grid: {str(e)[:80]}", "#FF5050")
        logger.error(f"Grid cycle:\n{tb}")


def start_bybit(strategy=None) -> bool:
    if bybit_state.running:
        return False
    bybit_state.running = True
    bybit_state._stop_event.clear()
    bybit_state.thread = threading.Thread(
        target=_run_bot, args=(bybit_state,), daemon=True
    )
    bybit_state.thread.start()
    return True


def stop_bybit() -> bool:
    bybit_state.running = False
    bybit_state._stop_event.set()
    return True
