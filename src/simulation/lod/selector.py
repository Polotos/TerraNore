from __future__ import annotations

from ..model import World


class DetailSelector:
    """Changes calculation detail without knowledge of presentation concerns."""

    LEVELS = ("summary", "standard", "detailed")

    def set_level(self, world: World, region_id: str, level: str) -> None:
        if level not in self.LEVELS:
            raise ValueError(f"unknown detail level: {level}")
        region = next((item for item in world.regions if item.id == region_id), None)
        if region is None:
            raise KeyError(region_id)
        region.detail_level = level
