from src.core.paper_trader import PaperTrader

trader = PaperTrader(bankroll=1000)

# Simular un trade
trader.place_trade(
    market_id="test-001",
    question="Will Bitcoin reach $100,000 by end of 2025?",
    true_prob=0.52,
    market_prob=0.45,
    ev=0.07,
    position_size=31.82
)

# Ver estadísticas
print("\n📊 ESTADÍSTICAS:")
stats = trader.get_stats()
for k, v in stats.items():
    print(f"   {k}: {v}")