import threading
import traceback
import logging
import time
import builtins
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class PolyState:
    def __init__(self):
        self.running      = False
        self.thread       = None
        self._lock        = threading.Lock()
        self._stop_event  = threading.Event()
        self.interval     = 15
        self.last_cycle   = None
        self.cycle_count  = 0
        self.logs         = []
        self.market_mode  = "btc_scalp"

    def add_log(self, msg, color="#ffffff"):
        with self._lock:
            self.logs.insert(0, {
                "time":  datetime.now(timezone(timedelta(hours=-3))).strftime("%H:%M:%S"),
                "msg":   msg,
                "color": color
            })
            if len(self.logs) > 120:
                self.logs.pop()

    def reset_runtime(self):
        with self._lock:
            self.logs = []
            self.last_cycle = None
            self.cycle_count = 0


poly_state = PolyState()


def _close_open_poly_positions(st: PolyState):
    try:
        from src.core.paper_trader import PaperTrader

        trader = PaperTrader(log_fn=st.add_log)
        active = trader.load_state().get("active_trades", [])
        if not active:
            return

        st.add_log(f"Cerrando {len(active)} trade(s) abiertos antes de detener", "#F5A623")
        result = trader.force_close_stale_trades(pnl_per_trade=0.0)
        st.add_log(f"Cierre al detener: {result['closed']}/{result['attempted']} trade(s) cerrados", "#41d6fc")
    except Exception as e:
        st.add_log(f"❌ Error cerrando trades al detener: {str(e)[:80]}", "#FF5050")
        logger.error(f"_close_open_poly_positions:\n{traceback.format_exc()}")


def _run_general(st: PolyState):
    from src.core.bot import TradingBot
    from src.core.resolver import auto_resolve_trades

    mode_label = {"crypto": "🪙 Crypto", "politics": "🏛️ Política", "all": "🌐 Todo"}.get(st.market_mode, st.market_mode)
    st.add_log(f"Bot Polymarket iniciado · {mode_label}", "#00E887")
    bot = TradingBot()
    orig_print = builtins.print

    def log_print(*args, **kwargs):
        msg = " ".join(str(a) for a in args).strip()
        if not msg or set(msg) <= {"=", "-", " "}:
            return
        color = (
            "#00E887" if any(x in msg for x in ["✅","🟢","TRADE","WIN","bankroll"]) else
            "#FF5050" if any(x in msg for x in ["❌","🔴","LOSS","Error","STOP","🛑"]) else
            "#F5A623" if any(x in msg for x in ["⏭️","skip","EV","Confianza","🚫","🤷","💰","🎯","⏸️"]) else
            "#41d6fc" if any(x in msg for x in ["🔍","📊","Ciclo","Evaluando","Escaneando","Bankroll","whale","🛡️"]) else
            "#ffffff60"
        )
        st.add_log(msg, color)
        orig_print(*args, **kwargs)

    builtins.print = log_print

    try:
        while st.running:
            try:
                st.cycle_count += 1
                st.last_cycle   = datetime.now().strftime("%H:%M:%S")
                st.add_log(f"━━ Ciclo #{st.cycle_count} · {mode_label} ━━", "#41d6fc")
                auto_resolve_trades()
                bot.run_once(mode=st.market_mode)
                st._stop_event.wait(timeout=st.interval * 60)
                st._stop_event.clear()
            except Exception as e:
                tb    = traceback.format_exc()
                lines = [l.strip() for l in tb.strip().split("\n") if l.strip()]
                st.add_log(f"❌ {str(e)[:80]}", "#FF5050")
                for line in lines[-3:]:
                    st.add_log(f"  {line[:90]}", "#FF5050")
                logger.error(f"Poly loop:\n{tb}")
                time.sleep(60)
    finally:
        builtins.print = orig_print
        st.add_log("Bot Polymarket detenido", "#FF5050")


def _run_btc_scalp(st: PolyState):
    from src.core.paper_trader import PaperTrader
    from src.core.btc_scalper import BTCScalper

    st.add_log("Bot BTC Scalp iniciado · Up/Down 5m", "#00E887")
    trader  = PaperTrader(log_fn=st.add_log)
    scalper = BTCScalper(trader=trader, log_fn=st.add_log)

    while st.running:
        try:
            st.cycle_count += 1
            scalper.run_once()
            st._stop_event.wait(timeout=20)
            st._stop_event.clear()
        except Exception as e:
            tb    = traceback.format_exc()
            lines = [l.strip() for l in tb.strip().split("\n") if l.strip()]
            st.add_log(f"❌ {str(e)[:80]}", "#FF5050")
            for line in lines[-3:]:
                st.add_log(f"  {line[:90]}", "#FF5050")
            logger.error(f"BTC scalp loop:\n{tb}")
            time.sleep(60)

    st.add_log("Bot BTC Scalp detenido", "#FF5050")


def start_poly(mode="btc_scalp") -> bool:
    if poly_state.running:
        return False
    poly_state.reset_runtime()
    poly_state.market_mode = mode
    poly_state.running = True
    poly_state._stop_event.clear()
    target = _run_btc_scalp if mode == "btc_scalp" else _run_general
    poly_state.thread = threading.Thread(
        target=target, args=(poly_state,), daemon=True
    )
    poly_state.thread.start()
    return True


def stop_poly() -> bool:
    poly_state.running = False
    poly_state._stop_event.set()
    try:
        if poly_state.thread and poly_state.thread.is_alive():
            poly_state.thread.join(timeout=5)
    except Exception:
        logger.error(f"stop_poly join:\n{traceback.format_exc()}")
    _close_open_poly_positions(poly_state)
    return True


def set_poly_mode(mode: str):
    poly_state.market_mode = mode
    poly_state.add_log(f"Modo cambiado → {mode}", "#41d6fc")
