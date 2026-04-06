import json
import logging
import time
import requests
from tqdm import tqdm
from src.core.probability_engine import estimate_probability, calculate_ev
from src.core.risk_engine import kelly_position_size

logger = logging.getLogger(__name__)

POLYMARKET_API = "https://gamma-api.polymarket.com"


def get_historical_markets(limit=200):
    """
    Obtiene mercados cerrados de Polymarket.
    """
    try:
        params = {
            "limit": limit,
            "closed": "true",
            "order": "volume",
            "ascending": "false"
        }
        response = requests.get(f"{POLYMARKET_API}/markets", params=params)
        response.raise_for_status()
        markets = response.json()
        logger.info(f"Obtenidos {len(markets)} mercados históricos")
        return markets
    except Exception as e:
        logger.error(f"Error obteniendo históricos: {e}")
        return []


def parse_winner(market):
    """
    Determina quién ganó un mercado resuelto.
    Retorna True si ganó Yes, False si ganó No, None si no aplica.
    """
    try:
        outcomes = market.get("outcomes", "[]")
        prices = market.get("outcomePrices", "[]")

        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(prices, str):
            prices = json.loads(prices)

        # Solo mercados Yes/No
        if "Yes" not in outcomes or "No" not in outcomes:
            return None

        idx_yes = outcomes.index("Yes")
        price_yes = float(prices[idx_yes])

        # El ganador tiene precio 1.0
        if price_yes >= 0.99:
            return True
        elif price_yes <= 0.01:
            return False
        return None

    except:
        return None


def run_backtest(n_markets=50, bankroll=1000, min_ev=0.05, delay=1):
    """
    Corre el backtesting sobre mercados históricos.
    """
    print("\n" + "="*60)
    print("📊 BACKTESTING — Mercados históricos de Polymarket")
    print("="*60)

    markets = get_historical_markets(limit=500)

    if not markets:
        print("❌ No se pudieron obtener mercados históricos")
        return

    # Filtrar mercados válidos
    valid = []
    for m in markets:
        # Solo cerrados
        if not m.get("closed", False):
            continue

        winner = parse_winner(m)
        if winner is None:
            continue

        try:
            outcomes = json.loads(m.get("outcomes", "[]"))
            prices = json.loads(m.get("outcomePrices", "[]"))
            if "Yes" not in outcomes:
                continue
            idx = outcomes.index("Yes")
            price = float(prices[idx])

            # Precio al momento de cierre no es útil,
            # usamos el precio resuelto para reconstruir
            # probabilidad implícita pre-cierre
            volume = float(m.get("volume", 0))
            if volume < 1000:
                continue

        except:
            continue

        valid.append(m)
        if len(valid) >= n_markets:
            break

    print(f"   Mercados válidos para testear: {len(valid)}\n")

    if not valid:
        print("❌ No hay mercados válidos")
        print("   Tip: La API de Polymarket tiene pocos mercados Yes/No cerrados disponibles")
        print("   Probá aumentar el límite o usar datos locales")
        return

    # Simulación
    current_bankroll = bankroll
    trades = []
    skipped = 0

    for market in tqdm(valid, desc="Evaluando"):
        question = market.get("question", "")
        outcomes = json.loads(market.get("outcomes", "[]"))
        prices = json.loads(market.get("outcomePrices", "[]"))
        idx = outcomes.index("Yes")
        final_price = float(prices[idx])
        winner = parse_winner(market)

        # Simular precio de mercado antes del cierre
        # Si ganó Yes (precio=1), el mercado estaba en algún punto entre 0.1-0.9
        # Usamos el precio final como referencia para reconstruir
        if winner:
            market_prob = max(0.1, min(0.9, final_price * 0.7 + 0.1))
        else:
            market_prob = max(0.1, min(0.9, final_price * 0.3 + 0.1))

        # Estimar con Claude
        result = estimate_probability(question, market_prob)
        if not result:
            skipped += 1
            continue

        true_prob = result["probability"]
        confidence = result["confidence"]
        ev = calculate_ev(true_prob, market_prob)

        if ev < min_ev or confidence == "low":
            skipped += 1
            time.sleep(delay)
            continue

        size = kelly_position_size(true_prob, market_prob, current_bankroll)
        if size <= 0:
            skipped += 1
            continue

        if winner:
            payout = size / market_prob
            pnl = payout - size
        else:
            pnl = -size

        current_bankroll += pnl

        trades.append({
            "question": question[:60],
            "market_prob": market_prob,
            "true_prob": true_prob,
            "ev": ev,
            "size": size,
            "winner": winner,
            "pnl": round(pnl, 2),
            "bankroll": round(current_bankroll, 2)
        })

        time.sleep(delay)

    print_backtest_report(trades, bankroll, current_bankroll, skipped)
    save_backtest_results(trades)
    return trades


def print_backtest_report(trades, initial_bankroll, final_bankroll, skipped):
    if not trades:
        print("\n❌ Sin trades en el backtest")
        return

    wins = [t for t in trades if t["winner"]]
    total_pnl = sum(t["pnl"] for t in trades)
    win_rate = len(wins) / len(trades)
    avg_ev = sum(t["ev"] for t in trades) / len(trades)

    pnls = [t["pnl"] for t in trades]
    avg_pnl = total_pnl / len(trades)
    if len(pnls) > 1:
        import statistics
        std_pnl = statistics.stdev(pnls)
        sharpe = avg_pnl / std_pnl if std_pnl > 0 else 0
    else:
        sharpe = 0

    peak = initial_bankroll
    max_dd = 0
    running = initial_bankroll
    for t in trades:
        running += t["pnl"]
        if running > peak:
            peak = running
        dd = (peak - running) / peak
        if dd > max_dd:
            max_dd = dd

    print("\n" + "="*60)
    print("📈 RESULTADOS DEL BACKTESTING")
    print("="*60)
    print(f"   Trades ejecutados:  {len(trades)}")
    print(f"   Trades salteados:   {skipped}")
    print(f"   Win rate:           {win_rate:.1%}")
    print(f"   Bankroll inicial:   ${initial_bankroll:,.2f}")
    print(f"   Bankroll final:     ${final_bankroll:,.2f}")
    print(f"   PnL total:          ${total_pnl:+,.2f}")
    print(f"   ROI:                {((final_bankroll/initial_bankroll)-1)*100:+.1f}%")
    print(f"   EV promedio:        {avg_ev:.4f}")
    print(f"   Sharpe ratio:       {sharpe:.2f}")
    print(f"   Max drawdown:       {max_dd:.1%}")
    print("="*60)

    print("\n📋 Últimos 10 trades:")
    print(f"{'Mercado':<45} {'EV':>6} {'Resultado':>10} {'PnL':>8}")
    print("-"*75)
    for t in trades[-10:]:
        resultado = "WIN" if t["winner"] else "LOSS"
        print(f"{t['question']:<45} {t['ev']:>6.3f} {resultado:>10} ${t['pnl']:>7.2f}")


def save_backtest_results(trades):
    from pathlib import Path
    Path("logs").mkdir(exist_ok=True)
    with open("logs/backtest_results.json", "w") as f:
        json.dump(trades, f, indent=2)
    print(f"\n💾 Resultados guardados en logs/backtest_results.json")