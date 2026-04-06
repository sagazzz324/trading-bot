from src.exchanges.binance_client import BinanceClient

b = BinanceClient()

print(f"BTC: ${b.get_price('BTCUSDT'):,.2f}")
print("\nTop 5 movers:")
movers = b.get_top_movers(5)
for m in movers:
    print(f"  {m['symbol']}: {m['change_pct']:+.1f}% | Vol: ${m['volume']:,.0f}")