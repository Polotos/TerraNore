"""Compact, multi-resolution storage for numeric simulation history."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from src.simulation.model import World


@dataclass(frozen=True)
class HistoryPolicy:
    """Retention boundaries, expressed in monthly simulation ticks."""

    full_months: int = 24
    monthly_years: int = 10
    quarterly_years: int = 30

    def __post_init__(self) -> None:
        if self.full_months < 0 or self.monthly_years < 0 or self.quarterly_years < self.monthly_years:
            raise ValueError("invalid history retention policy")

    def stride(self, age: int) -> int:
        if age <= self.full_months:
            return 1
        if age <= self.monthly_years * 12:
            return 1
        if age <= self.quarterly_years * 12:
            return 3
        return 12


@dataclass
class TimeSeries:
    """Numeric columns keyed by stable IDs, with age-based compaction.

    Names and units live in ``metadata`` once instead of being repeated in
    every sample.  ``points`` remains a convenient decoded compatibility view.
    Pinned intervals and significant ticks are never compacted away.
    """

    policy: HistoryPolicy = field(default_factory=HistoryPolicy)
    metadata: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "population": {"name": "Population", "unit": "people"},
        "treasury": {"name": "Treasury", "unit": "currency"},
    })
    _ticks: list[int] = field(default_factory=list, repr=False)
    _columns: dict[str, list[int | float]] = field(
        default_factory=lambda: {"population": [], "treasury": []}, repr=False
    )
    _pinned: list[tuple[int, int]] = field(default_factory=list, repr=False)
    _significant: set[int] = field(default_factory=set, repr=False)

    @property
    def points(self) -> list[dict]:
        return [
            {"tick": tick, **{metric: values[index] for metric, values in self._columns.items()}}
            for index, tick in enumerate(self._ticks)
        ]

    def capture(self, world: World, *, significant: bool = False) -> None:
        values: dict[str, int | float] = {
            "population": sum(region.population for region in world.regions),
            "treasury": round(sum(region.economy.treasury for region in world.regions), 2),
        }
        if self._ticks and self._ticks[-1] == world.tick:
            for metric, value in values.items():
                self._columns[metric][-1] = value
        else:
            self._ticks.append(world.tick)
            for metric, value in values.items():
                self._columns.setdefault(metric, []).append(value)
        if significant:
            self._significant.add(world.tick)

    def mark_significant(self, tick: int) -> None:
        self._significant.add(tick)

    def pin_interval(self, start: int, end: int) -> None:
        if start < 0 or end < start:
            raise ValueError("invalid pinned interval")
        self._pinned.append((start, end))

    def _protected(self, tick: int) -> bool:
        return tick in self._significant or any(start <= tick <= end for start, end in self._pinned)

    def compact(self, current_tick: int) -> None:
        """Apply deterministic LOD retention while keeping boundary samples."""
        if not self._ticks:
            return
        keep: list[int] = []
        for index, tick in enumerate(self._ticks):
            stride = self.policy.stride(max(0, current_tick - tick))
            if index in (0, len(self._ticks) - 1) or self._protected(tick) or tick % stride == 0:
                keep.append(index)
        self._ticks = [self._ticks[index] for index in keep]
        self._columns = {
            metric: [values[index] for index in keep] for metric, values in self._columns.items()
        }

    def between(self, start: int, end: int) -> list[dict]:
        return [point for point in self.points if start <= point["tick"] <= end]

    def extend_significant(self, ticks: Iterable[int]) -> None:
        self._significant.update(ticks)
