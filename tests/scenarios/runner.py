"""Deterministic scenario runner with explicit physical and monetary ledgers."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .fixed_world import Cargo, FixedWorld, RESOURCES, Route, Settlement

PRICE = {"food": 3, "ore": 5, "fuel": 4, "goods": 8}
CONSUMPTION_DIVISOR = {"food": 18, "ore": 0, "fuel": 0, "goods": 75}


@dataclass(frozen=True)
class Event:
    id: str
    month: int
    kind: str
    object_id: str
    reason: str
    causes: tuple[str, ...] = ()


@dataclass
class Ledger:
    initial_goods: dict[str, int]
    produced: dict[str, int] = field(default_factory=lambda: dict.fromkeys(RESOURCES, 0))
    consumed: dict[str, int] = field(default_factory=lambda: dict.fromkeys(RESOURCES, 0))
    losses: dict[str, int] = field(default_factory=lambda: dict.fromkeys(RESOURCES, 0))
    initial_money: int = 0
    money_sources: int = 0
    money_sinks: int = 0


@dataclass(frozen=True)
class Plan:
    settlement_id: str
    production: tuple[tuple[str, int], ...]
    consumption: tuple[tuple[str, int], ...]
    loss: tuple[tuple[str, int], ...]
    input_use: tuple[tuple[str, int], ...]


class ScenarioRunner:
    """Run the fixture using stable planning and centrally ordered commits."""

    def __init__(self, world: FixedWorld, workers: int = 1, default_lod: int = 2) -> None:
        if workers < 1 or default_lod not in (0, 1, 2):
            raise ValueError("workers must be positive and LOD must be 0, 1 or 2")
        self.world, self.workers, self.default_lod = world, workers, default_lod
        self.events: list[Event] = []
        self.history: list[dict] = []
        self.route_usage: dict[tuple[int, str], int] = {}
        self.arrivals: list[tuple[str, int, int]] = []
        initial = {r: sum(s.stocks[r] for s in world.settlements) for r in RESOURCES}
        self.ledger = Ledger(initial, initial_money=sum(s.money for s in world.settlements))

    def set_lod(self, settlement_id: str, lod: int, *, lock: bool = False) -> None:
        settlement = self._settlements()[settlement_id]
        settlement.lod = lod
        settlement.lod_lock = lod if lock else None

    def apply_automatic_lod(self, lod: int) -> None:
        self.default_lod = lod
        for settlement in self.world.settlements:
            if settlement.lod_lock is None:
                settlement.lod = lod

    def run(self, months: int, switches: dict[int, int] | None = None,
            checkpoint: Callable[["ScenarioRunner"], None] | None = None) -> "ScenarioRunner":
        for _ in range(months):
            if switches and self.world.month in switches:
                self.apply_automatic_lod(switches[self.world.month])
            self._tick()
            if checkpoint:
                checkpoint(self)
        return self

    def _settlements(self) -> dict[str, Settlement]:
        return {item.id: item for item in self.world.settlements}

    def _event(self, kind: str, object_id: str, reason: str, causes: tuple[str, ...] = ()) -> str:
        identifier = f"event-{len(self.events) + 1}"
        self.events.append(Event(identifier, self.world.month, kind, object_id, reason, causes))
        return identifier

    def _deliver(self) -> None:
        settlements, pending = self._settlements(), []
        for cargo in self.world.cargo:
            if cargo.due > self.world.month:
                pending.append(cargo)
                continue
            target = settlements[cargo.destination]
            free = target.warehouses[cargo.resource] - target.stocks[cargo.resource]
            accepted = min(free, cargo.amount)
            target.stocks[cargo.resource] += accepted
            self.ledger.losses[cargo.resource] += cargo.amount - accepted
            self.arrivals.append((cargo.id, cargo.due, self.world.month))
            self._event("shipment-arrived", cargo.destination,
                        f"{cargo.resource} cargo reached its calculated due month")
        self.world.cargo = pending

    @staticmethod
    def _plan(item: Settlement) -> Plan:
        stocks = dict(item.stocks)
        output: dict[str, int] = {}
        inputs = dict.fromkeys(RESOURCES, 0)
        # Goods require ore and fuel; this is intentionally calculated from a
        # private snapshot so parallel completion order cannot affect output.
        for resource in RESOURCES:
            amount = item.capacity[resource]
            if resource == "goods":
                amount = min(amount, stocks["ore"] // 2, stocks["fuel"])
                stocks["ore"] -= amount * 2
                stocks["fuel"] -= amount
                inputs["ore"] += amount * 2
                inputs["fuel"] += amount
            output[resource] = amount
            stocks[resource] += amount
        wanted = {r: (item.population // d if d else 0) for r, d in CONSUMPTION_DIVISOR.items()}
        used = {r: min(stocks[r], wanted[r]) for r in RESOURCES}
        for resource in RESOURCES:
            stocks[resource] -= used[resource]
        loss = {r: (stocks[r] // 200 if r == "food" else 0) for r in RESOURCES}
        return Plan(item.id, tuple(output.items()), tuple(used.items()), tuple(loss.items()), tuple(inputs.items()))

    def _produce_and_consume(self) -> None:
        ordered = sorted(self.world.settlements, key=lambda item: item.id)
        if self.workers == 1:
            plans = map(self._plan, ordered)
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                plans = pool.map(self._plan, ordered)
        for plan in plans:
            item = self._settlements()[plan.settlement_id]
            production, consumption, losses, inputs = map(dict, (
                plan.production, plan.consumption, plan.loss, plan.input_use))
            for resource in RESOURCES:
                item.stocks[resource] -= inputs[resource]
                item.stocks[resource] += production[resource]
                item.stocks[resource] -= consumption[resource] + losses[resource]
                overflow = max(0, item.stocks[resource] - item.warehouses[resource])
                item.stocks[resource] -= overflow
                self.ledger.produced[resource] += production[resource]
                self.ledger.consumed[resource] += consumption[resource] + inputs[resource]
                self.ledger.losses[resource] += losses[resource] + overflow
            if consumption["food"] < item.population // CONSUMPTION_DIVISOR["food"]:
                cause = self._event("deficit", item.id, "food demand exceeded available stock")
                old = item.population
                item.population = max(0, item.population - max(1, item.population // 500))
                self._event("demographic-crisis", item.id,
                            "food deficit reduced population", (cause,))
                if old and item.population == 0:
                    self._event("settlement-closed", item.id,
                                "population reached zero after shortages", (cause,))

    def _trade(self) -> None:
        settlements = self._settlements()
        for route in sorted(self.world.routes, key=lambda item: item.id):
            origin, target = settlements[route.origin], settlements[route.destination]
            used = 0
            for resource in RESOURCES:
                reserve = origin.warehouses[resource] // 3
                target_level = target.warehouses[resource] // 2
                amount = min(max(0, origin.stocks[resource] - reserve),
                             max(0, target_level - target.stocks[resource]), route.capacity - used)
                affordable = target.money // PRICE[resource]
                amount = min(amount, affordable)
                if amount <= 0:
                    continue
                value = amount * PRICE[resource]
                origin.stocks[resource] -= amount
                origin.money += value
                target.money -= value
                used += amount
                cargo_id = f"cargo-{self.world.month}-{route.id}-{resource}"
                self.world.cargo.append(Cargo(cargo_id, origin.id, target.id, resource,
                                              amount, self.world.month,
                                              self.world.month + route.travel_months, route.id))
                self._event("shipment-dispatched", route.id,
                            f"stock imbalance sent {resource} toward {target.id}")
            self.route_usage[(self.world.month, route.id)] = used

    def _tick(self) -> None:
        self._deliver()
        self._produce_and_consume()
        self._trade()
        self.world.month += 1
        self.history.append(self.metrics())

    def metrics(self) -> dict:
        stocks = {r: sum(s.stocks[r] for s in self.world.settlements) for r in RESOURCES}
        transit = {r: sum(c.amount for c in self.world.cargo if c.resource == r) for r in RESOURCES}
        return {
            "month": self.world.month,
            "population": sum(s.population for s in self.world.settlements),
            "production": dict(self.ledger.produced), "consumption": dict(self.ledger.consumed),
            "stocks": stocks, "in_transit": transit,
            "trade": sum(transit.values()),
            "infrastructure": sum(s.infrastructure for s in self.world.settlements),
            "money": sum(s.money for s in self.world.settlements),
            "crisis_dates": [e.month for e in self.events if e.kind == "demographic-crisis"],
        }

    def diagnostics(self) -> dict:
        """Expose the causal questions which make an acceptance build useful."""
        movements = [e for e in self.events if e.kind.startswith("shipment-")]
        deficits = [e for e in self.events if e.kind == "deficit"]
        return {
            "resource_sources": dict(self.ledger.produced),
            "goods_destinations": sorted({c.destination for c in self.world.cargo}),
            "critical_routes": sorted(r.id for r in self.world.routes
                                      if any(self.route_usage.get((m, r.id), 0) == r.capacity
                                             for m in range(self.world.month))),
            "bottlenecks": [r.id for r in self.world.routes
                            if self.route_usage.get((self.world.month - 1, r.id), 0) == r.capacity],
            "buildings": {s.id: sorted(s.open_buildings) for s in self.world.settlements},
            "closures": [asdict(e) for e in self.events if "closed" in e.kind],
            "deficits": [asdict(e) for e in deficits],
            "consequences": [asdict(e) for e in self.events if e.causes],
            "movements": [asdict(e) for e in movements],
        }

    def assert_invariants(self) -> None:
        for settlement in self.world.settlements:
            assert settlement.population >= 0 and settlement.money >= 0
            for resource in RESOURCES:
                assert 0 <= settlement.stocks[resource] <= settlement.warehouses[resource]
        for resource in RESOURCES:
            current = sum(s.stocks[resource] for s in self.world.settlements)
            current += sum(c.amount for c in self.world.cargo if c.resource == resource)
            expected = (self.ledger.initial_goods[resource] + self.ledger.produced[resource]
                        - self.ledger.consumed[resource] - self.ledger.losses[resource])
            assert current == expected, (resource, current, expected)
        money = sum(s.money for s in self.world.settlements)
        assert money == self.ledger.initial_money + self.ledger.money_sources - self.ledger.money_sinks
        assert all(actual >= due for _cargo, due, actual in self.arrivals)
        capacities = {r.id: r.capacity for r in self.world.routes}
        assert all(amount <= capacities[route] for (_month, route), amount in self.route_usage.items())
        assert all(event.reason for event in self.events if event.kind in {
            "deficit", "demographic-crisis", "settlement-closed", "shipment-dispatched"})

    def save(self, path: str | Path) -> int:
        world_data = asdict(self.world)
        for settlement in world_data["settlements"]:
            settlement["open_buildings"] = sorted(settlement["open_buildings"])
        payload = {
            "world": world_data, "workers": self.workers, "default_lod": self.default_lod,
            "events": [asdict(e) for e in self.events], "history": self.history,
            "route_usage": [[month, route, value] for (month, route), value in self.route_usage.items()],
            "arrivals": self.arrivals, "ledger": asdict(self.ledger),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        Path(path).write_text(encoded, encoding="utf-8")
        return len(encoded.encode())

    @classmethod
    def load(cls, path: str | Path, *, workers: int | None = None) -> "ScenarioRunner":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        raw = data["world"]
        settlements = [Settlement(**{**s, "open_buildings": set(s["open_buildings"])})
                       for s in raw["settlements"]]
        world = FixedWorld(raw["seed"], raw["month"], raw["sector"], tuple(raw["systems"]),
                           settlements, tuple(Route(**r) for r in raw["routes"]),
                           [Cargo(**c) for c in raw["cargo"]])
        runner = cls(world, workers or data["workers"], data["default_lod"])
        runner.events = [Event(**{**e, "causes": tuple(e["causes"])}) for e in data["events"]]
        runner.history = data["history"]
        runner.route_usage = {(m, r): v for m, r, v in data["route_usage"]}
        runner.arrivals = [tuple(item) for item in data["arrivals"]]
        runner.ledger = Ledger(**data["ledger"])
        return runner
