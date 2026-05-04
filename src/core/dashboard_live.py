import eventlet
import json
import logging
import traceback
from pathlib import Path
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit

from src.core.bot_controller_bybit import bybit_state, start_bybit, stop_bybit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")
logger = logging.getLogger(__name__)

TEMPLATE = Path(__file__).parent / "dashboard_template.html"


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_data() -> dict:
    s = bybit_state.get_stats()
    bybit = {
        "bankroll":        s["balance"],
        "initial_balance": bybit_state.initial_balance,
        "total_pnl":       round(s["balance"] - bybit_state.initial_balance, 2),
        "session_pnl":     s["session_pnl"],
        "trading_mode":    bybit_state.trading_mode,
        "win_rate":        s["win_rate"],
        "total_trades":    s["total_trades"],
        "open_positions":  bybit_state.open_positions,
        "recent_trades":   bybit_state.closed_trades[:20],
        "running":         bybit_state.running,
        "strategy":        bybit_state.strategy,
        "active_strategy": bybit_state.active_strategy,
        "regime":          bybit_state.regime,
        "orch_reason":     bybit_state.orch_reason,
        "logs":            bybit_state.logs[:50],
        "last_cycle":      bybit_state.last_cycle,
        "cycle_count":     bybit_state.cycle_count,
    }
    return {"bybit": bybit}


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return TEMPLATE.read_text(encoding="utf-8")


@app.route("/api/data")
def api_data():
    return jsonify(get_data())


@app.route("/api/positions")
def api_positions():
    return jsonify({"bybit": bybit_state.open_positions})


@app.route("/api/tv/signal")
def tv_signal():
    if not bybit_state.open_positions:
        return jsonify({"active": False})
    pos = bybit_state.open_positions[-1]
    return jsonify({
        "active":    True,
        "symbol":    pos.get("symbol"),
        "direction": pos.get("direction"),
        "entry":     pos.get("entry_price"),
        "sl":        pos.get("sl_price"),
        "tp":        pos.get("tp_price"),
    })


@app.route("/webhook/tradingview", methods=["POST"])
def tradingview_webhook():
    try:
        data   = request.json or {}
        symbol = data.get("symbol", "BTCUSDT").replace("BINANCE:", "").replace(".P", "")
        action = data.get("action", "notify_only").lower()
        price  = float(data.get("price", 0))
        bybit_state.add_log(f"TradingView: {action.upper()} {symbol} @ ${price:.4f}", "#FBBF24")
        socketio.emit("update", get_data())
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/equity")
def api_equity():
    try:
        from src.core.equity_tracker import get_summary
        return jsonify(get_summary())
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)})


@app.route("/api/equity/curve")
def api_equity_curve():
    data = _load_json(Path("logs/equity.json"))
    return jsonify(data.get("equity_curve", []))


@app.route("/api/equity/reset", methods=["POST"])
def api_equity_reset():
    eq_file = Path("logs/equity.json")
    if eq_file.exists():
        eq_file.unlink()
    return jsonify({"ok": True})


@app.route("/api/regimes")
def api_regimes():
    data = _load_json(Path("logs/regimes.json"))
    return jsonify(data)


# ── SOCKET EVENTS ─────────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    emit("update", get_data())


@socketio.on("start_bybit")
def on_start_bybit(data=None):
    strategy = (data or {}).get("strategy", "Auto")
    start_bybit(strategy)
    emit("update", get_data())


@socketio.on("stop_bybit")
def on_stop_bybit():
    stop_bybit()
    emit("update", get_data())


# ── PUSH LOOP ─────────────────────────────────────────────────────────────────

def push_loop():
    while True:
        eventlet.sleep(4)
        socketio.emit("update", get_data())


def run_dashboard(port=5000):
    eventlet.spawn(push_loop)
    print(f"\n🚀 Dashboard → http://localhost:{port}\n")
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
