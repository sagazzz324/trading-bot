"""
btc_scalper.py — BTC Up/Down 5m en Polymarket
Filosofía: "si no está claro cómo voy a salir, no entro"
"""
import requests
import logging
import time as time_module
from datetime import datetime

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"

# ── PARÁMETROS ────────────────────────────────────────────────────────────────
BANKROLL_RESERVE    = 0.30   # nunca usar más del 70% del bankroll
MAX_POSITION_PCT    = 0.05   # máximo 5% por trade
MIN_POSITION        = 2.0    # mínimo $2
MAX_SPREAD          = 0.04   # spread máximo tolerable (4%)
MIN_LIQUIDITY       = 500    # liquidez mínima en el mercado ($)
MIN_MOMENTUM        = 0.08   # % mínimo de movimiento para considerar señal real
MAX_TRADE_DURATION  = 180    # segundos máximos en un trade (3 min de 5)
BASE_TP             = 0.04   # take profit base 4%
BASE_SL             = 0.07   # stop loss base 7%


# ── PRECIO BTC ────────────────────────────────────────────────────────────────

def get_btc_data():
    """
    Obtiene precio actual y velas de BTC via CoinGecko.
    Retorna múltiples timeframes para confirmar señal.
    """
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc",
            params={"vs_currency": "usd", "days": "1"},
            timeout=10
        )
        data = r.json()
        if not data or len(data) < 12:
            return None

        closes = [c[4] for c in data]
        current = closes[-1]

        # Momentum en distintos timeframes (cada vela ~30min en CoinGecko daily)
        # Aproximamos con las últimas velas disponibles
        mom_1  = (closes[-1] - closes[-2])  / closes[-2]  * 100 if len(closes) >= 2  else 0
        mom_3  = (closes[-1] - closes[-4])  / closes[-4]  * 100 if len(closes) >= 4  else 0
        mom_6  = (closes[-1] - closes[-7])  / closes[-7]  * 100 if len(closes) >= 7  else 0

        # Detectar si el mercado está quieto (rango pequeño últimas 3 velas)
        recent_highs = [c[2] for c in data[-3:]]
        recent_lows  = [c[3] for c in data[-3:]]
        price_range  = (max(recent_highs) - min(recent_lows)) / current * 100

        return {
            "price":       round(current, 2),
            "mom_1":       round(mom_1, 4),
            "mom_3":       round(mom_3, 4),
            "mom_6":       round(mom_6, 4),
            "price_range": round(price_range, 4),
            "direction":   "up" if mom_1 > 0 else "down",
        }
    except Exception as e:
        import traceback
        logger.error(f"get_btc_data:\n{traceback.format_exc()}")
        return None


# ── BUSCAR MERCADO ────────────────────────────────────────────────────────────

