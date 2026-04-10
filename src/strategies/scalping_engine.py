"""
scalping_engine.py — fixed RSI/MACD + order book imbalance + microstructure
"""
import logging
logger = logging.getLogger(__name__)

FEE_RT = 0.002          # 0.1% × 2 round-trip
MIN_ATR_PCT = 0.15      # must exceed fees to have edge
SIGNAL_THRESHOLD = 70   # raised from 55
MIN_EDGE_GAP = 20       # long_score - short_score minimum


# ── INDICATORS (correct implementations) ─────────────────────────────────────

def calculate_rsi(closes: list, period: int = 14) -> float:
    """Wilder RSI — uses only last period+1 bars, proper smoothing."""
    if len(closes) < period + 1:
        return 50.0
    # Use only relevant window
    window = closes[-(period + 1):]
    gains, losses = [], []
    for i in range(1, len(window)):
        d = window[i] - window[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_ema(values: list, period: int) -> float:
    """Standard EMA — O(n), single pass."""
    if len(values) < period:
        return values[-1] if values else 0.0
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def calculate_macd(closes: list):
    """
    Correct MACD: signal line = EMA(9) of MACD values, not of raw closes.
    Returns (macd_val, signal_val, histogram)
    """
    if len(closes) < 35:
        return 0.0, 0.0, 0.0

    # Build full MACD line — O(n) each EMA
    macd_line = []
    for i in range(26, len(closes) + 1):
        e12 = calculate_ema(closes[:i], 12)
        e26 = calculate_ema(closes[:i], 26)
        macd_line.append(e12 - e26)

    if len(macd_line) < 9:
        return 0.0, 0.0, 0.0

    signal = calculate_ema(macd_line, 9)
    macd_val = macd_line[-1]
    histogram = macd_val - signal
    return round(macd_val, 6), round(signal, 6), round(histogram, 6)


def calculate_bollinger(closes: list, period: int = 20, std_mult: float = 2.0):
    if len(closes) < period:
        c = closes[-1]
        return c, c, c
    window = closes[-period:]
    sma = sum(window) / period
    variance = sum((c - sma) ** 2 for c in window) / period
    std = variance ** 0.5
    return round(sma + std_mult * std, 6), round(sma, 6), round(sma - std_mult * std, 6)


def calculate_atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    return sum(trs[-period:]) / min(period, len(trs))


def calculate_vwap(highs, lows, closes, volumes) -> float:
    """Session VWAP — volume-weighted average price."""
    tpv = sum((h + l + c) / 3 * v for h, l, c, v in zip(highs, lows, closes, volumes))
    tv  = sum(volumes)
    return tpv / tv if tv > 0 else closes[-1]


# ── ORDER BOOK IMBALANCE ──────────────────────────────────────────────────────

def get_order_book_imbalance(client, symbol: str, depth: int = 10) -> dict:
    """
    Fetch L2 order book and compute bid/ask imbalance.
    Imbalance > 0.6 = buy pressure, < 0.4 = sell pressure.
    """
    try:
        book = client.client.get_order_book(symbol=symbol, limit=depth)
        bid_vol = sum(float(b[1]) for b in book["bids"][:depth])
        ask_vol = sum(float(a[1]) for a in book["asks"][:depth])
        total   = bid_vol + ask_vol
        if total == 0:
            return {"imbalance": 0.5, "bid_vol": 0, "ask_vol": 0, "spread_pct": 0}
        best_bid = float(book["bids"][0][0])
        best_ask = float(book["asks"][0][0])
        spread_pct = (best_ask - best_bid) / best_bid * 100
        return {
            "imbalance":  round(bid_vol / total, 4),   # 0-1
            "bid_vol":    round(bid_vol, 2),
            "ask_vol":    round(ask_vol, 2),
            "spread_pct": round(spread_pct, 4),
            "best_bid":   best_bid,
            "best_ask":   best_ask,
        }
    except Exception as e:
        logger.debug(f"Order book error {symbol}: {e}")
        return {"imbalance": 0.5, "bid_vol": 0, "ask_vol": 0, "spread_pct": 0}


# ── LIQUIDITY ZONES ───────────────────────────────────────────────────────────

def find_liquidity_zones(highs: list, lows: list, closes: list, lookback: int = 50):
    """
    Identify swing highs/lows as liquidity zones.
    These are where stop clusters and limit orders accumulate.
    """
    if len(closes) < lookback:
        lookback = len(closes)

    h = highs[-lookback:]
    l = lows[-lookback:]
    c = closes[-lookback:]

    # Swing highs: local maxima
    swing_highs = []
    swing_lows  = []
    for i in range(2, len(h) - 2):
        if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
            swing_highs.append(h[i])
        if l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]:
            swing_lows.append(l[i])

    current = c[-1]

    # Nearest resistance above
    resistances = sorted([z for z in swing_highs if z > current])
    supports    = sorted([z for z in swing_lows  if z < current], reverse=True)

    nearest_resistance = resistances[0] if resistances else current * 1.02
    nearest_support    = supports[0]    if supports    else current * 0.98

    dist_to_resistance = (nearest_resistance - current) / current * 100
    dist_to_support    = (current - nearest_support)    / current * 100

    return {
        "nearest_resistance":    round(nearest_resistance, 6),
        "nearest_support":       round(nearest_support, 6),
        "dist_to_resistance_pct": round(dist_to_resistance, 3),
        "dist_to_support_pct":   round(dist_to_support, 3),
        # Don't enter long if resistance is within 0.3% — no room to run
        "room_to_run_long":  dist_to_resistance > 0.5,
        "room_to_run_short": dist_to_support    > 0.5,
    }


