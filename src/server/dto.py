"""Immutable values published by the local transport layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from src.simulation.model import World
from src.simulation.lod import external_lod


@dataclass(frozen=True)
class EconomyDTO:
    treasury: float
    production: float
    consumption: float
    price_index: float


@dataclass(frozen=True)
class RegionDTO:
    id: str
    name: str
    population: int
    resources: float
    economy: EconomyDTO
    detail_level: str
    system_id: str
    system_name: str
    planet_id: str
    planet_name: str


@dataclass(frozen=True)
class WorldDTO:
    seed: int
    tick: int
    system_count: int
    settlement_count: int
    start_date: str
    current_date: str
    accuracy_profile: str
    workers: int
    regions: tuple[RegionDTO, ...]

    @classmethod
    def from_world(cls, world: World, workers: int = 1) -> "WorldDTO":
        return cls(
            world.seed,
            world.tick,
            world.system_count,
            world.settlement_count,
            world.start_date,
            str(world.current_date),
            world.accuracy_profile,
            workers,
            tuple(
                RegionDTO(
                    region.id,
                    region.name,
                    region.population,
                    region.resources,
                    EconomyDTO(
                        region.economy.treasury,
                        region.economy.production,
                        region.economy.consumption,
                        region.economy.price_index,
                    ),
                    external_lod(world.simulation_node(region.id)),
                    region.system_id,
                    region.system_name,
                    region.planet_id,
                    region.planet_name,
                )
                for region in world.regions
            ),
        )

    def to_dict(self) -> dict:
        """Return a fresh JSON object, never a reference into the domain model."""
        result = asdict(self)
        for snake, camel in (
            ("system_count", "systemCount"), ("settlement_count", "settlementCount"),
            ("start_date", "startDate"), ("accuracy_profile", "accuracyProfile"),
            ("current_date", "currentDate"),
        ):
            result[camel] = result.pop(snake)
        for region in result["regions"]:
            for snake, camel in (
                ("detail_level", "detailLevel"), ("system_id", "systemId"),
                ("system_name", "systemName"), ("planet_id", "planetId"),
                ("planet_name", "planetName"),
            ):
                region[camel] = region[snake]
        return result


@dataclass(frozen=True)
class TaskDTO:
    id: str
    kind: str
    status: str
    completed: int
    progress: float
    cancellation_token: str
    start_tick: int
    target_tick: int
    error: str | None = None

    def to_dict(self) -> dict:
        result = asdict(self)
        result["cancellationToken"] = result.pop("cancellation_token")
        result["startTick"] = result.pop("start_tick")
        result["targetTick"] = result.pop("target_tick")
        return result
