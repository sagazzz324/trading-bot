from src.core.backtester import run_backtest

if __name__ == "__main__":
    run_backtest(
        n_markets=30,
        bankroll=1000,
        min_ev=0.05,
        delay=1
    )