def find_active_btc_5m_market():
    try:
        now = int(time_module.time())
        interval = 300
        slugs = []
        for i in range(0, 4):
            ts = ((now // interval) + i) * interval
            slugs.append(f"btc-updown-5m-{ts}")

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
                            logger.info(f"Mercado encontrado: {slug}")
                            return market

        logger.warning(f"No se encontró mercado BTC 5m")
        return None
    except Exception as e:
        logger.error(f"find_active_btc_5m_market: {e}")
        return None


def _is_valid_btc_updown(market) -> bool:
    import json
    q    = (market.get("question") or "").lower()
    slug = (market.get("slug") or "").lower()
    has_btc       = "btc" in q or "bitcoin" in q or "btc" in slug
    has_direction = any(w in q for w in ["up", "down", "higher", "lower"]) or "updown" in slug
    sports_block  = ["nba", "nfl", "nhl", "mlb", "basketball", "football",
                     "tennis", "golf", "ufc", "match", "game", "lakers",
                     "warriors", "mavericks", "grizzlies"]
    is_sports = any(w in q for w in sports_block)
    return has_btc and has_direction and not is_sports


def get_market_outcome_prices(market):
    import json
    try:
        outcomes = market.get("outcomes", "[]")
        prices   = market.get("outcomePrices", "[]")
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if isinstance(prices,   str): prices   = json.loads(prices)
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
            result["down_price"]   = float(prices[1]) if len(prices)>1 else 0.5
            result["down_outcome"] = outcomes[1] if len(outcomes)>1 else "Down"
        return result
    except Exception as e:
        logger.error(f"get_market_outcome_prices: {e}")
        return {"up_price":0.5,"down_price":0.5,"up_outcome":"Up","down_outcome":"Down"}


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
        if isinstance(prices,   str): prices   = json.loads(prices)
        for i, o in enumerate(outcomes):
            o_lower = o.lower()
            is_up   = any(w in o_lower for w in ["up","higher","above"])
            is_down = any(w in o_lower for w in ["down","lower","below"])
            if (direction=="up" and is_up) or (direction=="down" and is_down):
                return float(prices[i]) if i < len(prices) else None
        return None
    except Exception as e:
        logger.error(f"get_outcome_current_price: {e}")
        return None


def get_market_liquidity(market_id):
    """
    Obtiene spread y liquidez del mercado para filtrar entradas.
    """
    try:
        r = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=5)
        if r.status_code != 200:
            return None
        m = r.json()
        best_bid  = float(m.get("bestBid",  0) or 0)
        best_ask  = float(m.get("bestAsk",  1) or 1)
        liquidity = float(m.get("liquidityNum", 0) or 0)
        spread    = best_ask - best_bid if best_ask > best_bid else 1.0
        return {
            "spread":    round(spread, 4),
            "liquidity": round(liquidity, 2),
            "best_bid":  best_bid,
            "best_ask":  best_ask,
        }
    except Exception as e:
        logger.error(f"get_market_liquidity: {e}")
        return None


# ── LÓGICA DE DECISIÓN ────────────────────────────────────────────────────────

def analyze_entry(btc_data, prices, liquidity) -> dict:
    """
    Decide si entrar y con qué parámetros.
    Filosofía: "si no está claro cómo voy a salir, no entro"
    """
    reasons_no = []

    # 1. FILTRO DE LIQUIDEZ — lo primero
    if liquidity:
        if liquidity["spread"] > MAX_SPREAD:
            reasons_no.append(f"Spread {liquidity['spread']*100:.1f}% > máx {MAX_SPREAD*100:.1f}%")
        if liquidity["liquidity"] < MIN_LIQUIDITY:
            reasons_no.append(f"Liquidez ${liquidity['liquidity']:.0f} < mín ${MIN_LIQUIDITY}")

    if reasons_no:
        return {"enter": False, "reason": " | ".join(reasons_no)}

    # 2. FILTRO DE MERCADO MUERTO
    if btc_data["price_range"] < 0.05:
        return {"enter": False, "reason": f"Mercado quieto — rango {btc_data['price_range']:.3f}%"}

    # 3. FILTRO DE MOMENTUM MÍNIMO
    mom = abs(btc_data["mom_1"])
    if mom < MIN_MOMENTUM:
        return {"enter": False, "reason": f"Momentum {mom:.3f}% < mín {MIN_MOMENTUM}%"}

    # 4. CONFIRMACIÓN MULTI-TIMEFRAME
    # Los tres timeframes deben coincidir en dirección
    direction = btc_data["direction"]
    mom_3_dir = "up" if btc_data["mom_3"] > 0 else "down"
    mom_6_dir = "up" if btc_data["mom_6"] > 0 else "down"

    confirmations = sum([
        direction == mom_3_dir,
        direction == mom_6_dir,
    ])

    if confirmations < 1:
        return {"enter": False, "reason": f"Sin confirmación multi-timeframe (dir={direction} mom3={mom_3_dir} mom6={mom_6_dir})"}

    # 5. CÁLCULO DE EV
    market_prob = prices["up_price"] if direction == "up" else prices["down_price"]
    true_prob   = min(0.62, 0.50 + mom * 0.10 + (0.03 if confirmations == 2 else 0))
    ev          = true_prob * (1 - market_prob) - (1 - true_prob) * market_prob

    if ev < 0.03:
        return {"enter": False, "reason": f"EV {ev:.4f} insuficiente"}

    # 6. TP/SL ADAPTATIVO según liquidez
    spread = liquidity["spread"] if liquidity else 0.02
    tp = max(BASE_TP, spread * 2.5)   # TP mínimo que cubra el spread
    sl = max(BASE_SL, spread * 3.5)   # SL proporcional

    return {
        "enter":       True,
        "direction":   direction,
        "market_prob": round(market_prob, 4),
        "true_prob":   round(true_prob, 4),
        "ev":          round(ev, 4),
        "tp":          round(tp, 4),
        "sl":          round(sl, 4),
        "outcome":     prices["up_outcome"] if direction=="up" else prices["down_outcome"],
        "confirmations": confirmations,
    }


# ── SCALPER ───────────────────────────────────────────────────────────────────

class BTCScalper:
    def __init__(self, trader, log_fn=None):
        self.trader = trader
        self.log    = log_fn or print
        self.cycle  = 0
        # trade_id → {market_id, direction, entry_price, tp, sl, entered_at}
        self._open: dict = {}

    def _check_exits(self):
        """
        Revisa posiciones abiertas.
        Sale por: TP / SL / tiempo máximo / mercado cerrado
        """
        state  = self.trader.load_state()
        active = {t["id"]: t for t in state["active_trades"]}

        for trade_id, meta in list(self._open.items()):
            if trade_id not in active:
                self._open.pop(trade_id, None)
                continue

            trade = active[trade_id]
            pos   = trade["position_size"]

            # Salida por tiempo máximo
            elapsed = time_module.time() - meta["entered_at"]
            if elapsed >= MAX_TRADE_DURATION:
                # Obtener precio actual para calcular PnL real
                current = get_outcome_current_price(meta["market_id"], meta["direction"])
                if current:
                    pnl = round(pos * (current - meta["entry_price"]) / meta["entry_price"], 2)
                else:
                    pnl = 0
                self.trader.resolve_trade_with_pnl(trade_id, pnl)
                self._open.pop(trade_id, None)
                self.log(
                    f"⏱️  TIEMPO #{trade_id} · {'+'if pnl>=0 else ''}${pnl:.2f} ({elapsed:.0f}s)",
                    "#F5A623" if pnl >= 0 else "#FF5050"
                )
                continue

            # Obtener precio actual
            current = get_outcome_current_price(meta["market_id"], meta["direction"])
            if current is None:
                continue

            entry  = meta["entry_price"]
            change = (current - entry) / entry if entry > 0 else 0

            if change >= meta["tp"]:
                pnl = round(pos * change, 2)
                self.trader.resolve_trade_with_pnl(trade_id, pnl)
                self._open.pop(trade_id, None)
                self.log(f"✅ TP #{trade_id} · +${pnl:.2f} ({change*100:.1f}%) en {elapsed:.0f}s", "#00E887")

            elif change <= -meta["sl"]:
                pnl = round(pos * change, 2)
                self.trader.resolve_trade_with_pnl(trade_id, pnl)
                self._open.pop(trade_id, None)
                self.log(f"🛑 SL #{trade_id} · ${pnl:.2f} ({change*100:.1f}%) en {elapsed:.0f}s", "#FF5050")

    def run_once(self):
        self.cycle += 1

        # 1. Revisar salidas primero
        self._check_exits()

        state    = self.trader.load_state()
        active   = state["active_trades"]
        bankroll = state["bankroll"]

        # 2. Protección de capital — reservar 30%
        capital_disponible = bankroll * (1 - BANKROLL_RESERVE)
        capital_en_uso     = sum(t["position_size"] for t in active)
        if capital_en_uso >= capital_disponible:
            self.log(f"🔒 Capital protegido — en uso ${capital_en_uso:.2f} / disponible ${capital_disponible:.2f}", "#F5A623")
            return False

        # 3. Buscar mercado activo
        market = find_active_btc_5m_market()
        if not market:
            self.log("❌ No hay mercado BTC 5m activo", "#FF5050")
            return False

        market_id = market.get("conditionId") or market.get("id", "")
        question  = market.get("question", "")

        # 4. Ya tenemos posición en este mercado
        if any(t["market_id"] == market_id for t in active):
            return False

        # 5. Obtener datos de mercado y BTC
        liquidity = get_market_liquidity(market_id)
        btc       = get_btc_data()
        prices    = get_market_outcome_prices(market)

        if not btc or btc["price"] == 0:
            self.log("❌ Sin datos de BTC", "#FF5050")
            return False

        self.log(
            f"BTC ${btc['price']:,.2f} · "
            f"{'📈' if btc['direction']=='up' else '📉'} "
            f"m1={btc['mom_1']:+.3f}% m3={btc['mom_3']:+.3f}% m6={btc['mom_6']:+.3f}% · "
            f"rango={btc['price_range']:.3f}%",
            "#ffffff60"
        )

        if liquidity:
            self.log(
                f"📊 Mercado · spread={liquidity['spread']*100:.1f}% · liquidez=${liquidity['liquidity']:,.0f}",
                "#ffffff60"
            )

        # 6. Analizar entrada
        decision = analyze_entry(btc, prices, liquidity)

        if not decision["enter"]:
            self.log(f"⏸️  No entrar — {decision['reason']}", "#F5A623")
            return False

        # 7. Sizing
        position = round(min(
            bankroll * MAX_POSITION_PCT,
            capital_disponible - capital_en_uso,
            50.0
        ), 2)

        if position < MIN_POSITION:
            self.log(f"💰 Posición ${position:.2f} muy pequeña", "#FF5050")
            return False

        # 8. Ejecutar
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
                "tp":          decision["tp"],
                "sl":          decision["sl"],
                "entered_at":  time_module.time(),
            }
            self.log(
                f"✅ TRADE #{trade['id']} · "
                f"{'📈 UP' if decision['direction']=='up' else '📉 DOWN'} · "
                f"${position:.2f} · EV {decision['ev']:.3f} · "
                f"TP {decision['tp']*100:.1f}% SL {decision['sl']*100:.1f}% · "
                f"conf={decision['confirmations']}/2",
                "#00E887"
            )
            return True

        return False