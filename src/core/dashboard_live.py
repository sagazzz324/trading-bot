import eventlet
import json
import logging
import threading
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit
from src.core.bot_controller import state, start_poly, stop_poly, start_binance, stop_binance
from src.exchanges.binance_client import BinanceClient

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")
logger = logging.getLogger(__name__)

POLY_LOG = Path("logs/paper_trades.json")
BINANCE_LOG = Path("logs/binance_trades.json")
TEMPLATE = Path(__file__).parent / "dashboard_template.html"


def load_poly():
    if POLY_LOG.exists():
        with open(POLY_LOG) as f:
            return json.load(f)
    return {"bankroll": 1000, "initial_bankroll": 1000, "trades": [], "active_trades": []}


def load_binance():
    if BINANCE_LOG.exists():
        with open(BINANCE_LOG) as f:
            return json.load(f)
    return {"total_pnl": 0, "trades": [], "sessions": []}


def get_data():
    poly = load_poly()
    binance = load_binance()

    resolved = [t for t in poly["trades"] if t["status"] == "resolved"]
    wins = [t for t in resolved if t["result"] == "win"]
    total_pnl = sum(t["pnl"] for t in resolved) if resolved else 0
    win_rate = len(wins) / len(resolved) if resolved else 0
    drawdown = (poly["initial_bankroll"] - poly["bankroll"]) / poly["initial_bankroll"]

    try:
        client = BinanceClient()
        prices = {
            "BTC": client.get_price("BTCUSDT"),
            "ETH": client.get_price("ETHUSDT"),
            "SOL": client.get_price("SOLUSDT"),
        }
        movers = client.get_top_movers(5)
    except:
        prices = {"BTC": 0, "ETH": 0, "SOL": 0}
        movers = []

    pnl_history = []
    running = poly["initial_bankroll"]
    for trade in poly["trades"]:
        if trade["status"] == "resolved":
            running += trade["pnl"]
            pnl_history.append(round(running, 2))

    return {
        "poly": {
            "bankroll": round(poly["bankroll"], 2),
            "initial_bankroll": poly["initial_bankroll"],
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate * 100, 1),
            "total_trades": len(resolved),
            "active_trades": poly["active_trades"],
            "recent_trades": poly["trades"][-8:][::-1],
            "pnl_history": pnl_history
        },
        "binance": {
            "total_pnl": round(binance["total_pnl"], 6),
            "sessions": binance["sessions"][-12:][::-1],
            "prices": prices,
            "movers": movers[:5]
        },
        "drawdown": round(drawdown * 100, 2),
        "bot_state": {
            "poly_running": state.poly_running,
            "binance_running": state.binance_running,
            "poly_interval": state.poly_interval,
            "binance_interval": state.binance_interval,
            "binance_strategy": state.binance_strategy,
            "binance_profile": state.binance_profile,
            "last_cycle": state.last_cycle,
            "cycle_count": state.cycle_count,
            "logs": state.logs[:12]
        }
    }


@app.route("/")
def index():
    return TEMPLATE.read_text(encoding="utf-8")


@app.route("/api/data")
def api_data():
    return jsonify(get_data())


@app.route("/api/poly/start", methods=["POST"])
def poly_start():
    ok = start_poly(state)
    return jsonify({"ok": ok, "running": state.poly_running})


@app.route("/api/poly/stop", methods=["POST"])
def poly_stop():
    stop_poly(state)
    return jsonify({"ok": True, "running": False})


@app.route("/api/poly/interval", methods=["POST"])
def poly_interval():
    mins = int(request.json.get("minutes", 15))
    state.poly_interval = max(1, min(60, mins))
    state.add_log(f"Intervalo Poly → {state.poly_interval} min", "#41d6fc")
    return jsonify({"ok": True})


@app.route("/api/binance/start", methods=["POST"])
def binance_start():
    state.binance_strategy = request.json.get("strategy", state.binance_strategy)
    state.binance_profile = request.json.get("profile", state.binance_profile)
    ok = start_binance(state)
    return jsonify({"ok": ok, "running": state.binance_running})


@app.route("/api/binance/stop", methods=["POST"])
def binance_stop():
    stop_binance(state)
    return jsonify({"ok": True, "running": False})


@app.route("/api/binance/strategy", methods=["POST"])
def binance_strategy():
    was = state.binance_running
    if was:
        stop_binance(state)
        eventlet.sleep(1)
    state.binance_strategy = request.json.get("strategy", "Market Making")
    state.binance_profile = request.json.get("profile", "Moderado")
    if was:
        start_binance(state)
    state.add_log(f"Estrategia → {state.binance_strategy}", "#41d6fc")
    return jsonify({"ok": True})


@app.route("/api/trade/resolve", methods=["POST"])
def resolve_trade():
    trade_id = int(request.json.get("id"))
    outcome = bool(request.json.get("outcome"))
    from src.core.paper_trader import PaperTrader
    trader = PaperTrader()
    trader.resolve_trade(trade_id, outcome)
    state.add_log(f"Trade #{trade_id} → {'WIN' if outcome else 'LOSS'}", "#d558b7")
    return jsonify({"ok": True})


@socketio.on("connect")
def on_connect():
    emit("update", get_data())


def push_loop():
    while True:
        eventlet.sleep(8)
        socketio.emit("update", get_data())


def run_dashboard(port=5000):
    eventlet.spawn(push_loop)
    print(f"\n🚀 Dashboard → http://localhost:{port}\n")
    socketio.run(app, host="0.0.0.0", port=port, debug=False)