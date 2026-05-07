from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.lab.models import ApprovedStrategyProfile, StrategyCandidate, ValidationReport


REGISTRY_PATH = Path("data/lab/registry.json")


def _default_registry() -> dict[str, Any]:
    return {
        "version": 1,
        "candidates": [],
        "approved_profiles": [],
        "history": [],
    }


class StrategyRegistry:
    def __init__(self, path: Path | None = None):
        self.path = path or REGISTRY_PATH
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save(_default_registry())

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _default_registry()
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def add_candidate(self, candidate: StrategyCandidate) -> None:
        with self._lock:
            payload = self.load()
            payload["candidates"].append(candidate.to_dict())
            payload["history"].append({
                "event": "candidate_added",
                "candidate_id": candidate.candidate_id,
                "family": candidate.family,
                "market": candidate.market,
                "status": candidate.status,
                "timestamp": candidate.created_at,
            })
            self.save(payload)

    def promote_candidate(
        self,
        candidate: StrategyCandidate,
        report: ValidationReport,
        profile_id: str,
    ) -> ApprovedStrategyProfile:
        profile = ApprovedStrategyProfile(
            profile_id=profile_id,
            family=candidate.family,
            market=candidate.market,
            regime=candidate.regime,
            parameters=deepcopy(candidate.parameters),
            validation=report.to_dict(),
            candidate_id=candidate.candidate_id,
        )
        with self._lock:
            payload = self.load()
            for existing in payload["approved_profiles"]:
                if (
                    existing.get("family") == candidate.family
                    and existing.get("market") == candidate.market
                ):
                    existing["active"] = False
            payload["approved_profiles"].append(profile.to_dict())
            payload["history"].append({
                "event": "candidate_promoted",
                "candidate_id": candidate.candidate_id,
                "profile_id": profile.profile_id,
                "family": candidate.family,
                "market": candidate.market,
                "timestamp": profile.promoted_at,
            })
            self.save(payload)
        return profile

    def get_active_profile(self, family: str, market: str) -> dict[str, Any] | None:
        payload = self.load()
        approved = payload.get("approved_profiles", [])
        exact = [
            p for p in approved
            if p.get("active") and p.get("family") == family and p.get("market") == market
        ]
        if exact:
            return exact[-1]
        wildcard = [
            p for p in approved
            if p.get("active") and p.get("family") == family and p.get("market") in {"*", "MULTI"}
        ]
        return wildcard[-1] if wildcard else None

    def list_active_profiles(self) -> list[dict[str, Any]]:
        payload = self.load()
        return [p for p in payload.get("approved_profiles", []) if p.get("active")]

