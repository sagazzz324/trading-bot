import os
from dotenv import load_dotenv

load_dotenv("config/.env")

# Modo de operación
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"

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
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
BYBIT_API_KEY    = os.getenv("BYBIT_API_KEY")
BYBIT_SECRET_KEY = os.getenv("BYBIT_SECRET_KEY")
BYBIT_TESTNET    = os.getenv("BYBIT_TESTNET", "true").lower() == "true"