import os
from dotenv import load_dotenv


def _is_truthy(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# En Railway usamos solo variables del entorno remoto.
# El .env local queda reservado para desarrollo local.
if not os.getenv("RAILWAY_ENVIRONMENT"):
    load_dotenv("config/.env")


# Modo de operación
PAPER_TRADING = _is_truthy(os.getenv("PAPER_TRADING"), default=True)

# Capital
BANKROLL = float(os.getenv("BANKROLL", 1000))
MAX_TRADE_PCT = float(os.getenv("MAX_TRADE_PCT", 0.10))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", 0.25))
MAX_DRAWDOWN = float(os.getenv("MAX_DRAWDOWN", 0.20))
MIN_EV = float(os.getenv("MIN_EV", 0.05))

# APIs
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY")
POLYMARKET_SIGNER_ADDRESS = os.getenv("POLYMARKET_SIGNER_ADDRESS")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
BINANCE_TESTNET = _is_truthy(os.getenv("BINANCE_TESTNET"), default=True)
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY")
BYBIT_SECRET_KEY = os.getenv("BYBIT_SECRET_KEY")
BYBIT_TESTNET    = _is_truthy(os.getenv("BYBIT_TESTNET"), default=True)