# ── MICROSTRUCTURE ────────────────────────────────────────────────────────────

def microstructure_score(volumes: list, closes: list, highs: list, lows: list) -> dict:
    """
    Assess market microstructure quality for scalping:
    - Volume trend
    - Candle body ratio (real body vs wick — high wick = indecision)
    - Consecutive closes in same direction (momentum confirmation)
    """
    if len(closes) < 5:
        return {"score": 0, "volume_trend": 0, "body_ratio": 0, "momentum_consec": 0}

    # Volume trend: last 3 vs prior 3
    vol_recent = sum(volumes[-3:]) / 3
    vol_prior  = sum(volumes[-6:-3]) / 3
    volume_trend = (vol_recent - vol_prior) / (vol_prior + 1e-9)

    # Body ratio: avg |close-open| / (high-low) for last 5 candles
    # High body ratio = decisive candles, low = doji/indecision
    # We approximate open as prior close
    body_ratios = []
    for i in range(-5, 0):
        candle_range = highs[i] - lows[i]
        if candle_range > 0:
            body = abs(closes[i] - closes[i - 1])
            body_ratios.append(body / candle_range)
    body_ratio = sum(body_ratios) / len(body_ratios) if body_ratios else 0

    # Consecutive closes in same direction (last 3)
    dirs = [1 if closes[i] > closes[i-1] else -1 for i in range(-3, 0)]
    momentum_consec = abs(sum(dirs))  # 0-3, 3 = all same direction

    score = 0
    if volume_trend > 0.2:    score += 25
    if body_ratio > 0.5:      score += 35
    if momentum_consec >= 2:  score += 40

    return {
        "score":           score,
        "volume_trend":    round(volume_trend, 3),
        "body_ratio":      round(body_ratio, 3),
        "momentum_consec": momentum_consec,
        "quality":         "high" if score >= 60 else "medium" if score >= 35 else "low"
    }


# ── HTF TREND FILTER ─────────────────────────────────────────────────────────

def get_htf_trend(client, symbol: str) -> str:
    """
    1H trend via EMA20. Only trade in direction of HTF trend.
    Returns: 'up' | 'down' | 'neutral'
    """
    try:
        klines = client.get_klines(symbol, interval="1h", limit=25)
        if len(klines) < 20:
            return "neutral"
        closes = [k["close"] for k in klines]
        ema20  = calculate_ema(closes, 20)
        current = closes[-1]
        if current > ema20 * 1.002:
            return "up"
        if current < ema20 * 0.998:
            return "down"
        return "neutral"
    except Exception as e:
        logger.debug(f"HTF trend error {symbol}: {e}")
        return "neutral"


# ── MAIN SIGNAL ENGINE ────────────────────────────────────────────────────────

