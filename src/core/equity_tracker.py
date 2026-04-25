"""
equity_tracker.py — Métricas avanzadas de performance
Se llama desde paper_trader.py después de cada trade resuelto.
Guarda todo en logs/equity.json
"""
import json
import logging
import traceback
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

EQUITY_FILE = Path("logs/equity.json")


def _now_ar() -> str:
    ar = datetime.now(timezone(timedelta(hours=-3)))
    return ar.isoformat()


def _load() -> dict:
    if EQUITY_FILE.exists():
        try:
            with open(EQUITY_FILE) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"equity_tracker _load: {e}\n{traceback.format_exc()}")
    return _empty()


def _save(data: dict):
    try:
        EQUITY_FILE.parent.mkdir(exist_ok=True)
        with open(EQUITY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"equity_tracker _save: {e}\n{traceback.format_exc()}")


def _empty() -> dict:
    return {
        # Curva de equity
        "equity_curve": [],          # [{ts, balance, trade_id, pnl}]
        "trade_ledger": [],          # [{...detalle por trade...}]

        # Drawdown
        "peak_balance":      1000.0,
        "peak_ts":           "",
        "current_drawdown":  0.0,    # % desde el pico actual
        "max_drawdown":      0.0,    # % histórico máximo
        "max_dd_peak":       0.0,
        "max_dd_trough":     0.0,
        "max_dd_ts":         "",

        # Totales
        "total_trades":      0,
        "total_pnl":         0.0,
        "sum_wins":          0.0,    # suma de todos los PnL positivos
        "sum_losses":        0.0,    # suma absoluta de todos los PnL negativos
        "count_wins":        0,
        "count_losses":      0,

        # Métricas calculadas (se actualizan en cada trade)
        "win_rate":          0.0,
        "profit_factor":     0.0,    # sum_wins / sum_losses
        "expectancy":        0.0,    # (wr * avg_win) - (lr * avg_loss)
        "avg_win":           0.0,
        "avg_loss":          0.0,
        "avg_pnl_per_trade": 0.0,
        "recovery_factor":   0.0,    # total_pnl / max_drawdown_$

        # Rachas
        "current_streak":       0,   # positivo = wins, negativo = losses
        "max_win_streak":       0,
        "max_loss_streak":      0,
        "current_streak_type":  "",  # "win" | "loss" | ""

        # Tiempo en mercado
        "total_time_in_market": 0,   # segundos totales en posición
        "avg_trade_duration":   0.0, # segundos promedio por trade

        # Volatilidad del equity
        "equity_returns":    [],     # lista de retornos % para calcular std
        "equity_volatility": 0.0,    # std dev de los retornos
        "avg_entry_slippage": 0.0,
        "avg_realized_return": 0.0,
        "count_flats":       0,

        # Metadata
        "initial_balance":   1000.0,
        "last_updated":      "",
    }


