import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def get_market_context(question):
    """
    Construye contexto adicional para mejorar la estimación de Claude.
    Detecta el tipo de mercado y agrega información relevante.
    """
    question_lower = question.lower()
    context_parts = []

    # Fecha actual
    today = datetime.now().strftime("%B %d, %Y")
    context_parts.append(f"Fecha actual: {today}")

    # Detectar tipo de mercado
    if any(w in question_lower for w in ["nba", "lakers", "warriors", "celtics", "heat",
                                          "bulls", "nets", "knicks", "suns", "nuggets",
                                          "bucks", "76ers", "cavaliers", "pistons",
                                          "timberwolves", "spurs", "clippers", "thunder",
                                          "pelicans", "blazers", "jazz", "kings", "hornets",
                                          "hawks", "magic", "pacers", "raptors", "wizards"]):
        context_parts.append("Tipo: Partido NBA — usá standings actuales y forma reciente")
        context_parts.append("Considerá: record de la temporada, últimos 10 partidos, lesiones conocidas, ventaja de local")

    elif any(w in question_lower for w in ["nhl", "hockey", "bruins", "rangers", "maple",
                                            "canadiens", "penguins", "flyers", "capitals",
                                            "devils", "lightning", "panthers", "oilers",
                                            "flames", "canucks", "jets", "wild", "blues",
                                            "blackhawks", "avalanche", "golden knights",
                                            "kraken", "ducks", "sharks", "coyotes", "sabres",
                                            "senators", "red wings", "predators", "stars"]):
        context_parts.append("Tipo: Partido NHL — usá standings actuales y forma reciente")
        context_parts.append("Considerá: record, power play, goaltending, lesiones")

    elif any(w in question_lower for w in ["nfl", "super bowl", "touchdown", "quarterback",
                                            "patriots", "chiefs", "bills", "dolphins",
                                            "ravens", "bengals", "steelers", "browns",
                                            "texans", "colts", "jaguars", "titans",
                                            "broncos", "raiders", "chargers", "49ers",
                                            "seahawks", "rams", "cardinals", "cowboys",
                                            "giants", "eagles", "commanders", "bears",
                                            "lions", "packers", "vikings", "falcons",
                                            "panthers", "saints", "buccaneers"]):
        context_parts.append("Tipo: Partido NFL")
        context_parts.append("Considerá: record, forma reciente, lesiones en posiciones clave")

    elif any(w in question_lower for w in ["bitcoin", "btc", "ethereum", "eth", "crypto",
                                            "sol", "matic", "usdc", "usdt", "defi",
                                            "airdrop", "token", "nft", "web3", "binance",
                                            "coinbase", "polymarket"]):
        context_parts.append("Tipo: Mercado crypto/DeFi")
        context_parts.append("Considerá: tendencia reciente del mercado, sentiment, noticias macro de crypto")

    elif any(w in question_lower for w in ["president", "election", "congress", "senate",
                                            "trump", "biden", "harris", "democrat",
                                            "republican", "vote", "poll", "political",
                                            "iran", "russia", "ukraine", "war", "nato",
                                            "fed", "inflation", "gdp", "rate"]):
        context_parts.append("Tipo: Mercado político/macro")
        context_parts.append("Considerá: polls actuales, contexto geopolítico, datos económicos recientes")

    elif any(w in question_lower for w in ["oscar", "emmy", "grammy", "golden globe",
                                            "box office", "gross", "movie", "film"]):
        context_parts.append("Tipo: Entretenimiento/premios")
        context_parts.append("Considerá: favoritos de crítica, datos de taquilla, historial de premiaciones")

    elif any(w in question_lower for w in ["soccer", "football", "premier", "champions",
                                            "la liga", "serie a", "bundesliga", "mls",
                                            "world cup", "arsenal", "chelsea", "liverpool",
                                            "manchester", "barcelona", "real madrid",
                                            "bayern", "juventus", "psg"]):
        context_parts.append("Tipo: Fútbol")
        context_parts.append("Considerá: forma reciente, tabla de posiciones, lesiones, historial head-to-head")

    else:
        context_parts.append("Tipo: Mercado general")
        context_parts.append("Usá tu mejor criterio basado en información disponible")

    return " | ".join(context_parts)