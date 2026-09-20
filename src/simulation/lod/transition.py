from __future__ import annotations

from copy import deepcopy
import hashlib
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
                 "stock_capacity", "imports", "exports", "deficit", "rejected_cargo",
                 "losses", "rounding"):
        target = getattr(result, attr)
        for state in states:
            for resource, amount in getattr(state, attr).items():
                target[resource] = target.get(resource, 0.0) + amount
    result.shipments = [deepcopy(x) for s in states for x in s.shipments]
    result.construction = [deepcopy(x) for s in states for x in s.construction]
    result.events = [deepcopy(x) for s in states for x in s.events]
    result.money = sum(s.money for s in states)
    result.capital = sum(s.capital for s in states)
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


def _weights(children: list[SimulationNode], values: list[float]) -> list[float]:
    """Return normalized, non-negative weights in stable child-id order."""
    total = sum(max(0.0, value) for value in values)
    if total:
        return [max(0.0, value) / total for value in values]
    return [1.0 / len(children)] * len(children) if children else []


def _distribute_delta(target: float, current: list[float], weights: list[float]) -> tuple[list[float], float]:
    """Apply an aggregate delta and report the floating point residue."""
    delta = target - sum(current)
    result = [value + delta * weight for value, weight in zip(current, weights)]
    return result, target - sum(result)


def _stable_owner(project_id: str, candidates: list[SimulationNode]) -> SimulationNode:
    """Rendezvous hashing keeps construction placement independent of list order."""
    return max(candidates, key=lambda child: (
        hashlib.sha256(f"{project_id}\0{child.id}".encode()).digest(), child.id
    ))


def _apply_buffer(total: AggregateState, buffer: AggregateState) -> AggregateState:
    """Fold a previous reconciliation buffer into a newly collapsed aggregate."""
    result = aggregate([SimulationNode("aggregate", total), SimulationNode("buffer", buffer)])
    return result


def _disaggregate(node: SimulationNode,
                  money_policy: Callable[[SimulationNode, list[SimulationNode]], list[float]] | None = None) -> None:
    """Apply changes made at coarse LOD to the saved children deterministically."""
    children = sorted(node.children, key=lambda child: child.id)
    parent = node.state
    saved_population = [child.state.population for child in children]
    population_weights = _weights(children, saved_population)
    capital_weights = _weights(children, [child.state.capital for child in children])
    if money_policy is not None:
        capital_weights = _weights(children, list(money_policy(node, children)))

    buffer = AggregateState(infrastructure=0.0)

    def scalar(attribute: str, weights: list[float]) -> None:
        values, residue = _distribute_delta(
            getattr(parent, attribute), [getattr(child.state, attribute) for child in children], weights
        )
        for child, value in zip(children, values):
            setattr(child.state, attribute, value)
        setattr(buffer, attribute, residue)

    scalar("population", population_weights)
    scalar("available_labour", population_weights)
    scalar("money", capital_weights)
    scalar("capital", capital_weights)
    scalar("committed_money", capital_weights)

    trade_weights = _weights(children, [
        child.state.transport_capacity + sum(child.state.imports.values()) + sum(child.state.exports.values())
        for child in children
    ])
    scalar("transport_capacity", trade_weights)
    scalar("transport_load", trade_weights)

    map_rules = {
        "production_capacity": lambda resource: _weights(
            children, [child.state.production_capacity.get(resource, 0.0) for child in children]
        ),
        "stocks": lambda resource: _weights(
            children, [child.state.stock_capacity.get(resource, 0.0) for child in children]
        ),
        "stock_capacity": lambda resource: population_weights,
        "production": lambda resource: _weights(
            children, [child.state.production_capacity.get(resource, 0.0) for child in children]
        ),
        "consumption": lambda resource: population_weights,
        "imports": lambda resource: trade_weights,
        "exports": lambda resource: trade_weights,
        "deficit": lambda resource: population_weights,
        "rejected_cargo": lambda resource: trade_weights,
        "losses": lambda resource: population_weights,
        "rounding": lambda resource: _weights(
            children, [child.state.stock_capacity.get(resource, 0.0) for child in children]
        ),
    }
    for attribute, weight_for in map_rules.items():
        target = getattr(parent, attribute)
        keys = set(target).union(*(getattr(child.state, attribute) for child in children))
        for resource in sorted(keys):
            values, residue = _distribute_delta(
                target.get(resource, 0.0),
                [getattr(child.state, attribute).get(resource, 0.0) for child in children],
                weight_for(resource),
            )
            for child, value in zip(children, values):
                getattr(child.state, attribute)[resource] = value
            if residue:
                getattr(buffer, attribute)[resource] = residue

    # Preserve the aggregate population-weighted infrastructure exactly while
    # retaining the children's relative infrastructure where possible.
    weighted = sum(child.state.infrastructure * child.state.population for child in children)
    target_weighted = parent.infrastructure * parent.population
    if weighted:
        factor = target_weighted / weighted
        for child in children:
            child.state.infrastructure *= factor
    else:
        for child in children:
            child.state.infrastructure = parent.infrastructure

    # Coarse ticks own the authoritative in-transit set.  Incoming/outgoing
    # trade volumes choose a destination; warehouse capacity is the fallback.
    for child in children:
        child.state.shipments = []
    for shipment in parent.shipments:
        weights = [
            child.state.imports.get(shipment.resource, 0.0) + child.state.exports.get(shipment.resource, 0.0)
            for child in children
        ]
        if not any(value > 0 for value in weights):
            weights = [child.state.stock_capacity.get(shipment.resource, 0.0) for child in children]
        owner = max(zip(_weights(children, weights), children), key=lambda item: (item[0], item[1].id))[1]
        owner.state.shipments.append(deepcopy(shipment))

    # Completed projects vanished during coarse ticks; new/unfinished projects
    # are restored from the parent and assigned via a stable identifier.
    for child in children:
        child.state.construction = []
    for project in sorted(parent.construction, key=lambda item: item.id):
        industries = set(project.capacity_on_completion)
        suitable = [child for child in children if any(
            child.state.production_capacity.get(industry, 0.0) > 0 for industry in industries
        )] or children
        _stable_owner(project.id, suitable).state.construction.append(deepcopy(project))

    # Provision is a ratio, not an additive balance. Preserve the current
    # aggregate reading rather than resurrecting stale child values.
    for child in children:
        child.state.provision = dict(parent.provision)
    node.reconciliation_buffer = buffer


def change_lod(node: SimulationNode, target: LOD,
               synthesizer: Callable[[SimulationNode, LOD], list[SimulationNode]] | None = None,
               money_policy: Callable[[SimulationNode, list[SimulationNode]], list[float]] | None = None) -> None:
    """Change detail, preserving passive children and checking all balances."""
    if target < node.native_lod and node.children:
        before = balance_of(node.children)
        node.state = _apply_buffer(aggregate(node.children), node.reconciliation_buffer)
        node.reconciliation_buffer = AggregateState()
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
        # Synthesizers commonly return nodes with their dataclass default
        # (active=True).  Keep the activation boundary explicit: no detailed
        # node may be observed or ticked before it contains the coarse deltas.
        for child in node.children:
            child.active = False
        before = balance_of([node])
        _disaggregate(node, money_policy)
        for child in node.children:
            child.active = True
        # Activation happens only after redistribution.  The buffer participates
        # in conservation checks but is never activated or simulated.
        buffered = [*node.children, SimulationNode(f"{node.id}:reconciliation", node.reconciliation_buffer)]
        reconcile(before, balance_of(buffered))
        node.native_lod = target
