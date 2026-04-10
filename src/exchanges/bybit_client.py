"""
bybit_client.py — Bybit API client con verbose error logging
"""
import logging
import traceback
from pybit.unified_trading import HTTP
from config.settings import BYBIT_API_KEY, BYBIT_SECRET_KEY, BYBIT_TESTNET, PAPER_TRADING

logger = logging.getLogger(__name__)


class BybitClient:
    def __init__(self):
        self.client = HTTP(
            testnet=BYBIT_TESTNET,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_SECRET_KEY,
        )
        self.paper = PAPER_TRADING
        logger.info(f"Bybit init (testnet={BYBIT_TESTNET}, paper={self.paper})")

    # ── PRICE ─────────────────────────────────────────────────────────────────

    def get_price(self, symbol: str) -> float:
        try:
            r = self.client.get_tickers(category="linear", symbol=symbol)
            logger.debug(f"get_price raw {symbol}: {r}")
            items = r.get("result", {}).get("list", [])
            if not items:
                logger.error(f"get_price {symbol}: lista vacía — response={r}")
                return 0.0
            price = float(items[0]["lastPrice"])
            if price <= 0:
                logger.error(f"get_price {symbol}: precio={price} inválido")
            return price
        except Exception as e:
            logger.error(f"get_price {symbol}:\n{traceback.format_exc()}")
            return 0.0

    # ── KLINES ────────────────────────────────────────────────────────────────

    def get_klines(self, symbol: str, interval: str = "1", limit: int = 120) -> list:
        interval = self._convert_interval(interval)
        try:
            r = self.client.get_kline(
                category="linear",
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            logger.debug(f"get_klines raw {symbol} interval={interval}: retCode={r.get('retCode')}")

            raw = r.get("result", {}).get("list", [])
            if not raw:
                logger.error(f"get_klines {symbol}: lista vacía — response={r}")
                return []

            # Bybit devuelve newest first — revertir
            raw = list(reversed(raw))

            klines = []
            for k in raw:
                try:
                    klines.append({
                        "t":      int(k[0]),
                        "open":   float(k[1]),
                        "high":   float(k[2]),
                        "low":    float(k[3]),
                        "close":  float(k[4]),
                        "volume": float(k[5]),
                    })
                except (IndexError, ValueError) as parse_err:
                    logger.error(f"get_klines {symbol}: error parseando vela {k}: {parse_err}")
                    continue

            if klines:
                logger.debug(f"get_klines {symbol}: {len(klines)} velas, último close={klines[-1]['close']}")
            else:
                logger.error(f"get_klines {symbol}: 0 velas parseadas de {len(raw)} raw")

            return klines

        except Exception as e:
            logger.error(f"get_klines {symbol} interval={interval}:\n{traceback.format_exc()}")
            return []

    def _convert_interval(self, interval: str) -> str:
        mapping = {
            "1m": "1", "3m": "3", "5m": "5", "15m": "15",
            "30m": "30", "1h": "60", "4h": "240", "1d": "D"
        }
        return mapping.get(interval, interval)

    # ── ORDER BOOK ────────────────────────────────────────────────────────────

    def get_order_book(self, symbol: str, limit: int = 25) -> dict:
        try:
            r = self.client.get_orderbook(category="linear", symbol=symbol, limit=limit)
            result = r.get("result", {})
            return {
                "bids": [[b[0], b[1]] for b in result.get("b", [])],
                "asks": [[a[0], a[1]] for a in result.get("a", [])],
            }
        except Exception as e:
            logger.error(f"get_order_book {symbol}:\n{traceback.format_exc()}")
            return {"bids": [], "asks": []}

    # ── TOP MOVERS ────────────────────────────────────────────────────────────

    def get_top_movers(self, limit: int = 20) -> list:
        try:
            r = self.client.get_tickers(category="linear")
            tickers = r.get("result", {}).get("list", [])
            if not tickers:
                logger.error(f"get_top_movers: lista vacía — response={r}")
                return []

            usdt = [
                t for t in tickers
                if t["symbol"].endswith("USDT")
                and float(t.get("volume24h", 0)) > 1_000_000
            ]
            usdt.sort(key=lambda x: abs(float(x.get("price24hPcnt", 0))), reverse=True)

            movers = [{
                "symbol":     t["symbol"],
                "price":      float(t["lastPrice"]),
                "change_pct": float(t.get("price24hPcnt", 0)) * 100,
                "volume":     float(t.get("volume24h", 0)),
            } for t in usdt[:limit]]

            logger.debug(f"get_top_movers: {len(movers)} pares")
            return movers

        except Exception as e:
            logger.error(f"get_top_movers:\n{traceback.format_exc()}")
            return []

    # ── BALANCE ───────────────────────────────────────────────────────────────

    def get_balance(self, asset: str = "USDT") -> float:
        try:
            r = self.client.get_wallet_balance(accountType="UNIFIED", coin=asset)
            coins = r.get("result", {}).get("list", [{}])[0].get("coin", [])
            coin  = next((c for c in coins if c["coin"] == asset), None)
            if not coin:
                logger.error(f"get_balance: {asset} no encontrado — response={r}")
                return 0.0
            return float(coin["availableToWithdraw"])
        except Exception as e:
            logger.error(f"get_balance {asset}:\n{traceback.format_exc()}")
            return 0.0

    # ── PLACE ORDER ───────────────────────────────────────────────────────────

    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = "Market") -> dict | None:
        if self.paper:
            logger.info(f"[PAPER] {side} {qty} {symbol}")
            return {"paper": True, "symbol": symbol, "side": side, "qty": qty}
        try:
            r = self.client.place_order(
                category="linear",
                symbol=symbol,
                side=side.capitalize(),
                orderType=order_type,
                qty=str(qty),
                timeInForce="IOC",
            )
            logger.info(f"Order placed: {side} {qty} {symbol} → {r}")
            return r.get("result")
        except Exception as e:
            logger.error(f"place_order {symbol}:\n{traceback.format_exc()}")
            return None

    # ── WEBSOCKET ─────────────────────────────────────────────────────────────

    def start_kline_ws(self, symbols: list, interval: str, callback):
        from pybit.unified_trading import WebSocket
        ws = WebSocket(testnet=BYBIT_TESTNET, channel_type="linear")
        iv = self._convert_interval(interval)
        for sym in symbols:
            ws.kline_stream(
                interval=iv,
                symbol=sym,
                callback=lambda msg, s=sym: self._ws_callback(msg, s, callback)
            )
        logger.info(f"Bybit WS iniciado — {len(symbols)} símbolos interval={iv}")
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
                    "x":      k.get("confirm", False),
                }
                callback(symbol, kline)
        except Exception as e:
            logger.error(f"WS callback {symbol}:\n{traceback.format_exc()}")