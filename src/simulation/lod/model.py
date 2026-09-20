"""Domain model shared by all levels of detail.

The model deliberately uses resource dictionaries: adding a new commodity must not
require changing the LOD machinery.  Quantities are floats, while the (rare)
rounding difference introduced by a presentation/disaggregation rule is retained
in :attr:`AggregateState.rounding`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Iterable


class LOD(IntEnum):
    """Calculation detail; larger values mean more detail."""

    AGGREGATE = 0
    PLANET = 1
    ENTERPRISE = 2


class LODMode(Enum):
    AUTO = "auto"


AUTO = LODMode.AUTO


@dataclass
class Shipment:
    resource: str
    amount: float
    months_remaining: float
    value: float = 0.0


@dataclass
class Construction:
    id: str
    months_remaining: float
    capacity_on_completion: dict[str, float] = field(default_factory=dict)
    materials_spent: dict[str, float] = field(default_factory=dict)


@dataclass
class AggregateState:
    """Minimum conserved state of an aggregate at any LOD."""

    population: float = 0.0
    available_labour: float = 0.0
    production: dict[str, float] = field(default_factory=dict)
    production_capacity: dict[str, float] = field(default_factory=dict)
    consumption: dict[str, float] = field(default_factory=dict)
    stocks: dict[str, float] = field(default_factory=dict)
    stock_capacity: dict[str, float] = field(default_factory=dict)
    imports: dict[str, float] = field(default_factory=dict)
    exports: dict[str, float] = field(default_factory=dict)
    shipments: list[Shipment] = field(default_factory=list)
    money: float = 0.0
    # Productive/financial capital is distinct from liquid money and is used as
    # the default ownership key when an aggregate cash balance is split again.
    capital: float = 0.0
    committed_money: float = 0.0
    transport_capacity: float = 0.0
    transport_load: float = 0.0
    infrastructure: float = 1.0
    construction: list[Construction] = field(default_factory=list)
    deficit: dict[str, float] = field(default_factory=dict)
    provision: dict[str, float] = field(default_factory=dict)
    rounding: dict[str, float] = field(default_factory=dict)

    def resource_total(self, resource: str) -> float:
        """Goods owned by the node, including cargo and explicit round-off."""
        cargo = sum(s.amount for s in self.shipments if s.resource == resource)
        building = sum(c.materials_spent.get(resource, 0.0) for c in self.construction)
        return self.stocks.get(resource, 0.0) + cargo + building + self.rounding.get(resource, 0.0)


@dataclass
class SimulationNode:
    """Common LOD contract and a persistent node in the simulation tree.

    Children are never discarded by aggregation.  They become passive and are
    re-used when detail is restored.
    """

    id: str
    state: AggregateState = field(default_factory=AggregateState)
    native_lod: LOD = LOD.AGGREGATE
    lod_override: LOD | LODMode = AUTO
    children: list["SimulationNode"] = field(default_factory=list)
    active: bool = True
    # Numerical crumbs which cannot be represented by the child allocations.
    # This belongs to the parent even while its children are active and must
    # therefore never be advanced as an economic entity.
    reconciliation_buffer: AggregateState = field(default_factory=AggregateState)

    def effective_lod(self, inherited: LOD = LOD.AGGREGATE) -> LOD:
        if self.lod_override is not AUTO:
            return LOD(self.lod_override)
        return LOD(inherited)

    def set_lod(self, lod: LOD | LODMode | int | str) -> None:
        if lod is AUTO or lod == "auto":
            self.lod_override = AUTO
        else:
            self.lod_override = LOD(int(lod))

    def nodes_for_tick(self, inherited: LOD) -> Iterable[tuple["SimulationNode", LOD]]:
        """Yield active calculation roots, honouring overrides below a coarse root."""
        effective = self.effective_lod(inherited)
        yield self, effective
        for child in self.children:
            child_lod = child.effective_lod(effective)
            if child.lod_override is not AUTO and child_lod > effective:
                yield from child.nodes_for_tick(child_lod)
