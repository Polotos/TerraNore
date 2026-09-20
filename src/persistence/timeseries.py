"""Compact, multi-resolution storage for numeric simulation history."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

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
    """Numeric columns keyed by stable IDs, with loss-aware compaction.

    Old raw samples are replaced by bucket records rather than merely being
    discarded.  A bucket contains temporal bounds, the number of source
    samples and descriptive statistics for every metric.  Flow metrics also
    retain their sum; their public ``value`` is either that sum or their mean,
    as selected by the metric's ``aggregation`` metadata.

    Pinned intervals and significant ticks remain raw samples.
    """

    policy: HistoryPolicy = field(default_factory=HistoryPolicy)
    object_id: str | None = None
    metadata: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "population": {"name": "Population", "unit": "people", "aggregation": "snapshot"},
        "treasury": {"name": "Treasury", "unit": "currency", "aggregation": "snapshot"},
    })
    _ticks: list[int] = field(default_factory=list, repr=False)
    _columns: dict[str, list[int | float]] = field(default_factory=dict, repr=False)
    _buckets: list[dict] = field(default_factory=list, repr=False)
    _pinned: list[tuple[int, int]] = field(default_factory=list, repr=False)
    _significant: set[int] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        if self.object_id is not None and "production" not in self.metadata:
            self.metadata["production"] = {
                "name": "Production", "unit": "goods", "aggregation": "sum"
            }
        for metric, details in self.metadata.items():
            aggregation = details.setdefault("aggregation", "snapshot")
            if aggregation not in {"snapshot", "sum", "average"}:
                raise ValueError(f"invalid aggregation for {metric}: {aggregation}")
            self._columns.setdefault(metric, [])

    @property
    def points(self) -> list[dict]:
        """Return raw (unbucketed) samples for backwards compatibility."""
        return [
            {"tick": tick, **{metric: values[index] for metric, values in self._columns.items()}}
            for index, tick in enumerate(self._ticks)
        ]

    @property
    def buckets(self) -> list[dict]:
        """Return chronological copies of compacted bucket records."""
        return [self._copy_bucket(bucket) for bucket in sorted(self._buckets, key=lambda b: b["start_tick"])]

    @staticmethod
    def _copy_bucket(bucket: dict) -> dict:
        return {
            key: ({name: dict(stats) for name, stats in value.items()} if key == "metrics" else value)
            for key, value in bucket.items() if not key.startswith("_")
        }

    def append(self, tick: int, values: Mapping[str, int | float], *, significant: bool = False) -> None:
        """Append a decoded sample (also useful for non-World simulation metrics)."""
        if set(values) != set(self._columns):
            raise ValueError("sample metrics must match time-series metadata")
        if self._ticks and tick < self._ticks[-1]:
            raise ValueError("time-series ticks must be monotonic")
        if self._ticks and tick == self._ticks[-1]:
            for metric, value in values.items():
                self._columns[metric][-1] = value
        else:
            self._ticks.append(tick)
            for metric, value in values.items():
                self._columns[metric].append(value)
        if significant:
            self._significant.add(tick)

    def capture(self, world: World, *, significant: bool = False) -> None:
        regions = world.regions
        if self.object_id is not None:
            regions = [region for region in world.regions if region.id == self.object_id]
            if not regions:
                raise KeyError(self.object_id)
        available: dict[str, int | float] = {
            "population": sum(region.population for region in regions),
            "treasury": round(sum(region.economy.treasury for region in regions), 2),
            "production": round(sum(region.economy.production for region in regions), 2),
        }
        self.append(world.tick, {metric: available[metric] for metric in self._columns},
                    significant=significant)

    def mark_significant(self, tick: int) -> None:
        self._significant.add(tick)

    def pin_interval(self, start: int, end: int) -> None:
        if start < 0 or end < start:
            raise ValueError("invalid pinned interval")
        self._pinned.append((start, end))

    def _protected(self, tick: int) -> bool:
        return tick in self._significant or any(start <= tick <= end for start, end in self._pinned)

    def _make_bucket(self, indexes: list[int], stride: int) -> dict:
        bucket = {
            "start_tick": self._ticks[indexes[0]],
            "end_tick": self._ticks[indexes[-1]],
            "count": len(indexes),
            "metrics": {},
            "_stride": stride,
        }
        for metric, column in self._columns.items():
            values = [column[index] for index in indexes]
            total = sum(values)
            stats = {
                "first": values[0], "last": values[-1], "min": min(values),
                "max": max(values), "average": total / len(values),
            }
            aggregation = self.metadata[metric]["aggregation"]
            if aggregation in {"sum", "average"}:
                stats["sum"] = total
            stats["value"] = (total if aggregation == "sum" else
                              total / len(values) if aggregation == "average" else values[-1])
            bucket["metrics"][metric] = stats
        return bucket

    def _store_bucket(self, incoming: dict) -> None:
        """Merge samples that age into an already-created aligned bucket."""
        period = incoming["start_tick"] // incoming["_stride"]
        existing = next((bucket for bucket in self._buckets
                         if bucket["_stride"] == incoming["_stride"]
                         and bucket["start_tick"] // bucket["_stride"] == period), None)
        if existing is None:
            self._buckets.append(incoming)
            return
        old_count, new_count = existing["count"], incoming["count"]
        for metric, new in incoming["metrics"].items():
            old = existing["metrics"][metric]
            old_total = old["average"] * old_count
            new_total = new["average"] * new_count
            old["last"] = new["last"]
            old["min"] = min(old["min"], new["min"])
            old["max"] = max(old["max"], new["max"])
            old["average"] = (old_total + new_total) / (old_count + new_count)
            aggregation = self.metadata[metric]["aggregation"]
            if aggregation in {"sum", "average"}:
                old["sum"] = old_total + new_total
            old["value"] = (old_total + new_total if aggregation == "sum" else
                            old["average"] if aggregation == "average" else old["last"])
        existing["end_tick"] = incoming["end_tick"]
        existing["count"] += new_count

    def compact(self, current_tick: int) -> None:
        """Replace old, unprotected samples with aligned quarter/year buckets."""
        groups: dict[tuple[int, int], list[int]] = {}
        keep: list[int] = []
        for index, tick in enumerate(self._ticks):
            stride = self.policy.stride(max(0, current_tick - tick))
            if stride == 1 or self._protected(tick):
                keep.append(index)
            else:
                groups.setdefault((stride, tick // stride), []).append(index)
        for (stride, _), indexes in groups.items():
            self._store_bucket(self._make_bucket(indexes, stride))
        self._ticks = [self._ticks[index] for index in keep]
        self._columns = {
            metric: [values[index] for index in keep] for metric, values in self._columns.items()
        }

    def between(self, start: int, end: int) -> list[dict]:
        """Return raw points and buckets which overlap an inclusive range."""
        records = [point for point in self.points if start <= point["tick"] <= end]
        records.extend(bucket for bucket in self.buckets
                       if bucket["end_tick"] >= start and bucket["start_tick"] <= end)
        return sorted(records, key=lambda item: item["tick"] if "tick" in item else item["start_tick"])

    def extend_significant(self, ticks: Iterable[int]) -> None:
        self._significant.update(ticks)
