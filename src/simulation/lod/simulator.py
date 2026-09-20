"""Reference LOD 0--2 economic/physical stepping model."""

from __future__ import annotations

from copy import deepcopy

from .model import AUTO, AggregateState, LOD, SimulationNode
from .reconciliation import balance_of, reconcile
from .transition import aggregate


class LODSimulator:
    """Deterministic test implementation with monthly-or-smaller substeps."""

    def advance(self, node: SimulationNode, months: float, lod: LOD | None = None) -> None:
        if months < 0:
            raise ValueError("months must be non-negative")
        level = node.effective_lod(lod if lod is not None else node.native_lod)
        maximum_step = 1.0 if level < LOD.ENTERPRISE else 0.25
        remaining = months
        production: dict[str, float] = {}
        detailed_roots = self._detailed_roots(node, level)
        while remaining > 1e-12:
            step = min(maximum_step, remaining)
            if detailed_roots:
                # The coarse state is inclusive.  Run only its remainder here;
                # otherwise every pinned root would be simulated a second time.
                remainder = self._subtract(
                    node.state, [root.state for root, _ in detailed_roots]
                )
                self._substep(SimulationNode(f"{node.id}:remainder", remainder), step)
                for root, root_level in detailed_roots:
                    self.advance(root, step, root_level)

                parts = [
                    SimulationNode(f"{node.id}:remainder", remainder),
                    *(root for root, _ in detailed_roots),
                ]
                merged = aggregate(parts)
                # Check the join itself (rather than comparing across a tick,
                # during which production and consumption legitimately change).
                reconcile(balance_of(parts), balance_of([SimulationNode(node.id, merged)]))
                node.state = merged
            else:
                self._substep(node, step)
            for resource, amount in node.state.production.items():
                production[resource] = production.get(resource, 0.0) + amount
            remaining -= step
        if months > 0:
            node.state.production = production

    @classmethod
    def _detailed_roots(cls, node: SimulationNode, inherited: LOD) -> list[tuple[SimulationNode, LOD]]:
        """Return disjoint manually detailed roots below an inclusive aggregate."""
        roots: list[tuple[SimulationNode, LOD]] = []

        def visit(parent: SimulationNode) -> None:
            for child in parent.children:
                level = child.effective_lod(inherited)
                if child.lod_override is not AUTO and level > inherited:
                    roots.append((child, level))
                else:
                    visit(child)

        visit(node)
        return roots

    @staticmethod
    def _subtract(total: AggregateState, excluded: list[AggregateState]) -> AggregateState:
        """Copy ``total`` and remove states represented by detailed roots."""
        result = deepcopy(total)
        excluded_population = sum(state.population for state in excluded)
        remaining_population = result.population - excluded_population
        weighted_infrastructure = result.infrastructure * result.population
        weighted_infrastructure -= sum(state.infrastructure * state.population for state in excluded)

        for attribute in (
            "population", "available_labour", "money", "capital", "committed_money",
            "transport_capacity", "transport_load",
        ):
            value = getattr(result, attribute) - sum(getattr(state, attribute) for state in excluded)
            setattr(result, attribute, 0.0 if abs(value) < 1e-12 else value)

        for attribute in (
            "production", "production_capacity", "consumption", "stocks",
            "stock_capacity", "imports", "exports", "deficit", "rounding",
        ):
            values = getattr(result, attribute)
            for state in excluded:
                for resource, amount in getattr(state, attribute).items():
                    value = values.get(resource, 0.0) - amount
                    values[resource] = 0.0 if abs(value) < 1e-12 else value

        # Aggregate list members are deep copies, so value equality is the
        # appropriate multiset operation here.
        for attribute in ("shipments", "construction"):
            values = getattr(result, attribute)
            for state in excluded:
                for member in getattr(state, attribute):
                    try:
                        values.remove(member)
                    except ValueError:
                        pass

        result.infrastructure = (
            weighted_infrastructure / remaining_population
            if abs(remaining_population) > 1e-12 else 0.0
        )
        for resource in set(result.provision) | set().union(*(state.provision for state in excluded)):
            total_demand = total.consumption.get(resource, 0.0)
            excluded_demand = sum(state.consumption.get(resource, 0.0) for state in excluded)
            remaining_demand = total_demand - excluded_demand
            supplied_share = total.provision.get(resource, 1.0) * total_demand
            supplied_share -= sum(
                state.provision.get(resource, 1.0) * state.consumption.get(resource, 0.0)
                for state in excluded
            )
            result.provision[resource] = supplied_share / remaining_demand if remaining_demand else 1.0
        return result

    @staticmethod
    def _substep(node: SimulationNode, months: float) -> None:
        state = node.state
        # Arrival precedes production/consumption, so cargo due this month is usable.
        pending = []
        for shipment in state.shipments:
            shipment.months_remaining -= months
            if shipment.months_remaining <= 1e-12:
                capacity = state.stock_capacity.get(shipment.resource, float("inf"))
                accepted = min(shipment.amount,
                               max(0.0, capacity - state.stocks.get(shipment.resource, 0.0)))
                state.stocks[shipment.resource] = state.stocks.get(shipment.resource, 0.0) + accepted
                state.rounding[shipment.resource] = state.rounding.get(shipment.resource, 0.0) + shipment.amount - accepted
                state.committed_money = max(0.0, state.committed_money - shipment.value)
            else:
                pending.append(shipment)
        state.shipments = pending

        labour_factor = min(1.0, state.available_labour / max(1.0, state.population))
        infra_factor = max(0.0, min(1.0, state.infrastructure))
        state.production = {}
        resources = set(state.production_capacity) | set(state.consumption)
        for resource in resources:
            made = state.production_capacity.get(resource, 0.0) * labour_factor * infra_factor * months
            state.production[resource] = made
            available = state.stocks.get(resource, 0.0) + made
            wanted = state.consumption.get(resource, 0.0) * months
            used = min(available, wanted)
            capacity = state.stock_capacity.get(resource, float("inf"))
            state.stocks[resource] = min(capacity, available - used)
            state.rounding[resource] = state.rounding.get(resource, 0.0) + max(0.0, available - used - capacity)
            shortage = wanted - used
            state.deficit[resource] = state.deficit.get(resource, 0.0) + shortage
            state.provision[resource] = 1.0 if wanted == 0 else used / wanted

        unfinished = []
        for project in state.construction:
            project.months_remaining -= months
            if project.months_remaining <= 1e-12:
                for resource, capacity in project.capacity_on_completion.items():
                    state.production_capacity[resource] = state.production_capacity.get(resource, 0.0) + capacity
            else:
                unfinished.append(project)
        state.construction = unfinished
