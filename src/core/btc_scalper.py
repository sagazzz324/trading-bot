"""
btc_scalper.py — trader dedicado BTC Up/Down 5m
Ciclo rápido: evalúa cada 60s, entra cuando hay edge, sale anticipado si conviene
"""
import requests
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
GAMMA_API      = "https://gamma-api.polymarket.com"

TAKE_PROFIT_PCT = 0.15   # salir si el precio subió 15% a favor
STOP_LOSS_PCT   = 0.20   # salir si el precio bajó 20% en contra


def get_btc_momentum():
    try:
        r = requests.get(BINANCE_KLINES, params={"symbol":"BTCUSDT","interval":"1m","limit":10}, timeout=5)
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
    try:
        # 1. Buscar via events con slug
        r = requests.get(f"{GAMMA_API}/events",
            params={"limit":10,"active":"true","slug_contains":"btc-updown-5m"}, timeout=10)
        if r.status_code == 200:
            events = r.json()
            if events:
                markets = events[0].get("markets", [])
                if markets:
                    logger.info(f"BTC 5m via event: {markets[0].get('question','')[:60]}")
                    return markets[0]

        # 2. Buscar via markets slug
        r2 = requests.get(f"{GAMMA_API}/markets",
            params={"limit":50,"active":"true","closed":"false","slug_contains":"btc-updown"}, timeout=10)
        if r2.status_code == 200:
            mks = r2.json()
            if mks:
                mks.sort(key=lambda x: float(x.get("volume24hr") or 0), reverse=True)
                logger.info(f"BTC 5m via markets: {mks[0].get('question','')[:60]}")
                return mks[0]

        # 3. Fallback amplio
        r3 = requests.get(f"{GAMMA_API}/markets",
            params={"limit":100,"active":"true","closed":"false"}, timeout=10)
        if r3.status_code == 200:
            for m in r3.json():
                slug = (m.get("slug") or "").lower()
                q    = (m.get("question") or "").lower()
                if "btc-updown" in slug or ("btc" in q and ("up" in q or "higher" in q) and ("5m" in q or "5 min" in q)):
                    logger.info(f"BTC 5m via fallback: {m.get('question','')[:60]}")
                    return m

        logger.warning("No se encontró mercado BTC Up/Down 5m")
        return None
    except Exception as e:
        logger.error(f"find_active_btc_5m_market: {e}")
        return None


def get_current_price(market_id, direction):
    """Obtiene precio actual del outcome en el mercado."""
    try:
        import json
        r = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=5)
        if r.status_code != 200:
            return None
        m = r.json()
        outcomes = m.get("outcomes", "[]")
        prices   = m.get("outcomePrices", "[]")
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if isinstance(prices, str):   prices   = json.loads(prices)
        for i, o in enumerate(outcomes):
            o_lower = o.lower()
            is_up   = any(w in o_lower for w in ["up","higher","above"])
            is_down = any(w in o_lower for w in ["down","lower","below"])
            if (direction == "up" and is_up) or (direction == "down" and is_down):
                return float(prices[i]) if i < len(prices) else None
        return None
    except Exception as e:
        logger.error(f"get_current_price: {e}")
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
            if any(w in o for w in ["up","higher","above"]):
                result["up_price"] = p; result["up_outcome"] = outcome
            elif any(w in o for w in ["down","lower","below"]):
                result["down_price"] = p; result["down_outcome"] = outcome
        if "up_price"   not in result: result["up_price"]   = float(prices[0]) if prices else 0.5; result["up_outcome"]   = outcomes[0] if outcomes else "Up"
        if "down_price" not in result: result["down_price"] = float(prices[1]) if len(prices)>1 else 0.5; result["down_outcome"] = outcomes[1] if len(outcomes)>1 else "Down"
        return result
    except Exception as e:
        logger.error(f"get_market_outcome_prices: {e}")
        return {"up_price":0.5,"down_price":0.5,"up_outcome":"Up","down_outcome":"Down"}


def decide_trade(momentum, prices):
    direction  = momentum["direction"]
    confidence = momentum["confidence"]
    change_pct = abs(momentum["change_pct"])
    if confidence == "low":
        return None
    market_prob = prices["up_price"] if direction == "up" else prices["down_price"]
    true_prob   = min(0.72, 0.50 + change_pct * 0.8)
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
        self.trader    = trader
        self.log       = log_fn or print
        self.cycle     = 0
        # Track posiciones abiertas con market_id para poder cerrarlas
        self._open: dict = {}   # trade_id → {market_id, direction, entry_price, position_size}

    # ── CHEQUEAR SALIDA ANTICIPADA ──────────────────────────────────────────

    def _check_exits(self):
        state  = self.trader.load_state()
        active = state["active_trades"]
        if not active:
            return

        for trade in list(active):
            tid       = trade["id"]
            mkt_id    = trade["market_id"]
            direction = self._open.get(tid, {}).get("direction")
            if not direction:
                continue

            entry = trade["market_prob"]
            current = get_current_price(mkt_id, direction)
            if current is None:
                continue

            change = (current - entry) / entry

            if change >= TAKE_PROFIT_PCT:
                # Cerrar con ganancia
                pnl = round(trade["position_size"] * change, 2)
                self.trader.resolve_trade_with_pnl(tid, pnl)
                self._open.pop(tid, None)
                self.log(f"✅ TP anticipado #{tid} · +${pnl:.2f} ({change*100:.1f}%)", "#00E887")

            elif change <= -STOP_LOSS_PCT:
                # Cerrar con pérdida
                pnl = round(trade["position_size"] * change, 2)
                self.trader.resolve_trade_with_pnl(tid, pnl)
                self._open.pop(tid, None)
                self.log(f"🛑 SL anticipado #{tid} · ${pnl:.2f} ({change*100:.1f}%)", "#FF5050")

    # ── CICLO PRINCIPAL ─────────────────────────────────────────────────────

    def run_once(self):
        self.cycle += 1

        # 1. Chequear salidas primero
        self._check_exits()

        state    = self.trader.load_state()
        active   = state["active_trades"]
        bankroll = state["bankroll"]

        # 2. Buscar mercado activo
        market = find_active_btc_5m_market()
        if not market:
            self.log("❌ No hay mercado BTC activo", "#FF5050")
            return False

        question  = market.get("question", "")
        market_id = market.get("conditionId") or market.get("id", "")

        # 3. Verificar si ya tenemos posición en este mercado
        if any(t["market_id"] == market_id for t in active):
            self.log(f"⏭️  Posición activa en mercado actual — esperando salida", "#F5A623")
            return False

        # 4. Momentum
        momentum = get_btc_momentum()
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

        # 6. Sizing
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
            self._open[trade["id"]] = {
                "market_id":     market_id,
                "direction":     decision["direction"],
                "entry_price":   decision["market_prob"],
                "position_size": position
            }
            self.log(
                f"✅ TRADE #{trade['id']} · "
                f"{'📈 UP' if decision['direction']=='up' else '📉 DOWN'} · "
                f"${position:.2f} · EV {decision['ev']:.3f}",
                "#00E887"
            )
            return True

        return False