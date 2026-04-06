import logging
from src.exchanges.binance_client import BinanceClient

logger = logging.getLogger(__name__)


def analyze_symbol(symbol, client):
    """
    Analiza un símbolo y genera una señal de trading.
    Usa RSI simplificado y momentum.
    """
    klines = client.get_klines(symbol, interval="1h", limit=24)
    if len(klines) < 14:
        return None

    closes = [k["close"] for k in klines]

    # RSI simplificado
    rsi = calculate_rsi(closes)

    # Momentum (cambio último período)
    momentum = (closes[-1] - closes[-6]) / closes[-6] * 100

    # Volumen relativo
    volumes = [k["volume"] for k in klines]
    avg_volume = sum(volumes[:-1]) / len(volumes[:-1])
    current_volume = volumes[-1]
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1

    # Señal
    signal = None
    reason = ""

    if rsi < 30 and momentum > -5 and volume_ratio > 1.5:
        signal = "BUY"
        reason = f"RSI oversold ({rsi:.1f}) + volumen alto ({volume_ratio:.1f}x)"
    elif rsi > 70 and momentum < 5 and volume_ratio > 1.5:
        signal = "SELL"
        reason = f"RSI overbought ({rsi:.1f}) + volumen alto ({volume_ratio:.1f}x)"

    return {
        "symbol": symbol,
        "price": closes[-1],
        "rsi": rsi,
        "momentum": momentum,
        "volume_ratio": volume_ratio,
        "signal": signal,
        "reason": reason
    }


def calculate_rsi(closes, period=14):
    """Calcula RSI."""
    if len(closes) < period + 1:
        return 50

    gains = []
    losses = []

    for i in range(1, period + 1):
        diff = closes[i] - closes[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


def scan_binance_opportunities(top_n=20):
    """
    Escanea Binance buscando oportunidades con RSI + momentum.
    """
    client = BinanceClient()

    # Top pares por volumen (más líquidos)
    tickers = client.client.get_ticker()
    usdt_pairs = [
        t for t in tickers
        if t["symbol"].endswith("USDT")
        and float(t["quoteVolume"]) > 10000000
        and float(t["lastPrice"]) > 0.001
    ]

    usdt_pairs.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    top_pairs = [t["symbol"] for t in usdt_pairs[:top_n]]

    print(f"\n🔍 Escaneando {len(top_pairs)} pares de Binance...")
    opportunities = []

    for symbol in top_pairs:
        try:
            analysis = analyze_symbol(symbol, client)
            if analysis and analysis["signal"]:
                opportunities.append(analysis)
                print(f"   ✅ {symbol}: {analysis['signal']} — {analysis['reason']}")
        except Exception as e:
            continue

    if not opportunities:
        print("   Sin oportunidades detectadas")

    return opportunities