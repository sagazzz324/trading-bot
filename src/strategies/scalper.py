import logging
import time
import json
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

LOG_FILE = Path("logs/scalping_trades.json")

WHITELIST = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT",
    "LTCUSDT", "DOTUSDT", "ADAUSDT", "MATICUSDT",
    "NEARUSDT", "ATOMUSDT", "UNIUSDT", "AAVEUSDT",
    "INJUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT"
]


def load_state():
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            return json.load(f)
    return {
        "total_pnl": 0,
        "trades": [],
        "open_positions": [],
        "session_pnl": 0,
        "win_count": 0,
        "loss_count": 0
    }


def save_state(state):
    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE, "w") as f:
        json.dump(state, f, indent=2)


class ScalpingBot:
    def __init__(self, max_positions=3, risk_per_trade=0.01, capital=1000):
        from src.exchanges.binance_client import BinanceClient
        from src.strategies.scalping_engine import get_signal_strength
        self.get_signal_strength = get_signal_strength
        self.client = BinanceClient()
        self.max_positions = max_positions
        self.risk_per_trade = risk_per_trade
        self.capital = capital
        self.state = load_state()

def scan_pairs(self):
    print("\n🔍 Escaneando pares de calidad...")
    movers = self.client.get_top_movers(limit=100)
    movers_dict = {m["symbol"]: m for m in movers}

    candidates = []
    for sym in WHITELIST:
        if any(p["symbol"] == sym for p in self.state["open_positions"]):
            continue
        if sym in movers_dict:
            m = movers_dict[sym]
            if abs(m["change_pct"]) >= 0.1:
                candidates.append(m)
        else:
            # Si no está en movers, obtener precio directo
            price = self.client.get_price(sym)
            if price:
                candidates.append({
                    "symbol": sym,
                    "price": price,
                    "change_pct": 0,
                    "volume": 0
                })

    candidates.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    print(f"   {len(candidates)} candidatos de calidad encontrados")
    return candidates[:10]

    def analyze_pair(self, symbol):
        klines = self.client.get_klines(symbol, interval="5m", limit=100)
        if not klines or len(klines) < 30:
            return None

        signal = self.get_signal_strength(klines)
        if signal["direction"] == "none":
            return None

        rsi = signal.get("rsi", 50)
        atr_pct = signal.get("atr_pct", 0)
        momentum = signal.get("momentum", 0)

        if atr_pct > 2.0:
            print(f"   ⚠️  {symbol}: volatilidad extrema ({atr_pct:.2f}%) — saltando")
            return None

        movers = self.client.get_top_movers(limit=100)
        mover = next((m for m in movers if m["symbol"] == symbol), None)
        if mover and abs(mover["change_pct"]) > 30:
            print(f"   ⚠️  {symbol}: movimiento extremo ({mover['change_pct']:+.1f}%) — saltando")
            return None

        if signal["strength"] < 55:
            print(f"   📉 {symbol}: señal {signal['strength']}/100 — RSI:{rsi:.0f} momentum:{momentum:+.2f}% — skip")
            return None

        current_price = klines[-1]["close"]

        return {
            "symbol": symbol,
            "direction": signal["direction"],
            "strength": signal["strength"],
            "reasons": signal["reasons"],
            "price": current_price,
            "rsi": rsi,
            "momentum": momentum,
            "atr_pct": atr_pct,
            "claude_regime": "technical_only",
            "claude_risk": "medium"
        }

    def calculate_position(self, signal):
        price = signal["price"]
        atr_pct = signal.get("atr_pct", 0.1)
        position_usdt = self.capital * self.risk_per_trade * (signal["strength"] / 100)
        position_usdt = max(10, min(position_usdt, self.capital * 0.05))
        sl_pct = max(atr_pct * 1.5, 0.3) / 100
        tp_pct = sl_pct * 2.5
        if signal["direction"] == "long":
            sl_price = round(price * (1 - sl_pct), 4)
            tp_price = round(price * (1 + tp_pct), 4)
        else:
            sl_price = round(price * (1 + sl_pct), 4)
            tp_price = round(price * (1 - tp_pct), 4)
        return {
            "position_usdt": round(position_usdt, 2),
            "quantity": round(position_usdt / price, 6),
            "sl_price": sl_price,
            "tp_price": tp_price,
            "sl_pct": round(sl_pct * 100, 3),
            "tp_pct": round(tp_pct * 100, 3),
            "rr_ratio": 2.5
        }

    def open_position(self, signal):
        pos = self.calculate_position(signal)
        position = {
            "id": len(self.state["trades"]) + 1,
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "entry_price": signal["price"],
            "sl_price": pos["sl_price"],
            "tp_price": pos["tp_price"],
            "position_usdt": pos["position_usdt"],
            "quantity": pos["quantity"],
            "sl_pct": pos["sl_pct"],
            "tp_pct": pos["tp_pct"],
            "reasons": signal["reasons"],
            "strength": signal["strength"],
            "rsi": signal.get("rsi", 50),
            "regime": signal.get("claude_regime", "technical_only"),
            "timestamp": datetime.now().isoformat(),
            "status": "open"
        }
        self.state["open_positions"].append(position)
        save_state(self.state)
        direction_icon = "🟢 LONG" if signal["direction"] == "long" else "🔴 SHORT"
        print(f"\n   ✅ {direction_icon} {signal['symbol']}")
        print(f"      Entry: ${signal['price']:,.4f}")
        print(f"      SL:    ${pos['sl_price']:,.4f} (-{pos['sl_pct']}%)")
        print(f"      TP:    ${pos['tp_price']:,.4f} (+{pos['tp_pct']}%)")
        print(f"      Size:  ${pos['position_usdt']:.2f} | R:R 1:{pos['rr_ratio']}")
        print(f"      Razones: {', '.join(signal['reasons'][:3])}")
        return position

    def check_positions(self):
        if not self.state["open_positions"]:
            return
        print(f"\n📊 Revisando {len(self.state['open_positions'])} posicion(es) abierta(s)...")
        closed = []
        for pos in self.state["open_positions"]:
            symbol = pos["symbol"]
            current = self.client.get_price(symbol)
            if not current:
                continue
            entry = pos["entry_price"]
            sl = pos["sl_price"]
            tp = pos["tp_price"]
            direction = pos["direction"]
            if direction == "long":
                pnl_pct = (current - entry) / entry * 100
                hit_sl = current <= sl
                hit_tp = current >= tp
            else:
                pnl_pct = (entry - current) / entry * 100
                hit_sl = current >= sl
                hit_tp = current <= tp
            pnl_usdt = pos["position_usdt"] * (pnl_pct / 100)
            status_icon = "📈" if pnl_pct > 0 else "📉"
            print(f"   {status_icon} {symbol} {direction.upper()} | Entry ${entry:,.4f} -> ${current:,.4f} | PnL: {pnl_pct:+.2f}% (${pnl_usdt:+.2f})")
            if hit_tp:
                print(f"   🎯 TP alcanzado! +${pnl_usdt:.2f}")
                self._close_position(pos, current, "tp", pnl_usdt)
                closed.append(pos["id"])
            elif hit_sl:
                print(f"   🛑 SL alcanzado. -${abs(pnl_usdt):.2f}")
                self._close_position(pos, current, "sl", pnl_usdt)
                closed.append(pos["id"])
            else:
                if pnl_pct > 1.0:
                    if direction == "long":
                        new_sl = round(current * 0.997, 4)
                        if new_sl > pos["sl_price"]:
                            pos["sl_price"] = new_sl
                            print(f"   📐 Trailing SL -> ${new_sl:,.4f}")
                    else:
                        new_sl = round(current * 1.003, 4)
                        if new_sl < pos["sl_price"]:
                            pos["sl_price"] = new_sl
                            print(f"   📐 Trailing SL -> ${new_sl:,.4f}")
        if closed:
            self.state["open_positions"] = [
                p for p in self.state["open_positions"] if p["id"] not in closed
            ]
            save_state(self.state)

    def _close_position(self, pos, exit_price, reason, pnl_usdt):
        trade = {
            **pos,
            "exit_price": exit_price,
            "exit_reason": reason,
            "pnl_usdt": round(pnl_usdt, 4),
            "pnl_pct": round((exit_price - pos["entry_price"]) / pos["entry_price"] * 100, 3),
            "closed_at": datetime.now().isoformat(),
            "status": "closed"
        }
        self.state["trades"].append(trade)
        self.state["total_pnl"] = round(self.state["total_pnl"] + pnl_usdt, 4)
        self.state["session_pnl"] = round(self.state["session_pnl"] + pnl_usdt, 4)
        self.capital += pnl_usdt
        if pnl_usdt > 0:
            self.state["win_count"] += 1
        else:
            self.state["loss_count"] += 1
        save_state(self.state)

    def print_stats(self):
        total = self.state["win_count"] + self.state["loss_count"]
        wr = self.state["win_count"] / total * 100 if total > 0 else 0
        print(f"\n📈 ESTADISTICAS SCALPING:")
        print(f"   Capital:      ${self.capital:.2f}")
        print(f"   PnL total:    ${self.state['total_pnl']:+.4f}")
        print(f"   PnL sesion:   ${self.state['session_pnl']:+.4f}")
        print(f"   Win rate:     {wr:.1f}% ({self.state['win_count']}/{total})")
        print(f"   Posiciones:   {len(self.state['open_positions'])} abiertas")

    def run_once(self):
        print("\n" + "="*60)
        print(f"⚡ SCALPING BOT — {datetime.now().strftime('%H:%M:%S')}")
        print("="*60)
        self.check_positions()
        if len(self.state["open_positions"]) >= self.max_positions:
            print(f"\n⏸️  Maximo de posiciones ({self.max_positions}) alcanzado")
            self.print_stats()
            return
        candidates = self.scan_pairs()
        if not candidates:
            print("   Sin candidatos de calidad")
            self.print_stats()
            return
        entered = 0
        for candidate in candidates:
            if len(self.state["open_positions"]) + entered >= self.max_positions:
                break
            symbol = candidate["symbol"]
            print(f"\n🔬 Analizando {symbol} — cambio 24h: {candidate['change_pct']:+.1f}% | vol: ${candidate['volume']/1e6:.0f}M")
            signal = self.analyze_pair(symbol)
            if not signal:
                continue
            print(f"   📡 Señal: {signal['direction'].upper()} | Fuerza: {signal['strength']}/100 | RSI: {signal['rsi']:.0f}")
            print(f"   📊 Momentum: {signal['momentum']:+.2f}% | ATR: {signal['atr_pct']:.3f}%")
            self.open_position(signal)
            entered += 1
            time.sleep(0.5)
        self.print_stats()