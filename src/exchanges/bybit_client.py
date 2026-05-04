"""
bybit_client.py — Bybit API client con verbose error logging
"""
import logging
import traceback
from pybit.unified_trading import HTTP
from config.settings import (
    BYBIT_API_KEY,
    BYBIT_SECRET_KEY,
    BYBIT_TESTNET,
    TRADING_MODE,
    PAPER_TRADING,
    SHADOW_TRADING,
    LIVE_TRADING,
)

logger = logging.getLogger(__name__)


class BybitClient:
    def __init__(self):
        self.client = HTTP(
            testnet=BYBIT_TESTNET,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_SECRET_KEY,
        )
        self.mode = TRADING_MODE
        self.paper = PAPER_TRADING
        self.shadow = SHADOW_TRADING
        self.live = LIVE_TRADING
        logger.info(
            f"Bybit init (testnet={BYBIT_TESTNET}, mode={self.mode}, paper={self.paper})"
        )

    def get_execution_mode(self) -> str:
        return self.mode

    def can_execute_orders(self) -> bool:
        return self.live

    def build_order_preview(self, symbol: str, side: str, qty: float,
                            order_type: str = "Market",
                            reduce_only: bool = False) -> dict:
        preview = {
            "mode": self.mode,
            "category": "linear",
            "symbol": symbol,
            "side": side.capitalize(),
            "orderType": order_type,
            "qty": str(qty),
            "timeInForce": "IOC",
        }
        if reduce_only:
            preview["reduceOnly"] = True
        return preview

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

    def get_account_snapshot(self, asset: str = "USDT") -> dict:
        try:
            r = self.client.get_wallet_balance(accountType="UNIFIED", coin=asset)
            account = r.get("result", {}).get("list", [{}])[0]
            coins = account.get("coin", [])
            coin = next((c for c in coins if c.get("coin") == asset), None)
            if not coin:
                logger.error(f"get_account_snapshot: {asset} no encontrado â€” response={r}")
                return {
                    "asset": asset,
                    "wallet_balance": 0.0,
                    "equity": 0.0,
                    "free_balance": 0.0,
                    "unrealized_pnl": 0.0,
                    "account_im": 0.0,
                    "account_mm": 0.0,
                    "mode": self.mode,
                }
            return {
                "asset": asset,
                "wallet_balance": float(coin.get("walletBalance", 0) or 0),
                "equity": float(coin.get("equity", 0) or 0),
                "free_balance": float(coin.get("availableToWithdraw", 0) or 0),
                "unrealized_pnl": float(coin.get("unrealisedPnl", 0) or 0),
                "account_im": float(account.get("totalInitialMargin", 0) or 0),
                "account_mm": float(account.get("totalMaintenanceMargin", 0) or 0),
                "mode": self.mode,
            }
        except Exception:
            logger.error(f"get_account_snapshot {asset}:\n{traceback.format_exc()}")
            return {
                "asset": asset,
                "wallet_balance": 0.0,
                "equity": 0.0,
                "free_balance": 0.0,
                "unrealized_pnl": 0.0,
                "account_im": 0.0,
                "account_mm": 0.0,
                "mode": self.mode,
            }

    def get_positions(self, settle_coin: str = "USDT") -> list:
        try:
            r = self.client.get_positions(category="linear", settleCoin=settle_coin)
            items = r.get("result", {}).get("list", [])
            positions = []
            for item in items:
                size = float(item.get("size", 0) or 0)
                if size <= 0:
                    continue
                positions.append({
                    "symbol": item.get("symbol"),
                    "side": (item.get("side") or "").capitalize(),
                    "size": size,
                    "avg_price": float(item.get("avgPrice", 0) or 0),
                    "mark_price": float(item.get("markPrice", 0) or 0),
                    "unrealized_pnl": float(item.get("unrealisedPnl", 0) or 0),
                    "position_value": float(item.get("positionValue", 0) or 0),
                    "leverage": float(item.get("leverage", 0) or 0),
                    "updated_time": item.get("updatedTime"),
                })
            return positions
        except Exception:
            logger.error(f"get_positions:\n{traceback.format_exc()}")
            return []

    def get_open_position(self, symbol: str) -> dict | None:
        try:
            r = self.client.get_positions(category="linear", symbol=symbol)
            items = r.get("result", {}).get("list", [])
            for item in items:
                size = float(item.get("size", 0) or 0)
                if size <= 0:
                    continue
                return {
                    "symbol": item.get("symbol"),
                    "side": (item.get("side") or "").capitalize(),
                    "size": size,
                    "avg_price": float(item.get("avgPrice", 0) or 0),
                    "mark_price": float(item.get("markPrice", 0) or 0),
                    "unrealized_pnl": float(item.get("unrealisedPnl", 0) or 0),
                    "position_value": float(item.get("positionValue", 0) or 0),
                    "leverage": float(item.get("leverage", 0) or 0),
                    "updated_time": item.get("updatedTime"),
                }
            return None
        except Exception:
            logger.error(f"get_open_position {symbol}:\n{traceback.format_exc()}")
            return None

    # ── PLACE ORDER ───────────────────────────────────────────────────────────

    def place_order(self, symbol: str, side: str, qty: float,
                    order_type: str = "Market", reduce_only: bool = False) -> dict | None:
        preview = self.build_order_preview(
            symbol, side, qty, order_type=order_type, reduce_only=reduce_only
        )
        if self.paper:
            tag = "[SHADOW]" if self.shadow else "[PAPER]"
            logger.info(f"{tag} preview order: {preview}")
            return {"paper": not self.shadow, "shadow": self.shadow, "preview": preview}
        try:
            r = self.client.place_order(
                category="linear",
                symbol=symbol,
                side=side.capitalize(),
                orderType=order_type,
                qty=str(qty),
                timeInForce="IOC",
                reduceOnly=reduce_only,
            )
            logger.info(f"Order placed: {side} {qty} {symbol} → {r}")
            result = r.get("result", {})
            return {
                "mode": self.mode,
                "symbol": symbol,
                "side": side.capitalize(),
                "qty": qty,
                "order_type": order_type,
                "order_id": result.get("orderId"),
                "order_link_id": result.get("orderLinkId"),
                "raw": result,
            }
        except Exception as e:
            logger.error(f"place_order {symbol}:\n{traceback.format_exc()}")
            return None

    def place_market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False) -> dict | None:
        return self.place_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="Market",
            reduce_only=reduce_only,
        )

    def get_order_executions(self, symbol: str, order_id: str, limit: int = 20) -> list:
        try:
            r = self.client.get_executions(
                category="linear",
                symbol=symbol,
                orderId=order_id,
                limit=limit,
            )
            items = r.get("result", {}).get("list", [])
            executions = []
            for item in items:
                executions.append({
                    "symbol": item.get("symbol"),
                    "side": item.get("side"),
                    "order_id": item.get("orderId"),
                    "exec_id": item.get("execId"),
                    "price": float(item.get("execPrice", 0) or 0),
                    "qty": float(item.get("execQty", 0) or 0),
                    "value": float(item.get("execValue", 0) or 0),
                    "fee": float(item.get("execFee", 0) or 0),
                    "time": item.get("execTime"),
                })
            return executions
        except Exception:
            logger.error(f"get_order_executions {symbol} {order_id}:\n{traceback.format_exc()}")
            return []

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
