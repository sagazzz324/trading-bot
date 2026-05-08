from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ValidationThresholds:
    min_sharpe: float = 1.5
    max_drawdown_pct: float = 15.0
    min_profit_factor: float = 1.2
    min_trades: int = 20
    min_walk_forward_pass_rate: float = 0.7
    min_monte_carlo_pass_rate: float = 0.8
    min_permutation_pass_rate: float = 0.8
    out_of_sample_pct: float = 0.3
    monte_carlo_iterations: int = 500
    parameter_perturbation_pct: float = 0.1


@dataclass
class StrategyCandidate:
    candidate_id: str
    family: str
    market: str
    regime: str
    parameters: dict[str, Any]
    seed: int
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    source: str = "ga"
    status: str = "candidate"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    candidate_id: str
    status: str
    metrics: dict[str, Any]
    walk_forward: dict[str, Any]
    monte_carlo: dict[str, Any]
    permutation: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    validated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovedStrategyProfile:
    profile_id: str
    family: str
    market: str
    regime: str
    parameters: dict[str, Any]
    validation: dict[str, Any]
    candidate_id: str
    promoted_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
