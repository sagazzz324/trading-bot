from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from src.lab.registry import StrategyRegistry


@dataclass
class StrategyLabState:
    running: bool = False
    last_run_at: str | None = None
    jobs_completed: int = 0
    active_job: str | None = None
    last_error: str | None = None
    notes: list[str] = field(default_factory=list)


class StrategyLabService:
    def __init__(self):
        self.registry = StrategyRegistry()
        self.state = StrategyLabState()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self):
        if self.state.running:
            return False
        self.state.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self.state.running = False
        self._stop_event.set()
        return True

    def _run_loop(self):
        while not self._stop_event.wait(timeout=60):
            self.state.last_run_at = datetime.utcnow().isoformat() + "Z"
            self.state.active_job = "idle_maintenance"
            self.state.jobs_completed += 1
            self.state.notes = [
                "Laboratorio activo dentro de la app",
                "Pendiente conectar datasets, walk-forward y promotion pipeline completo",
            ]
            time.sleep(0.01)
            self.state.active_job = None

