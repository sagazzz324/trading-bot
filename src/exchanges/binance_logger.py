import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LOG_FILE = Path("logs/binance_trades.json")


def load_state():
    """Carga el estado anterior de Binance."""
    if LOG_FILE.exists():
        with open(LOG_FILE, "r") as f:
            data = json.load(f)
            logger.info(f"Estado Binance cargado: {len(data.get('trades', []))} trades")
            return data
    return {
        "total_pnl": 0,
        "trades": [],
        "sessions": []
    }


def save_trade(strategy, symbol, side, price, quantity, pnl=None):
    """Guarda un trade individual."""
    state = load_state()

    trade = {
        "id": len(state["trades"]) + 1,
        "timestamp": datetime.now().isoformat(),
        "strategy": strategy,
        "symbol": symbol,
        "side": side,
        "price": price,
        "quantity": quantity,
        "pnl": pnl
    }

    state["trades"].append(trade)
    if pnl:
        state["total_pnl"] = round(state["total_pnl"] + pnl, 6)

    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE, "w") as f:
        json.dump(state, f, indent=2)

    return trade


def save_session(strategy, symbol, trades, pnl, candles):
    """Guarda el resumen de una sesión completa."""
    state = load_state()

    session = {
        "id": len(state["sessions"]) + 1,
        "timestamp": datetime.now().isoformat(),
        "strategy": strategy,
        "symbol": symbol,
        "trades": trades,
        "pnl": round(pnl, 6),
        "candles_analyzed": candles
    }

    state["sessions"].append(session)
    state["total_pnl"] = round(state["total_pnl"] + pnl, 6)

    with open(LOG_FILE, "w") as f:
        json.dump(state, f, indent=2)

    logger.info(f"Sesión guardada: {strategy} | PnL: ${pnl:+.6f}")
    return session


def get_stats():
    """Retorna estadísticas generales de Binance."""
    state = load_state()
    sessions = state.get("sessions", [])
    trades = state.get("trades", [])

    if not sessions:
        return {"mensaje": "Sin sesiones todavía"}

    winning = [s for s in sessions if s["pnl"] > 0]
    losing = [s for s in sessions if s["pnl"] < 0]

    return {
        "total_sesiones": len(sessions),
        "sesiones_ganadoras": len(winning),
        "sesiones_perdedoras": len(losing),
        "win_rate": f"{len(winning)/len(sessions)*100:.1f}%" if sessions else "0%",
        "pnl_total": f"${state['total_pnl']:+.6f}",
        "total_trades": len(trades)
    }