import logging
from src.exchanges.binance_client import BinanceClient
from src.skills.binance_signals import scan_binance_opportunities
from src.core.risk_engine import kelly_position_size
from config.settings import BANKROLL, PAPER_TRADING

logger = logging.getLogger(__name__)


class BinanceTrader:

    def __init__(self):
        self.client = BinanceClient()
        self.trades = []
        logger.info("BinanceTrader iniciado")

    def run_once(self, bankroll=None):
        """
        Escanea y ejecuta trades en Binance.
        """
        if bankroll is None:
            bankroll = BANKROLL

        print("\n" + "="*50)
        print("📈 BINANCE — Escaneando oportunidades")
        print("="*50)

        opportunities = scan_binance_opportunities(top_n=30)

        if not opportunities:
            print("Sin oportunidades en Binance")
            return []

        executed = []
        for opp in opportunities[:3]:
            symbol = opp["symbol"]
            signal = opp["signal"]
            price = opp["price"]

            # Sizing conservador para crypto (5% del bankroll)
            position_usd = bankroll * 0.05
            quantity = round(position_usd / price, 6)

            if quantity <= 0:
                continue

            print(f"\n{'🟢 COMPRANDO' if signal == 'BUY' else '🔴 VENDIENDO'}: {symbol}")
            print(f"   Precio: ${price:,.4f}")
            print(f"   Cantidad: {quantity}")
            print(f"   Valor: ${position_usd:.2f}")
            print(f"   RSI: {opp['rsi']:.1f} | Razon: {opp['reason']}")

            # Ejecutar orden (paper o real)
            side = "BUY" if signal == "BUY" else "SELL"
            order = self.client.place_order(symbol, side, quantity)

            if order:
                trade = {
                    "symbol": symbol,
                    "side": side,
                    "price": price,
                    "quantity": quantity,
                    "value": position_usd,
                    "rsi": opp["rsi"],
                    "paper": PAPER_TRADING
                }
                self.trades.append(trade)
                executed.append(trade)
                print(f"   ✅ Orden {'simulada' if PAPER_TRADING else 'ejecutada'}")

        return executed