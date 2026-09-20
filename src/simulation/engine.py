from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from .model import World
from .workers import (
    Change,
    Delta,
    DeterministicScheduler,
    Event,
    Phase,
    Shipment,
    StableKey,
    TradeOrder,
    WorldSnapshot,
    freeze_world,
    random_stream,
    resolve_orders,
)


@dataclass(frozen=True)
class PublishedTick:
    tick: int
    population: int
    treasury: float
    event_count: int


class Simulation:
    """Barrier-based, deterministic simulation pipeline.

    Workers only see frozen snapshots and return :class:`Delta`; mutations are
    performed centrally after sorting.  Consequently thread completion order is
    never observable in world state.
    """

    TICKS_PER_YEAR = 12

    def __init__(
        self,
        seed: int = 42,
        workers: int | str | None = "auto",
        world: World | None = None,
        *,
        max_pending: int | None = None,
        history_limit: int = 1_200,
        event_limit: int = 10_000,
    ) -> None:
        if history_limit < 1 or event_limit < 1:
            raise ValueError("history and event limits must be positive")
        self.scheduler = DeterministicScheduler(workers, max_pending)
        self.world = world or World.create(seed)
        self.history: deque[PublishedTick] = deque(maxlen=history_limit)
        self.events: deque[Event] = deque(maxlen=event_limit)

    @staticmethod
    def _key(snapshot: WorldSnapshot, phase: Phase, region_id: str, operation: str, sequence: int = 0) -> StableKey:
        return StableKey(phase, snapshot.tick, region_id, operation, sequence)

    @staticmethod
    def _calculate_needs(snapshot: WorldSnapshot, region_id: str) -> Delta:
        region = snapshot.region(region_id)
        consumption = region.population * 0.00082 * region.economy.price_index
        balance = region.economy.production - consumption
        order = TradeOrder(region.id, -balance, region.economy.price_index)
        return Delta(
            Simulation._key(snapshot, Phase.NEEDS, region.id, "consumption"),
            (Change("economy.consumption", consumption), Change("economy.treasury", -consumption, operation="add")),
            trade_orders=(order,),
        )

    @staticmethod
    def _calculate_production(snapshot: WorldSnapshot, region_id: str) -> Delta:
        region = snapshot.region(region_id)
        rng = random_stream(snapshot.seed, snapshot.tick, Phase.PRODUCTION, region.id, "output")
        variation = rng.uniform(-0.00035, 0.00035)
        capacity = max(0.4, min(1.4, region.resources / 1_000))
        production = region.economy.production * (1 + 0.0015 * capacity + variation)
        return Delta(
            Simulation._key(snapshot, Phase.PRODUCTION, region.id, "production"),
            (
                Change("economy.production", production),
                Change("economy.treasury", production, operation="add"),
                Change("resources", max(0.0, region.resources - production * 0.00008)),
            ),
        )

    @staticmethod
    def _calculate_trade_offer(snapshot: WorldSnapshot, region_id: str) -> Delta:
        region = snapshot.region(region_id)
        balance = region.economy.production - region.economy.consumption
        order = TradeOrder(region.id, -balance, region.economy.price_index)
        demand_ratio = region.economy.consumption / max(region.economy.production, 0.01)
        price = max(0.5, min(3.0, region.economy.price_index * (0.995 + 0.005 * demand_ratio)))
        return Delta(
            Simulation._key(snapshot, Phase.TRADE, region.id, "offer"),
            (Change("economy.price_index", price),),
            trade_orders=(order,),
        )

    @staticmethod
    def _calculate_shipment(snapshot: WorldSnapshot, shipment: Shipment) -> Delta:
        # Route capacity is local to this shipment and can be calculated in
        # parallel.  The toy world currently has fully connected, unlimited
        # routes; this explicit phase is where route capacity belongs.
        value = shipment.quantity * shipment.unit_price
        return Delta(
            Simulation._key(snapshot, Phase.TRANSPORT, shipment.buyer_id, "shipment", shipment.sequence),
            (
                Change("economy.treasury", -value, shipment.buyer_id, "add"),
                Change("economy.treasury", value, shipment.seller_id, "add"),
            ),
            (Event("shipment", (("seller", shipment.seller_id), ("buyer", shipment.buyer_id), ("quantity", shipment.quantity))),),
        )

    @staticmethod
    def _calculate_demography(snapshot: WorldSnapshot, region_id: str) -> Delta:
        region = snapshot.region(region_id)
        rng = random_stream(snapshot.seed, snapshot.tick, Phase.DEMOGRAPHY, region.id, "population")
        variation = rng.uniform(-0.00035, 0.00035)
        population = max(1, round(region.population * (1.0002 + variation / 5)))
        return Delta(Simulation._key(snapshot, Phase.DEMOGRAPHY, region.id, "population"), (Change("population", population),))

    @staticmethod
    def _calculate_investment(snapshot: WorldSnapshot, region_id: str) -> Delta:
        region = snapshot.region(region_id)
        budget = max(0.0, region.economy.treasury - 1_000.0) * 0.0005
        if budget == 0:
            return Delta(Simulation._key(snapshot, Phase.INVESTMENT, region.id, "hold"))
        return Delta(
            Simulation._key(snapshot, Phase.INVESTMENT, region.id, "construction"),
            (
                Change("economy.treasury", -budget, operation="add"),
                Change("economy.production", budget * 0.01, operation="add"),
            ),
            (Event("construction", (("region", region.id), ("investment", budget))),),
        )

    @staticmethod
    def _calculate_maintenance(snapshot: WorldSnapshot, region_id: str) -> Delta:
        region = snapshot.region(region_id)
        maintenance = region.economy.production * 0.0001
        worn_production = region.economy.production * 0.99999
        return Delta(
            Simulation._key(snapshot, Phase.MAINTENANCE, region.id, "wear"),
            (
                Change("economy.treasury", -maintenance, operation="add"),
                Change("economy.production", worn_production),
            ),
        )

    @staticmethod
    def _invariant(snapshot: WorldSnapshot, region_id: str) -> Delta:
        region = snapshot.region(region_id)
        if region.population < 1 or region.resources < 0 or region.economy.production < 0:
            raise RuntimeError(f"world invariant failed for {region.id}")
        return Delta(Simulation._key(snapshot, Phase.INVARIANTS, region.id, "validated"))

    def _apply(self, deltas: list[Delta]) -> tuple[Event, ...]:
        regions = {region.id: region for region in self.world.regions}
        published: list[Event] = []
        for delta in sorted(deltas, key=lambda item: item.key):
            target_default = delta.key.object_id
            for change in delta.changes:
                target = regions[change.object_id or target_default]
                owner: object = target
                parts = change.path.split(".")
                for part in parts[:-1]:
                    owner = getattr(owner, part)
                current = getattr(owner, parts[-1])
                value = current + change.value if change.operation == "add" else change.value
                if change.operation not in ("add", "set"):
                    raise ValueError(f"unknown delta operation: {change.operation}")
                setattr(owner, parts[-1], value)
            self.events.extend(delta.events)
            published.extend(delta.events)
        return tuple(published)

    def _region_phase(
        self, phase: Phase, function: Callable[[WorldSnapshot, str], Delta]
    ) -> tuple[list[Delta], tuple[Event, ...]]:
        snapshot = freeze_world(self.world)
        deltas = self.scheduler.map(partial(_run_region_phase, function, snapshot), snapshot.regions)
        return deltas, self._apply(deltas)

    def _tick(self) -> tuple[Event, ...]:
        published: list[Event] = []

        def region_phase(phase: Phase, function: Callable[[WorldSnapshot, str], Delta]) -> list[Delta]:
            deltas, events = self._region_phase(phase, function)
            published.extend(events)
            return deltas

        # 1. Immutable input snapshot; each subsequent freeze is a phase barrier.
        freeze_world(self.world)
        region_phase(Phase.NEEDS, self._calculate_needs)
        region_phase(Phase.PRODUCTION, self._calculate_production)

        # 4. Publish in parallel, then resolve centrally and deterministically.
        offers = region_phase(Phase.TRADE, self._calculate_trade_offer)
        orders = (order for delta in sorted(offers, key=lambda item: item.key) for order in delta.trade_orders)
        shipments = resolve_orders(orders)

        # 5. Route admissibility/dispatch is independent per resolved shipment.
        transport_snapshot = freeze_world(self.world)
        published.extend(self._apply(
            self.scheduler.map(partial(self._calculate_shipment, transport_snapshot), shipments)
        ))
        region_phase(Phase.DEMOGRAPHY, self._calculate_demography)
        region_phase(Phase.INVESTMENT, self._calculate_investment)
        region_phase(Phase.MAINTENANCE, self._calculate_maintenance)
        region_phase(Phase.INVARIANTS, self._invariant)

        # Publication is after all barriers and retained in a bounded deque.
        self.world.tick += 1
        self.world.current_date = self.world.initial_date.add_ticks(self.world.tick)
        self.history.append(PublishedTick(
            self.world.tick,
            sum(region.population for region in self.world.regions),
            sum(region.economy.treasury for region in self.world.regions),
            len(self.events),
        ))
        return tuple(published)

    def step(self, ticks: int = 1) -> World:
        if ticks < 0:
            raise ValueError("ticks must be non-negative")
        for _ in range(ticks):
            self._tick()
        return self.world

    def run_years(self, years: int) -> World:
        if years < 0:
            raise ValueError("years must be non-negative")
        return self.step(years * self.TICKS_PER_YEAR)

    def close(self) -> None:
        self.scheduler.close()


def _run_region_phase(
    function: Callable[[WorldSnapshot, str], Delta], snapshot: WorldSnapshot, region: object
) -> Delta:
    """Pickle-friendly process worker used by every per-region phase."""
    return function(snapshot, region.id)  # type: ignore[attr-defined]
