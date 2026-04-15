"""
strategy_orchestrator.py
Analiza condiciones de mercado y decide qué estrategia correr.
Usa BTC como proxy del mercado general.
"""
import logging
import time

logger = logging.getLogger(__name__)

# Thresholds
SLOPE_TREND_THR  = 0.0008   # |slope| > thr = trending → Scalping
SLOPE_LATERAL_THR = 0.0003  # |slope| < thr = lateral  → Grid
VOL_SPIKE_MULT   = 2.2       # ATR > avg * mult         → Pause


def _ema(values: list, period: int) -> float:
    if len(values) < period:
        return values[-1]
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def _atr(highs, lows, closes, period=14) -> float:
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]))
        for i in range(1, len(closes))
    ]
    return sum(trs[-period:]) / min(period, len(trs)) if trs else 0.0


def _ema_slope(closes: list, period: int) -> float:
    if len(closes) < period + 5:
        return 0.0
    e_now  = _ema(closes, period)
    e_prev = _ema(closes[:-3], period)
    return (e_now - e_prev) / e_prev if e_prev else 0.0


class StrategyOrchestrator:
    """
    Decide qué estrategia usar basándose en condiciones del mercado.
    Llama a decide() cada ciclo para obtener la estrategia recomendada.
    """

    def __init__(self, client):
        self.client     = client
        self._cache     = {}        # {strategy, reason, atr, slope, ts}
        self._cache_ttl = 120       # segundos — no recalcular cada ciclo

    def decide(self) -> dict:
        """
        Returns:
            {
                strategy: "Scalping" | "Grid" | "Pause",
                reason:   str,
                slope:    float,
                atr_pct:  float,
                regime:   "trending" | "lateral" | "volatile"
            }
        """
        now = time.time()
        if self._cache and (now - self._cache.get("ts", 0)) < self._cache_ttl:
            return self._cache

        try:
            klines = self.client.get_klines("BTCUSDT", interval="15", limit=100)
            if len(klines) < 50:
                return self._fallback("Sin datos suficientes")

            closes = [k["close"] for k in klines]
            highs  = [k["high"]  for k in klines]
            lows   = [k["low"]   for k in klines]
            vols   = [k["volume"] for k in klines]

            atr     = _atr(highs, lows, closes)
            atr_pct = atr / closes[-1] * 100
            slope   = abs(_ema_slope(closes, 50))

            # Volatility spike check
            if len(klines) >= 50:
                atrs = [
                    _atr(highs[:i], lows[:i], closes[:i])
                    for i in range(30, len(klines), 5)
                ]
                import statistics
                avg_atr = statistics.mean(atrs) if atrs else atr
                vol_spike = atr > avg_atr * VOL_SPIKE_MULT
            else:
                vol_spike = False

            # Volume confirmation
            vol_avg    = sum(vols[-10:]) / 10
            vol_current = vols[-1]
            vol_ratio  = vol_current / vol_avg if vol_avg > 0 else 1.0

            # Decision tree
            if vol_spike:
                result = {
                    "strategy": "Pause",
                    "reason":   f"Volatilidad extrema — ATR {atr_pct:.2f}%",
                    "regime":   "volatile",
                    "slope":    round(slope, 5),
                    "atr_pct":  round(atr_pct, 3),
                    "vol_ratio": round(vol_ratio, 2),
                }
            elif slope > SLOPE_TREND_THR:
                result = {
                    "strategy": "Scalping",
                    "reason":   f"Tendencia detectada — slope {slope:.5f} | ATR {atr_pct:.2f}%",
                    "regime":   "trending",
                    "slope":    round(slope, 5),
                    "atr_pct":  round(atr_pct, 3),
                    "vol_ratio": round(vol_ratio, 2),
                }
            elif slope < SLOPE_LATERAL_THR:
                result = {
                    "strategy": "Grid",
                    "reason":   f"Mercado lateral — slope {slope:.5f} | ATR {atr_pct:.2f}%",
                    "regime":   "lateral",
                    "slope":    round(slope, 5),
                    "atr_pct":  round(atr_pct, 3),
                    "vol_ratio": round(vol_ratio, 2),
                }
            else:
                # Zona gris — mantener estrategia anterior o Scalping por defecto
                prev = self._cache.get("strategy", "Scalping")
                result = {
                    "strategy": prev,
                    "reason":   f"Zona indefinida — manteniendo {prev} | slope {slope:.5f}",
                    "regime":   "unclear",
                    "slope":    round(slope, 5),
                    "atr_pct":  round(atr_pct, 3),
                    "vol_ratio": round(vol_ratio, 2),
                }

            result["ts"] = now
            self._cache  = result
            logger.info(f"Orchestrator: {result['strategy']} — {result['reason']}")
            return result

        except Exception as e:
            logger.error(f"Orchestrator error: {e}")
            return self._fallback(str(e))

    def _fallback(self, reason: str) -> dict:
        return {
            "strategy": "Scalping",
            "reason":   f"Fallback: {reason}",
            "regime":   "unknown",
            "slope":    0.0,
            "atr_pct":  0.0,
            "vol_ratio": 1.0,
            "ts":       time.time(),
        }
