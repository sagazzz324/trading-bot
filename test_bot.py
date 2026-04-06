from src.core.market_data import get_markets, format_market

markets = get_markets(limit=10)
print(f"Total mercados: {len(markets)}\n")

for market in markets:
    m = format_market(market)
    print(f"Pregunta: {m['pregunta'][:60]}")
    print(f"  Volumen: ${m['volumen']:,.0f} | Precio Yes: {m['precio']:.1%}")
    print()