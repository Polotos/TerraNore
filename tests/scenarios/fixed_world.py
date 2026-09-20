"""The deliberately small, fixed acceptance-test universe.

Amounts are integer accounting units.  Keeping the fixture independent of the
procedural demo world makes regressions reproducible and balance assertions
exact rather than epsilon based.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RESOURCES = ("food", "ore", "fuel", "goods")


@dataclass
class Cargo:
    id: str
    origin: str
    destination: str
    resource: str
    amount: int
    dispatched: int
    due: int
    route: str


@dataclass
class Settlement:
    id: str
    system: str
    population: int
    money: int
    stocks: dict[str, int]
    warehouses: dict[str, int]
    capacity: dict[str, int]
    lod: int = 2
    lod_lock: int | None = None
    infrastructure: int = 100
    open_buildings: set[str] = field(default_factory=lambda: {"farm", "mine", "refinery", "factory"})


@dataclass(frozen=True)
class Route:
    id: str
    origin: str
    destination: str
    capacity: int
    travel_months: int


@dataclass
class FixedWorld:
    seed: int
    month: int
    sector: str
    systems: tuple[str, ...]
    settlements: list[Settlement]
    routes: tuple[Route, ...]
    cargo: list[Cargo] = field(default_factory=list)


def create_fixed_world() -> FixedWorld:
    """Return one sector, four systems, twelve settlements and four goods."""
    systems = ("sol", "alpha", "beta", "gamma")
    settlements: list[Settlement] = []
    for system_index, system in enumerate(systems):
        for local_index in range(3):
            index = system_index * 3 + local_index
            settlements.append(Settlement(
                id=f"{system}-{local_index + 1}",
                system=system,
                population=900 + index * 25,
                money=50_000 + index * 100,
                stocks={resource: 350 + ((index * 17 + position * 31) % 90)
                        for position, resource in enumerate(RESOURCES)},
                warehouses={resource: 1_200 for resource in RESOURCES},
                capacity={
                    "food": 95 + index % 3 * 8,
                    "ore": 58 + (index + 1) % 3 * 7,
                    "fuel": 52 + (index + 2) % 3 * 6,
                    "goods": 42 + index % 4 * 5,
                },
            ))
    # A directed ring is easy to audit and makes every link potentially critical.
    routes = tuple(Route(
        id=f"route-{index + 1}", origin=item.id,
        destination=settlements[(index + 1) % len(settlements)].id,
        capacity=85, travel_months=1 + index % 3,
    ) for index, item in enumerate(settlements))
    return FixedWorld(20260920, 0, "test-sector", systems, settlements, routes)
