"""
bybit_client.py — drop-in replacement for BinanceClient
Uses pybit (official Bybit SDK)
"""
import logging
from pybit.unified_trading import HTTP

from config.settings import (
    BYBIT_API_KEY, BYBIT_SECRET_KEY, BYBIT_TESTNET, PAPER_TRADING
)

logger = logging.getLogger(__name__)


class BybitClient:
    def __init__(self):
        self.client = HTTP(
            testnet=BYBIT_TESTNET,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_SECRET_KEY,
        )
        self.paper = PAPER_TRADING
        logger.info(f"Bybit conectado (testnet={BYBIT_TESTNET}, paper={self.paper})")

    # ── PRICE ────────────────────────────────────────────────────────────────

    def get_price(self, symbol: str) -> float:
        try:
            r = self.client.get_tickers(category="linear", symbol=symbol)
            return float(r["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.error(f"get_price {symbol}: {e}")
            return 0.0

    # ── KLINES ───────────────────────────────────────────────────────────────

    def get_klines(self, symbol: str, interval: str = "1", limit: int = 120) -> list:
        """
        interval: "1" "3" "5" "15" "30" "60" "240" "D"
        Bybit uses minutes as strings, not Binance format.
        """
        interval = self._convert_interval(interval)
        try:
            r = self.client.get_kline(
                category="linear",
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            raw = r["result"]["list"]
            # Bybit returns newest first — reverse
            raw = list(reversed(raw))
            return [{
                "t":      int(k[0]),
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            } for k in raw]
        except Exception as e:
            logger.error(f"get_klines {symbol}: {e}")
            return []

    def _convert_interval(self, interval: str) -> str:
        """Convert Binance-style interval to Bybit."""
        mapping = {
            "1m": "1", "3m": "3", "5m": "5", "15m": "15",
            "30m": "30", "1h": "60", "4h": "240", "1d": "D"
        }
        return mapping.get(interval, interval)

    # ── ORDER BOOK ───────────────────────────────────────────────────────────

    def get_order_book(self, symbol: str, limit: int = 25) -> dict:
        try:
            r = self.client.get_orderbook(category="linear", symbol=symbol, limit=limit)
            return {
                "bids": [[b[0], b[1]] for b in r["result"]["b"]],
                "asks": [[a[0], a[1]] for a in r["result"]["a"]],
            }
        except Exception as e:
            logger.error(f"order_book {symbol}: {e}")
            return {"bids": [], "asks": []}

    # ── TOP MOVERS ───────────────────────────────────────────────────────────

    def get_top_movers(self, limit: int = 20) -> list:
        try:
            r = self.client.get_tickers(category="linear")
            tickers = r["result"]["list"]
            usdt = [
                t for t in tickers
                if t["symbol"].endswith("USDT")
                and float(t.get("volume24h", 0)) > 1_000_000
            ]
            usdt.sort(key=lambda x: abs(float(x.get("price24hPcnt", 0))), reverse=True)
            return [{
                "symbol":     t["symbol"],
                "price":      float(t["lastPrice"]),
                "change_pct": float(t.get("price24hPcnt", 0)) * 100,
                "volume":     float(t.get("volume24h", 0)),
            } for t in usdt[:limit]]
        except Exception as e:
            logger.error(f"get_top_movers: {e}")
            return []

    # ── BALANCE ──────────────────────────────────────────────────────────────

    def get_balance(self, asset: str = "USDT") -> float:
        try:
            r = self.client.get_wallet_balance(accountType="UNIFIED", coin=asset)
            coins = r["result"]["list"][0]["coin"]
            coin  = next((c for c in coins if c["coin"] == asset), None)
            return float(coin["availableToWithdraw"]) if coin else 0.0
        except Exception as e:
            logger.error(f"get_balance: {e}")
            return 0.0

    # ── PLACE ORDER ──────────────────────────────────────────────────────────

    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = "Market") -> dict | None:
        if self.paper:
            logger.info(f"[PAPER] {side} {qty} {symbol}")
            return {"paper": True, "symbol": symbol, "side": side, "qty": qty}
        try:
            r = self.client.place_order(
                category="linear",
                symbol=symbol,
                side=side.capitalize(),   # "Buy" | "Sell"
                orderType=order_type,
                qty=str(qty),
                timeInForce="IOC",
            )
            logger.info(f"Order placed: {side} {qty} {symbol}")
            return r["result"]
        except Exception as e:
            logger.error(f"place_order {symbol}: {e}")
            return None

    # ── WEBSOCKET ────────────────────────────────────────────────────────────

    def start_kline_ws(self, symbols: list, interval: str, callback):
        """
        Start Bybit WebSocket for kline streams.
        callback(symbol, kline_dict) called on each update.
        """
        from pybit.unified_trading import WebSocket
        ws = WebSocket(testnet=BYBIT_TESTNET, channel_type="linear")
        iv = self._convert_interval(interval)
        for sym in symbols:
            ws.kline_stream(
                interval=iv,
                symbol=sym,
                callback=lambda msg, s=sym: self._ws_callback(msg, s, callback)
            )
        return ws

    def _ws_callback(self, msg: dict, symbol: str, callback):
        try:
            for k in msg.get("data", []):
                kline = {
                    "t":      int(k["start"]),
                    "open":   float(k["open"]),
                    "high":   float(k["high"]),
                    "low":    float(k["low"]),
                    "close":  float(k["close"]),
                    "volume": float(k["volume"]),
                    "x":      k.get("confirm", False),  # candle closed?
                }
                callback(symbol, kline)
        except Exception as e:
            logger.debug(f"WS callback error: {e}")
