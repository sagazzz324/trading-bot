def __init__(self):
    # Testnet para órdenes, API real para precios
    self.client = Client(
        api_key=BINANCE_API_KEY,
        api_secret=BINANCE_SECRET_KEY,
        testnet=False  # Precios reales, órdenes controladas por PAPER_TRADING
    )
    self.testnet = BINANCE_TESTNET
    logger.info(f"Binance conectado (paper trading: {BINANCE_TESTNET})")