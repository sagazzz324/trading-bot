"""
btc_scalper.py — trader dedicado para BTC Up/Down 5m en Polymarket
No usa Claude API — usa momentum de precio de BTC directamente
"""
import requests
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
BINANCE_PRICE = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"


def get_btc_price():
    try:
        r = requests.get(BINANCE_PRICE, timeout=5)
        return float(r.json()["price"])
    except Exception as e:
        logger.error(f"get_btc_price: {e}")
        return 0.0


def get_btc_momentum():
    """
    Obtiene momentum de BTC en los últimos 5 minutos.
    Retorna: {"direction": "up"|"down", "change_pct": float, "price": float}
    """
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": "BTCUSDT", "interval": "1m", "limit": 10}
        r = requests.get(url, params=params, timeout=5)
        klines = r.json()
        closes = [float(k[4]) for k in klines]
        current = closes[-1]
        prev5   = closes[-6] if len(closes) >= 6 else closes[0]
        change  = (current - prev5) / prev5 * 100
        vol_recent = sum(float(k[5]) for k in klines[-3:])
        vol_prior  = sum(float(k[5]) for k in klines[-6:-3])
        vol_ratio  = vol_recent / vol_prior if vol_prior > 0 else 1.0
        return {
            "direction":  "up" if change > 0 else "down",
            "change_pct": round(change, 4),
            "price":      round(current, 2),
            "vol_ratio":  round(vol_ratio, 2),
            "confidence": "high" if abs(change) > 0.15 and vol_ratio > 1.2 else
                          "medium" if abs(change) > 0.05 else "low"
        }
    except Exception as e:
        logger.error(f"get_btc_momentum: {e}")
        return {"direction": "up", "change_pct": 0, "price": 0, "vol_ratio": 1, "confidence": "low"}


def find_active_btc_5m_market():
    """
    Busca el mercado BTC Up/Down 5m activo en Polymarket.
    """
    try:
        params = {
            "limit": 100,
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false"
        }
        r = requests.get(f"{GAMMA_API}/markets", params=params, timeout=10)
        markets = r.json()

        candidates = []
        for m in markets:
            q = (m.get("question") or "").lower()
            slug = (m.get("slug") or "").lower()
            if ("btc" in q or "bitcoin" in q) and ("up" in q or "down" in q or "higher" in q or "lower" in q):
                candidates.append(m)
            elif "btc-updown" in slug or "btc-up-down" in slug:
                candidates.append(m)

        if not candidates:
            logger.warning("No se encontró mercado BTC Up/Down activo")
            return None

        # Preferir el de mayor volumen
        candidates.sort(key=lambda x: float(x.get("volume24hr") or 0), reverse=True)
        market = candidates[0]
        logger.info(f"Mercado BTC encontrado: {market.get('question', '')[:60]}")
        return market

    except Exception as e:
        logger.error(f"find_active_btc_5m_market: {e}")
        return None


def get_market_outcome_prices(market):
    """
    Extrae outcomes y precios del mercado.
    Retorna: {"up_price": float, "down_price": float, "up_id": str, "down_id": str}
    """
    import json
    try:
        outcomes = market.get("outcomes", "[]")
        prices   = market.get("outcomePrices", "[]")
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if isinstance(prices, str):   prices   = json.loads(prices)

        result = {}
        for i, outcome in enumerate(outcomes):
            o_lower = outcome.lower()
            price   = float(prices[i]) if i < len(prices) else 0.5
            if any(w in o_lower for w in ["up", "higher", "above", "sube"]):
                result["up_price"]   = price
                result["up_outcome"] = outcome
            elif any(w in o_lower for w in ["down", "lower", "below", "baja"]):
                result["down_price"]   = price
                result["down_outcome"] = outcome

        if "up_price" not in result:
            result["up_price"]   = float(prices[0]) if prices else 0.5
            result["up_outcome"] = outcomes[0] if outcomes else "Up"
        if "down_price" not in result:
            result["down_price"]   = float(prices[1]) if len(prices) > 1 else 0.5
            result["down_outcome"] = outcomes[1] if len(outcomes) > 1 else "Down"

        return result
    except Exception as e:
        logger.error(f"get_market_outcome_prices: {e}")
        return {"up_price": 0.5, "down_price": 0.5, "up_outcome": "Up", "down_outcome": "Down"}


