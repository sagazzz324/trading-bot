import logging
import time
from src.core.market_data import get_markets
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
        state = self.trader.load_state()
        bankroll   = state["bankroll"]
        active     = state["active_trades"]
        all_trades = state["trades"]

        print("\n" + "="*50)
        print("🤖 CICLO DEL BOT - PAPER TRADING")
        print("="*50)

        # 1. Stop losses
        print("\n🛡️  Revisando stop losses...")
        for trade in list(active):
            context = self._build_context(trade["question"], active)
            result  = estimate_probability(trade["question"], trade["market_prob"], context)
            current_prob = result["probability"]

            if check_stop_loss(trade, current_prob):
                print(f"   🛑 STOP LOSS activado: {trade['question'][:50]}")
                self.trader.resolve_trade(trade["id"], False)
                active = [t for t in active if t["id"] != trade["id"]]
            else:
                drop = (trade["true_prob"] - current_prob) / trade["true_prob"] * 100 if trade["true_prob"] > 0 else 0
                print(f"   ✅ {trade['question'][:50][:40]}… — sin stop loss ({drop:.1f}% caída)")

                # Si Claude dice que ya no conviene mantener, cerrar
                if not result.get("should_trade", True) and result.get("concerns"):
                    print(f"   ⚠️  Claude recomienda cerrar: {result['concerns']}")

        # 2. Riesgo global
        resolved = [t for t in all_trades if t["status"] == "resolved"]
        wins     = [t for t in resolved if t["result"] == "win"]
        drawdown = (state["initial_bankroll"] - bankroll) / state["initial_bankroll"]
        win_rate = len(wins) / len(resolved) if resolved else 0

        risk_ok, risk_msg = check_risk_rules(drawdown, len(active))
        if not risk_ok:
            print(f"\n🚨 RIESGO: {risk_msg}")
            return

        # 3. Escanear mercados
        print("\n🔍 Escaneando mercados...")
        markets = get_active_markets()
        if not markets:
            print("   Sin mercados disponibles")
            return

        whale_signals = get_whale_signals()
        print(f"   {len(markets)} mercados disponibles · {len(whale_signals)} señales whale")

        trades_placed = 0

        for market in markets[:12]:
            question  = market.get("question", "")
            mkt_price = market.get("price", 0.5)

            # Skip si ya tenemos posición
            if any(t["question"] == question for t in active):
                print(f"   ⏭️  Saltando '{question[:40]}' (ya tenemos posición)")
                continue

            # Skip si precio fuera de rango razonable
            if not (0.03 < mkt_price < 0.97):
                continue

            print(f"\n📊 Evaluando: {question[:55]}...")
            print(f"   Precio mercado: {mkt_price*100:.1f}%")

            # Buscar señal whale para este mercado
            whale = next((w for w in whale_signals if question[:20].lower() in w.get("market","").lower()), None)
            if whale:
                print(f"   🐋 Señal whale: {'📈' if whale['direction']=='up' else '📉'} {whale['change_pct']:+.1f}%")

            # Noticias relevantes
            news = get_news_context(question)

            # Contexto para Claude
            context = {
                "news": news,
                "whale_signal": whale,
                "active_trades": [t["question"][:30] for t in active],
                "similar_markets": [m["question"][:30] for m in markets if m["question"] != question and any(w in m["question"].lower() for w in question.lower().split()[:3])][:2]
            }

            # Claude razona
            result = estimate_probability(question, mkt_price, context)
            true_prob  = result["probability"]
            confidence = result["confidence"]
            should     = result.get("should_trade", True)
            concerns   = result.get("concerns", "")
            assessment = result.get("market_assessment", "")

            ev = true_prob * (1 - mkt_price) - (1 - true_prob) * mkt_price
            print(f"   Prob estimada: {true_prob*100:.1f}% | Confianza: {confidence} | EV: {ev:.4f}")
            print(f"   Mercado: {assessment} | Edge: {result.get('edge','')[:50]}")

            # Filtros
            if not should:
                print(f"   🚫 Claude dice NO entrar: {concerns}")
                continue

            if ev < 0.05:
                print(f"   📉 EV insuficiente ({ev:.4f} < 0.05)")
                continue

            if confidence == "low":
                print(f"   🤷 Confianza baja — saltando")
                continue

            if concerns:
                print(f"   ⚠️  Preocupaciones: {concerns}")
                # Si hay concerns serios y EV no es muy bueno, skip
                if ev < 0.10:
                    print(f"   ↩️  EV marginal con preocupaciones — saltando")
                    continue

            if not check_concentration(active, question):
                print(f"   🎯 Concentración: ya tenemos exposición en este tema")
                continue

            # Sizing
            position = kelly_position_size(true_prob, mkt_price, bankroll)
            if position < 5:
                print(f"   💰 Posición muy pequeña (${position:.2f}) — saltando")
                continue

            # Ejecutar
            trade = self.trader.place_trade(
                question=question,
                market_prob=mkt_price,
                true_prob=true_prob,
                ev=ev,
                position_size=position,
                confidence=confidence,
                reasoning=result.get("reasoning", "")
            )

            if trade:
                print(f"   ✅ TRADE COLOCADO: ${position:.2f} | {result.get('reasoning','')[:80]}")
                active = self.trader.load_state()["active_trades"]
                bankroll = self.trader.load_state()["bankroll"]
                trades_placed += 1

            if len(active) >= 5:
                print("   📊 Máximo de posiciones activas alcanzado")
                break

            time.sleep(1)

        # 4. Stats
        print(f"\n📈 ESTADÍSTICAS:")
        print(f"   Bankroll: ${bankroll:.2f}")
        print(f"   Drawdown: {drawdown*100:.1f}%")
        print(f"   Win rate: {win_rate*100:.1f}% ({len(wins)}/{len(resolved)})")
        print(f"\nCiclo completado. Trades colocados: {trades_placed}")

    def _build_context(self, question, active_trades):
        return {
            "active_trades": [t["question"][:30] for t in active_trades if t["question"] != question],
            "news": get_news_context(question)
        }