def get_signal_strength(klines: list, ob: dict = None, lz: dict = None, ms: dict = None) -> dict:
    """
    Multi-factor signal engine with correct indicators + microstructure.
    ob = order book imbalance dict
    lz = liquidity zones dict
    ms = microstructure score dict
    """
    if len(klines) < 35:
        return {"direction": "none", "strength": 0, "reasons": ["insufficient data"],
                "rsi": 50, "macd": 0, "momentum": 0, "atr_pct": 0}

    closes  = [k["close"]  for k in klines]
    highs   = [k["high"]   for k in klines]
    lows    = [k["low"]    for k in klines]
    volumes = [k["volume"] for k in klines]
    current = closes[-1]

    # Core indicators
    rsi                  = calculate_rsi(closes)
    macd_val, sig_val, hist = calculate_macd(closes)
    bb_upper, bb_mid, bb_lower = calculate_bollinger(closes)
    atr                  = calculate_atr(highs, lows, closes)
    atr_pct              = atr / current * 100
    momentum             = (current - closes[-5]) / closes[-5] * 100

    # Volume
    vol_avg     = sum(volumes[-10:]) / 10
    vol_spike   = volumes[-1] > vol_avg * 1.5
    vol_ratio   = volumes[-1] / vol_avg

    # VWAP
    vwap = calculate_vwap(highs, lows, closes, volumes)
    above_vwap = current > vwap

    # Volatility guard
    if atr_pct < MIN_ATR_PCT:
        return {"direction": "none", "strength": 0, "reasons": [f"ATR {atr_pct:.3f}% < min {MIN_ATR_PCT}%"],
                "rsi": rsi, "macd": macd_val, "momentum": momentum, "atr_pct": atr_pct}

    long_score  = 0
    short_score = 0
    reasons     = []

    # ── RSI ──
    if rsi < 32:
        long_score += 28; reasons.append(f"RSI oversold {rsi:.1f}")
    elif rsi < 44:
        long_score += 12; reasons.append(f"RSI low {rsi:.1f}")
    elif rsi > 68:
        short_score += 28; reasons.append(f"RSI overbought {rsi:.1f}")
    elif rsi > 56:
        short_score += 12; reasons.append(f"RSI high {rsi:.1f}")

    # ── MACD (histogram direction matters most) ──
    if hist > 0 and macd_val > sig_val:
        long_score  += 22; reasons.append("MACD bullish + hist positive")
    elif hist < 0 and macd_val < sig_val:
        short_score += 22; reasons.append("MACD bearish + hist negative")
    elif macd_val > sig_val:
        long_score  += 10; reasons.append("MACD cross up")
    elif macd_val < sig_val:
        short_score += 10; reasons.append("MACD cross down")

    # ── Bollinger Bands ──
    if current <= bb_lower * 1.001:
        long_score  += 18; reasons.append("Price at BB lower")
    elif current >= bb_upper * 0.999:
        short_score += 18; reasons.append("Price at BB upper")

    # ── VWAP ──
    if above_vwap:
        long_score  += 8; reasons.append("Above VWAP")
    else:
        short_score += 8; reasons.append("Below VWAP")

    # ── Momentum ──
    if momentum > 0.35:
        long_score  += 14; reasons.append(f"Momentum +{momentum:.2f}%")
    elif momentum < -0.35:
        short_score += 14; reasons.append(f"Momentum {momentum:.2f}%")

    # ── Volume ──
    if vol_spike:
        bonus = 18
        if current > closes[-2]:
            long_score  += bonus; reasons.append(f"Vol spike bullish {vol_ratio:.1f}x")
        else:
            short_score += bonus; reasons.append(f"Vol spike bearish {vol_ratio:.1f}x")

    # ── Order book imbalance (if available) ──
    if ob:
        imb = ob.get("imbalance", 0.5)
        spread = ob.get("spread_pct", 0)
        if spread > atr_pct * 0.5:
            # Spread too wide relative to ATR — not worth scalping
            return {"direction": "none", "strength": 0,
                    "reasons": [f"Spread {spread:.3f}% too wide vs ATR {atr_pct:.3f}%"],
                    "rsi": rsi, "macd": macd_val, "momentum": momentum, "atr_pct": atr_pct}
        if imb > 0.65:
            long_score  += 15; reasons.append(f"OB bid imbalance {imb:.2f}")
        elif imb < 0.35:
            short_score += 15; reasons.append(f"OB ask imbalance {imb:.2f}")

    # ── Liquidity zones (if available) ──
    if lz:
        if long_score > short_score and not lz.get("room_to_run_long", True):
            return {"direction": "none", "strength": 0,
                    "reasons": [f"Resistance at {lz['nearest_resistance']:.4f} — no room"],
                    "rsi": rsi, "macd": macd_val, "momentum": momentum, "atr_pct": atr_pct}
        if short_score > long_score and not lz.get("room_to_run_short", True):
            return {"direction": "none", "strength": 0,
                    "reasons": [f"Support at {lz['nearest_support']:.4f} — no room"],
                    "rsi": rsi, "macd": macd_val, "momentum": momentum, "atr_pct": atr_pct}

    # ── Microstructure quality filter ──
    if ms and ms.get("quality") == "low":
        return {"direction": "none", "strength": 0,
                "reasons": ["Microstructure quality low — doji/indecision candles"],
                "rsi": rsi, "macd": macd_val, "momentum": momentum, "atr_pct": atr_pct}

    # ── Direction decision ──
    edge_gap = abs(long_score - short_score)
    if edge_gap < MIN_EDGE_GAP:
        return {"direction": "none", "strength": 0,
                "reasons": [f"No clear edge (gap={edge_gap})"],
                "rsi": rsi, "macd": macd_val, "momentum": momentum, "atr_pct": atr_pct}

    if long_score >= SIGNAL_THRESHOLD and long_score > short_score:
        return {"direction": "long",  "strength": min(long_score, 100),  "reasons": reasons,
                "rsi": rsi, "macd": macd_val, "histogram": hist,
                "momentum": momentum, "atr_pct": atr_pct, "vwap": vwap}

    if short_score >= SIGNAL_THRESHOLD and short_score > long_score:
        return {"direction": "short", "strength": min(short_score, 100), "reasons": reasons,
                "rsi": rsi, "macd": macd_val, "histogram": hist,
                "momentum": momentum, "atr_pct": atr_pct, "vwap": vwap}

    return {"direction": "none", "strength": 0,
            "reasons": [f"Threshold not met (long={long_score} short={short_score})"],
            "rsi": rsi, "macd": macd_val, "momentum": momentum, "atr_pct": atr_pct}
