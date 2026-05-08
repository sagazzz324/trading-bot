from __future__ import annotations

from dataclasses import dataclass

from src.lab.datasets import OHLCVBar
from src.lab.strategy_family import EmaRsiTrendStrategy, TradeRecord
from src.lab.validation import PerformanceMetrics, compute_metrics


@dataclass
class BacktestRun:
    trades: list[TradeRecord]
    metrics: PerformanceMetrics


def run_backtest(
    bars: list[OHLCVBar],
    parameters: dict,
    fee_bps: float = 10.0,
    slippage_bps: float = 2.0,
) -> BacktestRun:
    strategy = EmaRsiTrendStrategy(parameters)
    trades = strategy.backtest(bars, fee_bps=fee_bps, slippage_bps=slippage_bps)
    trade_returns = [trade.net_return for trade in trades]
    metrics = compute_metrics(trade_returns)
    return BacktestRun(trades=trades, metrics=metrics)

