from src.core.probability_engine import estimate_probability, calculate_ev

question = "Will Bitcoin reach $100,000 by end of 2025?"
market_price = 0.45

result = estimate_probability(question, market_price)
if result:
    ev = calculate_ev(result["probability"], market_price)
    print(f"Probabilidad estimada: {result['probability']:.1%}")
    print(f"Confianza: {result['confidence']}")
    print(f"EV: {ev:.4f}")
    print(f"Razonamiento: {result['reasoning']}")