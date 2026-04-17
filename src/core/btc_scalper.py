"""
btc_scalper.py — BTC Up/Down 5m · Polymarket
Estrategia: Markov Chain + State Persistence Filter
Basado en el framework de Bonereaper / 0xe1D6b514

Estados:
  0: Strong Bull  (> +0.15%)
  1: Weak Bull    (0% a +0.15%)
  2: Weak Bear    (-0.15% a 0%)
  3: Strong Bear  (< -0.15%)

Condición de entrada (ambas deben cumplirse):
  1. gap = p_hat - q(w) >= ε       (edge vs mercado)
  2. p_j*j* >= τ                   (persistencia del estado)
"""
import requests
import logging
import time as time_module
import json
import numpy as np
from datetime import datetime

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"

# ── PARÁMETROS MARKOV ─────────────────────────────────────────────────────────
TAU         = 0.87   # persistencia mínima del estado (diagonal)
EPSILON     = 0.04   # gap mínimo entre p_hat y q(w)
Q_MIN       = 0.83   # precio mínimo del outcome para operar (como Bonereaper)
Q_MAX       = 0.97   # precio máximo del outcome

# ── PARÁMETROS DE RIESGO ──────────────────────────────────────────────────────
BANKROLL_RESERVE   = 0.30
MAX_POSITION_PCT   = 0.05
MIN_POSITION       = 2.0
MAX_SPREAD         = 0.04
MIN_LIQUIDITY      = 500
MAX_TRADE_DURATION = 180   # 3 min máximo en un trade de 5 min
TP_PCT             = 0.05
SL_PCT             = 0.08

# ── UMBRALES DE ESTADO ────────────────────────────────────────────────────────
STATE_THRESHOLDS = [0.15, 0.0, -0.15]  # límites entre estados 0/1, 1/2, 2/3


def classify_state(change_pct: float) -> int:
    """
    Clasifica un retorno porcentual en uno de los 4 estados.
    0: Strong Bull, 1: Weak Bull, 2: Weak Bear, 3: Strong Bear
    """
    if change_pct > STATE_THRESHOLDS[0]:  return 0
    if change_pct > STATE_THRESHOLDS[1]:  return 1
    if change_pct > STATE_THRESHOLDS[2]:  return 2
    return 3


def estimate_transition_matrix(states: list) -> np.ndarray:
    """
    Estima la matriz de transición P a partir de una secuencia de estados.
    Usa conteos con suavizado de Laplace para evitar ceros.
    """
    n = 4
    P = np.ones((n, n))  # suavizado Laplace: prior uniforme

    for i in range(len(states) - 1):
        P[states[i]][states[i + 1]] += 1

    # Normalizar filas
    row_sums = P.sum(axis=1, keepdims=True)
    P = P / row_sums
    return P


def should_enter(P: np.ndarray, current_state: int,
                 market_price: float,
                 tau: float = TAU, eps: float = EPSILON) -> dict:
    """
    Función de decisión basada en Markov Chain.

    Returns:
        {enter: bool, j_star: int, p_hat: float, persist: float, gap: float}
    """
    j_star  = int(np.argmax(P[current_state]))     # mejor próximo estado
    p_hat   = float(P[current_state][j_star])      # probabilidad del modelo
    persist = float(P[j_star][j_star])             # diagonal — persistencia
    gap     = p_hat - market_price                 # arbitrage gap

    return {
        "enter":   gap >= eps and persist >= tau,
        "j_star":  j_star,
        "p_hat":   round(p_hat, 4),
        "persist": round(persist, 4),
        "gap":     round(gap, 4),
        "reason":  f"j*={j_star} p_hat={p_hat:.3f} persist={persist:.3f} gap={gap:.4f}"
    }


# ── PRECIO BTC ────────────────────────────────────────────────────────────────

def get_btc_candles(n: int = 40) -> list:
    """
    Obtiene las últimas N velas de BTC via CoinGecko (OHLC diario en resolución ~30min).
    Retorna lista de cambios porcentuales entre cierres consecutivos.
    """
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc",
            params={"vs_currency": "usd", "days": "1"},
            timeout=10
        )
        data = r.json()
        if not data or len(data) < 5:
            return []

        closes = [c[4] for c in data]
        changes = [
            (closes[i] - closes[i-1]) / closes[i-1] * 100
            for i in range(1, len(closes))
        ]
        return changes[-n:]

    except Exception as e:
        import traceback
        logger.error(f"get_btc_candles:\n{traceback.format_exc()}")
        return []


