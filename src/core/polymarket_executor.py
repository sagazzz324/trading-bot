"""
polymarket_executor.py — Ejecutor de órdenes reales en Polymarket CLOB
Se usa cuando PAPER_TRADING=false
"""
import os
import logging
import traceback
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, MarketOrderArgs, OrderType
from py_clob_client.constants import POLYGON

logger = logging.getLogger(__name__)


def get_client() -> ClobClient:
    creds = ApiCreds(
        api_key=os.getenv("POLYMARKET_API_KEY"),
        api_secret=os.getenv("POLYMARKET_API_SECRET"),
        api_passphrase=os.getenv("POLYMARKET_API_PASSPHRASE"),
    )
    return ClobClient(
        host="https://clob.polymarket.com",
        key=os.getenv("POLYMARKET_PRIVATE_KEY"),
        chain_id=POLYGON,
        signature_type=0,
        funder=os.getenv("POLYMARKET_SIGNER_ADDRESS"),
        creds=creds
    )


def get_balance() -> float:
    """Retorna el balance de USDC disponible en Polymarket."""
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        client = get_client()
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        allowance = client.get_balance_allowance(params=params)
        return float(allowance) if allowance else 0.0
    except Exception as e:
        logger.error(f"get_balance: {e}\n{traceback.format_exc()}")
        return 0.0


@app.route("/api/test/polymarket")
def test_polymarket():
    try:
        from src.core.polymarket_executor import get_client
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        
        client = get_client()
        token_id = "10573704752591535651462031805725056300561251820094597326643531904905733104178"
        
        order_args = MarketOrderArgs(token_id=token_id, amount=1.0)
        signed_order = client.create_market_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK)
        
        return jsonify({
            "ok": True,
            "signed_order": str(signed_order),
            "resp": str(resp)
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()})


def get_open_positions() -> list:
    """Retorna las posiciones abiertas en Polymarket."""
    try:
        client = get_client()
        positions = client.get_positions()
        return positions or []
    except Exception as e:
        logger.error(f"get_open_positions: {e}\n{traceback.format_exc()}")
        return []


def get_trade_history(limit: int = 50) -> list:
    """Retorna el historial de trades reales."""
    try:
        client = get_client()
        trades = client.get_trades(limit=limit)
        return trades or []
    except Exception as e:
        logger.error(f"get_trade_history: {e}\n{traceback.format_exc()}")
        return []