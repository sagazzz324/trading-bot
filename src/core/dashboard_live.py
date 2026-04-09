import eventlet
import json
import logging
import threading
from pathlib import Path
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit
from src.core.bot_controller import state, start_poly, stop_poly, start_binance, stop_binance
from src.exchanges.binance_client import BinanceClient

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")
logger = logging.getLogger(__name__)

POLY_LOG    = Path("logs/paper_trades.json")
SCALP_LOG   = Path("logs/scalping_trades.json")
TEMPLATE    = Path(__file__).parent / "dashboard_template.html"


def load_poly():
    if POLY_LOG.exists():
        with open(POLY_LOG) as f:
            return json.load(f)
    return {"bankroll": 1000, "initial_bankroll": 1000, "trades": [], "active_trades": []}


def load_scalping():
    if SCALP_LOG.exists():
        try:
            with open(SCALP_LOG) as f:
                return json.load(f)
        except:
            pass
    return {"total_pnl": 0, "trades": [], "open_positions": [], "win_count": 0, "loss_count": 0}


def get_data():
    poly    = load_poly()
    scalp   = load_scalping()

    resolved  = [t for t in poly["trades"] if t["status"] == "resolved"]
    wins      = [t for t in resolved if t["result"] == "win"]
    total_pnl = sum(t["pnl"] for t in resolved) if resolved else 0
    win_rate  = len(wins) / len(resolved) if resolved else 0
    drawdown  = (poly["initial_bankroll"] - poly["bankroll"]) / poly["initial_bankroll"]

    try:
        client = BinanceClient()
        prices = {
            "BTC": client.get_price("BTCUSDT"),
            "ETH": client.get_price("ETHUSDT"),
            "SOL": client.get_price("SOLUSDT"),
        }
    except:
        prices = {"BTC": 0, "ETH": 0, "SOL": 0}

    all_open = poly["active_trades"] + scalp.get("open_positions", [])
    all_trades = poly["trades"][-8:][::-1]

    return {
        "bankroll":         round(poly["bankroll"], 2),
        "initial_bankroll": poly["initial_bankroll"],
        "total_pnl":        round(total_pnl, 2),
        "scalping_pnl":     round(scalp.get("total_pnl", 0), 4),
        "win_rate":         round(win_rate * 100, 1),
        "total_trades":     len(resolved),
        "open_positions":   all_open,
        "recent_trades":    all_trades,
        "drawdown":         round(drawdown * 100, 2),
        "bot_running":      state.poly_running or state.binance_running,
        "prices":           prices,
        "logs":             state.logs[:20],
        "bot_state": {
            "poly_running":     state.poly_running,
            "binance_running":  state.binance_running,
            "binance_strategy": state.binance_strategy,
            "binance_profile":  state.binance_profile,
            "last_cycle":       state.last_cycle,
            "cycle_count":      state.cycle_count,
        }
    }


# ── ROUTES ──────────────────────────────────────────

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
    state.binance_profile  = request.json.get("profile", state.binance_profile)
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
    state.binance_strategy = request.json.get("strategy", "Scalping")
    state.binance_profile  = request.json.get("profile", "Moderado")
    if was:
        start_binance(state)
    state.add_log(f"Estrategia → {state.binance_strategy}", "#41d6fc")
    return jsonify({"ok": True})


@app.route("/api/trade/resolve", methods=["POST"])
def resolve_trade():
    trade_id = int(request.json.get("id"))
    outcome  = bool(request.json.get("outcome"))
    from src.core.paper_trader import PaperTrader
    trader = PaperTrader()
    trader.resolve_trade(trade_id, outcome)
    state.add_log(f"Trade #{trade_id} → {'WIN' if outcome else 'LOSS'}", "#d558b7")
    return jsonify({"ok": True})


@app.route("/webhook/tradingview", methods=["POST"])
def tradingview_webhook():
    try:
        data   = request.json
        symbol = data.get("symbol", "BTCUSDT").replace("BINANCE:", "").replace(".P", "")
        action = data.get("action", "notify_only")
        price  = float(data.get("price", 0))

        state.add_log(f"TradingView: {action.upper()} {symbol} @ ${price:.4f}", "#f0c040")
        logger.info(f"Webhook TradingView: {action} {symbol} @ {price}")

        if action.lower() == "buy":
            from src.strategies.scalper import ScalpingBot
            bot    = ScalpingBot(capital=1000)
            klines = bot.client.get_klines(symbol, interval="5m", limit=100)
            if klines:
                signal = {
                    "symbol":        symbol,
                    "direction":     "long",
                    "strength":      80,
                    "reasons":       ["TradingView alert"],
                    "price":         price or klines[-1]["close"],
                    "rsi":           50,
                    "momentum":      0,
                    "atr_pct":       0.2,
                    "claude_regime": "tradingview",
                    "claude_risk":   "medium"
                }
                bot.open_position(signal)
                state.add_log(f"Posición abierta: LONG {symbol}", "#00FF9C")

        elif action.lower() == "sell":
            from src.strategies.scalper import ScalpingBot
            bot = ScalpingBot(capital=1000)
            for pos in list(bot.state["open_positions"]):
                if pos["symbol"] == symbol:
                    current = bot.client.get_price(symbol)
                    pnl     = pos["position_usdt"] * ((current - pos["entry_price"]) / pos["entry_price"])
                    bot._close_position(pos, current, "tradingview_signal", pnl)
                    state.add_log(f"Posición cerrada: {symbol} PnL ${pnl:+.2f}", "#d558b7")
                    break

        return jsonify({"ok": True, "symbol": symbol, "action": action})

    except Exception as e:
        logger.error(f"Error en webhook TradingView: {e}")
        return jsonify({"ok": False, "error": str(e)}), 400


# ── SOCKET EVENTS ────────────────────────────────────

@socketio.on("connect")
def on_connect():
    emit("update", get_data())
    logger.info("Cliente conectado al dashboard")


@socketio.on("disconnect")
def on_disconnect():
    logger.info("Cliente desconectado del dashboard")


@socketio.on("start_bot")
def on_start_bot():
    ok = start_binance(state)
    state.add_log("Bot Binance iniciado desde dashboard", "#00FF9C")
    logger.info(f"start_bot recibido — ok={ok}")
    emit("update", get_data())


@socketio.on("stop_bot")
def on_stop_bot():
    stop_binance(state)
    state.add_log("Bot Binance detenido desde dashboard", "#FF4D4D")
    logger.info("stop_bot recibido")
    emit("update", get_data())


@socketio.on("start_poly")
def on_start_poly():
    ok = start_poly(state)
    state.add_log("Bot Polymarket iniciado", "#00FF9C")
    emit("update", get_data())


@socketio.on("stop_poly")
def on_stop_poly():
    stop_poly(state)
    state.add_log("Bot Polymarket detenido", "#FF4D4D")
    emit("update", get_data())


# ── PUSH LOOP ────────────────────────────────────────

def push_loop():
    while True:
        eventlet.sleep(6)
        socketio.emit("update", get_data())


def run_dashboard(port=5000):
    eventlet.spawn(push_loop)
    print(f"\n🚀 Dashboard → http://localhost:{port}\n")
    socketio.run(app, host="0.0.0.0", port=port, debug=False)