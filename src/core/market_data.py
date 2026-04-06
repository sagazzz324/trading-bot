import requests
import logging
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

POLYMARKET_API = "https://gamma-api.polymarket.com"

def get_markets(limit=10):
    """
    Obtiene mercados activos y con volumen de Polymarket.
    """
    try:
        params = {
            "limit": limit,
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false"
        }

        response = requests.get(
            f"{POLYMARKET_API}/markets",
            params=params
        )
        response.raise_for_status()
        markets = response.json()
        logger.info(f"Se obtuvieron {len(markets)} mercados")
        return markets

    except Exception as e:
        logger.error(f"Error obteniendo mercados: {e}")
        return []


def format_market(market):
    """
    Formatea un mercado para mostrarlo limpio.
    """
    try:
        outcomes = market.get("outcomes", "[]")
        prices = market.get("outcomePrices", "[]")

        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(prices, str):
            prices = json.loads(prices)

        # Buscar precio del outcome "Yes"
        precio = 0
        if "Yes" in outcomes:
            idx = outcomes.index("Yes")
            precio = float(prices[idx])
        elif prices:
            precio = float(prices[0])

    except Exception as e:
        logger.error(f"Error parseando precios: {e}")
        precio = 0

    return {
        "id": market.get("conditionId", market.get("id", "")),
        "pregunta": market.get("question", ""),
        "activo": market.get("active", False),
        "volumen": float(market.get("volume", 0)),
        "volumen_24h": float(market.get("volume24hr", 0)),
        "precio": precio
    }