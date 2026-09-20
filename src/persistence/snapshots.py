from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.simulation.model import Economy, Region, World

FORMAT = "test-save-v1"


def clone_world(world: World) -> World:
    data = deepcopy(world.to_dict())
    regions = [Region(**{**region, "economy": Economy(**region["economy"])}) for region in data["regions"]]
    return World(seed=data["seed"], tick=data["tick"], regions=regions)


@dataclass(frozen=True)
class Snapshot:
    id: str
    tick: int
    blob_id: str
    branch_id: str
    reason: str
    created_at: str


class SnapshotStore:
    """Content-addressed copy-on-write snapshots with explicit branches."""

    def __init__(self, world: World, *, interval: int | None = None) -> None:
        if interval is not None and interval < 1:
            raise ValueError("snapshot interval must be positive")
        self.interval = interval
        self._blobs: dict[str, dict] = {}
        self.snapshots: dict[str, Snapshot] = {}
        self.branches: dict[str, str] = {"main": ""}
        initial = self.create(world, snapshot_id="initial", branch_id="main", reason="startup")
        self.branches["main"] = initial.id

    def create(self, world: World, *, snapshot_id: str | None = None, branch_id: str = "main", reason: str = "manual") -> Snapshot:
        identifier = snapshot_id or f"snapshot-{len(self.snapshots) + 1}"
        if identifier in self.snapshots:
            raise ValueError(f"duplicate snapshot id: {identifier}")
        data = deepcopy(world.to_dict())
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        blob_id = hashlib.sha256(encoded).hexdigest()
        self._blobs.setdefault(blob_id, data)  # identical worlds share storage
        snapshot = Snapshot(identifier, world.tick, blob_id, branch_id, reason, datetime.now(timezone.utc).isoformat())
        self.snapshots[identifier] = snapshot
        self.branches[branch_id] = identifier
        return snapshot

    def create_if_due(self, world: World, *, branch_id: str = "main") -> Snapshot | None:
        if self.interval and world.tick and world.tick % self.interval == 0:
            identifier = f"auto-{branch_id}-{world.tick}"
            return self.snapshots.get(identifier) or self.create(
                world, snapshot_id=identifier, branch_id=branch_id, reason="interval"
            )
        return None

    def open(self, snapshot_id: str) -> World:
        snapshot = self.snapshots[snapshot_id]
        data = self._blobs[snapshot.blob_id]
        return clone_world(World(
            seed=data["seed"], tick=data["tick"],
            regions=[Region(**{**item, "economy": Economy(**item["economy"])}) for item in data["regions"]],
        ))

    def branch(self, snapshot_id: str, branch_id: str) -> World:
        if not branch_id or branch_id in self.branches:
            raise ValueError("branch id must be new and non-empty")
        world = self.open(snapshot_id)
        head = self.create(world, snapshot_id=f"{branch_id}-origin", branch_id=branch_id, reason="branch")
        self.branches[branch_id] = head.id
        return world


def save_snapshot(world: World, path: str | Path) -> None:
    payload = {"format": FORMAT, "world": world.to_dict()}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_snapshot(path: str | Path) -> World:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("format") != FORMAT:
        raise ValueError("unsupported test save format")
    data = payload["world"]
    regions = [Region(**{**region, "economy": Economy(**region["economy"])}) for region in data["regions"]]
    return World(seed=data["seed"], tick=data["tick"], regions=regions)
