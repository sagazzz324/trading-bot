import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class PaperTrader:
    def __init__(self, bankroll=1000):
        self.bankroll          = bankroll
        self.initial_bankroll  = bankroll
        self.trades            = []
        self.active_trades     = []
        self.log_file          = Path("logs/paper_trades.json")
        self.log_file.parent.mkdir(exist_ok=True)
        self._load_state()

    def _load_state(self):
        if self.log_file.exists():
            try:
                with open(self.log_file) as f:
                    data = json.load(f)
                    self.bankroll         = data.get("bankroll",         self.bankroll)
                    self.initial_bankroll = data.get("initial_bankroll", self.initial_bankroll)
                    self.trades           = data.get("trades",           [])
                    self.active_trades    = data.get("active_trades",    [])
            except Exception as e:
                logger.error(f"Error cargando estado: {e}")

    def load_state(self):
        self._load_state()
        return {
            "bankroll":         self.bankroll,
            "initial_bankroll": self.initial_bankroll,
            "trades":           self.trades,
            "active_trades":    self.active_trades
        }

    def _save_state(self):
        with open(self.log_file, "w") as f:
            json.dump({
                "bankroll":         self.bankroll,
                "initial_bankroll": self.initial_bankroll,
                "trades":           self.trades,
                "active_trades":    self.active_trades
            }, f, indent=2)

    def place_trade(self, market_id, question, true_prob, market_prob, ev, position_size):
        if position_size <= 0:
            logger.warning("Tamaño de posición inválido")
            return None
        if position_size > self.bankroll:
            logger.warning(f"Sin suficiente bankroll. Disponible: ${self.bankroll:.2f}")
            return None

        trade = {
            "id":            len(self.trades) + 1,
            "timestamp":     datetime.now().isoformat(),
            "market_id":     market_id,
            "question":      question,
            "true_prob":     true_prob,
            "market_prob":   market_prob,
            "ev":            ev,
            "position_size": position_size,
            "status":        "active",
            "result":        None,
            "pnl":           None
        }

        self.bankroll -= position_size
        self.active_trades.append(trade)
        self.trades.append(trade)
        self._save_state()

        logger.info(f"Trade #{trade['id']} colocado: ${position_size:.2f} en '{question[:50]}'")
        print(f"\n✅ TRADE SIMULADO")
        print(f"   Mercado: {question[:60]}")
        print(f"   Prob: {true_prob:.1%} | Precio: {market_prob:.1%} | EV: {ev:.4f}")
        print(f"   Posición: ${position_size:.2f} | Bankroll: ${self.bankroll:.2f}")
        return trade

    def resolve_trade(self, trade_id, outcome):
        """Resuelve un trade por resultado binario (True=win, False=loss)."""
        trade = next((t for t in self.active_trades if t["id"] == trade_id), None)
        if not trade:
            logger.error(f"Trade #{trade_id} no encontrado")
            return

        position   = trade["position_size"]
        market_prob = trade["market_prob"]

        if outcome:
            payout = position / market_prob
            pnl    = payout - position
            self.bankroll += payout
        else:
            pnl = -position

        trade["status"] = "resolved"
        trade["result"] = "win" if outcome else "loss"
        trade["pnl"]    = round(pnl, 2)
        self.active_trades = [t for t in self.active_trades if t["id"] != trade_id]
        self._save_state()

        print(f"\n{'🟢 GANADO' if outcome else '🔴 PERDIDO'} - Trade #{trade_id}")
        print(f"   PnL: ${pnl:.2f} | Bankroll: ${self.bankroll:.2f}")

    def resolve_trade_with_pnl(self, trade_id, pnl):
        """Cierra un trade con PnL calculado externamente (salida anticipada)."""
        trade = next((t for t in self.active_trades if t["id"] == trade_id), None)
        if not trade:
            logger.error(f"resolve_trade_with_pnl: Trade #{trade_id} no encontrado")
            return

        trade["status"] = "resolved"
        trade["result"] = "win" if pnl >= 0 else "loss"
        trade["pnl"]    = round(pnl, 2)
        self.bankroll  += trade["position_size"] + pnl
        self.active_trades = [t for t in self.active_trades if t["id"] != trade_id]
        self._save_state()

        logger.info(f"Trade #{trade_id} cerrado anticipado: PnL ${pnl:.2f}")

    def get_stats(self):
        resolved = [t for t in self.trades if t["status"] == "resolved"]
        if not resolved:
            return {"mensaje": "Sin trades resueltos todavía"}

        wins      = [t for t in resolved if t["result"] == "win"]
        total_pnl = sum(t["pnl"] for t in resolved)
        win_rate  = len(wins) / len(resolved)
        drawdown  = (self.initial_bankroll - self.bankroll) / self.initial_bankroll

        return {
            "total_trades":   len(resolved),
            "win_rate":       f"{win_rate:.1%}",
            "total_pnl":      f"${total_pnl:.2f}",
            "bankroll_actual": f"${self.bankroll:.2f}",
            "drawdown":       f"{drawdown:.1%}"
        }