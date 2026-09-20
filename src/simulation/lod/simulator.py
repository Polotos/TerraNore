"""Reference LOD 0--2 economic/physical stepping model."""

from __future__ import annotations

from .model import AUTO, LOD, SimulationNode


class LODSimulator:
    """Deterministic test implementation with monthly-or-smaller substeps."""

    def advance(self, node: SimulationNode, months: float, lod: LOD | None = None) -> None:
        if months < 0:
            raise ValueError("months must be non-negative")
        level = node.effective_lod(lod if lod is not None else node.native_lod)
        maximum_step = 1.0 if level < LOD.ENTERPRISE else 0.25
        remaining = months
        while remaining > 1e-12:
            step = min(maximum_step, remaining)
            self._substep(node, step)
            # A manually detailed descendant is advanced despite coarse parent LOD.
            for child in node.children:
                child_level = child.effective_lod(level)
                if child.lod_override is not AUTO and child_level > level:
                    self.advance(child, step, child_level)
            remaining -= step

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
