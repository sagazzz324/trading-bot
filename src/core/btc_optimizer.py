# src/core/btc_optimizer.py

import json
import logging
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

POLY_LOG     = Path("logs/paper_trades.json")
REGIMES_FILE = Path("logs/regimes.json")

# Valores por defecto
DEFAULT_PARAMS = {
    "tau":     0.17,
    "epsilon": 0.03,
    "q_min":   0.35,
    "q_max":   0.72,
}

# Límites absolutos para evitar parámetros extremos
LIMITS = {
    "tau":     (0.10, 0.40),
    "epsilon": (0.01, 0.15),
    "q_min":   (0.10, 0.40),
    "q_max":   (0.60, 0.92),
}


def _detect_regime(resolved: list) -> str:
    """
    Detecta el régimen actual del mercado basándose en los últimos trades.
    Usa el market_prob promedio como proxy de qué tan sesgado está el mercado.
    """
    if len(resolved) < 10:
        return "unknown"

    recent = resolved[-20:]
    probs  = [t.get("market_prob", 0.5) for t in recent]
    avg_prob = sum(probs) / len(probs)

    # Qué tan lejos está el promedio de 0.5
    skew = abs(avg_prob - 0.5)

    if skew < 0.08:
        return "balanced"     # mercado equilibrado, precios cerca de 0.5
    elif skew < 0.18:
        return "mild_trend"   # tendencia suave
    else:
        return "strong_trend" # tendencia fuerte, precios muy sesgados


def _load_regimes() -> dict:
    """Carga la memoria de regímenes."""
    if REGIMES_FILE.exists():
        try:
            with open(REGIMES_FILE) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"_load_regimes: {e}")
    return {}


def _save_regimes(data: dict):
    """Guarda la memoria de regímenes."""
    try:
        REGIMES_FILE.parent.mkdir(exist_ok=True)
        with open(REGIMES_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"_save_regimes: {e}\n{traceback.format_exc()}")


def get_best_params_for_regime(regime: str) -> dict:
    """
    Retorna los mejores parámetros históricos para el régimen dado.
    Si no hay historial, retorna los defaults adaptados al régimen.
    """
    regimes = _load_regimes()

    if regime in regimes:
        entry = regimes[regime]
        # Solo usar si tiene suficientes trades y buen win rate
        if entry.get("trades", 0) >= 30 and entry.get("win_rate", 0) >= 0.50:
            logger.info(f"Usando parámetros históricos para régimen '{regime}': {entry['params']}")
            return entry["params"]

    # Defaults por régimen si no hay historial
    if regime == "balanced":
        return {"tau": 0.17, "epsilon": 0.03, "q_min": 0.35, "q_max": 0.72}
    elif regime == "mild_trend":
        return {"tau": 0.20, "epsilon": 0.04, "q_min": 0.25, "q_max": 0.78}
    elif regime == "strong_trend":
        return {"tau": 0.22, "epsilon": 0.05, "q_min": 0.18, "q_max": 0.85}
    else:
        return DEFAULT_PARAMS.copy()


def _clamp(value: float, key: str) -> float:
    lo, hi = LIMITS[key]
    return max(lo, min(hi, value))


