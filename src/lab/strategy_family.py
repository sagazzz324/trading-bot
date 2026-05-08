from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.lab.datasets import OHLCVBar


def ema(values: np.ndarray, period: int) -> np.ndarray:
    period = max(2, int(period))
    result = np.zeros_like(values, dtype=float)
    alpha = 2.0 / (period + 1.0)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1.0 - alpha) * result[i - 1]
    return result


def rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    period = max(2, int(period))
    out = np.full_like(values, 50.0, dtype=float)
    if len(values) <= period:
        return out
    deltas = np.diff(values)
    gains = np.maximum(deltas, 0.0)
    losses = np.maximum(-deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, len(values)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i - 1]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i - 1]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


@dataclass
class TradeRecord:
    entry_index: int
    exit_index: int
    direction: int
    entry_price: float
    exit_price: float
    net_return: float
    reason: str


class EmaRsiTrendStrategy:
    family_name = "scalper"

    def __init__(self, parameters: dict):
        self.parameters = parameters

    def backtest(self, bars: list[OHLCVBar], fee_bps: float = 10.0, slippage_bps: float = 2.0) -> list[TradeRecord]:
        if len(bars) < 100:
            return []

        closes = np.asarray([bar.close for bar in bars], dtype=float)
        highs = np.asarray([bar.high for bar in bars], dtype=float)
        lows = np.asarray([bar.low for bar in bars], dtype=float)
        opens = np.asarray([bar.open for bar in bars], dtype=float)

        fast = ema(closes, int(self.parameters["ema_fast"]))
        slow = ema(closes, int(self.parameters["ema_slow"]))
        momentum_rsi = rsi(closes, int(self.parameters["rsi_period"]))
        fee = fee_bps / 10000.0
        slippage = slippage_bps / 10000.0
        hold_bars = max(1, int(self.parameters["hold_bars"]))
        stop_pct = float(self.parameters["stop_pct"])
        take_pct = float(self.parameters["take_pct"])
        min_trend = float(self.parameters["trend_threshold"])

        trades: list[TradeRecord] = []
        position = 0
        entry_idx = -1
        entry_price = 0.0

        for i in range(30, len(bars) - 1):
            trend_gap = (fast[i] - slow[i]) / slow[i] if slow[i] else 0.0
            long_signal = (
                trend_gap > min_trend
                and momentum_rsi[i] < float(self.parameters["rsi_long_max"])
                and closes[i] > fast[i]
            )
            short_signal = (
                trend_gap < -min_trend
                and momentum_rsi[i] > float(self.parameters["rsi_short_min"])
                and closes[i] < fast[i]
            )

            if position == 0:
                if long_signal:
                    position = 1
                    entry_idx = i + 1
                    entry_price = opens[i + 1] * (1.0 + slippage)
                elif short_signal:
                    position = -1
                    entry_idx = i + 1
                    entry_price = opens[i + 1] * (1.0 - slippage)
                continue

            bars_held = i - entry_idx
            stop_price = entry_price * (1.0 - stop_pct) if position == 1 else entry_price * (1.0 + stop_pct)
            take_price = entry_price * (1.0 + take_pct) if position == 1 else entry_price * (1.0 - take_pct)

            exit_price = None
            reason = None
            if position == 1:
                if lows[i] <= stop_price:
                    exit_price = stop_price * (1.0 - slippage)
                    reason = "sl"
                elif highs[i] >= take_price:
                    exit_price = take_price * (1.0 - slippage)
                    reason = "tp"
            else:
                if highs[i] >= stop_price:
                    exit_price = stop_price * (1.0 + slippage)
                    reason = "sl"
                elif lows[i] <= take_price:
                    exit_price = take_price * (1.0 + slippage)
                    reason = "tp"

            if exit_price is None and bars_held >= hold_bars:
                exit_price = closes[i] * (1.0 - slippage if position == 1 else 1.0 + slippage)
                reason = "time"

            if exit_price is None:
                continue

            gross_return = ((exit_price - entry_price) / entry_price) * position
            net_return = gross_return - fee
            trades.append(
                TradeRecord(
                    entry_index=entry_idx,
                    exit_index=i,
                    direction=position,
                    entry_price=round(entry_price, 6),
                    exit_price=round(exit_price, 6),
                    net_return=round(float(net_return), 6),
                    reason=reason,
                )
            )
            position = 0
            entry_idx = -1
            entry_price = 0.0

        return trades


def default_search_dimensions() -> list[dict]:
    return [
        {"name": "ema_fast", "low": 5, "high": 25, "integer": True},
        {"name": "ema_slow", "low": 20, "high": 120, "integer": True},
        {"name": "rsi_period", "low": 5, "high": 20, "integer": True},
        {"name": "rsi_long_max", "low": 35, "high": 60, "integer": False},
        {"name": "rsi_short_min", "low": 40, "high": 70, "integer": False},
        {"name": "trend_threshold", "low": 0.0001, "high": 0.01, "integer": False},
        {"name": "stop_pct", "low": 0.002, "high": 0.03, "integer": False},
        {"name": "take_pct", "low": 0.003, "high": 0.06, "integer": False},
        {"name": "hold_bars", "low": 2, "high": 48, "integer": True},
        {"name": "max_positions", "low": 1, "high": 5, "integer": True},
        {"name": "risk_per_trade", "low": 0.0025, "high": 0.02, "integer": False},
        {"name": "cooldown_sec", "low": 15, "high": 180, "integer": True},
    ]

