import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from config.settings import PAPER_TRADING

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
                logger.error(f"Error cargando estado: {e}\n{traceback.format_exc()}")

    def load_state(self):
        self._load_state()
        return {
            "bankroll":         self.bankroll,
            "initial_bankroll": self.initial_bankroll,
            "trades":           self.trades,
            "active_trades":    self.active_trades
        }

    def _save_state(self):
        try:
            with open(self.log_file, "w") as f:
                json.dump({
                    "bankroll":         self.bankroll,
                    "initial_bankroll": self.initial_bankroll,
                    "trades":           self.trades,
                    "active_trades":    self.active_trades
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando estado: {e}\n{traceback.format_exc()}")

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
            "pnl":           None,
            "real":          not PAPER_TRADING,
            "order_id":      None,
            "token_id":      None,
        }

        # ── REAL TRADING ──────────────────────────────────────────────────────
        if not PAPER_TRADING:
            print(f"🔴 Ejecutando orden REAL: ${position_size:.2f} en {market_id[:20]}")
            try:
                from src.core.polymarket_executor import place_market_order, get_balance
                real_balance = get_balance()
                if real_balance < position_size:
                    logger.warning(f"Balance real insuficiente: ${real_balance:.2f} < ${position_size:.2f}")
                    return None
                resp = place_market_order(
                    token_id=market_id,
                    side="BUY",
                    amount_usdc=position_size
                )
                if not resp:
                    logger.error("Orden real fallida — no se ejecutó")
                    return None
                trade["order_id"] = resp.get("orderID") or resp.get("id", "")
                trade["token_id"] = market_id
                logger.info(f"Orden real ejecutada: {resp}")
            except Exception as e:
                logger.error(f"Error ejecutando orden real: {e}\n{traceback.format_exc()}")
                return None

        self.bankroll -= position_size
        self.active_trades.append(trade)
        self.trades.append(trade)
        self._save_state()

        mode = "🔴 REAL" if not PAPER_TRADING else "📄 PAPER"
        logger.info(f"{mode} Trade #{trade['id']}: ${position_size:.2f} en '{question[:50]}'")
        return trade

    def _sync_trade_in_history(self, trade_id: int, updates: dict):
        for t in self.trades:
            if t["id"] == trade_id:
                t.update(updates)
                return True
        logger.error(f"_sync_trade_in_history: Trade #{trade_id} no encontrado")
        return False

    def resolve_trade(self, trade_id, outcome):
        """Resuelve un trade por resultado binario (True=win, False=loss)."""
        trade = next((t for t in self.active_trades if t["id"] == trade_id), None)
        if not trade:
            logger.error(f"resolve_trade: Trade #{trade_id} no encontrado")
            return

        position    = trade["position_size"]
        market_prob = trade["market_prob"]
        entered_at  = trade.get("timestamp", "")

        if outcome:
            payout = position / market_prob
            pnl    = payout - position
            self.bankroll += payout
        else:
            pnl = -position

        updates = {
            "status": "resolved",
            "result": "win" if outcome else "loss",
            "pnl":    round(pnl, 2)
        }

        trade.update(updates)
        self._sync_trade_in_history(trade_id, updates)
        self.active_trades = [t for t in self.active_trades if t["id"] != trade_id]
        self._save_state()

        duration = self._calc_duration(entered_at)
        try:
            from src.core.equity_tracker import record_trade
            record_trade(trade_id, round(pnl, 2), self.bankroll, duration)
        except Exception as e:
            logger.error(f"equity_tracker error: {e}\n{traceback.format_exc()}")

        logger.info(f"Trade #{trade_id} resuelto: {'WIN' if outcome else 'LOSS'} PnL ${pnl:.2f}")

    def resolve_trade_with_pnl(self, trade_id, pnl):
        """Cierra un trade con PnL calculado externamente."""
        trade = next((t for t in self.active_trades if t["id"] == trade_id), None)
        if not trade:
            logger.error(f"resolve_trade_with_pnl: Trade #{trade_id} no encontrado")
            return

        entered_at = trade.get("timestamp", "")

        updates = {
            "status": "resolved",
            "result": "win" if pnl >= 0 else "loss",
            "pnl":    round(pnl, 2)
        }

        trade.update(updates)
        self._sync_trade_in_history(trade_id, updates)
        self.bankroll += trade["position_size"] + pnl
        self.active_trades = [t for t in self.active_trades if t["id"] != trade_id]
        self._save_state()

        duration = self._calc_duration(entered_at)
        try:
            from src.core.equity_tracker import record_trade
            record_trade(trade_id, round(pnl, 2), self.bankroll, duration)
        except Exception as e:
            logger.error(f"equity_tracker error: {e}\n{traceback.format_exc()}")

        logger.info(f"Trade #{trade_id} cerrado: {'WIN' if pnl >= 0 else 'LOSS'} PnL ${pnl:.2f} | Bankroll ${self.bankroll:.2f}")

    def _calc_duration(self, timestamp_str: str) -> float:
        try:
            from datetime import timezone
            opened = datetime.fromisoformat(timestamp_str)
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - opened).total_seconds()
        except Exception:
            return 0.0

    def force_close_stale_trades(self, pnl_per_trade=0.0):
        stale = list(self.active_trades)
        if not stale:
            return 0
        for trade in stale:
            self.resolve_trade_with_pnl(trade["id"], pnl_per_trade)
        return len(stale)

    def reset(self, bankroll=1000):
        self.bankroll         = bankroll
        self.initial_bankroll = bankroll
        self.trades           = []
        self.active_trades    = []
        self._save_state()
        try:
            from src.core.equity_tracker import reset as eq_reset
            eq_reset(bankroll)
        except Exception as e:
            logger.error(f"equity_tracker reset error: {e}")
        logger.info(f"PaperTrader reseteado — bankroll ${bankroll}")

    def get_stats(self):
        resolved = [t for t in self.trades if t.get("status") == "resolved"]
        if not resolved:
            return {"mensaje": "Sin trades resueltos todavía"}

        wins      = [t for t in resolved if t.get("result") == "win"]
        total_pnl = sum(t.get("pnl", 0) for t in resolved)
        win_rate  = len(wins) / len(resolved)
        drawdown  = (self.initial_bankroll - self.bankroll) / self.initial_bankroll

        return {
            "total_trades":    len(resolved),
            "win_rate":        f"{win_rate:.1%}",
            "total_pnl":       f"${total_pnl:.2f}",
            "bankroll_actual": f"${self.bankroll:.2f}",
            "drawdown":        f"{drawdown:.1%}",
            "mode":            "REAL" if not PAPER_TRADING else "PAPER"
        }