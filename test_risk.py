from src.core.risk_engine import kelly_position_size, check_risk_rules

# Simular el caso anterior: prob real 52%, precio mercado 45%
true_prob = 0.52
market_prob = 0.45
bankroll = 1000

size = kelly_position_size(true_prob, market_prob, bankroll)
print(f"Tamaño de posición: ${size}")

can_trade, reasons = check_risk_rules(current_drawdown=0.05, active_trades=2)
print(f"¿Puede operar?: {can_trade}")