from flask import Flask, jsonify, render_template_string
from src.core.paper_trader import PaperTrader

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Trading Bot Dashboard</title>
    <meta charset="utf-8">
    <meta http-equiv="refresh" content="30">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0f0f0f; color: #e0e0e0; font-family: 'Courier New', monospace; padding: 20px; }
        h1 { color: #00ff88; font-size: 24px; margin-bottom: 20px; border-bottom: 1px solid #333; padding-bottom: 10px; }
        h2 { color: #00aaff; font-size: 16px; margin: 20px 0 10px 0; }
        .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 30px; }
        .card { background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 15px; }
        .card .label { color: #888; font-size: 11px; text-transform: uppercase; margin-bottom: 5px; }
        .card .value { font-size: 22px; font-weight: bold; }
        .green { color: #00ff88; }
        .red { color: #ff4444; }
        .yellow { color: #ffaa00; }
        .white { color: #ffffff; }
        table { width: 100%; border-collapse: collapse; background: #1a1a1a; border-radius: 8px; overflow: hidden; }
        th { background: #252525; color: #888; font-size: 11px; text-transform: uppercase; padding: 10px 15px; text-align: left; }
        td { padding: 10px 15px; border-top: 1px solid #252525; font-size: 13px; }
        tr:hover { background: #222; }
        .badge { padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
        .badge-active { background: #1a3a2a; color: #00ff88; }
        .badge-win { background: #1a3a2a; color: #00ff88; }
        .badge-loss { background: #3a1a1a; color: #ff4444; }
        .footer { margin-top: 20px; color: #555; font-size: 11px; text-align: center; }
    </style>
</head>
<body>
    <h1>🤖 Trading Bot Dashboard — PAPER TRADING</h1>

    <div class="grid">
        <div class="card">
            <div class="label">Bankroll</div>
            <div class="value white">${{ "%.2f"|format(stats.bankroll) }}</div>
        </div>
        <div class="card">
            <div class="label">PnL Total</div>
            <div class="value {{ 'green' if stats.pnl >= 0 else 'red' }}">
                {{ '+' if stats.pnl >= 0 else '' }}${{ "%.2f"|format(stats.pnl) }}
            </div>
        </div>
        <div class="card">
            <div class="label">Win Rate</div>
            <div class="value {{ 'green' if stats.win_rate >= 0.5 else 'yellow' }}">
                {{ "%.0f"|format(stats.win_rate * 100) }}%
            </div>
        </div>
        <div class="card">
            <div class="label">Trades Totales</div>
            <div class="value white">{{ stats.total_trades }}</div>
        </div>
    </div>

    <h2>📊 Trades Activos ({{ active_trades|length }})</h2>
    {% if active_trades %}
    <table>
        <tr>
            <th>#</th>
            <th>Mercado</th>
            <th>Prob Estimada</th>
            <th>Precio Mercado</th>
            <th>EV</th>
            <th>Posición</th>
            <th>Estado</th>
        </tr>
        {% for t in active_trades %}
        <tr>
            <td>{{ t.id }}</td>
            <td>{{ t.question[:50] }}</td>
            <td class="green">{{ "%.0f"|format(t.true_prob * 100) }}%</td>
            <td>{{ "%.0f"|format(t.market_prob * 100) }}%</td>
            <td class="green">{{ "%.3f"|format(t.ev) }}</td>
            <td>${{ "%.2f"|format(t.position_size) }}</td>
            <td><span class="badge badge-active">ACTIVO</span></td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <p style="color:#555; padding: 15px;">Sin trades activos</p>
    {% endif %}

    <h2>📜 Historial de Trades ({{ resolved_trades|length }})</h2>
    {% if resolved_trades %}
    <table>
        <tr>
            <th>#</th>
            <th>Mercado</th>
            <th>Posición</th>
            <th>Resultado</th>
            <th>PnL</th>
            <th>Fecha</th>
        </tr>
        {% for t in resolved_trades %}
        <tr>
            <td>{{ t.id }}</td>
            <td>{{ t.question[:50] }}</td>
            <td>${{ "%.2f"|format(t.position_size) }}</td>
            <td>
                <span class="badge {{ 'badge-win' if t.result == 'win' else 'badge-loss' }}">
                    {{ 'GANADO' if t.result == 'win' else 'PERDIDO' }}
                </span>
            </td>
            <td class="{{ 'green' if t.pnl >= 0 else 'red' }}">
                {{ '+' if t.pnl >= 0 else '' }}${{ "%.2f"|format(t.pnl) }}
            </td>
            <td style="color:#555">{{ t.timestamp[:16] }}</td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <p style="color:#555; padding: 15px;">Sin trades resueltos todavia</p>
    {% endif %}

    <div class="footer">Actualiza cada 30 segundos | PAPER TRADING — Sin dinero real</div>
</body>
</html>
"""

@app.route("/")
def index():
    trader = PaperTrader()
    resolved = [t for t in trader.trades if t["status"] == "resolved"]
    active = trader.active_trades

    wins = [t for t in resolved if t["result"] == "win"]
    total_pnl = sum(t["pnl"] for t in resolved) if resolved else 0
    win_rate = len(wins) / len(resolved) if resolved else 0

    stats = {
        "bankroll": trader.bankroll,
        "pnl": total_pnl,
        "win_rate": win_rate,
        "total_trades": len(resolved)
    }

    return render_template_string(
        HTML,
        stats=stats,
        active_trades=active,
        resolved_trades=resolved
    )


if __name__ == "__main__":
    app.run(debug=False, port=5000)