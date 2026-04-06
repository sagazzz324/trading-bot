import requests
import logging
import json
from datetime import datetime

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


def get_price_movements(limit=50):
    try:
        params = {
            "limit": limit,
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false"
        }
        response = requests.get(f"{GAMMA_API}/markets", params=params)
        response.raise_for_status()
        markets = response.json()

        movements = []

        for market in markets:
            try:
                outcomes = market.get("outcomes", "[]")
                prices = market.get("outcomePrices", "[]")

                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes)
                if isinstance(prices, str):
                    prices = json.loads(prices)

                if "Yes" not in outcomes:
                    continue

                idx = outcomes.index("Yes")
                current_price = float(prices[idx])

                price_24h = market.get("oneDayPriceChange", 0)
                volume_24h = float(market.get("volume24hr", 0))
                volume_total = float(market.get("volume", 0))

                if price_24h is None:
                    price_24h = 0

                price_change = abs(float(price_24h))

                if price_change > 0.05 and volume_24h > 10000:
                    movements.append({
                        "market": market.get("question", ""),
                        "question": market.get("question", ""),
                        "current_price": current_price,
                        "price_change": float(price_24h),
                        "change_pct": float(price_24h) * 100,
                        "volume_24h": volume_24h,
                        "volume": volume_24h,
                        "volume_total": volume_total,
                        "direction": "up" if float(price_24h) > 0 else "down",
                        "signal": "bullish" if float(price_24h) > 0 else "bearish",
                        "has_signal": True
                    })

            except Exception:
                continue

        movements.sort(key=lambda x: abs(x["price_change"]), reverse=True)
        logger.info(f"Detectados {len(movements)} movimientos significativos")
        return movements

    except Exception as e:
        logger.error(f"Error detectando movimientos: {e}")
        return []


def get_whale_signals():
    """Retorna lista de señales whale — alias principal usado por bot.py"""
    return get_price_movements()


def get_whale_signal(market_question, market_price):
    """Retorna señal whale para un mercado específico."""
    movements = get_price_movements(limit=100)

    for m in movements:
        if m["question"] == market_question:
            return {
                "has_signal": True,
                "signal": m["signal"],
                "direction": m["direction"],
                "price_change": m["price_change"],
                "change_pct": m["change_pct"],
                "volume_24h": m["volume_24h"],
                "strength": "strong" if abs(m["price_change"]) > 0.10 else "moderate"
            }

    return {"has_signal": False}


def print_whale_report():
    """Muestra reporte de movimientos de whales."""
    print("\n🐋 WHALE TRACKER — Movimientos significativos")
    print("="*60)

    movements = get_price_movements(limit=100)

    if not movements:
        print("   Sin movimientos significativos detectados")
        return

    for m in movements[:10]:
        direction = "📈" if m["signal"] == "bullish" else "📉"
        print(f"\n{direction} {m['question'][:55]}")
        print(f"   Precio actual: {m['current_price']:.1%}")
        print(f"   Cambio 24h:    {m['price_change']:+.1%}")
        print(f"   Volumen 24h:   ${m['volume_24h']:,.0f}")