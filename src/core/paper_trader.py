import json
import logging
import os
import traceback
import time
import requests
from datetime import datetime
from pathlib import Path
from config.settings import PAPER_TRADING
print(f"🔧 PAPER_TRADING = {PAPER_TRADING}")

logger = logging.getLogger(__name__)
POLY_POSITIONS_URL = os.getenv("POLY_POSITIONS_URL", "https://data-api.polymarket.com/positions")
POLY_POSITION_USER = os.getenv("POLYMARKET_SIGNER_ADDRESS", "")


class PaperTrader:
    def __init__(self, bankroll=1000, log_fn=None):
        self.bankroll          = bankroll
        self.initial_bankroll  = bankroll
        self.trades            = []
        self.active_trades     = []
        self.wallet_balance    = bankroll
        self.balance_source    = "paper"
        self.last_balance_sync = None
        self.log               = log_fn or (lambda msg, color="#ffffff": None)
        self.log_file          = Path("logs/paper_trades.json")
        self._balance_sync_ts  = 0.0
        self.log_file.parent.mkdir(exist_ok=True)
        self._load_state()
        self._sync_real_balance(force=True)

    def _emit(self, msg: str, color: str = "#ffffff"):
        self.log(msg, color)
        print(msg)

    def _load_state(self):
        if self.log_file.exists():
            try:
                with open(self.log_file) as f:
                    data = json.load(f)
                    self.bankroll         = data.get("bankroll",         self.bankroll)
                    self.initial_bankroll = data.get("initial_bankroll", self.initial_bankroll)
                    self.trades           = data.get("trades",           [])
                    self.active_trades    = data.get("active_trades",    [])
                    self.wallet_balance   = data.get("wallet_balance",   self.bankroll)
                    self.balance_source   = data.get("balance_source",   self.balance_source)
                    self.last_balance_sync = data.get("last_balance_sync", self.last_balance_sync)
            except Exception as e:
                logger.error(f"Error cargando estado: {e}\n{traceback.format_exc()}")

    def _sync_real_balance(self, force: bool = False) -> float:
        if PAPER_TRADING:
            self.wallet_balance = self.bankroll
            self.balance_source = "paper"
            return self.bankroll

        now = time.time()
        if not force and now - self._balance_sync_ts < 10:
            return self.wallet_balance

        try:
            from src.core.polymarket_executor import get_balance

            balance = float(get_balance())
            if balance > 0:
                self.wallet_balance = balance
                self.bankroll = balance
                self.balance_source = "executor"
                self.last_balance_sync = datetime.now().isoformat()
                self._balance_sync_ts = now

                if not self.trades and not self.active_trades:
                    self.initial_bankroll = balance
        except Exception as e:
            logger.error(f"_sync_real_balance: {e}\n{traceback.format_exc()}")

        return self.wallet_balance

    def load_state(self):
        self._load_state()
        self._sync_real_balance()
        return {
            "bankroll":         self.bankroll,
            "initial_bankroll": self.initial_bankroll,
            "trades":           self.trades,
            "active_trades":    self.active_trades,
            "wallet_balance":   self.wallet_balance,
            "balance_source":   self.balance_source,
            "last_balance_sync": self.last_balance_sync,
        }

    def _save_state(self):
        try:
            with open(self.log_file, "w") as f:
                json.dump({
                    "bankroll":         self.bankroll,
                    "initial_bankroll": self.initial_bankroll,
                    "wallet_balance":   self.wallet_balance,
                    "balance_source":   self.balance_source,
                    "last_balance_sync": self.last_balance_sync,
                    "trades":           self.trades,
                    "active_trades":    self.active_trades
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando estado: {e}\n{traceback.format_exc()}")

    def _estimate_share_size(self, position_size: float, price: float) -> float:
        if price <= 0:
            return 0.0
        # Dejamos margen para fees/redondeos y evitar SELL rechazadas por exceso de size.
        return round((position_size / price) * 0.90, 6)

    def _extract_share_size(self, resp: dict | None, position_size: float, price: float) -> float:
        fallback = self._estimate_share_size(position_size, price)
        if not isinstance(resp, dict):
            return fallback

        for container in (resp, resp.get("order_info"), resp.get("resp")):
            if not isinstance(container, dict):
                continue
            for key in ("size_matched", "matched_size", "filled_size", "filledSize"):
                value = container.get(key)
                try:
                    if value is not None and float(value) > 0:
                        raw = float(value)
                        if raw > 100000:
                            raw = raw / 1e6
                        return round(raw, 6)
                except Exception:
                    pass

        return fallback

    def _is_successful_order_response(self, resp: dict | None) -> bool:
        if not isinstance(resp, dict):
            return False
        if not resp.get("ok"):
            return False
        nested = resp.get("resp")
        if isinstance(nested, dict):
            if nested.get("success") is False:
                return False
            if nested.get("errorMsg"):
                return False
        return True

    def _get_live_share_balance(self, token_id: str) -> float:
        try:
            from src.core.polymarket_executor import get_token_balance

            live_balance = float(get_token_balance(token_id))
            if live_balance > 0:
                return round(live_balance, 6)
        except Exception as e:
            logger.error(f"_get_live_share_balance: {e}\n{traceback.format_exc()}")
        return 0.0

    def _find_live_position(self, trade: dict) -> dict | None:
        if not POLY_POSITION_USER:
            return None
        condition_id = trade.get("condition_id")
        direction = (trade.get("direction") or "").lower()
        if not condition_id or direction not in ("up", "down"):
            return None
        try:
            r = requests.get(
                POLY_POSITIONS_URL,
                params={"user": POLY_POSITION_USER, "sizeThreshold": 0},
                timeout=10,
            )
            rows = r.json()
            expected_outcome = "Up" if direction == "up" else "Down"
            matches = [
                row for row in rows
                if str(row.get("conditionId", "")).lower() == str(condition_id).lower()
                and str(row.get("outcome", "")).lower() == expected_outcome.lower()
                and float(row.get("size", 0) or 0) > 0
            ]
            if not matches:
                return None
            matches.sort(key=lambda row: float(row.get("size", 0) or 0), reverse=True)
            return matches[0]
        except Exception as e:
            logger.error(f"_find_live_position: {e}\n{traceback.format_exc()}")
            return None

    def _place_real_exit(self, trade: dict, exit_price: float | None = None) -> dict | None:
        try:
            from src.core.polymarket_executor import place_market_order, redeem_position

            token_id = trade.get("token_id") or trade.get("market_id")
            live_size = self._get_live_share_balance(token_id) if token_id else 0.0
            live_position = None
            if live_size <= 0:
                live_position = self._find_live_position(trade)
                if live_position:
                    token_id = str(live_position.get("asset") or token_id)
                    live_size = float(live_position.get("size", 0) or 0)

            if live_position is None:
                live_position = self._find_live_position(trade)

            if live_position and live_position.get("redeemable"):
                condition_id = trade.get("condition_id")
                self._emit(
                    f"🪙 Redeem executor: condition={str(condition_id)[:20]}... "
                    f"outcome={live_position.get('outcome')} value={float(live_position.get('currentValue', 0) or 0):.4f}",
                    "#41d6fc"
                )
                resp = redeem_position(condition_id)
                self._emit(f"📦 Respuesta redeem executor: {resp}", "#ffffff60")
                return resp

            size = live_size
            if size <= 0:
                size = float(trade.get("share_size", 0) or 0)
            if size <= 0:
                entry_price = float(trade.get("entry_price", 0) or 0)
                position_size = float(trade.get("position_size", 0) or 0)
                if entry_price > 0:
                    size = round(position_size / entry_price, 6)
            if size > 0:
                size = round(size * 0.995, 6)
            price = 0.0

            if not token_id or size <= 0:
                logger.error(f"_place_real_exit inválido: token_id={token_id} size={size}")
                return None

            trade["token_id"] = token_id
            self._emit(f"🔁 Exit executor: token={token_id[:20]}... size={size:.2f} price={price:.3f} live_balance={live_size:.4f}", "#41d6fc")
            resp = place_market_order(
                token_id=token_id,
                side="SELL",
                amount_usdc=size,
                price=price,
                order_type="FAK",
            )
            self._emit(f"📦 Respuesta exit executor: {resp}", "#ffffff60")
            return resp
        except Exception as e:
            logger.error(f"_place_real_exit: {e}\n{traceback.format_exc()}")
            self._emit(f"❌ ERROR EXIT REAL: {e}", "#FF5050")
            return None

    def place_trade(self, market_id, question, true_prob, market_prob, ev, position_size,
                    price=0.51, condition_id: str | None = None, direction: str | None = None):
        if not PAPER_TRADING:
            self._sync_real_balance(force=True)
        if position_size <= 0:
            logger.warning("Tamaño de posición inválido")
            return None
        if position_size > self.bankroll:
            logger.warning(f"Sin suficiente bankroll. Disponible: ${self.bankroll:.2f}")
            self._emit(f"❌ Sin suficiente bankroll. Disponible: ${self.bankroll:.2f}", "#FF5050")
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
            "condition_id":  condition_id,
            "direction":     direction,
            "entry_price":   round(price, 4),
            "entry_value":   round(position_size * price, 4),
            "share_size":    self._estimate_share_size(position_size, price),
            "close_order_id": None,
            "exit_price":    None,
        }

        # ── REAL TRADING ──────────────────────────────────────────────────────
        if not PAPER_TRADING:
            try:
                from src.core.polymarket_executor import place_market_order
                exec_price = 0.0
                self._emit(f"🔄 Executor: token={market_id[:20]}... amount={position_size:.2f} price=auto (hint={price:.3f})", "#41d6fc")
                resp = place_market_order(
                    token_id=market_id,
                    side="BUY",
                    amount_usdc=position_size,
                    price=exec_price,
                    order_type="FAK",
                )
                self._emit(f"📦 Respuesta executor: {resp}", "#ffffff60")
                if not self._is_successful_order_response(resp):
                    logger.error("Orden real fallida — no se ejecutó")
                    self._emit(f"❌ Orden real fallida — {resp}", "#FF5050")
                    return None
                trade["order_id"] = resp.get("orderID") or resp.get("id", "")
                trade["share_size"] = self._extract_share_size(resp, position_size, price)
                trade["token_id"] = (
                    (resp.get("order_info") or {}).get("asset_id")
                    or market_id
                )
                logger.info(f"Orden real ejecutada: orderID={trade['order_id']} shares={trade['share_size']}")
                self._emit(f"✅ Orden real OK: orderID={trade['order_id']} shares={trade['share_size']}", "#00E887")
            except Exception as e:
                logger.error(f"Error ejecutando orden real: {e}\n{traceback.format_exc()}")
                self._emit(f"❌ ERROR ORDEN REAL: {e}", "#FF5050")
                return None

        if PAPER_TRADING:
            self.bankroll -= position_size
            self.wallet_balance = self.bankroll
        else:
            self._sync_real_balance(force=True)
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

    def resolve_trade(self, trade_id, outcome, exit_price: float | None = None):
        trade = next((t for t in self.active_trades if t["id"] == trade_id), None)
        if not trade:
            logger.error(f"resolve_trade: Trade #{trade_id} no encontrado")
            return False

        position    = trade["position_size"]
        market_prob = trade["market_prob"]
        entered_at  = trade.get("timestamp", "")

        exit_resp = None
        if not PAPER_TRADING and exit_price is not None:
            exit_resp = self._place_real_exit(trade, exit_price=exit_price)
            if not self._is_successful_order_response(exit_resp):
                logger.error(f"resolve_trade: no se pudo cerrar trade #{trade_id} en real")
                return False

        if outcome:
            payout = position / market_prob
            pnl    = payout - position
        else:
            pnl = -position

        if PAPER_TRADING and outcome:
            self.bankroll += payout

        updates = {
            "status": "resolved",
            "result": "win" if outcome else "loss",
            "pnl":    round(pnl, 2),
            "settlement": "paper" if PAPER_TRADING else ("live_sell" if exit_resp else "estimated_only"),
            "exit_price": round(exit_price, 4) if exit_price is not None else trade.get("exit_price"),
            "close_order_id": (exit_resp or {}).get("orderID") or (exit_resp or {}).get("id") or trade.get("close_order_id"),
        }

        trade.update(updates)
        self._sync_trade_in_history(trade_id, updates)
        self.active_trades = [t for t in self.active_trades if t["id"] != trade_id]
        if not PAPER_TRADING:
            self._sync_real_balance(force=True)
        self._save_state()

        duration = self._calc_duration(entered_at)
        try:
            from src.core.equity_tracker import record_trade
            record_trade(trade_id, round(pnl, 2), self.bankroll, duration)
        except Exception as e:
            logger.error(f"equity_tracker error: {e}\n{traceback.format_exc()}")

        logger.info(f"Trade #{trade_id} resuelto: {'WIN' if outcome else 'LOSS'} PnL ${pnl:.2f}")
        return True

    def resolve_trade_with_pnl(self, trade_id, pnl, exit_price: float | None = None):
        trade = next((t for t in self.active_trades if t["id"] == trade_id), None)
        if not trade:
            logger.error(f"resolve_trade_with_pnl: Trade #{trade_id} no encontrado")
            return False

        entered_at = trade.get("timestamp", "")
        exit_resp = None
        if not PAPER_TRADING:
            exit_resp = self._place_real_exit(trade, exit_price=exit_price)
            if not self._is_successful_order_response(exit_resp):
                logger.error(f"resolve_trade_with_pnl: no se pudo cerrar trade #{trade_id} en real")
                return False

        updates = {
            "status": "resolved",
            "result": "win" if pnl >= 0 else "loss",
            "pnl":    round(pnl, 2),
            "settlement": "paper" if PAPER_TRADING else "live_sell",
            "exit_price": round(exit_price, 4) if exit_price is not None else trade.get("exit_price"),
            "close_order_id": (exit_resp or {}).get("orderID") or (exit_resp or {}).get("id") or trade.get("close_order_id"),
        }

        trade.update(updates)
        self._sync_trade_in_history(trade_id, updates)
        if PAPER_TRADING:
            self.bankroll += trade["position_size"] + pnl
        self.active_trades = [t for t in self.active_trades if t["id"] != trade_id]
        if not PAPER_TRADING:
            self._sync_real_balance(force=True)
        self._save_state()

        duration = self._calc_duration(entered_at)
        try:
            from src.core.equity_tracker import record_trade
            record_trade(trade_id, round(pnl, 2), self.bankroll, duration)
        except Exception as e:
            logger.error(f"equity_tracker error: {e}\n{traceback.format_exc()}")

        logger.info(f"Trade #{trade_id} cerrado: {'WIN' if pnl >= 0 else 'LOSS'} PnL ${pnl:.2f} | Bankroll ${self.bankroll:.2f}")
        return True

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
        self.wallet_balance   = bankroll
        self.balance_source   = "paper" if PAPER_TRADING else self.balance_source
        self.last_balance_sync = None
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
            "bankroll_actual": f"${self.wallet_balance if not PAPER_TRADING else self.bankroll:.2f}",
            "drawdown":        f"{drawdown:.1%}",
            "mode":            "REAL" if not PAPER_TRADING else "PAPER"
        }
