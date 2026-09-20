from __future__ import annotations

from copy import deepcopy

from ..model import World
from .model import AUTO, AggregateState, LOD, SimulationNode
from .reconciliation import balance_of, reconcile
from .transition import change_lod


EXTERNAL_LODS = {
    "auto": AUTO,
    "lod-0": LOD.AGGREGATE,
    "lod-1": LOD.PLANET,
    "lod-2": LOD.ENTERPRISE,
}


def external_lod(node: SimulationNode) -> str:
    if node.lod_override is AUTO:
        return "auto"
    return f"lod-{int(node.lod_override)}"


def _synthesize(node: SimulationNode, target: LOD) -> list[SimulationNode]:
    # The transition service will conservatively distribute the parent state
    # into these stable children before activating them.
    return [
        SimulationNode(f"{node.id}:part-{index}", AggregateState(), target)
        for index in range(2)
    ]


def _represented_nodes(node: SimulationNode) -> list[SimulationNode]:
    if node.native_lod == LOD.AGGREGATE:
        return [node]
    buffer = SimulationNode(f"{node.id}:reconciliation", node.reconciliation_buffer)
    return [*node.children, buffer]


class DetailSelector:
    """Single transactional service for domain LOD transitions."""

    LEVELS = tuple(EXTERNAL_LODS)

    def set_level(self, world: World, object_id: str, level: str) -> SimulationNode:
        if level not in EXTERNAL_LODS:
            raise ValueError(f"unknown detail level: {level}")
        original = world.simulation_node(object_id)
        candidate = deepcopy(original)
        requested = EXTERNAL_LODS[level]
        target = LOD.AGGREGATE if requested is AUTO else LOD(requested)

        before = balance_of(_represented_nodes(candidate))
        change_lod(candidate, target, synthesizer=_synthesize)
        reconcile(before, balance_of(_represented_nodes(candidate)))

        # The manual override is committed only after transition and
        # reconciliation have both succeeded.
        candidate.set_lod(requested)
        world._lod_nodes[object_id] = candidate
        return candidate
