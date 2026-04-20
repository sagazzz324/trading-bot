import eventlet
import json
import logging
import traceback
from pathlib import Path
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit

from src.core.bot_controller_bybit import bybit_state, start_bybit, stop_bybit
from src.core.bot_controller_poly  import poly_state,  start_poly,  stop_poly
from src.core.btc_scalper import _price_cache as btc_price_cache

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")
logger = logging.getLogger(__name__)

TEMPLATE  = Path(__file__).parent / "dashboard_template.html"
POLY_LOG  = Path("logs/paper_trades.json")
SCALP_LOG = Path("logs/scalping_trades.json")


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except:
            pass
    return {}


def get_data() -> dict:
    # ── Bybit (from memory) ───────────────────────────────────────────────────
    s = bybit_state.get_stats()
    bybit = {
        "bankroll":        s["balance"],
        "initial_balance": bybit_state.initial_balance,
        "total_pnl":       round(s["balance"] - bybit_state.initial_balance, 2),
        "session_pnl":     s["session_pnl"],
        "win_rate":        s["win_rate"],
        "total_trades":    s["total_trades"],
        "open_positions":  bybit_state.open_positions,
        "recent_trades":   bybit_state.closed_trades[:10],
        "running":         bybit_state.running,
        "strategy":        bybit_state.strategy,
        "logs":            bybit_state.logs[:30],
        "last_cycle":      bybit_state.last_cycle,
        "cycle_count":     bybit_state.cycle_count,
    }

    # ── Polymarket (from JSON file) ───────────────────────────────────────────
    poly_data = _load_json(POLY_LOG)
    trades    = poly_data.get("trades", [])
    resolved  = [t for t in trades if t.get("status") == "resolved"]
    wins      = [t for t in resolved if t.get("result") == "win"]
    total_pnl = sum(t.get("pnl", 0) for t in resolved)
    bankroll  = poly_data.get("bankroll", 1000)
    init_bank = poly_data.get("initial_bankroll", 1000)

    import time as _t
    poly = {
        "bankroll":        round(bankroll, 2),
        "initial_balance": init_bank,
        "total_pnl":       round(total_pnl, 2),
        "win_rate":        round(len(wins) / len(resolved) * 100, 1) if resolved else 0,
        "total_trades":    len(resolved),
        "open_positions":  poly_data.get("active_trades", []),
        "recent_trades":   trades[-10:][::-1],
        "running":         poly_state.running,
        "logs":            poly_state.logs[:30],
        "last_cycle":      poly_state.last_cycle,
        "cycle_count":     poly_state.cycle_count,
        "interval":        poly_state.interval,
        "market_mode":     poly_state.market_mode,
        "btc_embed_slug":  f"btc-updown-5m-{(int(_t.time()) // 300 + 1) * 300}",
        "btc_price":       btc_price_cache.get("price", 0),
    }

    return {"bybit": bybit, "poly": poly}


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return TEMPLATE.read_text(encoding="utf-8")


@app.route("/api/data")
def api_data():
    return jsonify(get_data())


@app.route("/api/positions")
def api_positions():
    return jsonify({
        "bybit": bybit_state.open_positions,
        "poly":  _load_json(POLY_LOG).get("active_trades", [])
    })


@app.route("/api/tv/signal")
def tv_signal():
    if not bybit_state.open_positions:
        return jsonify({"active": False})
    pos = bybit_state.open_positions[-1]
    return jsonify({
        "active":    True,
        "symbol":    pos["symbol"],
        "direction": pos["direction"],
        "entry":     pos["entry_price"],
        "sl":        pos["sl_price"],
        "tp":        pos["tp_price"],
    })


@app.route("/api/poly/interval", methods=["POST"])
def poly_interval():
    mins = int((request.json or {}).get("minutes", 15))
    poly_state.interval = max(1, min(60, mins))
    poly_state.add_log(f"Intervalo → {poly_state.interval} min", "#41d6fc")
    return jsonify({"ok": True})


@app.route("/webhook/tradingview", methods=["POST"])
def tradingview_webhook():
    try:
        data   = request.json or {}
        symbol = data.get("symbol", "BTCUSDT").replace("BINANCE:", "").replace(".P", "")
        action = data.get("action", "notify_only").lower()
        price  = float(data.get("price", 0))
        bybit_state.add_log(f"TradingView: {action.upper()} {symbol} @ ${price:.4f}", "#f0c040")
        socketio.emit("update", get_data())
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/poly/reset", methods=["POST"])
def poly_reset():
    amount = float((request.json or {}).get("amount", 94))
    data = {"bankroll": amount, "initial_bankroll": amount, "trades": [], "active_trades": []}
    POLY_LOG.parent.mkdir(exist_ok=True)
    with open(POLY_LOG, "w") as f:
        json.dump(data, f)
    # resetear equity tracker también
    eq_file = Path("logs/equity.json")
    if eq_file.exists():
        eq_file.unlink()
    return jsonify({"ok": True, "bankroll": amount})


# ── SOCKET EVENTS ─────────────────────────────────────────────────────────────

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

@app.route("/api/test/polymarket")
def test_polymarket():
    try:
        from src.core.polymarket_executor import get_client
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        
        client = get_client()
        token_id = "10573704752591535651462031805725056300561251820094597326643531904905733104178"
        
        order_args = MarketOrderArgs(token_id=token_id, amount=1.0, side="BUY")
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

@socketio.on("connect")
def on_connect():
    emit("update", get_data())


@socketio.on("start_bybit")
def on_start_bybit(data=None):
    strategy = (data or {}).get("strategy", "Scalping")
    start_bybit(strategy)
    emit("update", get_data())


@socketio.on("stop_bybit")
def on_stop_bybit():
    stop_bybit()
    emit("update", get_data())


@socketio.on("start_poly")
def on_start_poly(data=None):
    mode = (data or {}).get("mode", poly_state.market_mode)
    start_poly(mode)
    emit("update", get_data())


@socketio.on("set_poly_mode")
def on_set_poly_mode(data=None):
    mode = (data or {}).get("mode", "all")
    from src.core.bot_controller_poly import set_poly_mode
    set_poly_mode(mode)
    emit("update", get_data())


@socketio.on("stop_poly")
def on_stop_poly():
    stop_poly()
    emit("update", get_data())


@socketio.on("start_bot")
def on_start_bot(data=None):
    strategy = (data or {}).get("strategy", "Scalping")
    start_bybit(strategy)
    emit("update", get_data())


@socketio.on("stop_bot")
def on_stop_bot():
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