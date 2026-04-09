import threading
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class BotState:
    def __init__(self):
        self.poly_running     = False
        self.binance_running  = False
        self.poly_thread      = None
        self.binance_thread   = None
        self.poly_interval    = 15
        self.binance_interval = 2
        self.binance_strategy = "Scalping"
        self.binance_profile  = "Moderado"
        self.last_cycle       = None
        self.cycle_count      = 0
        self.lock             = threading.Lock()

        # Estado en memoria (no depende de archivos JSON)
        self.logs          = []
        self.open_positions = []
        self.closed_trades  = []
        self.balance        = 1000.0
        self.initial_balance = 1000.0
        self.win_count      = 0
        self.loss_count     = 0
        self.session_pnl    = 0.0

    def add_log(self, msg, color="#ffffff"):
        with self.lock:
            self.logs.insert(0, {
                "time":  datetime.now().strftime("%H:%M:%S"),
                "msg":   msg,
                "color": color
            })
            if len(self.logs) > 100:
                self.logs.pop()

    def add_position(self, position):
        with self.lock:
            self.open_positions.append(position)

    def close_position(self, pos_id, exit_price, reason, pnl):
        with self.lock:
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
            if pnl > 0:
                self.win_count += 1
            else:
                self.loss_count += 1

    def get_stats(self):
        total = self.win_count + self.loss_count
        return {
            "balance":      self.balance,
            "session_pnl":  self.session_pnl,
            "win_rate":     round(self.win_count / total * 100, 1) if total else 0,
            "total_trades": total,
            "open":         len(self.open_positions),
        }


# Instancia global
state = BotState()


def run_poly_bot(st):
    from src.core.bot import TradingBot
    from src.core.resolver import auto_resolve_trades

    bot = TradingBot()
    st.add_log("Bot Polymarket iniciado", "#00FF9C")

    while st.poly_running:
        try:
            st.cycle_count += 1
            st.last_cycle   = datetime.now().strftime("%H:%M:%S")
            st.add_log(f"Ciclo #{st.cycle_count} · escaneando mercados", "#ffffff40")
            auto_resolve_trades()
            bot.run_once()
            for _ in range(st.poly_interval * 4):
                if not st.poly_running:
                    break
                time.sleep(15)
        except Exception as e:
            st.add_log(f"Error Poly: {str(e)[:60]}", "#FF4D4D")
            logger.error(f"Error bot poly: {e}")
            time.sleep(60)

    st.add_log("Bot Polymarket detenido", "#FF4D4D")


def run_binance_bot(st):
    st.add_log(f"Bot Binance iniciado · {st.binance_strategy}", "#00FF9C")

    while st.binance_running:
        try:
            strategy = st.binance_strategy
            st.cycle_count += 1
            st.last_cycle   = datetime.now().strftime("%H:%M:%S")
            st.add_log(f"Ciclo #{st.cycle_count} · {strategy}", "#ffffff40")

            if strategy in ("Scalping", "Scalping Momentum"):
                _run_scalping_cycle(st)

            elif strategy == "Market Making":
                from src.strategies.market_making import MarketMaker
                from src.strategies.market_making_profiles import PROFILES
                profile_key = next(
                    (k for k, v in PROFILES.items()
                     if v["name"].split(" ", 1)[-1] == st.binance_profile), "2"
                )
                bot = MarketMaker(symbol="BTCUSDT", params=PROFILES[profile_key]["params"])
                bot.run()
                st.add_log("Market Making · ciclo completado", "#41d6fc")

            elif strategy == "Mean Reversion":
                from src.strategies.mean_reversion import MeanReversionStrategy
                bot = MeanReversionStrategy(symbol="BTCUSDT")
                bot.run(cycles=2)
                st.add_log("Mean Reversion · ciclo completado", "#41d6fc")

            elif strategy == "Arbitraje Triangular":
                from src.strategies.arbitrage import TriangularArbitrage
                bot = TriangularArbitrage()
                bot.scan()
                st.add_log("Arbitraje · scan completado", "#41d6fc")

            # Esperar intervalo
            for _ in range(st.binance_interval * 60):
                if not st.binance_running:
                    break
                time.sleep(1)

        except Exception as e:
            st.add_log(f"Error Binance: {str(e)[:60]}", "#FF4D4D")
            logger.error(f"Error bot binance: {e}")
            time.sleep(30)

    st.add_log("Bot Binance detenido", "#FF4D4D")


def _run_scalping_cycle(st):
    """Corre un ciclo de scalping y sincroniza el estado en memoria."""
    from src.strategies.scalper import ScalpingBot
    from src.exchanges.binance_client import BinanceClient

    # Crear bot con estado actual en memoria
    bot = ScalpingBot(
        max_positions=3,
        risk_per_trade=0.01,
        capital=st.balance
    )

    # Sincronizar posiciones abiertas desde memoria
    bot.state["open_positions"] = list(st.open_positions)

    # Monkey-patch _close_position para capturar cierres en tiempo real
    original_close = bot._close_position

    def close_and_sync(pos, exit_price, reason, pnl_usdt):
        original_close(pos, exit_price, reason, pnl_usdt)
        st.close_position(pos["id"], exit_price, reason, pnl_usdt)
        direction = "▲" if pos["direction"] == "long" else "▼"
        color = "#00FF9C" if pnl_usdt > 0 else "#FF4D4D"
        st.add_log(
            f"{direction} {pos['symbol']} cerrado {reason.upper()} · PnL ${pnl_usdt:+.4f}",
            color
        )

    bot._close_position = close_and_sync

    # Monkey-patch open_position para capturar aperturas
    original_open = bot.open_position

    def open_and_sync(signal):
        pos = original_open(signal)
        if pos:
            st.add_position(pos)
            direction = "🟢 LONG" if signal["direction"] == "long" else "🔴 SHORT"
            st.add_log(
                f"{direction} {signal['symbol']} @ ${signal['price']:.4f} · fuerza {signal['strength']}/100",
                "#00FF9C" if signal["direction"] == "long" else "#FF4D4D"
            )
        return pos

    bot.open_position = open_and_sync

    # Ejecutar ciclo
    bot.run_once()

    # Sincronizar estado post-ciclo
    st.balance     = bot.capital
    st.session_pnl = round(st.session_pnl + bot.state.get("session_pnl", 0), 4)
    st.win_count   = bot.state.get("win_count", st.win_count)
    st.loss_count  = bot.state.get("loss_count", st.loss_count)

    stats = st.get_stats()
    st.add_log(
        f"Scalping · balance ${stats['balance']:.2f} · "
        f"WR {stats['win_rate']:.0f}% · {stats['open']} abiertas",
        "#41d6fc"
    )


def start_poly(st):
    if st.poly_running:
        return False
    st.poly_running = True
    st.poly_thread  = threading.Thread(target=run_poly_bot, args=(st,), daemon=True)
    st.poly_thread.start()
    return True


def stop_poly(st):
    st.poly_running = False
    return True


def start_binance(st):
    if st.binance_running:
        return False
    st.binance_running = True
    st.binance_thread  = threading.Thread(target=run_binance_bot, args=(st,), daemon=True)
    st.binance_thread.start()
    return True


def stop_binance(st):
    st.binance_running = False
    return True