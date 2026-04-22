"""
polymarket_executor.py — Llama al executor de Fly.io en Brasil
para ejecutar órdenes reales en Polymarket CLOB sin geoblock.
"""
import os
import logging
import traceback
import requests

logger = logging.getLogger(__name__)

EXECUTOR_URL    = os.getenv("EXECUTOR_URL", "https://poly-executor.fly.dev")
EXECUTOR_SECRET = os.getenv("EXECUTOR_SECRET", "neural_trade_2025")


def get_balance() -> float:
    """Retorna el balance de USDC.e disponible en la wallet."""
    try:
        r = requests.get(
            f"{EXECUTOR_URL}/balance",
            params={"secret": EXECUTOR_SECRET},
            timeout=10
        )
        data = r.json()
        if data.get("ok"):
            # El balance viene en formato raw (6 decimales USDC.e)
            raw = data.get("balance", "0") or "0"
            try:
                # Si viene como dict {'balance': '...'}
                if isinstance(raw, dict):
                    raw = raw.get("balance", "0") or "0"
                # Parsear el string del dict si viene así
                if isinstance(raw, str) and "balance" in raw:
                    import json as _json
                    parsed = _json.loads(raw.replace("'", '"'))
                    raw = parsed.get("balance", "0") or "0"
                return float(raw) / 1e6
            except Exception:
                return 0.0
        logger.error(f"get_balance error: {data}")
        return 0.0
    except Exception as e:
        logger.error(f"get_balance: {e}\n{traceback.format_exc()}")
        return 0.0


def place_market_order(token_id: str, side: str, amount_usdc: float,
                       price: float = 0.51, order_type: str = "FOK") -> dict | None:
    """
    Ejecuta una orden inmediata en Polymarket a través del executor en Fly.io Brasil.
    token_id: clobTokenId del outcome (up o down)
    side: "BUY" o "SELL"
    amount_usdc: en BUY es monto en USDC; en SELL es cantidad de shares
    price: precio límite de protección
    order_type: FOK o FAK
    """
    try:
        # Redondear price al tick size de 0.01
        price_clean = int(round(price * 100)) / 100

        r = requests.post(
            f"{EXECUTOR_URL}/order",
            json={
                "secret":   EXECUTOR_SECRET,
                "token_id": token_id,
                "side":     side,
                "amount":   amount_usdc,
                "price":    price_clean,
                "order_type": order_type,
            },
            timeout=15
        )
        data = r.json()
        if data.get("ok"):
            logger.info(f"Orden ejecutada via Fly.io: token={token_id[:20]}... "
                        f"side={side} amount={amount_usdc} price={price_clean} "
                        f"orderID={data.get('orderID')}")
            return data
        logger.error(f"place_market_order error: {data}")
        print(f"❌ ERROR ORDEN REAL: {data.get('error')}")
        return None
    except Exception as e:
        logger.error(f"place_market_order: {e}\n{traceback.format_exc()}")
        print(f"❌ ERROR ORDEN REAL: {e}")
        return None


def get_trade_history(limit: int = 50) -> list:
    """Retorna el historial de trades reales."""
    return []
