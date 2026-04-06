import logging
from src.exchanges.binance_client import BinanceClient
from src.skills.binance_signals import calculate_rsi

logger = logging.getLogger(__name__)


class MeanReversionStrategy:
    """
    Estrategia de Mean Reversion.
    Usa RSI + Bollinger Bands para detectar extremos.
    """

    def __init__(self, symbol="BTCUSDT", rsi_low=30, rsi_high=70):
        self.symbol = symbol
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.client = BinanceClient()
        self.trades = []
        self.pnl = 0

    def calculate_bollinger(self, closes, period=20, std_dev=2):
        """Calcula Bollinger Bands."""
        if len(closes) < period:
            return None

        recent = closes[-period:]
        sma = sum(recent) / period
        variance = sum((x - sma) ** 2 for x in recent) / period
        std = variance ** 0.5

        upper = sma + (std_dev * std)
        lower = sma - (std_dev * std)

        return {
            "upper": upper,
            "middle": sma,
            "lower": lower,
            "std": std
        }

    def analyze(self):
        """
        Analiza el símbolo con RSI + Bollinger.
        """
        klines = self.client.get_klines(self.symbol, interval="1h", limit=50)
        if len(klines) < 20:
            return None

        closes = [k["close"] for k in klines]
        current_price = closes[-1]

        rsi = calculate_rsi(closes)
        bb = self.calculate_bollinger(closes)

        if not bb:
            return None

        # Determinar posición en Bollinger
        bb_position = (current_price - bb["lower"]) / (bb["upper"] - bb["lower"])

        signal = None
        reason = ""

        # Oversold: RSI bajo + precio cerca de banda inferior
        if rsi < self.rsi_low and bb_position < 0.2:
            signal = "BUY"
            reason = f"RSI {rsi:.1f} + precio en banda inferior (BB pos: {bb_position:.2f})"

        # Overbought: RSI alto + precio cerca de banda superior
        elif rsi > self.rsi_high and bb_position > 0.8:
            signal = "SELL"
            reason = f"RSI {rsi:.1f} + precio en banda superior (BB pos: {bb_position:.2f})"

        return {
            "price": current_price,
            "rsi": rsi,
            "bb_upper": bb["upper"],
            "bb_middle": bb["middle"],
            "bb_lower": bb["lower"],
            "bb_position": bb_position,
            "signal": signal,
            "reason": reason
        }

    def run(self, cycles=3):
        """
        Corre la estrategia de mean reversion.
        """
        print(f"\n📉 MEAN REVERSION — {self.symbol}")
        print(f"   RSI oversold: <{self.rsi_low} | overbought: >{self.rsi_high}")
        print("="*50)

        for i in range(cycles):
            print(f"\n🔍 Ciclo {i+1}/{cycles}")
            data = self.analyze()

            if not data:
                print("   Sin datos suficientes")
                continue

            print(f"   Precio: ${data['price']:,.2f}")
            print(f"   RSI: {data['rsi']:.1f}")
            print(f"   Bollinger: ${data['bb_lower']:,.2f} — ${data['bb_middle']:,.2f} — ${data['bb_upper']:,.2f}")
            print(f"   Posición BB: {data['bb_position']:.2f}")

            if data["signal"]:
                print(f"   ✅ Señal: {data['signal']} — {data['reason']}")
            else:
                print(f"   Sin señal")