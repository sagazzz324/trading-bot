from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np


@dataclass
class PerformanceMetrics:
    total_return_pct: float
    sharpe: float
    max_drawdown_pct: float
    profit_factor: float
    win_rate_pct: float
    trades: int

    def to_dict(self) -> dict:
        return asdict(self)


def equity_curve_from_returns(trade_returns: Iterable[float], initial_equity: float = 1.0) -> np.ndarray:
    equity = [float(initial_equity)]
    for r in trade_returns:
        equity.append(equity[-1] * (1.0 + float(r)))
    return np.asarray(equity, dtype=float)


def compute_metrics(trade_returns: Iterable[float]) -> PerformanceMetrics:
    values = np.asarray(list(trade_returns), dtype=float)
    if values.size == 0:
        return PerformanceMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0)

    equity = equity_curve_from_returns(values)
    running_max = np.maximum.accumulate(equity)
    drawdowns = np.where(running_max > 0, (running_max - equity) / running_max, 0.0)
    max_dd = float(np.max(drawdowns)) * 100.0

    mean_ret = float(np.mean(values))
    std_ret = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    sharpe = 0.0 if std_ret == 0 else (mean_ret / std_ret) * math.sqrt(252)

    positive = values[values > 0]
    negative = values[values < 0]
    gross_profit = float(np.sum(positive)) if positive.size else 0.0
    gross_loss = abs(float(np.sum(negative))) if negative.size else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
    win_rate = float(np.mean(values > 0)) * 100.0
    total_return = float(equity[-1] / equity[0] - 1.0) * 100.0
    return PerformanceMetrics(
        total_return_pct=round(total_return, 4),
        sharpe=round(sharpe, 4),
        max_drawdown_pct=round(max_dd, 4),
        profit_factor=round(profit_factor, 4) if math.isfinite(profit_factor) else profit_factor,
        win_rate_pct=round(win_rate, 2),
        trades=int(values.size),
    )


def walk_forward_splits(length: int, train_size: int, test_size: int, step_size: int) -> list[tuple[slice, slice]]:
    splits = []
    start = 0
    while start + train_size + test_size <= length:
        train = slice(start, start + train_size)
        test = slice(start + train_size, start + train_size + test_size)
        splits.append((train, test))
        start += step_size
    return splits


def monte_carlo_permutation(trade_returns: Iterable[float], iterations: int = 500, seed: int = 7) -> dict:
    values = np.asarray(list(trade_returns), dtype=float)
    if values.size == 0:
        return {"iterations": iterations, "median_sharpe": 0.0, "pass_rate": 0.0}

    rng = np.random.default_rng(seed)
    sharpes = []
    max_dds = []
    for _ in range(iterations):
        sample = rng.permutation(values)
        metrics = compute_metrics(sample)
        sharpes.append(metrics.sharpe)
        max_dds.append(metrics.max_drawdown_pct)
    return {
        "iterations": iterations,
        "median_sharpe": round(float(np.median(sharpes)), 4),
        "p10_sharpe": round(float(np.percentile(sharpes, 10)), 4),
        "p90_drawdown_pct": round(float(np.percentile(max_dds, 90)), 4),
        "pass_rate": round(float(np.mean(np.asarray(sharpes) > 0.0)), 4),
    }


def parameter_permutations(parameters: dict[str, float], pct: float = 0.1) -> list[dict[str, float]]:
    variants = [dict(parameters)]
    for key, value in parameters.items():
        if not isinstance(value, (int, float)):
            continue
        low = dict(parameters)
        high = dict(parameters)
        low[key] = value * (1.0 - pct)
        high[key] = value * (1.0 + pct)
        variants.extend([low, high])
    return variants

