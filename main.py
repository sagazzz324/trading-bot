import time

from config.settings import ENABLE_LEGACY_BOTS


def menu_binance():
    print("\n[LEGACY] BINANCE")
    print("=" * 40)
    print("  1. Market Making")
    print("  2. Scalping")
    print("  3. Mean Reversion")
    print("  4. Arbitraje Triangular")
    print("=" * 40)
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
            print("\nScalping continuo — Ctrl+C para detener\n")
            cycle = 0
            try:
                while True:
                    cycle += 1
                    print(f"\nCiclo #{cycle}")
                    bot.run_once()
                    print("\nEsperando 2 minutos...")
                    time.sleep(120)
            except KeyboardInterrupt:
                print("\nBot detenido.")
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


def menu_bybit():
    print("\nBYBIT")
    print("=" * 40)
    print("  1. Dashboard live")
    print("  2. Scalping CLI")
    print("=" * 40)
    sub = input("Elegí (1-2): ").strip()

    if sub == "1":
        from src.core.dashboard_live import run_dashboard

        run_dashboard(port=5000)

    elif sub == "2":
        from src.strategies.scalper import ScalpingBot

        bot = ScalpingBot(max_positions=3, risk_per_trade=0.01, capital=1000)
        modo = input("¿Continuo? (s/n): ").strip().lower()
        if modo == "s":
            print("\nScalping Bybit continuo — Ctrl+C para detener\n")
            cycle = 0
            try:
                while True:
                    cycle += 1
                    print(f"\nCiclo #{cycle}")
                    bot.run_once()
                    print("\nEsperando 2 minutos...")
                    time.sleep(120)
            except KeyboardInterrupt:
                print("\nBot detenido.")
                bot.print_stats()
        else:
            bot.run_once()


def menu_legacy():
    print("\nLEGACY / ARCHIVADO")
    print("=" * 40)
    print("  1. Polymarket (deshabilitado operativamente)")
    print("  2. Binance (legado)")
    print("=" * 40)
    sub = input("Elegí (1-2): ").strip()

    if sub == "1":
        print("\nPolymarket quedó archivado por cumplimiento/regulación.")
        print("No se inicia desde este menú.")
        print("El código sigue preservado en el repo como referencia.")
    elif sub == "2":
        menu_binance()


def main():
    print("\n" + "=" * 40)
    print("        TRADING BOT")
    print("=" * 40)
    print("  1. Bybit")
    if ENABLE_LEGACY_BOTS:
        print("  2. Legacy / Archivado")
        print("=" * 40)
        choice = input("¿Qué módulo querés abrir? (1-2): ").strip()
    else:
        print("=" * 40)
        choice = input("¿Qué módulo querés abrir? (1): ").strip()

    if choice == "1":
        menu_bybit()
    elif choice == "2" and ENABLE_LEGACY_BOTS:
        menu_legacy()


if __name__ == "__main__":
    main()