def get_btc_current_price() -> float:
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "bitcoin", "vs_currencies": "usd"},
            timeout=5
        )
        return float(r.json()["bitcoin"]["usd"])
    except:
        return 0.0


# ── MERCADO POLYMARKET ────────────────────────────────────────────────────────

def find_active_btc_5m_market():
    try:
        now = int(time_module.time())
        interval = 300
        slugs = [f"btc-updown-5m-{((now // interval) + i) * interval}" for i in range(0, 4)]

        for slug in slugs:
            r = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=10)
            if r.status_code == 200:
                events = r.json()
                if events:
                    markets = events[0].get("markets", [])
                    if markets:
                        m = markets[0]
                        if _is_valid_btc_updown(m):
                            logger.info(f"Mercado: {slug}")
                            return m
        return None
    except Exception as e:
        logger.error(f"find_active_btc_5m_market: {e}")
        return None


def _is_valid_btc_updown(market) -> bool:
    q    = (market.get("question") or "").lower()
    slug = (market.get("slug") or "").lower()
    sports = ["nba","nfl","nhl","mlb","basketball","football","tennis","golf",
              "ufc","match","game","lakers","warriors","mavericks","grizzlies"]
    has_btc = "btc" in q or "bitcoin" in q or "btc" in slug
    has_dir = any(w in q for w in ["up","down","higher","lower"]) or "updown" in slug
    is_sport = any(w in q for w in sports)
    return has_btc and has_dir and not is_sport


def get_market_outcome_prices(market) -> dict:
    try:
        outcomes = market.get("outcomes", "[]")
        prices   = market.get("outcomePrices", "[]")
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if isinstance(prices,   str): prices   = json.loads(prices)
        result = {}
        for i, outcome in enumerate(outcomes):
            o = outcome.lower()
            p = float(prices[i]) if i < len(prices) else 0.5
            if any(w in o for w in ["up","higher","above"]):
                result["up_price"] = p; result["up_outcome"] = outcome
            elif any(w in o for w in ["down","lower","below"]):
                result["down_price"] = p; result["down_outcome"] = outcome
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


def get_market_liquidity(market_id) -> dict | None:
    try:
        r = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=5)
        if r.status_code != 200:
            return None
        m = r.json()
        best_bid  = float(m.get("bestBid",  0) or 0)
        best_ask  = float(m.get("bestAsk",  1) or 1)
        liquidity = float(m.get("liquidityNum", 0) or 0)
        spread    = best_ask - best_bid if best_ask > best_bid else 1.0
        return {"spread": round(spread,4), "liquidity": round(liquidity,2),
                "best_bid": best_bid, "best_ask": best_ask}
    except:
        return None


def get_outcome_current_price(market_id, direction) -> float | None:
    try:
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
            if (direction=="up"   and any(w in o_lower for w in ["up","higher","above"])) or \
               (direction=="down" and any(w in o_lower for w in ["down","lower","below"])):
                return float(prices[i]) if i < len(prices) else None
        return None
    except:
        return None


# ── SCALPER ───────────────────────────────────────────────────────────────────

