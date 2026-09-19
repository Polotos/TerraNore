from __future__ import annotations

from ..model import Region


def apply_growth(region: Region, variation: float) -> None:
    capacity = max(0.4, min(1.4, region.resources / 1_000))
    region.economy.production *= 1 + 0.0015 * capacity + variation
    region.population = max(1, round(region.population * (1.0002 + variation / 5)))
    region.resources = max(0.0, region.resources - region.economy.production * 0.00008)


def apply_consumption(region: Region) -> None:
    region.economy.consumption = region.population * 0.00082 * region.economy.price_index
    region.economy.treasury += region.economy.production - region.economy.consumption


def apply_market(region: Region) -> None:
    demand_ratio = region.economy.consumption / max(region.economy.production, 0.01)
    region.economy.price_index = max(0.5, min(3.0, region.economy.price_index * (0.995 + 0.005 * demand_ratio)))