def decide_trade(momentum, prices):
    """
    Decide si entrar y en qué dirección basándose en momentum.
    Solo entra si hay edge real (EV > 0.03) y confianza media/alta.
    """
    direction  = momentum["direction"]
    confidence = momentum["confidence"]
    change_pct = momentum["change_pct"]

    if confidence == "low":
        return None

    if direction == "up":
        market_prob = prices["up_price"]
        true_prob   = min(0.70, 0.50 + abs(change_pct) * 0.8)
    else:
        market_prob = prices["down_price"]
        true_prob   = min(0.70, 0.50 + abs(change_pct) * 0.8)

    ev = true_prob * (1 - market_prob) - (1 - true_prob) * market_prob

    if ev < 0.03:
        return None

    return {
        "direction":   direction,
        "true_prob":   round(true_prob, 3),
        "market_prob": round(market_prob, 3),
        "ev":          round(ev, 4),
        "outcome":     prices["up_outcome"] if direction == "up" else prices["down_outcome"]
    }


class BTCScalper:
    """
    Scalper dedicado a BTC Up/Down 5m en Polymarket.
    """

    def __init__(self, trader, log_fn=None):
        self.trader = trader
        self.log    = log_fn or print
        self.cycle  = 0

    def run_once(self):
        self.cycle += 1
        self.log(f"━━ BTC Scalp #{self.cycle} ━━", "#41d6fc")

        # 1. Buscar mercado activo
        market = find_active_btc_5m_market()
        if not market:
            self.log("❌ No hay mercado BTC activo — reintentando en 30s", "#FF5050")
            return False

        question = market.get("question", "")
        market_id = market.get("conditionId") or market.get("id", "")
        self.log(f"📊 {question[:60]}", "#41d6fc")

        # 2. Obtener momentum
        momentum = get_btc_momentum()
        self.log(
            f"BTC ${momentum['price']:,.2f} · "
            f"{'📈' if momentum['direction']=='up' else '📉'} "
            f"{momentum['change_pct']:+.3f}% · "
            f"vol {momentum['vol_ratio']:.1f}x · conf {momentum['confidence']}",
            "#ffffff60"
        )

        if momentum["confidence"] == "low":
            self.log("⏸️  Momentum bajo — skip este ciclo", "#F5A623")
            return False

        # 3. Obtener precios del mercado
        prices = get_market_outcome_prices(market)
        self.log(
            f"Up: {prices['up_price']*100:.1f}% · Down: {prices['down_price']*100:.1f}%",
            "#ffffff60"
        )

        # 4. Decidir
        decision = decide_trade(momentum, prices)
        if not decision:
            self.log("📉 EV insuficiente — skip", "#F5A623")
            return False

        # 5. Verificar que no tengamos ya una posición en este mercado
        state  = self.trader.load_state()
        active = state["active_trades"]
        bankroll = state["bankroll"]

        if any(t["market_id"] == market_id for t in active):
            self.log("⏭️  Ya tenemos posición en este mercado", "#F5A623")
            return False

        # 6. Sizing — máximo 5% del bankroll para BTC scalp
        position = round(min(bankroll * 0.05, 50.0), 2)
        if position < 2:
            self.log(f"💰 Bankroll insuficiente (${bankroll:.2f})", "#FF5050")
            return False

        # 7. Ejecutar
        trade = self.trader.place_trade(
            market_id=market_id,
            question=question,
            true_prob=decision["true_prob"],
            market_prob=decision["market_prob"],
            ev=decision["ev"],
            position_size=position
        )

        if trade:
            self.log(
                f"✅ TRADE #{trade['id']} · "
                f"{'📈 UP' if decision['direction']=='up' else '📉 DOWN'} · "
                f"${position:.2f} · EV {decision['ev']:.3f}",
                "#00E887"
            )
            return True

        return False