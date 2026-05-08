from __future__ import annotations

import argparse
import json

from src.lab.pipeline import run_research_job


def main():
    parser = argparse.ArgumentParser(description="Runner del laboratorio interno")
    parser.add_argument("--dataset", required=True, help="CSV OHLCV con timestamp,open,high,low,close,volume")
    parser.add_argument("--market", required=True, help="Ej: BTCUSDT, SPY")
    parser.add_argument("--regime", default="all")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    result = run_research_job(
        dataset_path=args.dataset,
        market=args.market,
        regime=args.regime,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
