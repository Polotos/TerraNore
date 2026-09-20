from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class Phase(IntEnum):
    SNAPSHOT = 1
    NEEDS = 2
    PRODUCTION = 3
    TRADE = 4
    TRANSPORT = 5
    DEMOGRAPHY = 6
    INVESTMENT = 7
    MAINTENANCE = 8
    INVARIANTS = 9
    PUBLISH = 10


@dataclass(frozen=True, order=True)
class StableKey:
    phase: int
    timestamp: int
    object_id: str
    operation: str
    sequence: int = 0


@dataclass(frozen=True)
class Change:
    """An assignment or addition to a dotted path on a target region."""
    path: str
    value: Any
    object_id: str | None = None
    operation: str = "set"


@dataclass(frozen=True)
class TradeOrder:
    region_id: str
    quantity: float  # positive is demand, negative is supply
    price: float
    sequence: int = 0


@dataclass(frozen=True)
class Event:
    kind: str
    payload: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class Delta:
    """The only value a phase worker may return."""
    key: StableKey
    changes: tuple[Change, ...] = ()
    events: tuple[Event, ...] = ()
    trade_orders: tuple[TradeOrder, ...] = ()


@dataclass(frozen=True)
class Shipment:
    seller_id: str
    buyer_id: str
    quantity: float
    unit_price: float
    sequence: int


@dataclass(frozen=True)
class EconomySnapshot:
    treasury: float
    production: float
    consumption: float
    price_index: float


@dataclass(frozen=True)
class RegionSnapshot:
    id: str
    name: str
    population: int
    resources: float
    economy: EconomySnapshot
    detail_level: str


@dataclass(frozen=True)
class WorldSnapshot:
    seed: int
    tick: int
    regions: tuple[RegionSnapshot, ...]

    def region(self, object_id: str) -> RegionSnapshot:
        return next(region for region in self.regions if region.id == object_id)


def freeze_world(world: Any) -> WorldSnapshot:
    return WorldSnapshot(
        seed=world.seed,
        tick=world.tick,
        regions=tuple(
            RegionSnapshot(
                region.id,
                region.name,
                region.population,
                region.resources,
                EconomySnapshot(
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
