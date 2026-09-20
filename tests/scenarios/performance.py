"""Repeatable performance instrumentation (not hard-coded pass/fail budgets)."""

from __future__ import annotations

import json
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Callable

from .fixed_world import create_fixed_world
from .runner import ScenarioRunner


def profile_scenario(years: int = 100, worker_counts: tuple[int, ...] = (1, 2, 4, 8),
                     ui_probe: Callable[[], object] | None = None) -> dict:
    durations: dict[int, float] = {}
    phase_time = {"simulation": 0.0, "serialization": 0.0, "ui_probe": 0.0}
    tracemalloc.start()
    reference: ScenarioRunner | None = None
    for workers in worker_counts:
        runner = ScenarioRunner(create_fixed_world(), workers=workers)
        started = time.perf_counter()
        runner.run(years * 12)
        durations[workers] = time.perf_counter() - started
        if workers == 1:
            reference = runner
    phase_time["simulation"] = sum(durations.values())
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert reference is not None
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "scenario.json"
        started = time.perf_counter()
        save_size = reference.save(path)
        phase_time["serialization"] = time.perf_counter() - started
    if ui_probe:
        started = time.perf_counter()
        ui_probe()
        phase_time["ui_probe"] = time.perf_counter() - started
    baseline = durations[worker_counts[0]]
    measured = sum(phase_time.values()) or 1.0
    return {
        "years": years, "total_seconds": durations[worker_counts[0]],
        "ticks_per_second": years * 12 / max(durations[worker_counts[0]], 1e-12),
        "peak_memory_bytes": peak, "save_bytes": save_size,
        "history_bytes": len(json.dumps(reference.history, separators=(",", ":")).encode()),
        "speedup": {str(n): baseline / max(value, 1e-12) for n, value in durations.items()},
        "phase_seconds": phase_time,
        "phase_fraction": {name: value / measured for name, value in phase_time.items()},
        "ui_latency_seconds": phase_time["ui_probe"] if ui_probe else None,
    }
