import os
import json
import logging
import anthropic
from dotenv import load_dotenv

load_dotenv("config/.env")

logger = logging.getLogger(__name__)


def get_client():
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def estimate_probability(question, market_prob, context=None):
    client = get_client()
    
    """
    Pide a Claude que razone profundo antes de estimar probabilidad.
    Retorna: {probability, confidence, reasoning, should_trade, concerns}
    """
    context_str = ""
    if context:
        if context.get("news"):
            context_str += f"\nNOTICIAS RECIENTES:\n" + "\n".join(context["news"][:3])
        if context.get("whale_signal"):
            w = context["whale_signal"]
            context_str += f"\nSEÑAL WHALE: {'▲' if w.get('direction') == 'up' else '▼'} {w.get('change_pct', 0):.1f}% · vol ${w.get('volume', 0)/1e6:.1f}M"
        if context.get("similar_markets"):
            context_str += f"\nMERCADOS RELACIONADOS: {context['similar_markets']}"
        if context.get("active_trades"):
            context_str += f"\nPOSICIONES ACTIVAS: {context['active_trades']}"

    prompt = f"""Sos un analista de mercados de predicción con experiencia en trading cuantitativo.

MERCADO A EVALUAR:
- Pregunta: "{question}"
- Precio actual del mercado: {market_prob*100:.1f}%
- Esto significa que el mercado estima {market_prob*100:.1f}% de probabilidad de que ocurra
{context_str}

TU TAREA: Razonar paso a paso y decidir si hay una oportunidad real.

Pensá en:
1. ¿Qué información tenés sobre este tema? ¿Cuál es tu probabilidad estimada?
2. ¿El mercado tiene razón, está sobreestimando o subestimando?
3. ¿Hay señales recientes que cambien el análisis? (noticias, whales, volumen)
4. ¿Cuánta confianza tenés en tu estimación?
5. ¿Hay razones para NO entrar aunque el EV sea positivo? (liquidez, timing, concentración)
6. ¿Cuándo vence este mercado? ¿Es razonable el timing?

Respondé SOLO con JSON, sin markdown:
{{
  "probability": 0.XX,
  "confidence": "low|medium|high",
  "reasoning": "explicación breve de por qué estimás esa probabilidad",
  "should_trade": true|false,
  "concerns": "razones para no entrar aunque el EV sea bueno, o vacío si no hay",
  "market_assessment": "overpriced|underpriced|fairly_priced",
  "edge": "descripción del edge si existe"
}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        # Limpiar posibles backticks
        text = text.replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        logger.info(f"Claude eval '{question[:50]}': prob={result['probability']:.2f} confidence={result['confidence']} should_trade={result['should_trade']}")
        return result
    except Exception as e:
        logger.error(f"Error en probability engine: {e}")
        return {"probability": market_prob, "confidence": "low", "should_trade": False, "concerns": str(e), "reasoning": "Error", "market_assessment": "unknown", "edge": ""}


def analyze_market_conditions(symbol, klines, current_price, context=None):
    client = get_client()
    """
    Claude analiza si las condiciones del mercado son buenas para operar.
    Retorna: {should_trade, strategy, reasoning, risk_level, spread_recommendation}
    """
    if not klines or len(klines) < 10:
        return {"should_trade": False, "reasoning": "Sin suficientes datos", "risk_level": "high"}

    closes = [k["close"] for k in klines[-20:]]
    highs  = [k["high"]  for k in klines[-20:]]
    lows   = [k["low"]   for k in klines[-20:]]
    volumes = [k.get("volume", 0) for k in klines[-20:]]

    sma5  = sum(closes[-5:]) / 5
    sma20 = sum(closes) / len(closes)
    avg_range = sum(h - l for h, l in zip(highs[-10:], lows[-10:])) / 10
    volatility_pct = avg_range / current_price * 100
    avg_vol = sum(volumes) / len(volumes) if volumes[0] > 0 else 0
    last_vol = volumes[-1] if volumes else 0
    trend = "up" if sma5 > sma20 * 1.001 else "down" if sma5 < sma20 * 0.999 else "lateral"
    price_change_1h = (closes[-1] - closes[-12]) / closes[-12] * 100 if len(closes) >= 12 else 0

    prompt = f"""Sos un trader algorítmico analizando si conviene hacer Market Making en {symbol} ahora mismo.

DATOS DEL MERCADO ({symbol}):
- Precio actual: ${current_price:,.2f}
- Tendencia: {trend} (SMA5 ${sma5:,.2f} vs SMA20 ${sma20:,.2f})
- Volatilidad promedio vela: ${avg_range:.2f} ({volatility_pct:.3f}%)
- Cambio última hora: {price_change_1h:+.2f}%
- Volumen promedio: {avg_vol:.1f} unidades
- Volumen última vela: {last_vol:.1f} unidades
- Últimos 5 cierres: {[round(c,1) for c in closes[-5:]]}

ESTRATEGIAS DISPONIBLES:
1. Market Making - funciona mejor en mercados LATERALES con buen volumen
2. Scalping Momentum - funciona mejor con tendencia CLARA y volumen creciente
3. Mean Reversion - funciona cuando hay sobrecompra/venta (RSI extremo)
4. Pausar - a veces no operar es la mejor decisión

Analizá:
- ¿Es buen momento para operar o hay mucho riesgo?
- ¿Qué estrategia tiene más sentido ahora mismo?
- ¿Qué spread sería razonable dado la volatilidad?
- ¿Hay señales de que el mercado está muy inestable?

Respondé SOLO con JSON:
{{
  "should_trade": true|false,
  "strategy": "Market Making|Scalping Momentum|Mean Reversion|Pausar",
  "reasoning": "explicación concisa de tu análisis",
  "risk_level": "low|medium|high",
  "spread_recommendation": 0.0001,
  "confidence": "low|medium|high",
  "market_regime": "trending|ranging|volatile|thin_liquidity"
}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        logger.info(f"Análisis mercado {symbol}: strategy={result['strategy']} risk={result['risk_level']} should_trade={result['should_trade']}")
        return result
    except Exception as e:
        logger.error(f"Error analizando mercado: {e}")
        return {"should_trade": False, "strategy": "Pausar", "reasoning": str(e), "risk_level": "high", "spread_recommendation": 0.0001, "confidence": "low", "market_regime": "unknown"}
    
def calculate_ev(true_prob, market_prob):
    return true_prob * (1 - market_prob) - (1 - true_prob) * market_prob