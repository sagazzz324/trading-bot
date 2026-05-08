from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OHLCVBar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: str


def load_ohlcv_csv(path: str | Path, symbol: str | None = None) -> list[OHLCVBar]:
    csv_path = Path(path)
    rows: list[OHLCVBar] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_symbol = symbol or row.get("symbol") or csv_path.stem.upper()
            rows.append(
                OHLCVBar(
                    timestamp=str(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0) or 0),
                    symbol=row_symbol,
                )
            )
    return rows


def split_out_of_sample(data: list[OHLCVBar], out_of_sample_pct: float) -> tuple[list[OHLCVBar], list[OHLCVBar]]:
    if not data:
        return [], []
    split_idx = max(1, int(len(data) * (1.0 - out_of_sample_pct)))
    split_idx = min(split_idx, len(data) - 1)
    return data[:split_idx], data[split_idx:]

