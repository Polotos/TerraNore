from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import struct
import threading
from copy import deepcopy
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.persistence import EventLog, EventRecord, FORMAT, SnapshotStore, TimeSeries, load_snapshot, save_snapshot
from src.simulation import Simulation
from src.simulation.lod import DetailSelector, external_lod
from src.simulation.model import Economy, Region, World

from .dto import WorldDTO
from .tasks import SimulationTask

API = "/api/test/v1"
UI_ROOT = Path(__file__).parents[1] / "ui"
MAX_SERIES_POINTS = 10_000


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _clone_world(world: World) -> World:
    data = world.to_dict()
    regions = [Region(**{**item, "economy": Economy(**item["economy"])}) for item in data["regions"]]
    return World(
        data["seed"], data["tick"], regions, data["system_count"],
        data["settlement_count"], data["start_date"],
        data["accuracy_profile"],
    )


ACCURACY_PROFILES = frozenset({"fast", "balanced", "research"})


def _world_configuration(data: dict, defaults: World, default_workers: int) -> dict:
    """Validate and normalize the public world-creation contract."""
    seed = data.get("seed", defaults.seed)
    if isinstance(seed, bool) or not isinstance(seed, int) or not -(2**63) <= seed < 2**63:
        raise ApiError(400, "seed must be a signed 64-bit integer")
    systems = data.get("systemCount", defaults.system_count)
    settlements = data.get("settlementCount", defaults.settlement_count)
    if isinstance(systems, bool) or not isinstance(systems, int) or not 1 <= systems <= 10_000:
        raise ApiError(400, "systemCount must be between 1 and 10000")
    if isinstance(settlements, bool) or not isinstance(settlements, int) or not 1 <= settlements <= 100_000:
        raise ApiError(400, "settlementCount must be between 1 and 100000")
    start_date = data.get("startDate", defaults.start_date)
    try:
        date.fromisoformat(start_date)
    except (TypeError, ValueError):
        raise ApiError(400, "startDate must be an ISO calendar date") from None
    profile = data.get("accuracyProfile", defaults.accuracy_profile)
    if profile not in ACCURACY_PROFILES:
        raise ApiError(400, "unsupported accuracyProfile")
    workers = data.get("workers", default_workers)
    if workers != "auto" and (isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 256):
        raise ApiError(400, "workers must be 'auto' or between 1 and 256")
    return {
        "seed": seed, "system_count": systems, "settlement_count": settlements,
        "start_date": start_date, "accuracy_profile": profile, "workers": workers,
    }


