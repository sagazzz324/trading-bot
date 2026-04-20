"""
polymarket_executor.py — Ejecutor de órdenes reales en Polymarket CLOB
Se usa cuando PAPER_TRADING=false
"""
import os
import logging
import traceback
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, ApiCreds
from py_clob_client.constants import POLYGON


logger = logging.getLogger(__name__)


def get_client() -> ClobClient:
    return ClobClient(
        host="https://clob.polymarket.com",
        key=os.getenv("POLYMARKET_PRIVATE_KEY"),
        chain_id=POLYGON,
        signature_type=0,
        funder=os.getenv("POLYMARKET_SIGNER_ADDRESS"),
        creds={
            "apiKey":      os.getenv("POLYMARKET_API_KEY"),
            "secret":      os.getenv("POLYMARKET_API_SECRET"),
            "passphrase":  os.getenv("POLYMARKET_API_PASSPHRASE"),
        }
    )


def get_balance() -> float:
    """Retorna el balance de USDC en la wallet en Polygon."""
    try:
        from web3 import Web3
        # USDC en Polygon
        USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        RPC = "https://polygon-rpc.com"
        w3 = Web3(Web3.HTTPProvider(RPC))
        abi = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS),
            abi=abi
        )
        wallet = os.getenv("POLYMARKET_SIGNER_ADDRESS")
        balance = contract.functions.balanceOf(
            Web3.to_checksum_address(wallet)
        ).call()
        return round(balance / 1e6, 2)  # USDC tiene 6 decimales
    except Exception as e:
        logger.error(f"get_balance: {e}\n{traceback.format_exc()}")
        return 0.0


def place_market_order(token_id: str, side: str, amount_usdc: float) -> dict | None:
    """
    Ejecuta una orden de mercado en Polymarket.
    token_id: conditionId del outcome (up o down)
    side: "BUY"
    amount_usdc: monto en USDC a invertir
    """
    try:
        client = get_client()
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount_usdc,
        )
        signed_order = client.create_market_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK)
        logger.info(f"Orden ejecutada: {resp}")
        return resp
    except Exception as e:
        logger.error(f"place_market_order: {e}\n{traceback.format_exc()}")
        print(f"❌ ERROR ORDEN REAL: {e}")
        return None
    


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