"""Immutable values published by the local transport layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from src.simulation.model import World


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


@dataclass(frozen=True)
class WorldDTO:
    seed: int
    tick: int
    regions: tuple[RegionDTO, ...]

    @classmethod
    def from_world(cls, world: World) -> "WorldDTO":
        return cls(
            world.seed,
            world.tick,
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
                    region.detail_level,
                )
                for region in world.regions
            ),
        )

    def to_dict(self) -> dict:
        """Return a fresh JSON object, never a reference into the domain model."""
        return asdict(self)


@dataclass(frozen=True)
class TaskDTO:
    id: str
    kind: str
    status: str
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
