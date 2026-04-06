import requests
import logging
from src.core.paper_trader import PaperTrader

logger = logging.getLogger(__name__)

POLYMARKET_API = "https://gamma-api.polymarket.com"

def get_market_result(market_id):
    """
    Consulta Polymarket para ver si un mercado cerró y quién ganó.
    Retorna: True (Yes ganó), False (No ganó), None (sigue abierto)
    """
    try:
        response = requests.get(f"{POLYMARKET_API}/markets/{market_id}")
        response.raise_for_status()
        market = response.json()

        # Si el mercado no cerró todavía
        if market.get("active", True):
            return None

        import json
        outcomes = market.get("outcomes", "[]")
        prices = market.get("outcomePrices", "[]")

        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(prices, str):
            prices = json.loads(prices)

        # El outcome ganador tiene precio 1.0
        for i, price in enumerate(prices):
            if float(price) >= 0.99:
                winner = outcomes[i]
                logger.info(f"Mercado resuelto: {winner} ganó")
                return winner == "Yes"

        return None

    except Exception as e:
        logger.error(f"Error consultando resultado: {e}")
        return None


def auto_resolve_trades():
    """
    Revisa todos los trades activos y resuelve los que ya cerraron.
    """
    trader = PaperTrader()

    if not trader.active_trades:
        logger.info("No hay trades activos para resolver")
        return

    print(f"\n🔍 Revisando {len(trader.active_trades)} trades activos...")
    resolved_count = 0

    for trade in trader.active_trades.copy():
        trade_id = trade["id"]
        market_id = trade["market_id"]
        question = trade["question"]

        print(f"   Chequeando: {question[:50]}...")
        result = get_market_result(market_id)

        if result is None:
            print(f"   ⏳ Sigue abierto")
            continue

        # Resolver el trade
        trader.resolve_trade(trade_id, result)
        resolved_count += 1

    if resolved_count == 0:
        print("   No hay trades para resolver todavía")
    else:
        print(f"\n✅ {resolved_count} trades resueltos")