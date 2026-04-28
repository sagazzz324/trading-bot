"""
btc_scalper.py — BTC Up/Down 5m · Polymarket
Markov Chain + State Persistence + Arbitrage Gap
"""
import requests
import logging
import time as time_module
import json
import numpy as np
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"

# ── PARAMETROS MARKOV ─────────────────────────────────────────────────────────
TAU         = 0.10
EPSILON     = 0.03
Q_MIN       = 0.35
Q_MAX       = 0.72

# ── PARAMETROS DE RIESGO ──────────────────────────────────────────────────────
BANKROLL_RESERVE   = 0.30
MAX_POSITION_PCT   = 0.05
MIN_POSITION       = 5.0
MAX_SPREAD         = 0.06
MIN_LIQUIDITY      = 300
ALLOW_DOWN_ENTRIES = False

# ── PARAMETROS DE SALIDA ──────────────────────────────────────────────────────
MAX_TRADE_DURATION  = 360   # 6 min — el mercado de 5m debería resolver
RESOLUTION_WIN      = 0.92  # precio que indica "casi ganó"
RESOLUTION_LOSS     = 0.08  # precio que indica "casi perdió"
DEFENSIVE_DROP      = 0.35  # si el precio cae 35% desde entrada, salir

# ── PARAMETROS DE SEGURIDAD ───────────────────────────────────────────────────
SL_COOLDOWN_SEC    = 30
SL_STREAK_LIMIT    = 3
SL_STREAK_PAUSE    = 300
DAILY_LOSS_LIMIT   = -50.0

STATE_THRESHOLDS = [0.15, 0.0, -0.15]

_tune_counter = 0
_TUNE_EVERY   = 50


def classify_state(change_pct: float) -> int:
    if change_pct > STATE_THRESHOLDS[0]:  return 0
    if change_pct > STATE_THRESHOLDS[1]:  return 1
    if change_pct > STATE_THRESHOLDS[2]:  return 2
    return 3


def estimate_transition_matrix(states: list) -> np.ndarray:
    n = 4
    P = np.ones((n, n))
    for i in range(len(states) - 1):
        P[states[i]][states[i + 1]] += 1
    row_sums = P.sum(axis=1, keepdims=True)
    return P / row_sums


def should_enter(P: np.ndarray, current_state: int,
                 market_price: float, direction: str,
                 tau: float = TAU, eps: float = EPSILON) -> dict:
    if direction == "up":
        p_hat = float(P[current_state][0] + P[current_state][1])
    else:
        p_hat = float(P[current_state][2] + P[current_state][3])

    j_star  = int(np.argmax(P[current_state]))
    persist = float(P[j_star][j_star])
    gap     = p_hat - market_price

    return {
        "enter":   gap >= eps and persist >= tau,
        "j_star":  j_star,
        "p_hat":   round(p_hat, 4),
        "persist": round(persist, 4),
        "gap":     round(gap, 4),
        "reason":  f"p_{direction}={p_hat:.3f} persist={persist:.3f} gap={gap:.4f}"
    }


# ── CACHE COINGECKO ───────────────────────────────────────────────────────────
_btc_cache   = {"data": [], "ts": 0}
_price_cache = {"price": 0.0, "ts": 0}


def get_btc_candles(n: int = 40) -> list:
    global _btc_cache
    if time_module.time() - _btc_cache["ts"] < 60 and _btc_cache["data"]:
        return _btc_cache["data"][-n:]
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc",
            params={"vs_currency": "usd", "days": "1"},
            timeout=10
        )
        data = r.json()
        if not data or len(data) < 5:
            return _btc_cache["data"][-n:] if _btc_cache["data"] else []
        closes  = [c[4] for c in data]
        changes = [(closes[i] - closes[i-1]) / closes[i-1] * 100
                   for i in range(1, len(closes))]
        _btc_cache = {"data": changes, "ts": time_module.time()}
        return changes[-n:]
    except Exception as e:
        logger.error(f"get_btc_candles: {e}")
        return _btc_cache["data"][-n:] if _btc_cache["data"] else []


def get_btc_current_price() -> float:
    global _price_cache
    if time_module.time() - _price_cache["ts"] < 15 and _price_cache["price"] > 0:
        return _price_cache["price"]
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "BTCUSDT"},
            timeout=5
        )
        price = float(r.json()["price"])
        _price_cache = {"price": price, "ts": time_module.time()}
        return price
    except:
        try:
            r2 = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                timeout=8
            )
            price = float(r2.json()["bitcoin"]["usd"])
            _price_cache = {"price": price, "ts": time_module.time()}
            return price
        except:
            return _price_cache["price"] if _price_cache["price"] > 0 else 0.0