def record_trade(trade_id: int, pnl: float, balance_after: float,
                 duration_seconds: float = 0.0, trade_data: dict | None = None):
    """
    Llamar después de cada trade resuelto.
    trade_id: ID del trade
    pnl: PnL del trade (positivo = win, negativo = loss)
    balance_after: bankroll después del trade
    duration_seconds: cuánto tiempo estuvo abierto el trade
    """
    try:
        data = _load()
        ts   = _now_ar()

        # ── Curva de equity ──────────────────────────────────────────────
        prev_balance = data["equity_curve"][-1]["balance"] if data["equity_curve"] else data["initial_balance"]
        data["equity_curve"].append({
            "ts":       ts,
            "balance":  round(balance_after, 2),
            "trade_id": trade_id,
            "pnl":      round(pnl, 2),
        })
        # Mantener últimos 2000 puntos para no inflar el archivo
        if len(data["equity_curve"]) > 2000:
            data["equity_curve"] = data["equity_curve"][-2000:]

        if trade_data:
            ledger_item = {
                "ts": ts,
                "trade_id": trade_id,
                "pnl": round(pnl, 2),
                "balance_after": round(balance_after, 2),
                "question": trade_data.get("question"),
                "direction": trade_data.get("direction"),
                "result": trade_data.get("result"),
                "entry_price_hint": trade_data.get("entry_price_hint"),
                "entry_price_real": trade_data.get("entry_price_real"),
                "entry_slippage": trade_data.get("entry_slippage"),
                "exit_price_real": trade_data.get("exit_price_real"),
                "filled_entry_usdc": trade_data.get("filled_entry_usdc"),
                "filled_exit_usdc": trade_data.get("filled_exit_usdc"),
                "wallet_balance_before_entry": trade_data.get("wallet_balance_before_entry"),
                "wallet_balance_after_exit": trade_data.get("wallet_balance_after_exit"),
                "share_size": trade_data.get("share_size"),
                "settlement": trade_data.get("settlement"),
            }
            data["trade_ledger"].append(ledger_item)
            if len(data["trade_ledger"]) > 500:
                data["trade_ledger"] = data["trade_ledger"][-500:]

        # ── Totales ──────────────────────────────────────────────────────
        data["total_trades"] += 1
        data["total_pnl"]     = round(data["total_pnl"] + pnl, 2)

        is_win = pnl > 0
        is_flat = pnl == 0
        if is_win:
            data["sum_wins"]    = round(data["sum_wins"] + pnl, 2)
            data["count_wins"] += 1
        elif is_flat:
            data["count_flats"] += 1
        else:
            data["sum_losses"]    = round(data["sum_losses"] + abs(pnl), 2)
            data["count_losses"] += 1

        # ── Drawdown ─────────────────────────────────────────────────────
        if balance_after > data["peak_balance"]:
            data["peak_balance"] = round(balance_after, 2)
            data["peak_ts"]      = ts

        if data["peak_balance"] > 0:
            dd_pct = (data["peak_balance"] - balance_after) / data["peak_balance"] * 100
            data["current_drawdown"] = round(dd_pct, 2)

            if dd_pct > data["max_drawdown"]:
                data["max_drawdown"]  = round(dd_pct, 2)
                data["max_dd_peak"]   = data["peak_balance"]
                data["max_dd_trough"] = round(balance_after, 2)
                data["max_dd_ts"]     = ts

        # ── Métricas derivadas ───────────────────────────────────────────
        n       = data["total_trades"]
        n_wins  = data["count_wins"]
        n_loss  = data["count_losses"]
        wr      = n_wins / n if n > 0 else 0.0
        lr      = n_loss / n if n > 0 else 0.0

        avg_win  = data["sum_wins"]   / n_wins if n_wins > 0 else 0.0
        avg_loss = data["sum_losses"] / n_loss if n_loss > 0 else 0.0

        data["win_rate"]          = round(wr * 100, 2)
        data["avg_win"]           = round(avg_win, 2)
        data["avg_loss"]          = round(avg_loss, 2)
        data["avg_pnl_per_trade"] = round(data["total_pnl"] / n, 2) if n > 0 else 0.0

        # Profit factor
        data["profit_factor"] = round(data["sum_wins"] / data["sum_losses"], 3) \
            if data["sum_losses"] > 0 else 999.0

        # Expectancy: cuánto ganás en promedio por dólar arriesgado
        data["expectancy"] = round((wr * avg_win) - (lr * avg_loss), 2)

        # Recovery factor: total_pnl / max_drawdown en $
        max_dd_dollars = data["max_dd_peak"] - data["max_dd_trough"]
        data["recovery_factor"] = round(data["total_pnl"] / max_dd_dollars, 2) \
            if max_dd_dollars > 0 else 0.0

        # ── Rachas ───────────────────────────────────────────────────────
        if is_win:
            if data["current_streak_type"] == "win":
                data["current_streak"] += 1
            else:
                data["current_streak"]      = 1
                data["current_streak_type"] = "win"
            data["max_win_streak"] = max(data["max_win_streak"], data["current_streak"])
        else:
            if data["current_streak_type"] == "loss":
                data["current_streak"] += 1
            else:
                data["current_streak"]      = 1
                data["current_streak_type"] = "loss"
            data["max_loss_streak"] = max(data["max_loss_streak"], data["current_streak"])

        # ── Tiempo en mercado ────────────────────────────────────────────
        data["total_time_in_market"] += int(duration_seconds)
        data["avg_trade_duration"]    = round(
            data["total_time_in_market"] / n, 1
        ) if n > 0 else 0.0

        # ── Volatilidad del equity ───────────────────────────────────────
        if prev_balance > 0:
            ret = (balance_after - prev_balance) / prev_balance * 100
            data["equity_returns"].append(round(ret, 4))
            # Mantener últimos 200 retornos
            if len(data["equity_returns"]) > 200:
                data["equity_returns"] = data["equity_returns"][-200:]

        if len(data["equity_returns"]) >= 2:
            returns = data["equity_returns"]
            mean    = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / len(returns)
            data["equity_volatility"] = round(math.sqrt(variance), 4)

        ledger = data.get("trade_ledger", [])
        if ledger:
            slips = [float(t["entry_slippage"]) for t in ledger if t.get("entry_slippage") is not None]
            realized_returns = []
            for t in ledger:
                entry_usdc = float(t.get("filled_entry_usdc") or 0)
                exit_usdc = float(t.get("filled_exit_usdc") or 0)
                if entry_usdc > 0 and exit_usdc > 0:
                    realized_returns.append((exit_usdc - entry_usdc) / entry_usdc * 100)
            data["avg_entry_slippage"] = round(sum(slips) / len(slips), 4) if slips else 0.0
            data["avg_realized_return"] = round(sum(realized_returns) / len(realized_returns), 4) if realized_returns else 0.0

        data["last_updated"] = ts
        _save(data)
        logger.debug(f"equity_tracker: trade #{trade_id} PnL={pnl:.2f} balance={balance_after:.2f} "
                     f"WR={data['win_rate']}% PF={data['profit_factor']} DD={data['current_drawdown']}%")

    except Exception as e:
        logger.error(f"equity_tracker record_trade: {e}\n{traceback.format_exc()}")


