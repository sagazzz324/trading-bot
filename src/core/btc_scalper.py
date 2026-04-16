"""
btc_scalper.py — trader dedicado BTC Up/Down 5m en Polymarket
Precio via CoinGecko (no bloqueado en Railway)
Ciclo cada 60s, salida anticipada con TP/SL
"""
import requests
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

GAMMA_API       = "https://gamma-api.polymarket.com"
TAKE_PROFIT_PCT = 0.15
STOP_LOSS_PCT   = 0.20


def get_btc_momentum():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc",
            params={"vs_currency": "usd", "days": "1"},
            timeout=10
        )
        data = r.json()
        if not data or len(data) < 6:
            logger.error(f"CoinGecko: {len(data) if data else 0} puntos")
            return {"direction": "up", "change_pct": 0, "price": 0, "vol_ratio": 1, "confidence": "low"}

        closes  = [candle[4] for candle in data]
        current = closes[-1]
        prev5   = closes[-6]
        change  = (current - prev5) / prev5 * 100

        return {
            "direction":  "up" if change > 0 else "down",
            "change_pct": round(change, 4),
            "price":      round(current, 2),
            "vol_ratio":  1.0,
            "confidence": "high"   if abs(change) > 0.15 else
                          "medium" if abs(change) > 0.05 else "low"
        }
    except Exception as e:
        import traceback
        logger.error(f"get_btc_momentum:\n{traceback.format_exc()}")
        return {"direction": "up", "change_pct": 0, "price": 0, "vol_ratio": 1, "confidence": "low"}


