import feedparser
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Fuentes de noticias RSS gratuitas
RSS_FEEDS = {
    "general": [
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.reuters.com/reuters/topNews",
    ],
    "sports": [
        "https://www.espn.com/espn/rss/news",
        "https://rss.nba.com/rss/nba_global_feed_rss.xml",
    ],
    "crypto": [
        "https://cointelegraph.com/rss",
        "https://coindesk.com/arc/outboundfeeds/rss/",
    ],
    "politics": [
        "https://feeds.bbci.co.uk/news/politics/rss.xml",
        "https://rss.reuters.com/reuters/politicsNews",
    ]
}


def get_relevant_news(question, max_articles=3):
    """
    Busca noticias relevantes para una pregunta de mercado.
    """
    question_lower = question.lower()

    # Detectar categoría
    if any(w in question_lower for w in ["nba", "nhl", "nfl", "vs", "game", "match",
                                          "lakers", "warriors", "celtics", "chiefs"]):
        feeds = RSS_FEEDS["sports"]
    elif any(w in question_lower for w in ["bitcoin", "crypto", "eth", "token", "defi"]):
        feeds = RSS_FEEDS["crypto"]
    elif any(w in question_lower for w in ["election", "president", "congress", "iran",
                                            "russia", "ukraine", "trump", "biden"]):
        feeds = RSS_FEEDS["politics"]
    else:
        feeds = RSS_FEEDS["general"]

    articles = []
    keywords = extract_keywords(question)

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:20]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")[:200]

                # Verificar relevancia
                if any(kw.lower() in title.lower() or kw.lower() in summary.lower()
                       for kw in keywords):
                    articles.append({
                        "title": title,
                        "summary": summary,
                        "source": feed_url.split("/")[2]
                    })

                if len(articles) >= max_articles:
                    break

        except Exception as e:
            logger.debug(f"Error leyendo feed {feed_url}: {e}")
            continue

    return articles


def extract_keywords(question):
    """Extrae palabras clave de la pregunta."""
    # Palabras a ignorar
    stopwords = {"will", "the", "a", "an", "be", "is", "are", "was", "were",
                 "by", "on", "in", "at", "to", "for", "of", "and", "or",
                 "vs", "than", "more", "than", "above", "below", "win", "beat"}

    words = question.replace("?", "").replace(".", "").split()
    keywords = [w for w in words if w.lower() not in stopwords and len(w) > 3]
    return keywords[:5]


def get_news_context(question):
    """Alias para compatibilidad con bot.py"""
    articles = get_relevant_news(question)
    return [f"{a['title']} ({a['source']})" for a in articles]

    if not articles:
        return ""

    context = "Noticias recientes relevantes:\n"
    for i, article in enumerate(articles, 1):
        context += f"{i}. {article['title']} ({article['source']})\n"
        if article['summary']:
            context += f"   {article['summary'][:150]}\n"

    return context