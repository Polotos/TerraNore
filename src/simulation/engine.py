from __future__ import annotations

import random

from .model import Region, World
from .systems import apply_consumption, apply_growth, apply_market
from .workers import DeterministicScheduler


class Simulation:
    TICKS_PER_YEAR = 12

    def __init__(self, seed: int = 42, workers: int = 1, world: World | None = None) -> None:
        self.world = world or World.create(seed)
        self.scheduler = DeterministicScheduler(workers)

    def _update_region(self, region: Region) -> Region:
        rng = random.Random(f"{self.world.seed}:{self.world.tick}:{region.id}")
        apply_growth(region, rng.uniform(-0.00035, 0.00035))
        apply_consumption(region)
        apply_market(region)
        return region

    def step(self, ticks: int = 1) -> World:
        if ticks < 0:
            raise ValueError("ticks must be non-negative")
        for _ in range(ticks):
            self.world.regions = self.scheduler.map(self._update_region, self.world.regions)
            self.world.tick += 1
        return self.world

    def run_years(self, years: int) -> World:
        return self.step(years * self.TICKS_PER_YEAR)
