import logging
from config.settings import BANKROLL, MAX_TRADE_PCT, KELLY_FRACTION, MAX_DRAWDOWN, MIN_EV

logger = logging.getLogger(__name__)


def kelly_position_size(true_prob, market_prob, bankroll):
    """
    Calcula el tamaño de posición usando Kelly Criterion.
    """
    b = (1 - market_prob) / market_prob
    p = true_prob
    q = 1 - true_prob

    kelly_full = (p * b - q) / b
    kelly_fraction = kelly_full * KELLY_FRACTION
    max_allowed = bankroll * MAX_TRADE_PCT
    position_size = min(kelly_fraction * bankroll, max_allowed)

    if kelly_fraction <= 0:
        logger.info("Kelly negativo, no operar")
        return 0

    logger.info(f"Kelly completo: {kelly_full:.4f} | Kelly ajustado: {kelly_fraction:.4f} | Tamaño: ${position_size:.2f}")
    return round(position_size, 2)


def check_risk_rules(current_drawdown, active_trades, max_trades=5):
    """
    Verifica si el bot puede operar según las reglas de riesgo.
    """
    reasons = []

    if current_drawdown >= MAX_DRAWDOWN:
        reasons.append(f"Drawdown {current_drawdown:.1%} supera máximo {MAX_DRAWDOWN:.1%}")

    if active_trades >= max_trades:
        reasons.append(f"Trades activos ({active_trades}) alcanzó el máximo ({max_trades})")

    if reasons:
        for r in reasons:
            logger.warning(f"RIESGO: {r}")
        return False, reasons

    return True, []


def check_stop_loss(trade, current_prob, stop_loss_pct=0.30):
    """
    Verifica si un trade activo debe cerrarse por stop loss.
    Si la probabilidad bajó más del stop_loss_pct desde la entrada, salimos.
    stop_loss_pct: porcentaje de caída para activar el stop (default 30%)
    """
    entry_prob = trade["true_prob"]
    drop = (entry_prob - current_prob) / entry_prob

    if drop >= stop_loss_pct:
        logger.warning(
            f"STOP LOSS activado: prob cayó {drop:.1%} "
            f"(entrada: {entry_prob:.1%} → actual: {current_prob:.1%})"
        )
        return True

    return False


def check_concentration(active_trades, new_question, max_same_topic=1):
    """
    Evita concentración en un mismo tema.
    Detecta si ya tenemos trades en mercados similares.
    """
    keywords = extract_topic_keywords(new_question)

    similar_count = 0
    for trade in active_trades:
        existing_keywords = extract_topic_keywords(trade["question"])
        overlap = set(keywords) & set(existing_keywords)
        if len(overlap) >= 2:
            similar_count += 1

    if similar_count >= max_same_topic:
        logger.warning(f"Concentración detectada: ya tenemos {similar_count} trade(s) en tema similar")
        return False, similar_count

    return True, similar_count


def extract_topic_keywords(question):
    """Extrae palabras clave del tema de un mercado."""
    stopwords = {"will", "the", "a", "an", "be", "is", "are", "was", "were",
                 "by", "on", "in", "at", "to", "for", "of", "and", "or",
                 "vs", "than", "more", "above", "below", "win", "beat",
                 "forces", "enter", "reach", "hit", "above", "below"}

    words = question.lower().replace("?", "").replace(".", "").split()
    return [w for w in words if w not in stopwords and len(w) > 3]