class AppState:
    def __init__(self, seed: int = 42, snapshot_interval: int | None = None) -> None:
        self.simulation = Simulation(seed)
        self.branch_id = "main"
        self.snapshot_store = SnapshotStore(self.simulation.world, interval=snapshot_interval)
        self.series = TimeSeries()
        self.series.capture(self.simulation.world)
        self.object_series = self._new_object_series(self.simulation.world)
        self.lock = threading.RLock()
        self.revision = 1
        self.read_only = False
        self.tasks: dict[str, SimulationTask] = {}
        self.active_task_id: str | None = None
        self.event_log = EventLog()

    def _assert_writable(self) -> None:
        if self.read_only:
            raise ApiError(409, "snapshot is read-only")

    def _record_step(self) -> None:
        before = len(self.simulation.events)
        self.simulation.step(1)  # A tick is the atomic consistency boundary.
        tick = self.simulation.world.tick
        for offset, event in enumerate(list(self.simulation.events)[before:]):
            self.event_log.append(EventRecord(
                id=f"event-{self.branch_id}-{tick}-{offset}", tick=tick, kind=event.kind,
                payload=dict(event.payload),
            ))
        self.series.capture(self.simulation.world)
        for series in self.object_series.values():
            series.capture(self.simulation.world)
        self.snapshot_store.create_if_due(self.simulation.world, branch_id=self.branch_id)
        self.series.compact(tick)
        for series in self.object_series.values():
            series.compact(tick)
        self.revision += 1

    def step(self, ticks: int) -> None:
        self._assert_writable()
        if ticks < 0:
            raise ApiError(400, "period must be non-negative")
        for _ in range(ticks):
            self._record_step()

    def payload(self) -> dict:
        world = self.simulation.world
        return {
            "product": "TerraNore Test", "saveFormat": FORMAT, "revision": self.revision,
            "readOnly": self.read_only, "branchId": self.branch_id,
            "world": WorldDTO.from_world(world, self.simulation.scheduler.workers).to_dict(),
            "summary": {
                "year": world.tick // Simulation.TICKS_PER_YEAR,
                "month": world.tick % Simulation.TICKS_PER_YEAR + 1,
                "population": sum(item.population for item in world.regions),
                "treasury": round(sum(item.economy.treasury for item in world.regions), 2),
                "production": round(sum(item.economy.production for item in world.regions), 2),
            },
            "series": deepcopy(self.series.points[-120:]),
        }

    def start_run(self, target_tick: int) -> SimulationTask:
        self._assert_writable()
        current = self.simulation.world.tick
        if target_tick < current:
            raise ApiError(400, "target date precedes current date")
        if self.active_task_id and self.tasks[self.active_task_id].status in ("queued", "running", "paused"):
            raise ApiError(409, "another simulation task is active")
        task = SimulationTask("run", current, target_tick)
        self.tasks[task.id] = task
        self.active_task_id = task.id
        threading.Thread(target=self._run, args=(task,), daemon=True, name=f"simulation-{task.id[:8]}").start()
        return task

    def _run(self, task: SimulationTask) -> None:
        task.status = "running"
        try:
            while True:
                if task.cancel_requested.is_set():
                    task.status = "cancelled"
                    return
                if task.pause_requested.is_set():
                    task.status = "paused"
                    return
                with self.lock:
                    # Recheck after acquiring the mutation lock: a pause or
                    # cancel request may have arrived while this worker waited
                    # for the preceding atomic tick to publish.
                    if task.cancel_requested.is_set():
                        task.status = "cancelled"
                        return
                    if task.pause_requested.is_set():
                        task.status = "paused"
                        return
                    if self.simulation.world.tick >= task.target_tick:
                        task.status = "completed"
                        return
                    self._record_step()
                    task.completed += 1
                # Requests can run between atomic ticks even for short worlds.
                threading.Event().wait(0.001)
        except Exception as error:  # task failures are observable via the task resource
            task.error = str(error)
            task.status = "failed"

    def control(self, operation: str, task_id: str | None, token: str | None) -> SimulationTask:
        task_id = task_id or self.active_task_id
        if not task_id or task_id not in self.tasks:
            raise ApiError(404, "unknown task")
        task = self.tasks[task_id]
        if token is not None and token != task.cancellation_token:
            raise ApiError(403, "invalid cancellation token")
        if operation == "pause":
            if task.status not in ("queued", "running"):
                raise ApiError(409, "task cannot be paused")
            task.pause_requested.set()
            # The request owns the application lock, so no atomic tick can be
            # in progress here.  Publishing "paused" is therefore immediate
            # and still describes a fully consistent world.
            task.status = "paused"
        elif operation == "resume":
            if task.status != "paused":
                raise ApiError(409, "task is not paused")
            task.pause_requested.clear()
            threading.Thread(target=self._run, args=(task,), daemon=True).start()
        elif operation == "cancel":
            if task.status not in ("queued", "running", "paused"):
                raise ApiError(409, "task cannot be cancelled")
            task.cancel_requested.set()
            task.status = "cancelled"
        return task

    def replace_world(self, world: World, *, workers: int | str | None = None,
                      read_only: bool = False, branch_id: str = "main") -> None:
        if self.active_task_id and self.tasks[self.active_task_id].status in ("queued", "running", "paused"):
            raise ApiError(409, "a simulation task is active")
        self.simulation.close()
        self.simulation = Simulation(world=world, workers="auto" if workers is None else workers)
        self.series = TimeSeries()
        self.series.capture(world)
        self.object_series = self._new_object_series(world)
        self.event_log = EventLog()
        self.branch_id = branch_id
        self.read_only = read_only
        self.revision += 1

    @staticmethod
    def _new_object_series(world: World) -> dict[str, TimeSeries]:
        result = {region.id: TimeSeries(object_id=region.id) for region in world.regions}
        for series in result.values():
            series.capture(world)
        return result


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class TestRequestHandler(BaseHTTPRequestHandler):
    server_version = "TerraNoreTest/0.2"

    @property
    def app(self) -> AppState:
        return self.server.app  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        try:
            if path == f"{API}/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
                self._websocket()
                return
            with self.app.lock:
                payload = self._get_api(path, query)
            if payload is not None:
                _json(self, 200, payload)
            else:
                self._static(path)
        except ApiError as error:
            _json(self, error.status, {"error": str(error), "revision": self.app.revision})
        except (ValueError, KeyError) as error:
            _json(self, 400, {"error": str(error), "revision": self.app.revision})

    def _get_api(self, path: str, query: dict[str, list[str]]) -> dict | None:
        if not path.startswith(API):
            return None
        if path == f"{API}/state":
            return self.app.payload()
        if path.startswith(f"{API}/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            if task_id not in self.app.tasks:
                raise ApiError(404, "unknown task")
            return {"revision": self.app.revision, "task": self.app.tasks[task_id].dto().to_dict()}
        if path.endswith("/children") and path.startswith(f"{API}/nodes/"):
            object_id = path[len(f"{API}/nodes/"):-len("/children")].strip("/")
            if object_id in ("world", "root"):
                children = [self._region_card(region) for region in self.app.simulation.world.regions]
            else:
                self._region(object_id)
                children = []
            return {"revision": self.app.revision, "items": children}
        if path.startswith(f"{API}/objects/"):
            return {"revision": self.app.revision, "object": self._region_card(self._region(path.rsplit("/", 1)[-1]))}
        if path == f"{API}/timeseries":
            return self._timeseries(query)
        if path == f"{API}/compare":
            left = self._integer(query, "left", self._integer(query, "from", 0))
            right = self._integer(query, "right", self._integer(query, "to", self.app.simulation.world.tick))
            return self._compare(left, right)
        if path == f"{API}/events":
            start, end = self._range(query)
            return {"revision": self.app.revision, "items": self.app.event_log.between(start, end)}
        if path.startswith(f"{API}/events/") and path.endswith("/causes"):
            event_id = path[len(f"{API}/events/"):-len("/causes")].strip("/")
            event = self.app.event_log.get(event_id)
            if event is None:
                raise ApiError(404, "unknown event")
            return {"revision": self.app.revision, "event": event.to_dict(),
                    "chain": self.app.event_log.causal_chain(event_id)}
        if path == f"{API}/anomalies":
            start, end = self._range(query)
            points = [p for p in self.app.series.points if start <= p["tick"] <= end]
            anomalies = [{"tick": p["tick"], "kind": "negative-treasury", "value": p["treasury"]} for p in points if p["treasury"] < 0]
            return {"revision": self.app.revision, "items": anomalies}
        raise ApiError(404, "unknown test endpoint")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            size = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(data, dict):
                raise ApiError(400, "JSON object expected")
            with self.app.lock:
                status, payload = self._post_api(path, data)
            _json(self, status, payload)
        except ApiError as error:
            _json(self, error.status, {"error": str(error), "revision": self.app.revision})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            _json(self, 400, {"error": str(error), "revision": self.app.revision})

    def do_PUT(self) -> None:
        self.do_POST()

    def _post_api(self, path: str, data: dict) -> tuple[int, dict]:
        if path in (f"{API}/worlds", f"{API}/world", f"{API}/reset"):
            self.app._assert_writable()
            configuration = _world_configuration(
                data, self.app.simulation.world, self.app.simulation.scheduler.workers
            )
            workers = configuration.pop("workers")
            world = World.create(**configuration)
            self.app.replace_world(world, workers=workers)
            return (200 if path.endswith("/reset") else 201), self.app.payload()
        if path in (f"{API}/tick", f"{API}/simulation/step"):
            ticks = int(data.get("ticks", 1))
            self.app.step(ticks)
            return 200, self.app.payload()
        if path == f"{API}/simulation/run":
            target = self._target_tick(data)
            task = self.app.start_run(target)
            return 202, {"revision": self.app.revision, "task": task.dto().to_dict()}
        if path in (f"{API}/simulation/pause", f"{API}/simulation/resume", f"{API}/simulation/cancel"):
            operation = path.rsplit("/", 1)[-1]
            task = self.app.control(operation, data.get("taskId"), data.get("cancellationToken"))
            return 202, {"revision": self.app.revision, "task": task.dto().to_dict()}
        if path == f"{API}/lod":
            object_id, level = data["regionId"], data["level"]
            return self._set_lod(object_id, level)
        if path.startswith(f"{API}/objects/") and path.endswith("/lod"):
            object_id = path[len(f"{API}/objects/"):-len("/lod")].strip("/")
            return self._set_lod(object_id, data["level"])
        if path == f"{API}/snapshots":
            snapshot_id = data.get("id")
            snapshot = self.app.snapshot_store.create(
                self.app.simulation.world, snapshot_id=str(snapshot_id) if snapshot_id else None,
                branch_id=self.app.branch_id,
            )
            return 201, {"revision": self.app.revision, "snapshot": {"id": snapshot.id, "tick": snapshot.tick}}
        if path.startswith(f"{API}/snapshots/") and path.endswith("/open"):
            snapshot_id = path[len(f"{API}/snapshots/"):-len("/open")].strip("/")
            if snapshot_id not in self.app.snapshot_store.snapshots:
                raise ApiError(404, "unknown snapshot")
            self.app.replace_world(self.app.snapshot_store.open(snapshot_id), read_only=True,
                                   branch_id=self.app.snapshot_store.snapshots[snapshot_id].branch_id)
            return 200, self.app.payload()
        if path.startswith(f"{API}/snapshots/") and path.endswith("/branch"):
            snapshot_id = path[len(f"{API}/snapshots/"):-len("/branch")].strip("/")
            if snapshot_id not in self.app.snapshot_store.snapshots:
                raise ApiError(404, "unknown snapshot")
            branch_id = str(data.get("branchId", "")).strip()
            try:
                world = self.app.snapshot_store.branch(snapshot_id, branch_id)
            except ValueError as error:
                raise ApiError(409, str(error)) from None
            self.app.replace_world(world, branch_id=branch_id)
            return 201, self.app.payload()
        if path == f"{API}/save":
            target = data.get("path")
            if not target:
                raise ApiError(400, "path is required")
            save_snapshot(_clone_world(self.app.simulation.world), target)
            return 200, {"revision": self.app.revision, "format": FORMAT, "path": str(target)}
        if path == f"{API}/load":
            target = data.get("path")
            if not target:
                raise ApiError(400, "path is required")
            self.app.replace_world(load_snapshot(target))
            return 200, self.app.payload()
        raise ApiError(404, "unknown test endpoint")

    def _set_lod(self, object_id: str, level: str) -> tuple[int, dict]:
        self.app._assert_writable()
        try:
            DetailSelector().set_level(self.app.simulation.world, object_id, level)
        except KeyError:
            raise ApiError(404, "unknown object") from None
        except ValueError:
            raise ApiError(400, "unsupported LOD") from None
        self.app.revision += 1
        return 200, self.app.payload()

    def _region(self, object_id: str) -> Region:
        region = next((r for r in self.app.simulation.world.regions if r.id == object_id), None)
        if region is None:
            raise ApiError(404, "unknown object")
        return region

    def _region_card(self, region: Region) -> dict:
        return {
            "id": region.id, "type": "region", "name": region.name,
            "population": region.population, "resources": region.resources,
            "detailLevel": external_lod(self.app.simulation.world.simulation_node(region.id)),
            "economy": deepcopy(region.economy.__dict__),
        }

    @staticmethod
    def _integer(query: dict[str, list[str]], key: str, default: int) -> int:
        return int(query.get(key, [str(default)])[0])

    def _range(self, query: dict[str, list[str]]) -> tuple[int, int]:
        start = self._integer(query, "from", 0)
        end = self._integer(query, "to", self.app.simulation.world.tick)
        if start < 0 or end < start:
            raise ApiError(400, "period must be non-negative")
        return start, end

    def _timeseries(self, query: dict[str, list[str]]) -> dict:
        start, end = self._range(query)
        requested = end - start + 1
        limit = self._integer(query, "limit", MAX_SERIES_POINTS)
        if limit < 1 or limit > MAX_SERIES_POINTS or requested > MAX_SERIES_POINTS:
            raise ApiError(413, "time-series request is too large")
        metric = query.get("metric", [None])[0]
        allowed = {None, "population", "treasury", "production"}
        if metric not in allowed:
            raise ApiError(400, "unknown time-series metric")
        object_id = query.get("objectId", [None])[0]
        series = self.app.series
        if object_id is not None:
            if object_id not in self.app.object_series:
                raise ApiError(404, "unknown object")
            series = self.app.object_series[object_id]
        elif metric == "production":
            raise ApiError(400, "production requires objectId")
        points = [deepcopy(p) for p in series.points if start <= p["tick"] <= end][:limit]
        if metric:
            points = [{"tick": point["tick"], metric: point[metric]} for point in points]
        return {"revision": self.app.revision, "items": points}

    def _compare(self, left: int, right: int) -> dict:
        if left < 0 or right < 0:
            raise ApiError(400, "period must be non-negative")
        by_tick = {p["tick"]: p for p in self.app.series.points}
        if left not in by_tick or right not in by_tick:
            raise ApiError(404, "date is not available")
        a, b = by_tick[left], by_tick[right]
        return {"revision": self.app.revision, "left": deepcopy(a), "right": deepcopy(b),
                "delta": {key: b[key] - a[key] for key in ("population", "treasury")}}

    @staticmethod
    def _target_tick(data: dict) -> int:
        if "targetTick" in data:
            return int(data["targetTick"])
        value = data.get("targetDate")
        if isinstance(value, int):
            return value
        if isinstance(value, dict):
            year, month = int(value.get("year", 0)), int(value.get("month", 1))
            if year < 0 or not 1 <= month <= Simulation.TICKS_PER_YEAR:
                raise ApiError(400, "invalid target date")
            return year * Simulation.TICKS_PER_YEAR + month - 1
        raise ApiError(400, "targetTick or targetDate is required")

    def _static(self, path: str) -> None:
        relative = "index.html" if path == "/" else path.lstrip("/")
        candidate = (UI_ROOT / relative).resolve()
        if UI_ROOT.resolve() not in candidate.parents or not candidate.is_file():
            self.send_error(404)
            return
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(candidate)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400)
            return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        data = json.dumps(self.app.payload(), ensure_ascii=False).encode()
        header = b"\x81" + (bytes([len(data)]) if len(data) < 126 else b"\x7e" + struct.pack("!H", len(data)))
        self.connection.sendall(header + data)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[TerraNore Test] {self.address_string()} - {format % args}")


def create_server(host: str = "127.0.0.1", port: int = 0, seed: int = 42,
                  snapshot_interval: int | None = None) -> ThreadingHTTPServer:
    # Refuse an accidental public bind: this API controls local files and worlds.
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("the test API may only bind to a loopback interface")
    server = ThreadingHTTPServer((host, port), TestRequestHandler)
    server.app = AppState(seed, snapshot_interval)  # type: ignore[attr-defined]
    return server
