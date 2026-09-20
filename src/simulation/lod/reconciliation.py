"""Conservation checks used before and after LOD transitions."""

from __future__ import annotations

from dataclasses import dataclass

from .model import AggregateState, SimulationNode


def _sum_maps(states: list[AggregateState], attribute: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for state in states:
        for key, value in getattr(state, attribute).items():
            result[key] = result.get(key, 0.0) + value
    return result


@dataclass(frozen=True)
class Balance:
    resources: dict[str, float]
    population: float
    money: float
    production_capacity: dict[str, float]
    infrastructure: float
    shipments: dict[str, float]
    construction_materials: dict[str, float]


def balance_of(nodes: list[SimulationNode]) -> Balance:
    states = [node.state for node in nodes]
    resources: dict[str, float] = {}
    shipments: dict[str, float] = {}
    materials: dict[str, float] = {}
    for state in states:
        keys = set(state.stocks) | set(state.rounding)
        for shipment in state.shipments:
            keys.add(shipment.resource)
            shipments[shipment.resource] = shipments.get(shipment.resource, 0.0) + shipment.amount
        for project in state.construction:
            for resource, amount in project.materials_spent.items():
                keys.add(resource)
                materials[resource] = materials.get(resource, 0.0) + amount
        for key in keys:
            resources[key] = resources.get(key, 0.0) + state.resource_total(key)
    population = sum(s.population for s in states)
    infrastructure = (
        sum(s.infrastructure * s.population for s in states) / population if population else 0.0
    )
    return Balance(resources, population, sum(s.money for s in states),
                   _sum_maps(states, "production_capacity"), infrastructure,
                   shipments, materials)


def reconcile(before: Balance, after: Balance, tolerance: float = 1e-9) -> None:
    """Raise with a useful field name if a LOD transition loses conserved state."""
    def close(left: float, right: float) -> bool:
        return abs(left - right) <= tolerance * max(1.0, abs(left), abs(right))

    for name in ("population", "money", "infrastructure"):
        if not close(getattr(before, name), getattr(after, name)):
            raise ValueError(f"LOD reconciliation failed for {name}")
    for name in ("resources", "production_capacity", "shipments", "construction_materials"):
        left, right = getattr(before, name), getattr(after, name)
        for key in set(left) | set(right):
            if not close(left.get(key, 0.0), right.get(key, 0.0)):
                raise ValueError(f"LOD reconciliation failed for {name}.{key}")