class BTCScalper:
    def __init__(self, trader, log_fn=None):
        self.trader = trader
        self.log    = log_fn or print
        self.cycle  = 0
        # trade_id → {market_id, direction, entry_price, entered_at}
        self._open: dict = {}

    def _check_exits(self):
        state  = self.trader.load_state()
        active = {t["id"]: t for t in state["active_trades"]}

        for trade_id, meta in list(self._open.items()):
            if trade_id not in active:
                self._open.pop(trade_id, None)
                continue

            trade   = active[trade_id]
            pos     = trade["position_size"]
            elapsed = time_module.time() - meta["entered_at"]
            current = get_outcome_current_price(meta["market_id"], meta["direction"])

            # Salida por tiempo máximo
            if elapsed >= MAX_TRADE_DURATION:
                pnl = round(pos * (current - meta["entry_price"]) / meta["entry_price"], 2) if current else 0
                self.trader.resolve_trade_with_pnl(trade_id, pnl)
                self._open.pop(trade_id, None)
                self.log(f"⏱️  TIEMPO #{trade_id} · {'+'if pnl>=0 else ''}${pnl:.2f} ({elapsed:.0f}s)",
                         "#F5A623" if pnl >= 0 else "#FF5050")
                continue

            if current is None:
                continue

            entry  = meta["entry_price"]
            change = (current - entry) / entry if entry > 0 else 0

            if change >= TP_PCT:
                pnl = round(pos * change, 2)
                self.trader.resolve_trade_with_pnl(trade_id, pnl)
                self._open.pop(trade_id, None)
                self.log(f"✅ TP #{trade_id} · +${pnl:.2f} ({change*100:.1f}%) {elapsed:.0f}s", "#00E887")

            elif change <= -SL_PCT:
                pnl = round(pos * change, 2)
                self.trader.resolve_trade_with_pnl(trade_id, pnl)
                self._open.pop(trade_id, None)
                self.log(f"🛑 SL #{trade_id} · ${pnl:.2f} ({change*100:.1f}%) {elapsed:.0f}s", "#FF5050")

    def run_once(self):
        self.cycle += 1
        self._check_exits()

        state    = self.trader.load_state()
        active   = state["active_trades"]
        bankroll = state["bankroll"]

        # Protección de capital
        capital_disponible = bankroll * (1 - BANKROLL_RESERVE)
        capital_en_uso     = sum(t["position_size"] for t in active)
        if capital_en_uso >= capital_disponible:
            self.log(f"🔒 Capital protegido · en uso ${capital_en_uso:.2f}", "#F5A623")
            return False

        # Mercado activo
        market = find_active_btc_5m_market()
        if not market:
            self.log("❌ Sin mercado BTC 5m", "#FF5050")
            return False

        market_id = market.get("conditionId") or market.get("id", "")
        question  = market.get("question", "")

        if any(t["market_id"] == market_id for t in active):
            return False

        # Filtro de liquidez
        liquidity = get_market_liquidity(market_id)
        if liquidity:
            if liquidity["spread"] > MAX_SPREAD:
                self.log(f"⏸️  Spread {liquidity['spread']*100:.1f}% muy alto", "#F5A623")
                return False
            if liquidity["liquidity"] < MIN_LIQUIDITY:
                self.log(f"⏸️  Liquidez ${liquidity['liquidity']:.0f} insuficiente", "#F5A623")
                return False

        # Obtener velas y construir estados
        changes = get_btc_candles(40)
        if len(changes) < 10:
            self.log("❌ Sin datos BTC suficientes", "#FF5050")
            return False

        states = [classify_state(c) for c in changes]
        P      = estimate_transition_matrix(states)
        current_state = states[-1]
        btc_price = get_btc_current_price()

        self.log(
            f"BTC ${btc_price:,.0f} · estado={current_state} · "
            f"P={[[round(p,2) for p in row] for row in P.tolist()]}",
            "#ffffff40"
        )

        # Precios del mercado
        prices = get_market_outcome_prices(market)

        # Probar entrada en Up
        if prices["up_price"] >= Q_MIN and prices["up_price"] <= Q_MAX:
            decision = should_enter(P, current_state, prices["up_price"])
            if decision["enter"]:
                return self._execute(market_id, question, "up",
                                     prices["up_price"], prices["up_outcome"],
                                     decision, bankroll, capital_disponible, capital_en_uso)
            else:
                self.log(f"⏸️  Up — {decision['reason']}", "#F5A623")

        # Probar entrada en Down
        if prices["down_price"] >= Q_MIN and prices["down_price"] <= Q_MAX:
            decision = should_enter(P, current_state, prices["down_price"])
            if decision["enter"]:
                return self._execute(market_id, question, "down",
                                     prices["down_price"], prices["down_outcome"],
                                     decision, bankroll, capital_disponible, capital_en_uso)
            else:
                self.log(f"⏸️  Down — {decision['reason']}", "#F5A623")

        return False

    def _execute(self, market_id, question, direction, market_price,
                 outcome, decision, bankroll, cap_disp, cap_uso):
        position = round(min(bankroll * MAX_POSITION_PCT, cap_disp - cap_uso, 50.0), 2)
        if position < MIN_POSITION:
            self.log(f"💰 Posición ${position:.2f} muy pequeña", "#FF5050")
            return False

        trade = self.trader.place_trade(
            market_id=market_id,
            question=question,
            true_prob=decision["p_hat"],
            market_prob=market_price,
            ev=decision["gap"],
            position_size=position
        )

        if trade:
            self._open[trade["id"]] = {
                "market_id":   market_id,
                "direction":   direction,
                "entry_price": market_price,
                "entered_at":  time_module.time(),
            }
            self.log(
                f"✅ TRADE #{trade['id']} · {'📈 UP' if direction=='up' else '📉 DOWN'} · "
                f"${position:.2f} · p_hat={decision['p_hat']:.3f} · "
                f"persist={decision['persist']:.3f} · gap={decision['gap']:.4f}",
                "#00E887"
            )
            return True
        return False