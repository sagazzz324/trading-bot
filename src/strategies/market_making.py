import logging
from src.exchanges.binance_client import BinanceClient
from src.exchanges.binance_logger import save_session, get_stats
from src.core.probability_engine import analyze_market_conditions

logger = logging.getLogger(__name__)


class MarketMaker:
    def __init__(self, symbol="BTCUSDT", params=None):
        self.symbol = symbol
        defaults = {
            "spread_pct":    0.0001,
            "quantity":      0.001,
            "max_inventory": 0.003,
            "stop_loss_pct": 0.010,
            "max_daily_loss":25.0,
            "trend_filter":  True,
            "volatility_adjust": True
        }
        p = params or defaults
        self.spread_pct       = p["spread_pct"]
        self.quantity         = p["quantity"]
        self.max_inventory    = p["max_inventory"]
        self.stop_loss_pct    = p["stop_loss_pct"]
        self.max_daily_loss   = p["max_daily_loss"]
        self.trend_filter     = p["trend_filter"]
        self.volatility_adjust= p["volatility_adjust"]
        self.client = BinanceClient()
        self.trades       = []
        self.cash         = 0
        self.inventory    = 0
        self.entry_prices = []

    def get_sma(self, closes, period):
        if len(closes) < period:
            return closes[-1]
        return sum(closes[-period:]) / period

    def detect_trend(self, closes):
        if len(closes) < 20:
            return "lateral"
        sma5  = self.get_sma(closes, 5)
        sma20 = self.get_sma(closes, 20)
        diff  = (sma5 - sma20) / sma20
        if diff > 0.0005:  return "up"
        if diff < -0.0005: return "down"
        return "lateral"

    def get_dynamic_spread(self, candles):
        if not self.volatility_adjust or len(candles) < 5:
            return self.spread_pct
        highs = [c["high"] for c in candles[-5:]]
        lows  = [c["low"]  for c in candles[-5:]]
        avg_range = sum(h - l for h, l in zip(highs, lows)) / 5
        mid = candles[-1]["close"]
        volatility = avg_range / mid
        return max(self.spread_pct, volatility * 0.3)

    def check_stop_loss(self, current_price):
        if not self.entry_prices or self.inventory <= 0:
            return False
        avg_entry = sum(self.entry_prices) / len(self.entry_prices)
        loss_pct  = (avg_entry - current_price) / avg_entry
        if loss_pct >= self.stop_loss_pct:
            print(f"   🛑 STOP LOSS: ${avg_entry:,.2f} → ${current_price:,.2f} ({loss_pct*100:.2f}%)")
            return True
        return False

    def simulate_on_candles(self, candles, spread_override=None):
        results = []
        closes  = [c["close"] for c in candles]
        dynamic_spread = spread_override or self.get_dynamic_spread(candles)

        for i, candle in enumerate(candles):
            open_p  = candle["open"]
            high_p  = candle["high"]
            low_p   = candle["low"]
            close_p = candle["close"]

            our_bid = round(open_p * (1 - dynamic_spread), 2)
            our_ask = round(open_p * (1 + dynamic_spread), 2)

            bought = sold = stopped = False

            if self.check_stop_loss(close_p):
                if self.inventory > 0:
                    self.cash     += close_p * self.inventory
                    self.inventory = 0
                    self.entry_prices = []
                    stopped = True

            current_pnl = self.cash + (self.inventory * close_p)
            if current_pnl < -self.max_daily_loss:
                print(f"   ⛔ Pérdida máxima alcanzada (${current_pnl:.2f})")
                break

            if not stopped:
                trend        = self.detect_trend(closes[:i+1]) if self.trend_filter else "lateral"
                sma20        = self.get_sma(closes[:i+1], 20)
                price_ok     = close_p > sma20
                can_buy      = (trend != "down") and price_ok and (self.inventory < self.max_inventory)

                if low_p <= our_bid and can_buy:
                    self.cash     -= our_bid * self.quantity
                    self.inventory = round(self.inventory + self.quantity, 6)
                    self.entry_prices.append(our_bid)
                    bought = True
                    self.trades.append({"side": "BUY", "price": our_bid})

                if high_p >= our_ask and self.inventory >= self.quantity:
                    self.cash     += our_ask * self.quantity
                    self.inventory = round(self.inventory - self.quantity, 6)
                    if self.entry_prices:
                        self.entry_prices.pop(0)
                    sold = True
                    self.trades.append({"side": "SELL", "price": our_ask})

            mtm_pnl = self.cash + (self.inventory * close_p)
            trend_icon = {"up":"📈","down":"📉","lateral":"➡️"}.get(
                self.detect_trend(closes[:i+1]) if self.trend_filter else "lateral", "➡️"
            )
            icon = "🛑STOP" if stopped else "🟢BUY 🔴SELL" if bought and sold else "🟢BUY " if bought else "🔴SELL" if sold else "⏳"
            print(f"   Vela {i+1:2d} {trend_icon} | O:${open_p:,.1f} H:${high_p:,.1f} L:${low_p:,.1f} | {icon} | Inv:{self.inventory:.3f} | PnL:${mtm_pnl:+.4f}")
            results.append({"candle": i+1, "bought": bought, "sold": sold, "pnl": round(mtm_pnl, 4)})

        return results

    def run(self, cycles=1, sleep_seconds=0):
        print(f"\n🏦 MARKET MAKING — {self.symbol}")
        print("="*60)

        # Obtener velas
        candles = self.client.get_klines(self.symbol, interval="1m", limit=50)
        if not candles:
            print("❌ Sin datos de velas")
            return

        current_price = candles[-1]["close"]

        # ── ANÁLISIS INTELIGENTE CON CLAUDE ──
        print("\n🧠 Claude analizando condiciones del mercado...")
        analysis = analyze_market_conditions(self.symbol, candles, current_price)

        print(f"   Estrategia recomendada: {analysis['strategy']}")
        print(f"   Régimen: {analysis.get('market_regime','?')} | Riesgo: {analysis['risk_level']}")
        print(f"   Razonamiento: {analysis['reasoning'][:100]}")

        if not analysis["should_trade"]:
            print(f"\n⏸️  Claude recomienda PAUSAR — {analysis['reasoning']}")
            save_session("Market Making (pausado)", self.symbol, 0, 0, len(candles))
            return

        # Usar spread recomendado por Claude si es mayor al default
        recommended_spread = analysis.get("spread_recommendation", self.spread_pct)
        final_spread = max(self.spread_pct, recommended_spread)
        if recommended_spread > self.spread_pct:
            print(f"   📐 Spread ajustado por Claude: {final_spread*100:.4f}% (era {self.spread_pct*100:.4f}%)")
            self.spread_pct = final_spread

        print(f"\n   Spread final: {self.spread_pct*100:.4f}% | Cantidad: {self.quantity} | Max inv: {self.max_inventory}")
        print(f"   Stop loss: {self.stop_loss_pct*100:.1f}% | Max pérdida diaria: ${self.max_daily_loss}")
        print(f"   Filtro tendencia: {'✅' if self.trend_filter else '❌'} | Ajuste volatilidad: {'✅' if self.volatility_adjust else '❌'}")

        # Simular
        print(f"\n📊 Simulando sobre últimas {len(candles)} velas...")
        results = self.simulate_on_candles(candles)

        # Resumen
        buys  = [t for t in self.trades if t["side"] == "BUY"]
        sells = [t for t in self.trades if t["side"] == "SELL"]
        pairs = min(len(buys), len(sells))
        last_price = candles[-1]["close"]
        final_pnl  = self.cash + (self.inventory * last_price)

        avg_pair_profit = 0
        if pairs > 0:
            profits = [(sells[j]["price"] - buys[j]["price"]) * self.quantity for j in range(pairs)]
            avg_pair_profit = sum(profits) / pairs

        print(f"\n📊 Resumen:")
        print(f"   Velas:            {len(results)}")
        print(f"   Compras:          {len(buys)}")
        print(f"   Ventas:           {len(sells)}")
        print(f"   Pares completos:  {pairs}")
        print(f"   Ganancia x par:   ${avg_pair_profit:+.6f}")
        print(f"   Inventario:       {self.inventory:.4f} BTC")
        print(f"   PnL final:        ${final_pnl:+.6f}")
        print(f"   Régimen mercado:  {analysis.get('market_regime','?')}")

        save_session("Market Making", self.symbol, len(self.trades), final_pnl, len(results))

        print(f"\n📈 Stats acumuladas:")
        stats = get_stats()
        for k, v in stats.items():
            print(f"   {k}: {v}")