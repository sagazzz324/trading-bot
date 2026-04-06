from src.skills.binance_signals import scan_binance_opportunities

opportunities = scan_binance_opportunities(top_n=20)

print(f"\n📊 Total oportunidades: {len(opportunities)}")
for opp in opportunities:
    print(f"\n  {opp['symbol']}")
    print(f"    Precio: ${opp['price']:,.4f}")
    print(f"    RSI: {opp['rsi']:.1f}")
    print(f"    Momentum: {opp['momentum']:+.2f}%")
    print(f"    Señal: {opp['signal']}")