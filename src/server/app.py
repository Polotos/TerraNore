from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import socket
import struct
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from urllib.parse import parse_qs, urlparse

from src.persistence import EventLog, EventRecord, FORMAT, SnapshotStore, TimeSeries, load_snapshot, save_snapshot
from src.simulation import Simulation
from src.simulation.lod import DetailSelector, external_lod
from src.simulation.model import Economy, Region, SimulationDate, World

from .dto import WorldDTO
from .tasks import SimulationTask

API = "/api/test/v1"
UI_ROOT = Path(__file__).parents[1] / "ui"
MAX_SERIES_POINTS = 10_000
MAX_WEBSOCKET_MESSAGE = 1_048_576


def _websocket_frame(opcode: int, payload: bytes = b"") -> bytes:
    """Encode an unmasked server frame, including RFC 6455 64-bit lengths."""
    size = len(payload)
    if size < 126:
        length = bytes((size,))
    elif size <= 0xFFFF:
        length = b"\x7e" + struct.pack("!H", size)
    else:
        length = b"\x7f" + struct.pack("!Q", size)
    return bytes((0x80 | opcode,)) + length + payload


class WebSocketClient:
    """A registered connection with serialized writes from arbitrary threads."""

    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection
        self.write_lock = threading.Lock()

    def send(self, opcode: int, payload: bytes = b"") -> None:
        with self.write_lock:
            self.connection.sendall(_websocket_frame(opcode, payload))

    def close(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class WebSocketRegistry:
    """Thread-safe collection of live WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[WebSocketClient] = set()
        self._lock = threading.Lock()

    def add(self, client: WebSocketClient) -> None:
        with self._lock:
            self._clients.add(client)

    def discard(self, client: WebSocketClient) -> None:
        with self._lock:
            self._clients.discard(client)

    def broadcast(self, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        with self._lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                client.send(0x1, encoded)
            except OSError:
                self.discard(client)
                client.close()

    def close_all(self) -> None:
        with self._lock:
            clients, self._clients = tuple(self._clients), set()
        for client in clients:
            client.close()


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class WebSocketProtocolError(Exception):
    pass


def _clone_world(world: World) -> World:
    data = world.to_dict()
    regions = [Region(**{**item, "economy": Economy(**item["economy"])}) for item in data["regions"]]
    cloned = World(
        seed=data["seed"], tick=data["tick"], regions=regions,
        system_count=data["system_count"], settlement_count=data["settlement_count"],
        initial_date=SimulationDate.parse(data["initial_date"]),
        current_date=SimulationDate.parse(data["current_date"]),
        accuracy_profile=data["accuracy_profile"],
    )
    cloned._legacy_iso_date = "start_date" in data and "-" in data["start_date"]
    return cloned


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
        SimulationDate.parse(start_date)
    except (TypeError, ValueError, OverflowError):
        raise ApiError(400, "startDate must use day.decade\\third.quarter.year") from None
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
    def __init__(self, seed: int = 42, snapshot_interval: int | None = None, *,
                 saves_dir: str | Path = "saves", enable_developer_import: bool = False) -> None:
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
        self.websockets = WebSocketRegistry()
        self.saves_dir = Path(saves_dir).resolve()
        self.saves_dir.mkdir(parents=True, exist_ok=True)
        self.enable_developer_import = enable_developer_import

    def save_path(self, name: object) -> Path:
        """Resolve a client save identifier without permitting filesystem escape."""
        if not isinstance(name, str) or not name.strip():
            raise ApiError(400, "save name is required")
        candidate = Path(name.strip())
        windows_candidate = PureWindowsPath(name.strip())
        if (candidate.is_absolute() or windows_candidate.anchor
                or ".." in candidate.parts or ".." in windows_candidate.parts):
            raise ApiError(400, "save name must be relative and may not contain '..'")
        normalized = Path(*(part for part in candidate.parts if part not in ("", ".")))
        if not normalized.parts:
            raise ApiError(400, "save name is required")
        resolved = (self.saves_dir / normalized).resolve()
        if resolved == self.saves_dir or self.saves_dir not in resolved.parents:
            raise ApiError(400, "save name resolves outside the saves directory")
        return resolved

    def _assert_writable(self) -> None:
        if self.read_only:
            raise ApiError(409, "snapshot is read-only")

    def _record_step(self) -> None:
        before_objects = {
            region.id: (region.population, region.resources, tuple(region.economy.__dict__.values()))
            for region in self.simulation.world.regions
        }
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
        changed = [
            region.id for region in self.simulation.world.regions
            if before_objects.get(region.id) != (
                region.population, region.resources, tuple(region.economy.__dict__.values())
            )
        ]
        self.websockets.broadcast(self.tick_message(changed))

    def tick_message(self, changed_object_ids: list[str]) -> dict:
        """Build the deliberately compact notification sent at a tick boundary."""
        world = self.simulation.world
        task = self.tasks.get(self.active_task_id) if self.active_task_id else None
        return {
            "revision": self.revision,
            "currentDate": (f"{world.current_date.year:04d}-"
                            f"{(world.current_date.quarter - 1) * 3 + world.current_date.third:02d}"
                            if world._legacy_iso_date else str(world.current_date)),
            "activeTask": task.dto().to_dict() if task else None,
            "summary": {
                "population": sum(item.population for item in world.regions),
                "treasury": round(sum(item.economy.treasury for item in world.regions), 2),
                "production": round(sum(item.economy.production for item in world.regions), 2),
            },
            "changedObjectIds": changed_object_ids,
        }

    def step(self, ticks: int) -> None:
        self._assert_writable()
        if self.active_task_id and self.tasks[self.active_task_id].status in ("queued", "running", "paused"):
            raise ApiError(409, "a simulation task is active")
        if ticks < 0:
            raise ApiError(400, "period must be non-negative")
        for _ in range(ticks):
            self._record_step()

    def payload(self) -> dict:
        world = self.simulation.world
        active_task = None
        if self.active_task_id:
            task = self.tasks[self.active_task_id]
            if task.status in ("queued", "running", "paused"):
                active_task = task.dto().to_dict()
        return {
            "product": "TerraNore Test", "saveFormat": FORMAT, "revision": self.revision,
            "readOnly": self.read_only, "branchId": self.branch_id,
            "activeTask": active_task,
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
        try:
            self.simulation.world.initial_date.add_ticks(target_tick)
        except OverflowError:
            raise ApiError(400, "target date exceeds the supported period") from None
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
                    task.completed += 1
                    final_tick = self.simulation.world.tick + 1 >= task.target_tick
                    if final_tick:
                        task.status = "completed"
                    try:
                        self._record_step()
                    except Exception:
                        task.completed -= 1
                        task.status = "running"
                        raise
                    if final_tick:
                        return
                # Requests can run between atomic ticks even for short worlds.
                threading.Event().wait(0.001)
        except Exception as error:  # task failures are observable via the task resource
            task.error = str(error)
            task.status = "failed"

    def control(self, operation: str, task_id: str | None, token: str | None) -> SimulationTask:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ApiError(400, "taskId is required")
        if not isinstance(token, str) or not token.strip():
            raise ApiError(400, "cancellationToken is required")
        if task_id not in self.tasks:
            raise ApiError(404, "unknown task")
        task = self.tasks[task_id]
        if token != task.cancellation_token:
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
            target = self.app.save_path(data.get("name", data.get("id")))
            save_snapshot(_clone_world(self.app.simulation.world), target)
            return 200, {"revision": self.app.revision, "format": FORMAT,
                         "name": str(target.relative_to(self.app.saves_dir))}
        if path == f"{API}/load":
            target = self.app.save_path(data.get("name", data.get("id")))
            self.app.replace_world(load_snapshot(target))
            return 200, self.app.payload()
        if path == f"{API}/developer/import":
            if not self.app.enable_developer_import:
                raise ApiError(404, "developer import is disabled")
            source = data.get("path")
            if not isinstance(source, str) or not source:
                raise ApiError(400, "path is required")
            self.app.replace_world(load_snapshot(Path(source).expanduser().resolve()))
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
        payload = self.app.payload()
        payload["object"] = self._region_card(self._region(object_id))
        return 200, payload

    def _region(self, object_id: str) -> Region:
        region = next((r for r in self.app.simulation.world.regions if r.id == object_id), None)
        if region is None:
            raise ApiError(404, "unknown object")
        return region

    def _region_card(self, region: Region) -> dict:
        node = self.app.simulation.world.simulation_node(region.id)
        manual_lod = external_lod(node)
        effective_lod = f"lod-{int(node.effective_lod())}"
        return {
            "id": region.id, "type": "region", "name": region.name,
            "population": region.population, "resources": region.resources,
            # Keep detailLevel as the backwards-compatible manual selection.
            "detailLevel": manual_lod, "manualLod": manual_lod,
            "effectiveLod": effective_lod,
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

    def _target_tick(self, data: dict) -> int:
        if "targetTick" in data:
            return int(data["targetTick"])
        value = data.get("targetDate")
        if isinstance(value, str):
            try:
                target = SimulationDate.parse(value)
                relative = self.app.simulation.world.current_date.ticks_until(target)
                return self.app.simulation.world.tick + relative
            except ValueError as error:
                raise ApiError(400, str(error)) from None
            except OverflowError:
                raise ApiError(400, "target date exceeds the supported period") from None
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
        if not key or self.headers.get("Sec-WebSocket-Version") != "13":
            self.send_error(400)
            return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        client = WebSocketClient(self.connection)
        self.app.websockets.add(client)
        self.connection.settimeout(30)
        try:
            client.send(0x1, json.dumps(
                self.app.tick_message([]), ensure_ascii=False, separators=(",", ":")
            ).encode())
            while True:
                try:
                    opcode, payload = self._read_websocket_frame()
                except TimeoutError:
                    client.send(0x9, b"terranore")
                    continue
                if opcode == 0x8:  # close
                    if len(payload) == 1:
                        raise WebSocketProtocolError("invalid close payload")
                    client.send(0x8, payload or struct.pack("!H", 1000))
                    break
                if opcode == 0x9:  # ping
                    client.send(0xA, payload)
                elif opcode == 0xA:  # pong
                    continue
                else:
                    client.send(0x8, struct.pack("!H", 1003))
                    break
        except WebSocketProtocolError:
            try:
                client.send(0x8, struct.pack("!H", 1002))
            except OSError:
                pass
        except (EOFError, OSError):
            pass
        finally:
            self.app.websockets.discard(client)

    def _read_websocket_frame(self) -> tuple[int, bytes]:
        header = self._read_exact(2)
        first, second = header
        if first & 0x70 or not first & 0x80:
            raise WebSocketProtocolError("invalid or fragmented frame")
        opcode = first & 0x0F
        if opcode not in (0x8, 0x9, 0xA):
            raise WebSocketProtocolError("unsupported opcode")
        masked = bool(second & 0x80)
        if not masked:  # RFC 6455 requires every client frame to be masked.
            raise WebSocketProtocolError("unmasked client frame")
        size = second & 0x7F
        if size == 126:
            size = struct.unpack("!H", self._read_exact(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", self._read_exact(8))[0]
            if size & (1 << 63):
                raise WebSocketProtocolError("invalid frame length")
        if size > MAX_WEBSOCKET_MESSAGE or (opcode >= 0x8 and size > 125):
            raise WebSocketProtocolError("frame too large")
        mask = self._read_exact(4)
        payload = self._read_exact(size)
        return opcode, bytes(value ^ mask[index % 4] for index, value in enumerate(payload))

    def _read_exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = self.connection.recv(size - len(result))
            if not chunk:
                raise EOFError
            result.extend(chunk)
        return bytes(result)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[TerraNore Test] {self.address_string()} - {format % args}")


class TerraNoreHTTPServer(ThreadingHTTPServer):
    def server_close(self) -> None:
        self.app.websockets.close_all()  # type: ignore[attr-defined]
        super().server_close()


def create_server(host: str = "127.0.0.1", port: int = 0, seed: int = 42,
                  snapshot_interval: int | None = None, *, saves_dir: str | Path = "saves",
                  enable_developer_import: bool = False) -> ThreadingHTTPServer:
    # Refuse an accidental public bind: this API controls local files and worlds.
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("the test API may only bind to a loopback interface")
    server = TerraNoreHTTPServer((host, port), TestRequestHandler)
    server.app = AppState(seed, snapshot_interval, saves_dir=saves_dir,
                          enable_developer_import=enable_developer_import)  # type: ignore[attr-defined]
    return server
