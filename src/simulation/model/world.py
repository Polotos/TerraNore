from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..lod.model import SimulationNode


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
    start_date: str = "2200-01-01"
    accuracy_profile: str = "balanced"

    def __post_init__(self) -> None:
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
        start_date: str = "2200-01-01",
        accuracy_profile: str = "balanced",
    ) -> "World":
        """Create the requested deterministic world topology.

        Regions are the simulation's settlement nodes.  ``system_id`` assigns
        each one to a stable stellar system, so both requested dimensions are
        represented without maintaining a second mutable hierarchy.
        """
        if system_count < 1 or settlement_count < 1:
            raise ValueError("world dimensions must be positive")
        date.fromisoformat(start_date)
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
        return cls(
            seed=seed, regions=regions, system_count=system_count,
            settlement_count=settlement_count, start_date=start_date,
            accuracy_profile=accuracy_profile,
        )

    def to_dict(self) -> dict:
        return asdict(self)