def get_summary() -> dict:
    """Retorna un resumen de las métricas actuales."""
    data = _load()
    return {
        "balance":            data["equity_curve"][-1]["balance"] if data["equity_curve"] else data["initial_balance"],
        "total_pnl":          data["total_pnl"],
        "total_trades":       data["total_trades"],
        "win_rate":           data["win_rate"],
        "profit_factor":      data["profit_factor"],
        "expectancy":         data["expectancy"],
        "avg_win":            data["avg_win"],
        "avg_loss":           data["avg_loss"],
        "avg_pnl_per_trade":  data["avg_pnl_per_trade"],
        "max_drawdown_pct":   data["max_drawdown"],
        "current_drawdown":   data["current_drawdown"],
        "peak_balance":       data["peak_balance"],
        "recovery_factor":    data["recovery_factor"],
        "max_win_streak":     data["max_win_streak"],
        "max_loss_streak":    data["max_loss_streak"],
        "current_streak":     f"{data['current_streak']} {data['current_streak_type']}",
        "avg_trade_duration": f"{data['avg_trade_duration']:.0f}s",
        "equity_volatility":  data["equity_volatility"],
        "avg_entry_slippage": data.get("avg_entry_slippage", 0.0),
        "avg_realized_return": data.get("avg_realized_return", 0.0),
        "count_flats":        data.get("count_flats", 0),
        "trade_ledger":       data.get("trade_ledger", [])[-20:][::-1],
        "last_updated":       data["last_updated"],
    }


def reset(initial_balance: float = 1000.0):
    """Reset completo del tracker."""
    data = _empty()
    data["initial_balance"] = initial_balance
    data["peak_balance"]    = initial_balance
    data["last_updated"]    = _now_ar()
    _save(data)
    logger.info(f"equity_tracker reseteado — balance inicial ${initial_balance}")
