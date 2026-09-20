"""Cooperative long-running simulation tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from uuid import uuid4

from .dto import TaskDTO


@dataclass
class SimulationTask:
    kind: str
    start_tick: int
    target_tick: int
    id: str = field(default_factory=lambda: uuid4().hex)
    cancellation_token: str = field(default_factory=lambda: uuid4().hex)
    status: str = "queued"
    completed: int = 0
    error: str | None = None
    pause_requested: Event = field(default_factory=Event, repr=False)
    cancel_requested: Event = field(default_factory=Event, repr=False)

    def dto(self) -> TaskDTO:
        total = max(0, self.target_tick - self.start_tick)
        progress = 1.0 if self.status == "completed" else (min(1.0, self.completed / total) if total else 1.0)
        return TaskDTO(
            self.id,
            self.kind,
            self.status,
            round(progress, 6),
            self.cancellation_token,
            self.start_tick,
            self.target_tick,
            self.error,
        )
