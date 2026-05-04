# Trading Bot

## Estado actual

El proyecto entra en una etapa de migración operativa a **Bybit**.

### Activo
- Bybit
- Dashboard live de Bybit
- Estrategias y controladores de Bybit

### Congelado / legado
- Polymarket
- Binance

El código legado **no se borra**, pero queda fuera del flujo operativo por defecto.

## Motivo

- Polymarket quedó bloqueado desde Argentina.
- No se van a usar bypass, proxies, VPN ni mecanismos similares.
- Se prioriza simplicidad operativa, trazabilidad y menor riesgo regulatorio.

## Cómo correr

### Flujo principal
- `python main.py`
- opción `Bybit`

### Dashboard
- `python dashboard.py`

## Legado

El menú legado queda oculto por defecto.

Si alguna vez necesitás volver a inspeccionar componentes archivados sin borrarlos:

- definir `ENABLE_LEGACY_BOTS=true`
- luego ejecutar `python main.py`

Eso solo reabre accesos de diagnóstico/manuales. No cambia la decisión de migración a Bybit.

## Plan de migración

1. Congelar Polymarket y Binance sin borrar código.
2. Consolidar Bybit como única ruta operativa.
3. Auditar cliente, fills, balance, posiciones y PnL reales de Bybit.
4. Reutilizar dashboard, logs, bankroll y gestión de riesgo sobre Bybit.
5. Retirar dependencias de `shares`, `redeem`, `conditionId`, `token_id` y CLOB del flujo principal.
