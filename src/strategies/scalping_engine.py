import logging

logger = logging.getLogger(__name__)


def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_ema(closes, period):
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def calculate_macd(closes):
    if len(closes) < 26:
        return 0, 0
    ema12 = calculate_ema(closes, 12)
    ema26 = calculate_ema(closes, 26)
    macd  = ema12 - ema26
    signal = calculate_ema(closes[-9:], 9) if len(closes) >= 35 else macd
    return round(macd, 4), round(signal, 4)


def calculate_bollinger(closes, period=20):
    if len(closes) < period:
        return closes[-1], closes[-1], closes[-1]
    sma   = sum(closes[-period:]) / period
    std   = (sum((c - sma)**2 for c in closes[-period:]) / period) ** 0.5
    return round(sma + 2*std, 4), round(sma, 4), round(sma - 2*std, 4)


def get_signal_strength(klines):
    if len(klines) < 30:
        return {"direction": "none", "strength": 0, "reasons": ["Sin datos suficientes"]}

    closes  = [k["close"]  for k in klines]
    volumes = [k["volume"] for k in klines]
    highs   = [k["high"]   for k in klines]
    lows    = [k["low"]    for k in klines]

    rsi            = calculate_rsi(closes)
    macd, signal   = calculate_macd(closes)
    bb_upper, bb_mid, bb_lower = calculate_bollinger(closes)
    current        = closes[-1]
    prev           = closes[-2]
    momentum       = (current - closes[-5]) / closes[-5] * 100
    vol_avg        = sum(volumes[-10:]) / 10
    vol_current    = volumes[-1]
    vol_spike      = vol_current > vol_avg * 1.5

    atr_vals = []
    for i in range(1, min(14, len(klines))):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        atr_vals.append(tr)
    atr = sum(atr_vals) / len(atr_vals) if atr_vals else 0
    atr_pct = atr / current * 100

    long_score  = 0
    short_score = 0
    reasons     = []

    if rsi < 35:
        long_score += 25
        reasons.append(f"RSI oversold {rsi:.0f}")
    elif rsi < 45:
        long_score += 10
        reasons.append(f"RSI bajo {rsi:.0f}")
    elif rsi > 65:
        short_score += 25
        reasons.append(f"RSI overbought {rsi:.0f}")
    elif rsi > 55:
        short_score += 10
        reasons.append(f"RSI alto {rsi:.0f}")

    if macd > signal and macd > 0:
        long_score += 20
        reasons.append("MACD bullish crossover")
    elif macd < signal and macd < 0:
        short_score += 20
        reasons.append("MACD bearish crossover")
    elif macd > signal:
        long_score += 10
        reasons.append("MACD positivo")
    elif macd < signal:
        short_score += 10
        reasons.append("MACD negativo")

    if current <= bb_lower * 1.001:
        long_score += 20
        reasons.append("Precio en BB inferior")
    elif current >= bb_upper * 0.999:
        short_score += 20
        reasons.append("Precio en BB superior")

    if momentum > 0.3:
        long_score += 15
        reasons.append(f"Momentum +{momentum:.2f}%")
    elif momentum < -0.3:
        short_score += 15
        reasons.append(f"Momentum {momentum:.2f}%")

    if vol_spike:
        if current > prev:
            long_score += 20
            reasons.append(f"Volume spike bullish {vol_current/vol_avg:.1f}x")
        else:
            short_score += 20
            reasons.append(f"Volume spike bearish {vol_current/vol_avg:.1f}x")

    if atr_pct < 0.05:
        return {"direction": "none", "strength": 0, "reasons": ["Volatilidad insuficiente"]}

    if long_score >= 50 and long_score > short_score * 1.3:
        return {"direction": "long", "strength": min(long_score, 100), "reasons": reasons,
                "rsi": rsi, "macd": macd, "momentum": momentum, "atr_pct": atr_pct}
    elif short_score >= 50 and short_score > long_score * 1.3:
        return {"direction": "short", "strength": min(short_score, 100), "reasons": reasons,
                "rsi": rsi, "macd": macd, "momentum": momentum, "atr_pct": atr_pct}
    else:
        return {"direction": "none", "strength": 0, "reasons": ["Sin señal clara"],
                "rsi": rsi, "macd": macd, "momentum": momentum, "atr_pct": atr_pct}