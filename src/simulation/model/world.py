from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..lod.model import SimulationNode


@dataclass(frozen=True, order=True)
class SimulationDate:
    """A date in the Imperial 360-day calendar."""

    year: int
    quarter: int = 1
    third: int = 1
    decade: int = 1
    day: int = 1

    DAYS_PER_YEAR = 360
    DAYS_PER_TICK = 30
    MAX_ORDINAL = 2**63 - 1
    _PATTERN = re.compile(r"^(\d{1,2})\.(\d)\\(\d)\.(\d)\.(\d+)$")

    def __post_init__(self) -> None:
        if self.year < 0 or not 1 <= self.quarter <= 4 or not 1 <= self.third <= 3:
            raise ValueError("invalid simulation date")
        if not 1 <= self.decade <= 3 or not 1 <= self.day <= 10:
            raise ValueError("invalid simulation date")
        if self.ordinal > self.MAX_ORDINAL:
            raise OverflowError("simulation date exceeds the supported period")

    @property
    def ordinal(self) -> int:
        return (self.year * self.DAYS_PER_YEAR + (self.quarter - 1) * 90
                + (self.third - 1) * 30 + (self.decade - 1) * 10 + self.day - 1)

    @classmethod
    def parse(cls, value: str | "SimulationDate") -> "SimulationDate":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("simulation date must be a string")
        match = cls._PATTERN.fullmatch(value.strip())
        if not match:
            legacy = re.fullmatch(r"(\d+)-(\d{2})-(\d{2})", value.strip())
            if legacy:
                year, month, day_of_month = map(int, legacy.groups())
                if 1 <= month <= 12 and 1 <= day_of_month <= 30:
                    quarter, third = divmod(month - 1, 3)
                    decade, day = divmod(day_of_month - 1, 10)
                    return cls(year, quarter + 1, third + 1, decade + 1, day + 1)
            raise ValueError("date must have format day.decade\\third.quarter.year")
        day, decade, third, quarter, year = map(int, match.groups())
        return cls(year, quarter, third, decade, day)

    @classmethod
    def from_ordinal(cls, ordinal: int) -> "SimulationDate":
        if ordinal < 0 or ordinal > cls.MAX_ORDINAL:
            raise OverflowError("simulation date exceeds the supported period")
        year, within_year = divmod(ordinal, 360)
        quarter, within_quarter = divmod(within_year, 90)
        third, within_third = divmod(within_quarter, 30)
        decade, day = divmod(within_third, 10)
        return cls(year, quarter + 1, third + 1, decade + 1, day + 1)

    def add_ticks(self, ticks: int) -> "SimulationDate":
        if ticks < 0:
            raise ValueError("ticks must be non-negative")
        return self.from_ordinal(self.ordinal + ticks * self.DAYS_PER_TICK)

    def ticks_until(self, target: "SimulationDate") -> int:
        delta = target.ordinal - self.ordinal
        if delta < 0:
            raise ValueError("target date precedes current date")
        # A partial third still requires one simulation tick to reach/past it.
        return (delta + self.DAYS_PER_TICK - 1) // self.DAYS_PER_TICK

    def __str__(self) -> str:
        return f"{self.day:02d}.{self.decade}\\{self.third}.{self.quarter}.{self.year}"


@dataclass
class Economy:
    treasury: float = 1_000.0
    production: float = 100.0
    consumption: float = 82.0
    price_index: float = 1.0


@dataclass
class Region:
    id: str
    name: str
    population: int
    resources: float
    economy: Economy = field(default_factory=Economy)
    detail_level: str = "summary"
    system_id: str = "system-1"


@dataclass
class World:
    seed: int
    tick: int = 0
    regions: list[Region] = field(default_factory=list)
    system_count: int = 1
    settlement_count: int = 4
    initial_date: SimulationDate = field(default_factory=lambda: SimulationDate(4300))
    current_date: SimulationDate | None = None
    accuracy_profile: str = "balanced"

    def __post_init__(self) -> None:
        legacy = isinstance(self.initial_date, str) and "-" in self.initial_date
        self.initial_date = SimulationDate.parse(self.initial_date)
        self.current_date = (self.initial_date.add_ticks(self.tick) if self.current_date is None
                             else SimulationDate.parse(self.current_date))
        self._legacy_iso_date = legacy
        # LOD nodes are operational domain objects, rather than presentation
        # strings on Region.  Keeping the registry outside the dataclass fields
        # also prevents asdict() from trying to serialise enums and passive
        # decomposition children.
        from ..lod.model import AggregateState, SimulationNode

        self._lod_nodes = {
            region.id: SimulationNode(region.id, AggregateState(
                population=float(region.population),
                available_labour=float(region.population),
                production={"output": region.economy.production},
                production_capacity={"output": region.economy.production},
                stocks={"resources": region.resources},
                stock_capacity={"resources": region.resources},
                money=region.economy.treasury,
            ))
            for region in self.regions
        }

    def simulation_node(self, object_id: str) -> SimulationNode:
        """Find an LOD domain node by its stable public identifier."""
        return self._lod_nodes[object_id]

    @classmethod
    def create(
        cls,
        seed: int = 42,
        *,
        system_count: int = 1,
        settlement_count: int = 4,
        start_date: str = "01.1\\1.1.4300",
        accuracy_profile: str = "balanced",
    ) -> "World":
        """Create the requested deterministic world topology.

        Regions are the simulation's settlement nodes.  ``system_id`` assigns
        each one to a stable stellar system, so both requested dimensions are
        represented without maintaining a second mutable hierarchy.
        """
        if system_count < 1 or settlement_count < 1:
            raise ValueError("world dimensions must be positive")
        initial_date = SimulationDate.parse(start_date)
        names = ("North Reach", "Amber Coast", "Verdant Basin", "Iron Vale")
        regions = [
            Region(
                f"region-{i + 1}",
                names[i] if i < len(names) else f"Settlement {i + 1}",
                80_000 + ((seed * 7919 + i * 17389) % 70_000),
                900 + (i % system_count) * 140,
                system_id=f"system-{i % system_count + 1}",
            )
            for i in range(settlement_count)
        ]
        world = cls(
            seed=seed, regions=regions, system_count=system_count,
            settlement_count=settlement_count, initial_date=initial_date,
            accuracy_profile=accuracy_profile,
        )
        world._legacy_iso_date = "-" in start_date
        return world

    def to_dict(self) -> dict:
        result = asdict(self)
        result["initial_date"] = str(self.initial_date)
        result["current_date"] = str(self.current_date)
        if self._legacy_iso_date:
            result["start_date"] = self.start_date
        return result

    @property
    def start_date(self) -> str:
        """Compatibility name for clients of the former Gregorian field."""
        if getattr(self, "_legacy_iso_date", False):
            month = (self.initial_date.quarter - 1) * 3 + self.initial_date.third
            day = (self.initial_date.decade - 1) * 10 + self.initial_date.day
            return f"{self.initial_date.year:04d}-{month:02d}-{day:02d}"
        return str(self.initial_date)

    @start_date.setter
    def start_date(self, value: str) -> None:
        self.initial_date = SimulationDate.parse(value)
        self.current_date = self.initial_date.add_ticks(self.tick)
        self._legacy_iso_date = "-" in value
