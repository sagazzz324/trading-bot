from __future__ import annotations

from src.lab.registry import StrategyRegistry


def get_runtime_profile(family: str, market: str = "MULTI") -> dict:
    registry = StrategyRegistry()
    return registry.get_active_profile(family=family, market=market) or {}

