import logging
from src.exchanges.binance_client import BinanceClient

logger = logging.getLogger(__name__)


class MomentumScalper:
    """
    Estrategia de Scalping por Momentum.
    Entra en la dirección del movimiento fuerte con volumen alto.
    """

    def __init__(self, symbol="BTCUSDT", profit_target=0.003, stop_loss=0.002):
        self.symbol = symbol
        self.profit_target = profit_target  # 0.3% de ganancia objetivo
        self.stop_loss = stop_loss          # 0.2% de stop loss
        self.client = BinanceClient()
        self.trades = []
        self.pnl = 0

    def detect_momentum(self):
        """
        Detecta momentum usando velas de 1 minuto.
        """
        klines = self.client.get_klines(self.symbol, interval="1m", limit=10)
        if len(klines) < 5:
            return None

        closes = [k["close"] for k in klines]
        volumes = [k["volume"] for k in klines]

        # Momentum de los últimos 3 períodos
        momentum = (closes[-1] - closes[-4]) / closes[-4] * 100

        # Volumen relativo
        avg_vol = sum(volumes[:-1]) / len(volumes[:-1])
        vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1

        # Señal solo si hay momentum fuerte Y volumen alto
        signal = None
        if momentum > 0.15 and vol_ratio > 2.0:
            signal = "BUY"
        elif momentum < -0.15 and vol_ratio > 2.0:
            signal = "SELL"

        return {
            "price": closes[-1],
            "momentum": momentum,
            "vol_ratio": vol_ratio,
            "signal": signal
        }

    def simulate_trade(self, entry_price, side, quantity=0.001):
        """
        Simula un trade de scalping con target y stop loss.
        """
        if side == "BUY":
            target = entry_price * (1 + self.profit_target)
            stop = entry_price * (1 - self.stop_loss)
        else:
            target = entry_price * (1 - self.profit_target)
            stop = entry_price * (1 + self.stop_loss)

        # Obtener precio siguiente para simular resultado
        klines = self.client.get_klines(self.symbol, interval="1m", limit=3)
        if not klines:
            return None

        next_high = max(k["high"] for k in klines)
        next_low = min(k["low"] for k in klines)

        # Determinar resultado
        if side == "BUY":
            if next_high >= target:
                exit_price = target
                result = "WIN"
            elif next_low <= stop:
                exit_price = stop
                result = "LOSS"
            else:
                exit_price = klines[-1]["close"]
                result = "OPEN"
        else:
            if next_low <= target:
                exit_price = target
                result = "WIN"
            elif next_high >= stop:
                exit_price = stop
                result = "LOSS"
            else:
                exit_price = klines[-1]["close"]
                result = "OPEN"

        pnl = (exit_price - entry_price) * quantity
        if side == "SELL":
            pnl = -pnl

        return {
            "side": side,
            "entry": entry_price,
            "exit": exit_price,
            "result": result,
            "pnl": round(pnl, 6)
        }

    def run(self, cycles=5):
        """
        Corre el scalper por N ciclos.
        """
        print(f"\n⚡ SCALPING MOMENTUM — {self.symbol}")
        print(f"   Target: {self.profit_target*100:.1f}% | Stop: {self.stop_loss*100:.1f}%")
        print("="*50)

        for i in range(cycles):
            print(f"\n🔍 Ciclo {i+1}/{cycles}")
            data = self.detect_momentum()

            if not data:
                print("   Sin datos suficientes")
                continue

            print(f"   Precio: ${data['price']:,.2f}")
            print(f"   Momentum: {data['momentum']:+.3f}%")
            print(f"   Volumen ratio: {data['vol_ratio']:.2f}x")

            if not data["signal"]:
                print("   Sin señal de momentum")
                continue

            print(f"   🚀 Señal: {data['signal']}")
            trade = self.simulate_trade(data["price"], data["signal"])

            if trade:
                self.pnl += trade["pnl"]
                self.trades.append(trade)
                icon = "✅" if trade["result"] == "WIN" else "❌" if trade["result"] == "LOSS" else "⏳"
                print(f"   {icon} {trade['result']}: entrada ${trade['entry']:,.2f} → salida ${trade['exit']:,.2f} | PnL: ${trade['pnl']:+.6f}")

        print(f"\n📊 Resumen Scalping:")
        print(f"   Trades: {len(self.trades)}")
        print(f"   PnL total: ${self.pnl:+.6f}")