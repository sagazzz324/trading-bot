import logging
import time
from src.core.market_data import get_markets, format_market
from src.core.paper_trader import PaperTrader
from src.core.probability_engine import estimate_probability
from src.core.risk_engine import kelly_position_size, check_risk_rules, check_stop_loss, check_concentration
from src.core.resolver import auto_resolve_trades
from src.skills.whale_tracker import get_whale_signals
from src.skills.news_fetcher import get_news_context

logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(self):
        self.trader = PaperTrader()

    def run_once(self):
        state = self.trader.load_state()          # FIX: usar método público
        bankroll   = state["bankroll"]
        active     = state["active_trades"]
        all_trades = state["trades"]

        print("\n" + "="*50)
        print("🤖 CICLO DEL BOT - PAPER TRADING")
        print("="*50)
        print(f"   Bankroll: ${bankroll:.2f} | Trades activos: {len(active)}")

        # 1. Stop losses
        print("\n🛡️  Revisando stop losses...")
        for trade in list(active):
            result = estimate_probability(trade["question"], trade["market_prob"])
            if not result:
                continue
            current_prob = result["probability"]
            if check_stop_loss(trade, current_prob):
                print(f"   🛑 STOP LOSS: {trade['question'][:50]}")
                self.trader.resolve_trade(trade["id"], False)
                state = self.trader.load_state()
                active = state["active_trades"]

        # 2. Riesgo global
        resolved = [t for t in all_trades if t["status"] == "resolved"]
        wins = [t for t in resolved if t["result"] == "win"]
        drawdown = (state["initial_bankroll"] - bankroll) / state["initial_bankroll"]
        win_rate = len(wins) / len(resolved) if resolved else 0

        risk_ok, risk_msg = check_risk_rules(drawdown, len(active))
        if not risk_ok:
            print(f"\n🚨 RIESGO — no operar: {risk_msg}")
            return

        # 3. Escanear mercados
        print("\n🔍 Escaneando mercados...")
        raw_markets = get_markets(limit=20)           # FIX: función correcta
        if not raw_markets:
            print("   Sin mercados disponibles")
            return

        markets = []
        for m in raw_markets:
            fm = format_market(m)
            if 0.03 < fm["precio"] < 0.97 and fm["volumen"] > 100000:
                markets.append({
                    "question": fm["pregunta"],
                    "price": fm["precio"],
                    "volume": fm["volumen"],
                    "id": fm["id"]
                })

        whale_signals = get_whale_signals()
        print(f"   {len(markets)} mercados válidos · {len(whale_signals)} señales whale")

        if not markets:
            print("   Sin mercados que pasen filtros")
            return

        trades_placed = 0

        for market in markets[:10]:
            question  = market["question"]
            mkt_price = market["price"]

            if any(t["question"] == question for t in active):
                print(f"   ⏭️  Ya tenemos posición en '{question[:40]}'")
                continue

            print(f"\n📊 Evaluando: {question[:55]}")
            print(f"   Precio mercado: {mkt_price*100:.1f}%")

            whale = next((w for w in whale_signals if question[:20].lower() in w.get("market","").lower()), None)
            news  = get_news_context(question)
            context = {"news": news, "whale_signal": whale, "active_trades": [t["question"][:30] for t in active]}

            result = estimate_probability(question, mkt_price, context)
            if not result:
                print(f"   ❌ Claude no respondió")
                continue

            true_prob  = result["probability"]
            confidence = result["confidence"]
            should     = result.get("should_trade", True)
            concerns   = result.get("concerns", "")
            ev = true_prob * (1 - mkt_price) - (1 - true_prob) * mkt_price

            print(f"   Prob: {true_prob*100:.1f}% | Conf: {confidence} | EV: {ev:.4f} | Trade: {should}")

            if not should:
                print(f"   🚫 Claude dice NO: {concerns}")
                continue
            if ev < 0.05:
                print(f"   📉 EV {ev:.4f} < 0.05 — skip")
                continue
            if confidence == "low":
                print(f"   🤷 Confianza baja — skip")
                continue

            # FIX: check_concentration devuelve tuple (bool, count)
            can_concentrate, similar_count = check_concentration(active, question)
            if not can_concentrate:
                print(f"   🎯 Concentración: {similar_count} trades similares — skip")
                continue

            position = kelly_position_size(true_prob, mkt_price, bankroll)
            if position < 5:
                print(f"   💰 Posición ${position:.2f} muy pequeña — skip")
                continue

            trade = self.trader.place_trade(
                market_id=market["id"],
                question=question,
                true_prob=true_prob,
                market_prob=mkt_price,
                ev=ev,
                position_size=position
            )

            if trade:
                print(f"   ✅ PAPER TRADE EJECUTADO #{trade['id']} — ${position:.2f}")
                state    = self.trader.load_state()
                active   = state["active_trades"]
                bankroll = state["bankroll"]
                trades_placed += 1

            if len(active) >= 5:
                print("   📊 Máximo 5 posiciones — stop scan")
                break

            time.sleep(1)

        print(f"\n📈 Ciclo completado | Trades colocados: {trades_placed} | Bankroll: ${bankroll:.2f}")

    def _build_context(self, question, active_trades):
        return {"active_trades": [t["question"][:30] for t in active_trades if t["question"] != question]}