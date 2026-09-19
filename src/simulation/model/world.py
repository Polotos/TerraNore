from __future__ import annotations

from dataclasses import asdict, dataclass, field


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


@dataclass
class World:
    seed: int
    tick: int = 0
    regions: list[Region] = field(default_factory=list)

    @classmethod
    def create(cls, seed: int = 42) -> "World":
        names = ("North Reach", "Amber Coast", "Verdant Basin", "Iron Vale")
        regions = [
            Region(f"region-{i + 1}", name, 80_000 + ((seed * 7919 + i * 17389) % 70_000), 900 + i * 140)
            for i, name in enumerate(names)
        ]
        return cls(seed=seed, regions=regions)

    def to_dict(self) -> dict:
        return asdict(self)
