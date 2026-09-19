from __future__ import annotations

import json
from pathlib import Path

from src.simulation.model import Economy, Region, World

FORMAT = "test-save-v1"


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
