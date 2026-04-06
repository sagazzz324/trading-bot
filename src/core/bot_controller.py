import threading
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

class BotState:
    """Estado compartido entre el bot y el dashboard."""
    def __init__(self):
        self.poly_running = False
        self.binance_running = False
        self.poly_thread = None
        self.binance_thread = None
        self.poly_interval = 15
        self.binance_interval = 5
        self.binance_strategy = "Market Making"
        self.binance_profile = "Moderado"
        self.last_cycle = None
        self.cycle_count = 0
        self.logs = []
        self.lock = threading.Lock()

    def add_log(self, msg, color="#41d6fc"):
        with self.lock:
            self.logs.insert(0, {
                "time": datetime.now().strftime("%H:%M"),
                "msg": msg,
                "color": color
            })
            if len(self.logs) > 50:
                self.logs.pop()

# Instancia global
state = BotState()


def run_poly_bot(st):
    """Loop del bot de Polymarket."""
    from src.core.bot import TradingBot
    from src.core.resolver import auto_resolve_trades

    bot = TradingBot()
    st.add_log("Bot Polymarket iniciado", "#5db05e")

    while st.poly_running:
        try:
            st.cycle_count += 1
            st.last_cycle = datetime.now().strftime("%H:%M:%S")
            st.add_log(f"Ciclo #{st.cycle_count} · escaneando mercados", "#ffffff40")

            auto_resolve_trades()
            bot.run_once()

            # Esperar el intervalo en chunks para poder parar
            for _ in range(st.poly_interval * 4):
                if not st.poly_running:
                    break
                time.sleep(15)

        except Exception as e:
            st.add_log(f"Error: {str(e)[:60]}", "#ff5566")
            logger.error(f"Error en bot poly: {e}")
            time.sleep(60)

    st.add_log("Bot Polymarket detenido", "#ff5566")


def run_binance_bot(st):
    """Loop del bot de Binance."""
    from src.strategies.market_making import MarketMaker
    from src.strategies.scalping import MomentumScalper
    from src.strategies.mean_reversion import MeanReversionStrategy
    from src.strategies.arbitrage import TriangularArbitrage
    from src.strategies.market_making_profiles import PROFILES

    st.add_log(f"Bot Binance iniciado · {st.binance_strategy}", "#5db05e")

    while st.binance_running:
        try:
            strategy = st.binance_strategy

            if strategy == "Market Making":
                profile_key = next(
                    (k for k,v in PROFILES.items() if v["name"].split(" ",1)[-1] == st.binance_profile),
                    "2"
                )
                params = PROFILES[profile_key]["params"]
                bot = MarketMaker(symbol="BTCUSDT", params=params)
                bot.run()
            elif strategy == "Scalping Momentum":
                bot = MomentumScalper(symbol="BTCUSDT")
                bot.run(cycles=3)
            elif strategy == "Mean Reversion":
                bot = MeanReversionStrategy(symbol="BTCUSDT")
                bot.run(cycles=2)
            elif strategy == "Arbitraje Triangular":
                bot = TriangularArbitrage()
                bot.scan()

            st.add_log(f"Binance {strategy} · ciclo completado", "#41d6fc")

            for _ in range(st.binance_interval * 4):
                if not st.binance_running:
                    break
                time.sleep(15)

        except Exception as e:
            st.add_log(f"Error Binance: {str(e)[:60]}", "#ff5566")
            time.sleep(60)

    st.add_log("Bot Binance detenido", "#ff5566")


def start_poly(st):
    if st.poly_running:
        return False
    st.poly_running = True
    st.poly_thread = threading.Thread(target=run_poly_bot, args=(st,), daemon=True)
    st.poly_thread.start()
    return True


def stop_poly(st):
    st.poly_running = False
    return True


def start_binance(st):
    if st.binance_running:
        return False
    st.binance_running = True
    st.binance_thread = threading.Thread(target=run_binance_bot, args=(st,), daemon=True)
    st.binance_thread.start()
    return True


def stop_binance(st):
    st.binance_running = False
    return True