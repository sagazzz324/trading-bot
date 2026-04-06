import time
from src.core.scheduler import run_loop


def menu_binance():
    print("\n📊 BINANCE")
    print("="*40)
    print("  1. Market Making")
    print("  2. Scalping")
    print("  3. Mean Reversion")
    print("  4. Arbitraje Triangular")
    print("="*40)
    sub = input("Elegí (1-4): ").strip()

    if sub == "1":
        from src.strategies.market_making import MarketMaker
        from src.strategies.market_making_profiles import PROFILES
        print("\nPerfiles: 1=Conservador 2=Moderado 3=Agresivo")
        p = input("Perfil (1-3): ").strip() or "2"
        profile = PROFILES.get(p, PROFILES["2"])
        bot = MarketMaker(symbol="BTCUSDT", params=profile["params"])
        bot.run()

    elif sub == "2":
        from src.strategies.scalper import ScalpingBot
        bot = ScalpingBot(max_positions=3, risk_per_trade=0.01, capital=1000)
        modo = input("¿Continuo? (s/n): ").strip().lower()
        if modo == "s":
            print("\n⚡ Scalping continuo — Ctrl+C para detener\n")
            cycle = 0
            try:
                while True:
                    cycle += 1
                    print(f"\n⏰ Ciclo #{cycle}")
                    bot.run_once()
                    print("\n💤 Esperando 2 minutos...")
                    time.sleep(120)
            except KeyboardInterrupt:
                print("\n⛔ Bot detenido.")
                bot.print_stats()
        else:
            bot.run_once()

    elif sub == "3":
        from src.strategies.mean_reversion import MeanReversionStrategy
        bot = MeanReversionStrategy(symbol="BTCUSDT")
        bot.run(cycles=2)

    elif sub == "4":
        from src.strategies.arbitrage import TriangularArbitrage
        bot = TriangularArbitrage()
        bot.scan()


def main():
    print("\n" + "="*40)
    print("        🤖 TRADING BOT")
    print("="*40)
    print("  1. Polymarket")
    print("  2. Binance")
    print("  3. Dashboard")
    print("="*40)

    choice = input("¿Qué mercado querés operar? (1-3): ").strip()

    if choice == "1":
        print("\n📊 POLYMARKET")
        print("="*40)
        print("  1. Iniciar bot")
        print("="*40)
        sub = input("Elegí (1): ").strip()
        if sub == "1":
            run_loop(interval_minutes=15)

    elif choice == "2":
        menu_binance()

    elif choice == "3":
        from src.core.dashboard_live import run_dashboard
        run_dashboard(port=5000)


if __name__ == "__main__":
    main()