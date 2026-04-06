import logging
from src.exchanges.binance_client import BinanceClient

logger = logging.getLogger(__name__)


class TriangularArbitrage:
    """
    Arbitraje triangular en Binance.
    Detecta oportunidades entre 3 pares: A→B→C→A
    """

    def __init__(self, min_profit_pct=0.002):
        self.min_profit = min_profit_pct  # 0.2% mínimo
        self.client = BinanceClient()
        self.opportunities = []

    def get_prices(self, symbols):
        """Obtiene precios de múltiples pares."""
        prices = {}
        for symbol in symbols:
            try:
                price = self.client.get_price(symbol)
                if price > 0:
                    prices[symbol] = price
            except:
                continue
        return prices

    def check_triangle(self, base, mid, quote, prices):
        """
        Verifica si hay oportunidad de arbitraje triangular.
        Ruta: base → mid → quote → base
        Ejemplo: BTC → ETH → USDT → BTC
        """
        pair1 = f"{base}{mid}"    # BTC/ETH
        pair2 = f"{mid}{quote}"   # ETH/USDT
        pair3 = f"{base}{quote}"  # BTC/USDT

        if not all(p in prices for p in [pair1, pair2, pair3]):
            return None

        # Simulación: empezamos con 1 unidad de base
        # Paso 1: Vendemos base por mid
        step1 = 1 / prices[pair1]
        # Paso 2: Vendemos mid por quote
        step2 = step1 * prices[pair2]
        # Paso 3: Compramos base con quote
        step3 = step2 / prices[pair3]

        # Fee de Binance: 0.1% por trade → 3 trades = 0.3%
        fee = 0.999 ** 3
        profit = (step3 * fee) - 1

        if profit > self.min_profit:
            return {
                "route": f"{base}→{mid}→{quote}→{base}",
                "pairs": [pair1, pair2, pair3],
                "profit_pct": profit * 100,
                "multiplier": step3 * fee
            }
        return None

    def scan(self):
        """
        Escanea oportunidades de arbitraje triangular.
        """
        print(f"\n🔺 ARBITRAJE TRIANGULAR")
        print(f"   Profit mínimo: {self.min_profit*100:.2f}%")
        print("="*50)

        # Triángulos comunes en Binance
        triangles = [
            ("BTC", "ETH", "USDT"),
            ("BNB", "BTC", "USDT"),
            ("ETH", "BNB", "USDT"),
            ("SOL", "BTC", "USDT"),
            ("ADA", "BTC", "USDT"),
            ("XRP", "BTC", "USDT"),
            ("DOT", "BTC", "USDT"),
            ("MATIC", "ETH", "USDT"),
        ]

        # Obtener todos los precios necesarios
        all_symbols = set()
        for base, mid, quote in triangles:
            all_symbols.add(f"{base}{mid}")
            all_symbols.add(f"{mid}{quote}")
            all_symbols.add(f"{base}{quote}")

        print(f"   Obteniendo precios de {len(all_symbols)} pares...")
        prices = self.get_prices(list(all_symbols))

        opportunities = []
        for base, mid, quote in triangles:
            opp = self.check_triangle(base, mid, quote, prices)
            if opp:
                opportunities.append(opp)
                print(f"\n   ✅ Oportunidad: {opp['route']}")
                print(f"      Profit: {opp['profit_pct']:+.4f}%")

        if not opportunities:
            print("   Sin oportunidades de arbitraje detectadas")

        self.opportunities = opportunities
        return opportunities