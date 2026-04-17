# src/core/btc_optimizer.py

import json
from pathlib import Path

POLY_LOG = Path("logs/paper_trades.json")

def analyze_and_tune(min_trades=50):
    """
    Analiza los últimos trades y sugiere mejores parámetros.
    Retorna dict con TAU y EPSILON optimizados.
    """
    if not POLY_LOG.exists():
        return None

    with open(POLY_LOG) as f:
        data = json.load(f)

    resolved = [t for t in data.get("trades", []) if t.get("status") == "resolved"]
    if len(resolved) < min_trades:
        return None

    # Agrupar por persist y gap en el momento de entrada
    # (guardados como true_prob y ev en el trade)
    wins_by_gap  = {}
    total_by_gap = {}

    for t in resolved[-200:]:  # últimos 200
        gap = round(t.get("ev", 0), 2)
        win = 1 if t.get("result") == "win" else 0

        if gap not in wins_by_gap:
            wins_by_gap[gap]  = 0
            total_by_gap[gap] = 0

        wins_by_gap[gap]  += win
        total_by_gap[gap] += 1

    # Encontrar el gap mínimo que da win rate > 52%
    best_eps = 0.03
    for gap, total in sorted(total_by_gap.items()):
        if total >= 10:
            wr = wins_by_gap[gap] / total
            if wr >= 0.52:
                best_eps = gap
                break

    # Win rate general
    total    = len(resolved[-200:])
    wins     = sum(1 for t in resolved[-200:] if t.get("result") == "win")
    win_rate = wins / total if total > 0 else 0.5

    # Ajustar TAU según win rate
    if win_rate > 0.55:
        best_tau = 0.17   # está funcionando, mantener
    elif win_rate > 0.50:
        best_tau = 0.20   # subir un poco el filtro
    else:
        best_tau = 0.25   # mercado difícil, ser más selectivo

    return {
        "tau":      best_tau,
        "epsilon":  best_eps,
        "win_rate": round(win_rate, 3),
        "trades":   total,
    }