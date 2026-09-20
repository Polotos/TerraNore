"""Permanent event journal and causal links."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field


LOD0_SIGNIFICANT_KINDS = frozenset({
    "shortage", "deficit", "construction", "construction-started", "construction-completed",
    "route-changed", "enterprise-closed", "demographic-crisis",
})


@dataclass(frozen=True)
class EventRecord:
    id: str
    tick: int
    kind: str
    object_id: str | None = None
    payload: dict = field(default_factory=dict)
    causes: tuple[str, ...] = ()
    significant: bool = False

    def to_dict(self) -> dict:
        value = asdict(self)
        value["causes"] = list(self.causes)
        return value


class EventLog:
    """Append-only journal; records and causal edges are never compacted."""

    def __init__(self) -> None:
        self._records: list[EventRecord] = []
        self._by_id: dict[str, EventRecord] = {}

    def append(self, record: EventRecord) -> None:
        if record.id in self._by_id:
            raise ValueError(f"duplicate event id: {record.id}")
        missing = [cause for cause in record.causes if cause not in self._by_id]
        if missing:
            raise ValueError(f"unknown event cause: {missing[0]}")
        if record.kind in LOD0_SIGNIFICANT_KINDS and not record.significant:
            record = EventRecord(**{**asdict(record), "significant": True})
        self._records.append(record)
        self._by_id[record.id] = record

    def between(self, start: int, end: int) -> list[dict]:
        return [deepcopy(record.to_dict()) for record in self._records if start <= record.tick <= end]

    def get(self, event_id: str) -> EventRecord | None:
        return self._by_id.get(event_id)

    def causal_chain(self, event_id: str) -> list[dict]:
        result: list[dict] = []
        visited: set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in visited:
                return
            visited.add(identifier)
            record = self._by_id.get(identifier)
            if record is None:
                return
            for cause in record.causes:
                visit(cause)
            if identifier != event_id:
                result.append(deepcopy(record.to_dict()))

        visit(event_id)
        return result

    @property
    def records(self) -> tuple[EventRecord, ...]:
        return tuple(self._records)
