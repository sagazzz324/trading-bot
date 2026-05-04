import os
from dotenv import load_dotenv


def _is_truthy(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_trading_mode(value: str | None) -> str:
    mode = (value or "").strip().lower()
    if not mode:
        return "paper"
    aliases = {
        "sim": "paper",
        "simulate": "paper",
        "dry": "shadow",
        "dry-run": "shadow",
        "readonly": "shadow",
        "real": "live",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"paper", "shadow", "live"}:
        return "paper"
    return mode


# En Railway usamos solo variables del entorno remoto.
# El .env local queda reservado para desarrollo local.
if not os.getenv("RAILWAY_ENVIRONMENT"):
    load_dotenv("config/.env")


# Modo de operación
TRADING_MODE = _normalize_trading_mode(os.getenv("TRADING_MODE"))
PAPER_TRADING = TRADING_MODE != "live"
SHADOW_TRADING = TRADING_MODE == "shadow"
LIVE_TRADING = TRADING_MODE == "live"

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
ENABLE_LEGACY_BOTS = _is_truthy(os.getenv("ENABLE_LEGACY_BOTS"), default=False)
