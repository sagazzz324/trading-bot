import time
import logging
from datetime import datetime
from src.core.bot import TradingBot
from src.core.resolver import auto_resolve_trades

logger = logging.getLogger(__name__)

def run_loop(interval_minutes=30):
    """
    Corre el bot automáticamente cada X minutos.
    """
    bot = TradingBot()
    ciclo = 1

    print(f"\n🚀 Bot iniciado. Escaneando cada {interval_minutes} minutos.")
    print(f"   Presioná Ctrl+C para detener.\n")

    while True:
        try:
            print(f"\n⏰ {datetime.now().strftime('%H:%M:%S')} - Ciclo #{ciclo}")

            # Primero resolver trades cerrados
            auto_resolve_trades()

            # Después buscar nuevas oportunidades
            bot.run_once()
            ciclo += 1

            print(f"\n💤 Esperando {interval_minutes} minutos...")
            time.sleep(interval_minutes * 60)

        except KeyboardInterrupt:
            print("\n\n⛔ Bot detenido manualmente.")
            print("📊 Resumen final:")
            stats = bot.trader.get_stats()
            for k, v in stats.items():
                print(f"   {k}: {v}")
            break

        except Exception as e:
            logger.error(f"Error en ciclo #{ciclo}: {e}")
            print(f"\n⚠️ Error: {e}")
            print(f"   Reintentando en 5 minutos...")
            time.sleep(300)