def find_active_btc_5m_market():
    try:
        import time as time_module

        # Calcular slugs de los próximos 3 intervalos de 5 minutos
        now = int(time_module.time())
        interval = 300  # 5 minutos en segundos
        slugs = []
        for i in range(1, 4):  # próximos 3 intervalos
            ts = ((now // interval) + i) * interval
            slugs.append(f"btc-updown-5m-{ts}")

        # También agregar intervalo actual
        ts_current = ((now // interval)) * interval
        slugs.insert(0, f"btc-updown-5m-{ts_current}")

        for slug in slugs:
            r = requests.get(
                f"{GAMMA_API}/events",
                params={"slug": slug},
                timeout=10
            )
            if r.status_code == 200:
                events = r.json()
                if events:
                    markets = events[0].get("markets", [])
                    if markets:
                        market = markets[0]
                        if _is_valid_btc_updown(market):
                            logger.info(f"BTC 5m encontrado: {slug}")
                            return market

        logger.warning(f"No se encontró mercado BTC 5m. Slugs probados: {slugs}")
        return None

    except Exception as e:
        logger.error(f"find_active_btc_5m_market: {e}")
        return None


def _is_valid_btc_updown(market) -> bool:
    """Valida que el mercado sea realmente BTC Up/Down — rechaza NBA y cualquier otra cosa."""
    import json
    q    = (market.get("question") or "").lower()
    slug = (market.get("slug") or "").lower()

    # Debe tener btc o bitcoin
    has_btc = "btc" in q or "bitcoin" in q or "btc" in slug

    # Debe tener up/down o higher/lower
    has_direction = any(w in q for w in ["up", "down", "higher", "lower"]) or \
                    any(w in slug for w in ["up", "down", "updown"])

    # No debe ser deportes
    sports_block = ["nba", "nfl", "nhl", "mlb", "soccer", "basketball",
                    "football", "tennis", "golf", "ufc", "match", "game",
                    "lakers", "warriors", "mavericks", "grizzlies"]
    is_sports = any(w in q for w in sports_block)

    valid = has_btc and has_direction and not is_sports
    if not valid:
        logger.debug(f"Mercado rechazado: {q[:60]} (btc={has_btc} dir={has_direction} sports={is_sports})")
    return valid


def get_outcome_current_price(market_id, direction):
    try:
        import json
        r = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=5)
        if r.status_code != 200:
            return None
        m        = r.json()
        outcomes = m.get("outcomes", "[]")
        prices   = m.get("outcomePrices", "[]")
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if isinstance(prices, str):   prices   = json.loads(prices)
        for i, o in enumerate(outcomes):
            o_lower = o.lower()
            is_up   = any(w in o_lower for w in ["up", "higher", "above"])
            is_down = any(w in o_lower for w in ["down", "lower", "below"])
            if (direction == "up" and is_up) or (direction == "down" and is_down):
                return float(prices[i]) if i < len(prices) else None
        return None
    except Exception as e:
        logger.error(f"get_outcome_current_price: {e}")
        return None


def get_market_outcome_prices(market):
    import json
    try:
        outcomes = market.get("outcomes", "[]")
        prices   = market.get("outcomePrices", "[]")
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if isinstance(prices, str):   prices   = json.loads(prices)
        result = {}
        for i, outcome in enumerate(outcomes):
            o = outcome.lower()
            p = float(prices[i]) if i < len(prices) else 0.5
            if any(w in o for w in ["up", "higher", "above"]):
                result["up_price"]   = p
                result["up_outcome"] = outcome
            elif any(w in o for w in ["down", "lower", "below"]):
                result["down_price"]   = p
                result["down_outcome"] = outcome
        if "up_price"   not in result:
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
    direction  = momentum["direction"]
    confidence = momentum["confidence"]
    change_pct = abs(momentum["change_pct"])

    if confidence == "low":
        return None

    market_prob = prices["up_price"] if direction == "up" else prices["down_price"]
    true_prob   = min(0.65, 0.50 + change_pct * 0.15)
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
    def __init__(self, trader, log_fn=None):
        self.trader  = trader
        self.log     = log_fn or print
        self.cycle   = 0
        # market_id → {trade_id, direction, entry_price}
        # Persiste solo en memoria — se resetea si el bot se reinicia
        self._open: dict = {}

    def _check_exits(self):
        """Revisa posiciones abiertas y cierra si llegaron a TP o SL."""
        state  = self.trader.load_state()
        active = {t["id"]: t for t in state["active_trades"]}

        for trade_id, meta in list(self._open.items()):
            if trade_id not in active:
                # Ya fue resuelto externamente
                self._open.pop(trade_id, None)
                continue

            current = get_outcome_current_price(meta["market_id"], meta["direction"])
            if current is None:
                continue

            entry  = meta["entry_price"]
            change = (current - entry) / entry if entry > 0 else 0
            pos    = active[trade_id]["position_size"]

            if change >= TAKE_PROFIT_PCT:
                pnl = round(pos * change, 2)
                self.trader.resolve_trade_with_pnl(trade_id, pnl)
                self._open.pop(trade_id, None)
                self.log(f"✅ TP #{trade_id} · +${pnl:.2f} ({change*100:.1f}%)", "#00E887")

            elif change <= -STOP_LOSS_PCT:
                pnl = round(pos * change, 2)
                self.trader.resolve_trade_with_pnl(trade_id, pnl)
                self._open.pop(trade_id, None)
                self.log(f"🛑 SL #{trade_id} · ${pnl:.2f} ({change*100:.1f}%)", "#FF5050")

    def run_once(self):
        self.cycle += 1

        # 1. Revisar salidas primero
        self._check_exits()

        state    = self.trader.load_state()
        active   = state["active_trades"]
        bankroll = state["bankroll"]

        # 2. Buscar mercado activo
        market = find_active_btc_5m_market()
        if not market:
            self.log("❌ No hay mercado BTC activo", "#FF5050")
            return False

        market_id = market.get("conditionId") or market.get("id", "")
        question  = market.get("question", "")

        # 3. Ya tenemos posición en este mercado → esperar
        if any(t["market_id"] == market_id for t in active):
            return False

        # 4. Momentum
        momentum = get_btc_momentum()

        if momentum["price"] == 0:
            self.log("❌ Sin precio BTC — CoinGecko falló", "#FF5050")
            return False

        self.log(
            f"BTC ${momentum['price']:,.2f} · "
            f"{'📈' if momentum['direction']=='up' else '📉'} "
            f"{momentum['change_pct']:+.3f}% · conf {momentum['confidence']}",
            "#ffffff60"
        )

        if momentum["confidence"] == "low":
            return False

        # 5. Precios y decisión
        prices   = get_market_outcome_prices(market)
        decision = decide_trade(momentum, prices)
        if not decision:
            self.log(f"📉 EV insuficiente — skip", "#F5A623")
            return False

        # 6. Sizing — máximo 5% del bankroll o $50
        position = round(min(bankroll * 0.05, 50.0), 2)
        if position < 2:
            self.log(f"💰 Bankroll insuficiente (${bankroll:.2f})", "#FF5050")
            return False

        # 7. Ejecutar trade
        trade = self.trader.place_trade(
            market_id=market_id,
            question=question,
            true_prob=decision["true_prob"],
            market_prob=decision["market_prob"],
            ev=decision["ev"],
            position_size=position
        )

        if trade:
            self._open[trade["id"]] = {
                "market_id":   market_id,
                "direction":   decision["direction"],
                "entry_price": decision["market_prob"],
            }
            self.log(
                f"✅ TRADE #{trade['id']} · "
                f"{'📈 UP' if decision['direction']=='up' else '📉 DOWN'} · "
                f"${position:.2f} · EV {decision['ev']:.3f}",
                "#00E887"
            )
            return True

        return False