# ── MERCADO POLYMARKET ────────────────────────────────────────────────────────

def find_active_btc_5m_market(log_fn=None):
    log = log_fn or (lambda m, c="#ffffff40": None)
    now = int(time_module.time())
    interval = 300

    for i in [0, 1, 2, 3, -1]:
        ts = ((now // interval) + i) * interval
        slug = f"btc-updown-5m-{ts}"
        try:
            r = requests.get(f"{GAMMA_API}/events",
                             params={"slug": slug}, timeout=6)
            if r.status_code == 200:
                events = r.json()
                if events and events[0].get("markets"):
                    m = events[0]["markets"][0]
                    if _is_valid_btc_updown(m) and _market_has_live_orderbook(m):
                        logger.info(f"Mercado encontrado: {slug}")
                        return m
        except Exception:
            continue

    try:
        r = requests.get(
            f"{GAMMA_API}/events",
            params={"active": "true", "limit": 50, "tag_slug": "crypto"},
            timeout=8
        )
        if r.status_code == 200:
            events = r.json()
            for event in events:
                slug = (event.get("slug") or "").lower()
                if "btc-updown-5m-" in slug:
                    markets = event.get("markets", [])
                    if markets and _is_valid_btc_updown(markets[0]) and _market_has_live_orderbook(markets[0]):
                        logger.info(f"Mercado encontrado por keyword: {slug}")
                        return markets[0]
    except Exception as e:
        logger.error(f"find_active_btc_5m_market fallback: {e}")

    log(f"❌ Sin mercado activo", "#FF5050")
    return None


def _is_valid_btc_updown(market) -> bool:
    q    = (market.get("question") or "").lower()
    slug = (market.get("slug") or "").lower()
    sports = ["nba","nfl","nhl","mlb","basketball","football","tennis",
              "golf","ufc","match","game","lakers","warriors","mavericks"]
    has_btc = "btc" in q or "bitcoin" in q or "btc" in slug
    has_dir = any(w in q for w in ["up","down","higher","lower"]) or "updown" in slug
    if not has_btc or not has_dir: return False
    if any(w in q for w in sports): return False
    if market.get("closed"): return False
    if not market.get("acceptingOrders", True): return False
    try:
        prices = json.loads(market.get("outcomePrices","[]")) if isinstance(market.get("outcomePrices"), str) else (market.get("outcomePrices") or [])
        if prices and any(float(p) in (0.0, 1.0) for p in prices):
            return False
    except:
        pass
    return True


def _token_has_orderbook(token_id: str) -> bool:
    if not token_id:
        return False
    try:
        r = requests.get(
            "https://clob.polymarket.com/book",
            params={"token_id": token_id},
            timeout=4
        )
        return r.status_code == 200
    except Exception:
        return False


def _get_token_book(token_id: str) -> dict | None:
    if not token_id:
        return None
    try:
        r = requests.get(
            "https://clob.polymarket.com/book",
            params={"token_id": token_id},
            timeout=4
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _token_has_executable_asks(token_id: str) -> bool:
    book = _get_token_book(token_id)
    if not isinstance(book, dict):
        return False
    asks = book.get("asks") or []
    return len(asks) > 0


def _market_has_live_orderbook(market: dict) -> bool:
    try:
        tokens = market.get("clobTokenIds", "[]")
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if not tokens:
            return False
        return any(_token_has_orderbook(str(token_id)) for token_id in tokens)
    except Exception as e:
        logger.error(f"_market_has_live_orderbook: {e}")
        return False


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
        if "up_price" not in result:
            result["up_price"]   = float(prices[0]) if prices else 0.5
            result["up_outcome"] = outcomes[0] if outcomes else "Up"
        if "down_price" not in result:
            result["down_price"]   = float(prices[1]) if len(prices)>1 else 0.5
            result["down_outcome"] = outcomes[1] if len(outcomes)>1 else "Down"
        return result
    except Exception as e:
        logger.error(f"get_market_outcome_prices: {e}")
        return {"up_price":0.5,"down_price":0.5,"up_outcome":"Up","down_outcome":"Down"}


def _get_clob_token_id(market: dict, direction: str) -> str:
    try:
        clob_tokens = market.get("clobTokenIds", "[]")
        if isinstance(clob_tokens, str):
            clob_tokens = json.loads(clob_tokens)
        if not clob_tokens:
            return market.get("conditionId") or market.get("id", "")
        outcomes = market.get("outcomes", "[]")
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        for i, outcome in enumerate(outcomes):
            o = outcome.lower()
            if direction == "up" and any(w in o for w in ["up", "higher", "above"]):
                return clob_tokens[i] if i < len(clob_tokens) else clob_tokens[0]
            if direction == "down" and any(w in o for w in ["down", "lower", "below"]):
                return clob_tokens[i] if i < len(clob_tokens) else clob_tokens[1]
        idx = 0 if direction == "up" else 1
        return clob_tokens[idx] if idx < len(clob_tokens) else clob_tokens[0]
    except Exception as e:
        logger.error(f"_get_clob_token_id: {e}")
        return market.get("conditionId") or market.get("id", "")


def _get_best_ask(market: dict, direction: str) -> float:
    try:
        prices = get_market_outcome_prices(market)
        base = prices["up_price"] if direction == "up" else prices["down_price"]
        return round(max(0.01, min(0.99, base + 0.01)), 2)
    except Exception as e:
        logger.error(f"_get_best_ask: {e}")
        return 0.51


def _has_active_market_position(active_trades: list, condition_id: str, question: str) -> bool:
    for trade in active_trades:
        if trade.get("condition_id") == condition_id:
            return True
        if trade.get("question") == question:
            return True
    return False


def _is_btc_scalp_trade(trade: dict) -> bool:
    question = (trade.get("question") or "").lower()
    slug = (trade.get("slug") or "").lower()
    direction = (trade.get("direction") or "").lower()
    has_btc = "btc" in question or "bitcoin" in question or "btc" in slug
    has_5m = "5m" in question or "5 min" in question or "5-minute" in question or "up or down" in question
    return direction in ("up", "down") and has_btc and has_5m


def _fetch_market_by_condition_id(condition_id: str) -> dict | None:
    try:
        r = requests.get(
            f"{GAMMA_API}/markets",
            params={"condition_ids": condition_id},
            timeout=5
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return None
    except Exception as e:
        logger.error(f"_fetch_market_by_condition_id: {e}")
        return None


def get_market_liquidity(market_id: str) -> dict | None:
    try:
        m = _fetch_market_by_condition_id(market_id)
        if not m:
            return None
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


def get_outcome_current_price(market_id: str, direction: str) -> float | None:
    try:
        m = _fetch_market_by_condition_id(market_id)
        if not m:
            return None
        outcomes = m.get("outcomes", "[]")
        prices   = m.get("outcomePrices", "[]")
        if isinstance(outcomes, str): outcomes = json.loads(outcomes)
        if isinstance(prices,   str): prices   = json.loads(prices)
        for i, o in enumerate(outcomes):
            o_lower = o.lower()
            if (direction == "up"   and any(w in o_lower for w in ["up","higher","above"])) or \
               (direction == "down" and any(w in o_lower for w in ["down","lower","below"])):
                return float(prices[i]) if i < len(prices) else None
        return None
    except Exception as e:
        logger.error(f"get_outcome_current_price: {e}")
        return None


def _get_argentina_day() -> str:
    ar = datetime.now(timezone(timedelta(hours=-3)))
    return ar.strftime("%Y-%m-%d")


# ── SCALPER ───────────────────────────────────────────────────────────────────

class BTCScalper:
    def __init__(self, trader, log_fn=None):
        self.trader = trader
        self.log    = log_fn or print
        self.cycle  = 0
        self._open: dict = {}

        self._last_sl_time       = 0.0
        self._sl_streak          = 0
        self._streak_pause_until = 0.0
        self._daily_pnl          = 0.0
        self._daily_day          = ""

        self._regime         = "unknown"
        self._regime_checked = 0.0
        self._REGIME_TTL     = 300

        self._load_regime_params()

    def _load_regime_params(self):
        global TAU, EPSILON, Q_MIN, Q_MAX
        try:
            from src.core.btc_optimizer import get_best_params_for_regime
            params = get_best_params_for_regime(self._regime)
            TAU     = params["tau"]
            EPSILON = params["epsilon"]
            Q_MIN   = params["q_min"]
            Q_MAX   = params["q_max"]
            self.log(
                f"📊 Régimen '{self._regime}' · "
                f"TAU={TAU} EPS={EPSILON} Q=[{Q_MIN},{Q_MAX}]",
                "#A78BFA"
            )
        except Exception as e:
            logger.error(f"_load_regime_params: {e}")

    def _reset_daily_if_needed(self):
        today = _get_argentina_day()
        if self._daily_day != today:
            self._daily_day  = today
            self._daily_pnl  = 0.0
            self._sl_streak  = 0
            self.log(f"📅 Nuevo día · daily PnL reseteado", "#ffffff40")

    def _register_sl(self, pnl: float):
        self._last_sl_time = time_module.time()
        self._sl_streak   += 1
        self._daily_pnl   += pnl
        if self._sl_streak >= SL_STREAK_LIMIT:
            self._streak_pause_until = time_module.time() + SL_STREAK_PAUSE
            self.log(
                f"⛔ Racha de {self._sl_streak} SL seguidos · pausando {SL_STREAK_PAUSE//60} min",
                "#FF5050"
            )

    def _register_tp(self, pnl: float):
        self._sl_streak  = 0
        self._daily_pnl += pnl

    def _can_enter(self) -> tuple[bool, str]:
        now = time_module.time()
        if now < self._streak_pause_until:
            remaining = int(self._streak_pause_until - now)
            return False, f"⏸️ Pausa post-racha · {remaining}s restantes"
        since_sl = now - self._last_sl_time
        if since_sl < SL_COOLDOWN_SEC:
            remaining = int(SL_COOLDOWN_SEC - since_sl)
            return False, f"⏸️ Cooldown post-SL · {remaining}s restantes"
        if self._daily_pnl <= DAILY_LOSS_LIMIT:
            return False, f"⛔ Daily loss ${self._daily_pnl:.2f} · límite alcanzado"
        return True, ""

    def _calc_position_size(self, gap: float, bankroll: float,
                             cap_disp: float, cap_uso: float,
                             current_state: int) -> float:
        if gap < 0.08:
            pct = 0.03
        elif gap < 0.15:
            pct = 0.04
        else:
            pct = MAX_POSITION_PCT

        position = round(min(
            bankroll * pct,
            cap_disp - cap_uso,
            50.0
        ), 2)
        return position

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
            entry   = meta["entry_price"]

            # Sin precio — cerrar si ya expiró el tiempo
            if current is None:
                if elapsed >= MAX_TRADE_DURATION:
                    pnl = round(-pos * 0.05, 2)
                    closed = self.trader.resolve_trade_with_pnl(trade_id, pnl, exit_price=entry * 0.95)
                    if closed:
                        self._open.pop(trade_id, None)
                        self._register_sl(pnl)
                        self.log(f"⏱️ TIEMPO sin precio #{trade_id} · ${pnl:.2f}", "#F5A623")
                continue

            pnl_actual = round(pos * (current - entry) / entry, 2) if entry > 0 else 0

            # 1. EXIT POR RESOLUCIÓN — ganó
            if current >= RESOLUTION_WIN:
                closed = self.trader.resolve_trade_with_pnl(trade_id, pnl_actual, exit_price=current)
                if closed:
                    self._open.pop(trade_id, None)
                    self._register_tp(pnl_actual)
                    self.log(
                        f"✅ WIN #{trade_id} · +${pnl_actual:.2f} @ {current:.3f} ({elapsed:.0f}s)",
                        "#00E887"
                    )
                continue

            # 2. EXIT POR RESOLUCIÓN — perdió
            if current <= RESOLUTION_LOSS:
                closed = self.trader.resolve_trade_with_pnl(trade_id, pnl_actual, exit_price=current)
                if closed:
                    self._open.pop(trade_id, None)
                    self._register_sl(pnl_actual)
                    self.log(
                        f"🛑 LOSS #{trade_id} · ${pnl_actual:.2f} @ {current:.3f} ({elapsed:.0f}s)",
                        "#FF5050"
                    )
                continue

            # 3. EXIT DEFENSIVO — precio cayó mucho desde entrada
            drop = (entry - current) / entry if entry > 0 else 0
            if drop >= DEFENSIVE_DROP:
                closed = self.trader.resolve_trade_with_pnl(trade_id, pnl_actual, exit_price=current)
                if closed:
                    self._open.pop(trade_id, None)
                    self._register_sl(pnl_actual)
                    self.log(
                        f"🛡️ DEFENSIVO #{trade_id} · ${pnl_actual:.2f} caída {drop*100:.0f}% ({elapsed:.0f}s)",
                        "#FF5050"
                    )
                continue

            # 4. EXIT POR TIEMPO — cierre forzado con lo que haya
            if elapsed >= MAX_TRADE_DURATION:
                closed = self.trader.resolve_trade_with_pnl(trade_id, pnl_actual, exit_price=current)
                if not closed:
                    self.log(f"❌ No se pudo cerrar trade #{trade_id}", "#FF5050")
                    continue
                self._open.pop(trade_id, None)
                if pnl_actual >= 0:
                    self._register_tp(pnl_actual)
                else:
                    self._register_sl(pnl_actual)
                self.log(
                    f"⏱️ TIEMPO #{trade_id} · {'+'if pnl_actual>=0 else ''}${pnl_actual:.2f} @ {current:.3f} ({elapsed:.0f}s)",
                    "#F5A623" if pnl_actual >= 0 else "#FF5050"
                )

    def run_once(self):
        global _tune_counter, TAU, EPSILON, Q_MIN, Q_MAX

        self.cycle += 1
        self.log(f"🧭 Ciclo BTCScalp #{self.cycle} · start", "#ffffff30")
        self._reset_daily_if_needed()
        self.log("🧭 Paso 1/6 · precio BTC", "#ffffff30")
        get_btc_current_price()
        self.log("🧭 Paso 2/6 · revisar salidas", "#ffffff30")
        self._check_exits()

        self.log("🧭 Paso 3/6 · control de entrada", "#ffffff30")
        can_enter, reason = self._can_enter()
        if not can_enter:
            self.log(reason, "#F5A623")
            return False

        self.log("🧭 Paso 4/6 · cargar estado/balance", "#ffffff30")
        state    = self.trader.load_state()
        active_all = state["active_trades"]
        active   = [t for t in active_all if _is_btc_scalp_trade(t)]
        bankroll = state["bankroll"]

        cap_disp = bankroll * (1 - BANKROLL_RESERVE)
        cap_uso  = sum(t["position_size"] for t in active)
        ignored_active = len(active_all) - len(active)
        if ignored_active > 0:
            self.log(f"Ignorando {ignored_active} trade(s) activos viejos fuera de BTC scalp", "#ffffff40")
        if cap_uso >= cap_disp:
            self.log(f"🔒 Capital protegido · en uso ${cap_uso:.2f}", "#F5A623")
            return False

        self.log("🧭 Paso 5/6 · buscar mercado BTC 5m", "#ffffff30")
        market = find_active_btc_5m_market(log_fn=self.log)
        if not market:
            return False

        market_id = market.get("conditionId") or market.get("id", "")
        question  = market.get("question", "")

        if _has_active_market_position(active, market_id, question):
            return False

        liquidity = get_market_liquidity(market_id)
        if liquidity:
            if liquidity["spread"] > MAX_SPREAD:
                self.log(f"⏸️ Spread {liquidity['spread']*100:.1f}% alto", "#F5A623")
                return False
            if liquidity["liquidity"] < MIN_LIQUIDITY:
                self.log(f"⏸️ Liquidez ${liquidity['liquidity']:.0f} baja", "#F5A623")
                return False

        self.log("🧭 Paso 6/6 · modelo y decisión", "#ffffff30")
        changes = get_btc_candles(40)
        if len(changes) < 8:
            self.log("❌ Sin datos BTC suficientes", "#FF5050")
            return False

        states        = [classify_state(c) for c in changes]
        P             = estimate_transition_matrix(states)
        current_state = states[-1]

        prices = get_market_outcome_prices(market)
        self.log(
            f"Poly Up={prices['up_price']:.3f} Down={prices['down_price']:.3f} · "
            f"estado={current_state} · "
            f"p_up={P[current_state][0]+P[current_state][1]:.2f} "
            f"p_down={P[current_state][2]+P[current_state][3]:.2f}",
            "#ffffff60"
        )
        self.log(f"💰 up={prices['up_price']:.3f} down={prices['down_price']:.3f}", "#41D6FC")

        if Q_MIN <= prices["up_price"] <= Q_MAX:
            dec = should_enter(P, current_state, prices["up_price"], "up")
            if dec["enter"]:
                return self._execute(market, market_id, question, "up",
                                     prices["up_price"], dec, bankroll,
                                     cap_disp, cap_uso, current_state)
            self.log(f"⏸️ Up — {dec['reason']}", "#F5A623")

        if Q_MIN <= prices["down_price"] <= Q_MAX:
            dec = should_enter(P, current_state, prices["down_price"], "down")
            if dec["enter"]:
                if not ALLOW_DOWN_ENTRIES:
                    self.log(f"⏸️ Down bloqueado por modo UP-only — {dec['reason']}", "#F5A623")
                    return False
                return self._execute(market, market_id, question, "down",
                                     prices["down_price"], dec, bankroll,
                                     cap_disp, cap_uso, current_state)
            self.log(f"⏸️ Down — {dec['reason']}", "#F5A623")

        # ── Auto-tuning cada 50 trades ────────────────────────────────────
        _tune_counter += 1
        if _tune_counter % _TUNE_EVERY == 0:
            try:
                from src.core.btc_optimizer import analyze_and_tune
                result = analyze_and_tune(min_trades=50)
                if result:
                    TAU          = result["tau"]
                    EPSILON      = result["epsilon"]
                    Q_MIN        = result["q_min"]
                    Q_MAX        = result["q_max"]
                    self._regime = result["regime"]
                    self.log(
                        f"🧠 Auto-tune · WR={result['win_rate']*100:.1f}% · "
                        f"régimen='{result['regime']}' · "
                        f"TAU={TAU} EPS={EPSILON} Q=[{Q_MIN:.2f},{Q_MAX:.2f}] · "
                        f"n={result['trades']}",
                        "#A78BFA"
                    )
            except Exception as e:
                logger.error(f"auto-tune: {e}")

        # ── Re-detectar régimen cada 5 minutos ────────────────────────────
        now_ts = time_module.time()
        if now_ts - self._regime_checked > self._REGIME_TTL:
            self._regime_checked = now_ts
            try:
                from src.core.btc_optimizer import _detect_regime, get_best_params_for_regime
                from pathlib import Path
                poly_log = Path("logs/paper_trades.json")
                if poly_log.exists():
                    with open(poly_log) as f:
                        d = json.load(f)
                    resolved = [t for t in d.get("trades", []) if t.get("status") == "resolved"]
                    if len(resolved) >= 10:
                        new_regime = _detect_regime(resolved)
                        if new_regime != self._regime:
                            self._regime = new_regime
                            self._load_regime_params()
            except Exception as e:
                logger.error(f"regime check: {e}")

        return False

    def _execute(self, market, market_id, question, direction, market_price,
                 decision, bankroll, cap_disp, cap_uso, current_state):

        position = self._calc_position_size(
            decision["gap"], bankroll, cap_disp, cap_uso, current_state
        )

        if position < MIN_POSITION:
            if cap_disp - cap_uso >= MIN_POSITION:
                position = MIN_POSITION
            else:
                self.log(f"💰 Capital insuficiente para mínimo ${MIN_POSITION}", "#FF5050")
                return False

        clob_token_id = _get_clob_token_id(market, direction)
        entry_price   = _get_best_ask(market, direction)

        if not _token_has_executable_asks(clob_token_id):
            self.log(f"⏸️ Sin asks reales para {direction.upper()} en CLOB", "#F5A623")
            return False

        self.log(f"🔑 clob_token={clob_token_id[:20]}... dir={direction} ask={entry_price:.3f}", "#ffffff30")

        trade = self.trader.place_trade(
            market_id=clob_token_id,
            question=question,
            true_prob=decision["p_hat"],
            market_prob=market_price,
            ev=decision["gap"],
            position_size=position,
            price=entry_price,
            condition_id=market_id,
            direction=direction,
        )

        if trade:
            self._open[trade["id"]] = {
                "market_id":   market_id,
                "direction":   direction,
                "entry_price": entry_price,
                "entered_at":  time_module.time(),
            }
            gap_label = "🔥" if decision["gap"] >= 0.15 else "✅"
            self.log(
                f"{gap_label} TRADE #{trade['id']} · "
                f"{'📈 UP' if direction=='up' else '📉 DOWN'} · "
                f"${position:.2f} @ {entry_price:.3f} · "
                f"p_hat={decision['p_hat']:.3f} · "
                f"persist={decision['persist']:.3f} · gap={decision['gap']:.4f} · "
                f"daily=${self._daily_pnl:+.2f}",
                "#00E887"
            )
            return True
        return False
