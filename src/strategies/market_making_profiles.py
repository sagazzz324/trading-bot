"""
Perfiles de riesgo para Market Making.
Cada perfil tiene parámetros optimizados para un tipo de mercado/riesgo.
"""

PROFILES = {
    "1": {
        "name": "🟢 Conservador",
        "description": "Para mercados laterales. Spread amplio, poco inventario. Pocas operaciones pero más seguras.",
        "recommended_for": "Principiantes o mercados sin tendencia clara",
        "params": {
            "spread_pct": 0.0003,
            "quantity": 0.001,
            "max_inventory": 0.002,
            "stop_loss_pct": 0.005,
            "max_daily_loss": 10.0,
            "trend_filter": True,
            "volatility_adjust": False
        },
        "expected_trades_per_hour": "2-5",
        "risk_level": "BAJO"
    },
    "2": {
        "name": "🟡 Moderado",
        "description": "Balance entre actividad y seguridad. Spread medio, inventario controlado.",
        "recommended_for": "Mercados con movimiento moderado",
        "params": {
            "spread_pct": 0.0001,
            "quantity": 0.001,
            "max_inventory": 0.003,
            "stop_loss_pct": 0.010,
            "max_daily_loss": 25.0,
            "trend_filter": True,
            "volatility_adjust": True
        },
        "expected_trades_per_hour": "5-15",
        "risk_level": "MEDIO"
    },
    "3": {
        "name": "🔴 Agresivo",
        "description": "Spread ajustado, más operaciones. Mayor ganancia potencial pero más riesgo.",
        "recommended_for": "Mercados activos con experiencia previa",
        "params": {
            "spread_pct": 0.00005,
            "quantity": 0.002,
            "max_inventory": 0.010,
            "stop_loss_pct": 0.020,
            "max_daily_loss": 50.0,
            "trend_filter": False,
            "volatility_adjust": True
        },
        "expected_trades_per_hour": "15-40",
        "risk_level": "ALTO"
    },
    "4": {
        "name": "⚙️ Personalizado",
        "description": "Configurá cada parámetro manualmente.",
        "recommended_for": "Usuarios avanzados",
        "params": None,
        "expected_trades_per_hour": "variable",
        "risk_level": "VARIABLE"
    }
}


def show_profiles():
    """Muestra los perfiles disponibles."""
    print("\n📋 PERFILES DE MARKET MAKING")
    print("="*60)
    for key, profile in PROFILES.items():
        print(f"\n  {key}. {profile['name']}  [Riesgo: {profile['risk_level']}]")
        print(f"     {profile['description']}")
        print(f"     ✅ Recomendado para: {profile['recommended_for']}")
        print(f"     📊 Trades/hora estimados: {profile['expected_trades_per_hour']}")
        if profile["params"]:
            p = profile["params"]
            print(f"     ⚙️  Spread: {p['spread_pct']*100:.4f}% | Max inv: {p['max_inventory']} | Stop loss: {p['stop_loss_pct']*100:.1f}%")
    print("="*60)


def get_custom_params():
    """Solicita parámetros personalizados al usuario."""
    print("\n⚙️  CONFIGURACIÓN PERSONALIZADA")
    print("   (Enter para usar el valor por defecto)\n")

    def ask(prompt, default, type_fn=float):
        val = input(f"   {prompt} [{default}]: ").strip()
        return type_fn(val) if val else default

    spread = ask("Spread % (ej: 0.01 = 0.01%)", 0.01) / 100
    quantity = ask("Cantidad por trade en BTC (ej: 0.001)", 0.001)
    max_inv = ask("Inventario máximo en BTC (ej: 0.005)", 0.005)
    stop_loss = ask("Stop loss % (ej: 1.0 = 1%)", 1.0) / 100
    max_loss = ask("Pérdida diaria máxima en USDT (ej: 20)", 20.0)
    trend = ask("Filtro de tendencia activo? (1=sí, 0=no)", 1, int)
    vol_adj = ask("Ajuste por volatilidad? (1=sí, 0=no)", 1, int)

    return {
        "spread_pct": spread,
        "quantity": quantity,
        "max_inventory": max_inv,
        "stop_loss_pct": stop_loss,
        "max_daily_loss": max_loss,
        "trend_filter": bool(trend),
        "volatility_adjust": bool(vol_adj)
    }


def select_profile():
    """Muestra perfiles y retorna los parámetros elegidos."""
    show_profiles()
    opcion = input("\nElegí un perfil (1-4): ").strip()

    if opcion not in PROFILES:
        print("Opción inválida, usando Moderado por defecto")
        opcion = "2"

    profile = PROFILES[opcion]

    if opcion == "4":
        params = get_custom_params()
    else:
        params = profile["params"]
        print(f"\n✅ Perfil seleccionado: {profile['name']}")

    return params