import threading
import traceback
import logging
import time
from datetime import datetime

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

    def add_log(self, msg, color="#ffffff"):
        with self._lock:
            self.logs.insert(0, {
                "time":  datetime.now().strftime("%H:%M:%S"),
                "msg":   msg,
                "color": color
            })
            if len(self.logs) > 120:
                self.logs.pop()


poly_state = PolyState()


def _run_bot(st: PolyState):
    from src.core.bot import TradingBot
    from src.core.resolver import auto_resolve_trades

    st.add_log("Bot Polymarket iniciado", "#00E887")
    bot = TradingBot()

    while st.running:
        try:
            st.cycle_count += 1
            st.last_cycle   = datetime.now().strftime("%H:%M:%S")
            st.add_log(f"Ciclo #{st.cycle_count} · escaneando mercados", "#ffffff40")
            auto_resolve_trades()
            bot.run_once()
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

    st.add_log("Bot Polymarket detenido", "#FF5050")


def start_poly() -> bool:
    if poly_state.running:
        return False
    poly_state.running = True
    poly_state._stop_event.clear()
    poly_state.thread = threading.Thread(
        target=_run_bot, args=(poly_state,), daemon=True
    )
    poly_state.thread.start()
    return True


def stop_poly() -> bool:
    poly_state.running = False
    poly_state._stop_event.set()
    return True
