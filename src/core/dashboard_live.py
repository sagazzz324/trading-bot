import eventlet
import json
import logging
from pathlib import Path
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit
from src.core.bot_controller import state, start_poly, stop_poly, start_binance, stop_binance

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")
logger = logging.getLogger(__name__)

TEMPLATE = Path(__file__).parent / "dashboard_template.html"


def get_data():
    """Todo viene del estado en memoria — no depende de archivos."""
    stats = state.get_stats()
    total_pnl = stats["balance"] - state.initial_balance

    return {
        "bankroll":         stats["balance"],
        "initial_bankroll": state.initial_balance,
        "total_pnl":        round(total_pnl, 2),
        "session_pnl":      state.session_pnl,
        "win_rate":         stats["win_rate"],
        "total_trades":     stats["total_trades"],
        "open_positions":   state.open_positions,
        "recent_trades":    state.closed_trades[:10],
        "bot_running":      state.poly_running or state.binance_running,
        "logs":             state.logs[:30],
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


@app.route("/api/binance/start", methods=["POST"])
def binance_start():
    data = request.json or {}
    state.binance_strategy = data.get("strategy", state.binance_strategy)
    state.binance_profile  = data.get("profile",  state.binance_profile)
    ok = start_binance(state)
    return jsonify({"ok": ok, "running": state.binance_running})


@app.route("/api/binance/stop", methods=["POST"])
def binance_stop():
    stop_binance(state)
    return jsonify({"ok": True, "running": False})


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
    mins = int((request.json or {}).get("minutes", 15))
    state.poly_interval = max(1, min(60, mins))
    state.add_log(f"Intervalo Poly → {state.poly_interval} min", "#41d6fc")
    return jsonify({"ok": True})


@app.route("/api/trade/resolve", methods=["POST"])
def resolve_trade():
    trade_id = int((request.json or {}).get("id"))
    outcome  = bool((request.json or {}).get("outcome"))
    from src.core.paper_trader import PaperTrader
    trader = PaperTrader()
    trader.resolve_trade(trade_id, outcome)
    state.add_log(f"Trade #{trade_id} → {'WIN' if outcome else 'LOSS'}", "#d558b7")
    return jsonify({"ok": True})

@app.route("/api/positions")
def api_positions():
    positions = []
    for pos in state.open_positions:
        positions.append({
            "symbol":    pos["symbol"],
            "direction": pos["direction"],
            "entry":     pos["entry_price"],
            "sl":        pos["sl_price"],
            "tp":        pos["tp_price"],
            "size":      pos["position_usdt"],
            "time":      pos["timestamp"],
            "status":    "open"
        })
    for trade in state.closed_trades[:20]:
        positions.append({
            "symbol":    trade["symbol"],
            "direction": trade["direction"],
            "entry":     trade["entry_price"],
            "exit":      trade.get("exit_price", 0),
            "pnl":       trade.get("pnl_usdt", 0),
            "reason":    trade.get("exit_reason", ""),
            "time":      trade["timestamp"],
            "status":    "closed"
        })
    return jsonify({"ok": True, "positions": positions})


@app.route("/api/tv/signal")
def tv_signal():
    if not state.open_positions:
        return jsonify({"active": False, "signal": "none"})
    pos = state.open_positions[-1]
    return jsonify({
        "active":    True,
        "signal":    pos["direction"],
        "symbol":    pos["symbol"],
        "entry":     pos["entry_price"],
        "sl":        pos["sl_price"],
        "tp":        pos["tp_price"],
        "size":      pos["position_usdt"],
        "strength":  pos.get("strength", 0),
    })

@app.route("/webhook/tradingview", methods=["POST"])
def tradingview_webhook():
    try:
        data   = request.json or {}
        symbol = data.get("symbol", "BTCUSDT").replace("BINANCE:", "").replace(".P", "")
        action = data.get("action", "notify_only").lower()
        price  = float(data.get("price", 0))

        state.add_log(f"TradingView: {action.upper()} {symbol} @ ${price:.4f}", "#f0c040")

        if action == "buy":
            from src.strategies.scalper import ScalpingBot
            bot = ScalpingBot(capital=state.balance)
            bot.state["open_positions"] = list(state.open_positions)
            klines = bot.client.get_klines(symbol, interval="5m", limit=100)
            if klines:
                signal = {
                    "symbol":        symbol,
                    "direction":     "long",
                    "strength":      75,
                    "reasons":       ["TradingView signal"],
                    "price":         price or klines[-1]["close"],
                    "rsi":           50,
                    "momentum":      0,
                    "atr_pct":       0.2,
                    "claude_regime": "tradingview",
                    "claude_risk":   "medium",
                }
                pos = bot.open_position(signal)
                if pos:
                    state.add_position(pos)
                    state.add_log(f"LONG {symbol} abierto via TradingView", "#00FF9C")

        elif action == "sell":
            for pos in list(state.open_positions):
                if pos["symbol"] == symbol:
                    from src.exchanges.binance_client import BinanceClient
                    current = BinanceClient().get_price(symbol)
                    pnl = pos["position_usdt"] * ((current - pos["entry_price"]) / pos["entry_price"])
                    state.close_position(pos["id"], current, "tradingview_sell", pnl)
                    state.add_log(f"{symbol} cerrado via TradingView · PnL ${pnl:+.2f}", "#d558b7")
                    break

        socketio.emit("update", get_data())
        return jsonify({"ok": True, "symbol": symbol, "action": action})

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 400


# ── SOCKET EVENTS ────────────────────────────────────

@socketio.on("connect")
def on_connect():
    emit("update", get_data())
    logger.info("Cliente conectado")


@socketio.on("start_bot")
def on_start_bot():
    ok = start_binance(state)
    state.add_log("Bot Binance iniciado", "#00FF9C")
    emit("update", get_data())


@socketio.on("stop_bot")
def on_stop_bot():
    stop_binance(state)
    state.add_log("Bot detenido", "#FF4D4D")
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
        eventlet.sleep(4)
        socketio.emit("update", get_data())


def run_dashboard(port=5000):
    eventlet.spawn(push_loop)
    print(f"\n🚀 Dashboard → http://localhost:{port}\n")
    socketio.run(app, host="0.0.0.0", port=port, debug=False)