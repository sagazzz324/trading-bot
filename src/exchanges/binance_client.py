import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
from config.settings import BINANCE_API_KEY, BINANCE_SECRET_KEY, BINANCE_TESTNET

logger = logging.getLogger(__name__)


class BinanceClient:

    def __init__(self):
        self.client = Client(
            api_key=BINANCE_API_KEY,
            api_secret=BINANCE_SECRET_KEY,
            testnet=False
        )
        self.testnet = BINANCE_TESTNET
        logger.info(f"Binance conectado (paper trading: {BINANCE_TESTNET})")

    def get_balance(self, asset="USDT"):
        """Obtiene balance de un asset."""
        try:
            balance = self.client.get_asset_balance(asset=asset)
            return float(balance["free"])
        except BinanceAPIException as e:
            logger.error(f"Error obteniendo balance: {e}")
            return 0

    def get_price(self, symbol="BTCUSDT"):
        """Obtiene precio actual de un par."""
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except BinanceAPIException as e:
            logger.error(f"Error obteniendo precio: {e}")
            return 0

    def get_top_movers(self, limit=10):
        """
        Obtiene los pares con mayor movimiento en 24hs.
        """
        try:
            tickers = self.client.get_ticker()
            usdt_pairs = [
                t for t in tickers
                if t["symbol"].endswith("USDT")
                and float(t["volume"]) > 1000000
            ]

            usdt_pairs.sort(
                key=lambda x: abs(float(x["priceChangePercent"])),
                reverse=True
            )

            movers = []
            for t in usdt_pairs[:limit]:
                movers.append({
                    "symbol": t["symbol"],
                    "price": float(t["lastPrice"]),
                    "change_pct": float(t["priceChangePercent"]),
                    "volume": float(t["volume"]),
                    "high": float(t["highPrice"]),
                    "low": float(t["lowPrice"])
                })

            return movers

        except BinanceAPIException as e:
            logger.error(f"Error obteniendo movers: {e}")
            return []

    def get_klines(self, symbol, interval="1h", limit=24):
        """
        Obtiene velas históricas.
        interval: 1m, 5m, 15m, 1h, 4h, 1d
        """
        try:
            klines = self.client.get_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            return [{
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5])
            } for k in klines]
        except BinanceAPIException as e:
            logger.error(f"Error obteniendo klines: {e}")
            return []

    def place_order(self, symbol, side, quantity, order_type="MARKET"):
        """
        Coloca una orden. En paper trading solo loguea.
        """
        if self.testnet:
            logger.info(f"[PAPER] Orden simulada: {side} {quantity} {symbol}")
            return {
                "paper": True,
                "symbol": symbol,
                "side": side,
                "quantity": quantity
            }

        try:
            order = self.client.create_order(
                symbol=symbol,
                side=side,
                type=order_type,
                quantity=quantity
            )
            logger.info(f"Orden ejecutada: {side} {quantity} {symbol}")
            return order
        except BinanceAPIException as e:
            logger.error(f"Error colocando orden: {e}")
            return None