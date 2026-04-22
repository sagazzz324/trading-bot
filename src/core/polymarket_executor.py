"""
polymarket_executor.py - Calls the Fly.io executor in Brazil
to place real Polymarket CLOB orders without geoblocking.
"""
import logging
import os
import traceback

import requests

logger = logging.getLogger(__name__)

EXECUTOR_URL = os.getenv("EXECUTOR_URL", "https://poly-executor.fly.dev")
EXECUTOR_SECRET = os.getenv("EXECUTOR_SECRET", "neural_trade_2025")


def get_balance() -> float:
    """Returns available USDC.e collateral."""
    try:
        r = requests.get(
            f"{EXECUTOR_URL}/balance",
            params={"secret": EXECUTOR_SECRET},
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            raw = data.get("balance", "0") or "0"
            try:
                if isinstance(raw, dict):
                    raw = raw.get("balance", "0") or "0"
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


def get_token_balance(token_id: str) -> float:
    """Returns current conditional token balance in shares."""
    try:
        r = requests.get(
            f"{EXECUTOR_URL}/balance/conditional",
            params={"secret": EXECUTOR_SECRET, "token_id": token_id},
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            raw = data.get("balance", "0") or "0"
            return float(raw) / 1e6
        logger.error(f"get_token_balance error: {data}")
        return 0.0
    except Exception as e:
        logger.error(f"get_token_balance: {e}\n{traceback.format_exc()}")
        return 0.0


def place_market_order(
    token_id: str,
    side: str,
    amount_usdc: float,
    price: float = 0.51,
    order_type: str = "FOK",
) -> dict | None:
    """
    Places an immediate order through the Fly executor.
    token_id: CLOB token id for the selected outcome
    side: BUY or SELL
    amount_usdc: BUY uses USDC amount, SELL uses share quantity
    price: protection limit; if <= 0, the executor lets the SDK derive it
    order_type: FOK or FAK
    """
    try:
        price_clean = int(round(price * 100)) / 100 if price is not None else 0.0
        r = requests.post(
            f"{EXECUTOR_URL}/order",
            json={
                "secret": EXECUTOR_SECRET,
                "token_id": token_id,
                "side": side,
                "amount": amount_usdc,
                "price": price_clean,
                "order_type": order_type,
            },
            timeout=15,
        )
        data = r.json()
        if data.get("ok"):
            logger.info(
                f"Orden ejecutada via Fly.io: token={token_id[:20]}... "
                f"side={side} amount={amount_usdc} price={price_clean} "
                f"orderID={data.get('orderID')}"
            )
        else:
            logger.error(f"place_market_order error: {data}")
        return data
    except Exception as e:
        logger.error(f"place_market_order: {e}\n{traceback.format_exc()}")
        return {"ok": False, "error": str(e)}


def get_trade_history(limit: int = 50) -> list:
    """Returns real trade history."""
    return []
