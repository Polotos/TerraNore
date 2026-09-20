from __future__ import annotations

from copy import deepcopy
from typing import Callable

from .model import AggregateState, LOD, SimulationNode
from .reconciliation import balance_of, reconcile


def aggregate(children: list[SimulationNode]) -> AggregateState:
    """Create a conservative aggregate without changing the detailed objects."""
    if not children:
        return AggregateState()
    result = AggregateState()
    states = [child.state for child in children]
    result.population = sum(s.population for s in states)
    result.available_labour = sum(s.available_labour for s in states)
    for attr in ("production", "production_capacity", "consumption", "stocks",
                 "stock_capacity", "imports", "exports", "deficit", "rounding"):
        target = getattr(result, attr)
        for state in states:
            for resource, amount in getattr(state, attr).items():
                target[resource] = target.get(resource, 0.0) + amount
    result.shipments = [deepcopy(x) for s in states for x in s.shipments]
    result.construction = [deepcopy(x) for s in states for x in s.construction]
    result.money = sum(s.money for s in states)
    result.committed_money = sum(s.committed_money for s in states)
    result.transport_capacity = sum(s.transport_capacity for s in states)
    result.transport_load = sum(s.transport_load for s in states)
    result.infrastructure = (sum(s.infrastructure * s.population for s in states) /
                             result.population if result.population else 0.0)
    for resource in set().union(*(s.provision for s in states)):
        demand = sum(s.consumption.get(resource, 0.0) for s in states)
        result.provision[resource] = (sum(s.provision.get(resource, 1.0) *
                                             s.consumption.get(resource, 0.0) for s in states) /
                                      demand if demand else 1.0)
    return result


def change_lod(node: SimulationNode, target: LOD,
               synthesizer: Callable[[SimulationNode, LOD], list[SimulationNode]] | None = None) -> None:
    """Change detail, preserving passive children and checking all balances."""
    if target < node.native_lod and node.children:
        before = balance_of(node.children)
        node.state = aggregate(node.children)
        for child in node.children:
            child.active = False
        node.native_lod = target
        reconcile(before, balance_of([node]))
        return
    if target > node.native_lod:
        if not node.children:
            if synthesizer is None:
                raise ValueError("missing detail and no synthesizer was provided")
            node.children = synthesizer(node, target)
        before = balance_of([node])
        for child in node.children:
            child.active = True
        reconcile(before, balance_of(node.children))
        node.native_lod = target