def analyze_and_tune(min_trades=50) -> dict | None:
    """
    Analiza los últimos trades y optimiza TAU, EPSILON, Q_MIN y Q_MAX.
    También guarda los resultados en la memoria de regímenes.
    Retorna dict con todos los parámetros optimizados.
    """
    if not POLY_LOG.exists():
        return None

    try:
        with open(POLY_LOG) as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"analyze_and_tune load: {e}")
        return None

    resolved = [t for t in data.get("trades", []) if t.get("status") == "resolved"]
    if len(resolved) < min_trades:
        return None

    recent   = resolved[-200:]
    total    = len(recent)
    wins     = sum(1 for t in recent if t.get("result") == "win")
    win_rate = wins / total if total > 0 else 0.5

    # ── Detectar régimen actual ───────────────────────────────────────────────
    regime = _detect_regime(recent)

    # ── Optimizar EPSILON (gap mínimo) ────────────────────────────────────────
    wins_by_gap  = {}
    total_by_gap = {}
    for t in recent:
        gap = round(t.get("ev", 0), 2)
        win = 1 if t.get("result") == "win" else 0
        wins_by_gap[gap]  = wins_by_gap.get(gap, 0) + win
        total_by_gap[gap] = total_by_gap.get(gap, 0) + 1

    best_eps = DEFAULT_PARAMS["epsilon"]
    for gap, cnt in sorted(total_by_gap.items()):
        if cnt >= 10:
            wr = wins_by_gap[gap] / cnt
            if wr >= 0.52:
                best_eps = gap
                break
    best_eps = _clamp(best_eps, "epsilon")

    # ── Optimizar TAU (persistencia mínima) ───────────────────────────────────
    if win_rate > 0.55:
        best_tau = 0.17
    elif win_rate > 0.52:
        best_tau = 0.20
    elif win_rate > 0.50:
        best_tau = 0.22
    else:
        best_tau = 0.25
    best_tau = _clamp(best_tau, "tau")

    # ── Optimizar Q_MIN y Q_MAX ───────────────────────────────────────────────
    # Analizamos en qué rangos de market_prob el bot tuvo mejor win rate
    range_stats = {}
    for t in recent:
        mp  = t.get("market_prob", 0.5)
        win = 1 if t.get("result") == "win" else 0
        bucket = round(mp * 10) / 10  # bucketing en 0.1
        if bucket not in range_stats:
            range_stats[bucket] = {"wins": 0, "total": 0}
        range_stats[bucket]["wins"]  += win
        range_stats[bucket]["total"] += 1

    # Encontrar el rango de precios con win rate >= 50%
    good_buckets = []
    for price, stats in range_stats.items():
        if stats["total"] >= 5:
            wr_bucket = stats["wins"] / stats["total"]
            if wr_bucket >= 0.50:
                good_buckets.append(price)

    if good_buckets:
        best_qmin = _clamp(min(good_buckets) - 0.05, "q_min")
        best_qmax = _clamp(max(good_buckets) + 0.05, "q_max")
    else:
        # Sin datos suficientes — ampliar según régimen
        if regime == "strong_trend":
            best_qmin = 0.18
            best_qmax = 0.85
        elif regime == "mild_trend":
            best_qmin = 0.25
            best_qmax = 0.78
        else:
            best_qmin = DEFAULT_PARAMS["q_min"]
            best_qmax = DEFAULT_PARAMS["q_max"]

    best_qmin = _clamp(best_qmin, "q_min")
    best_qmax = _clamp(best_qmax, "q_max")

    result = {
        "tau":      best_tau,
        "epsilon":  best_eps,
        "q_min":    best_qmin,
        "q_max":    best_qmax,
        "win_rate": round(win_rate, 3),
        "trades":   total,
        "regime":   regime,
    }

    # ── Guardar en memoria de regímenes ───────────────────────────────────────
    regimes = _load_regimes()
    prev    = regimes.get(regime, {})

    # Solo actualizar si el nuevo win rate es mejor o si hay más datos
    should_update = (
        win_rate >= prev.get("win_rate", 0) or
        total >= prev.get("trades", 0) * 1.5
    )

    if should_update:
        regimes[regime] = {
            "params":     {"tau": best_tau, "epsilon": best_eps,
                           "q_min": best_qmin, "q_max": best_qmax},
            "win_rate":   round(win_rate, 3),
            "trades":     total,
            "updated_at": datetime.now(timezone(timedelta(hours=-3))).isoformat(),
        }
        _save_regimes(regimes)
        logger.info(f"Régimen '{regime}' actualizado: WR={win_rate:.1%} params={result}")

    return result


def get_regime_summary() -> dict:
    """Retorna un resumen de todos los regímenes conocidos."""
    regimes = _load_regimes()
    if not regimes:
        return {"message": "Sin datos de regímenes todavía"}
    return regimes