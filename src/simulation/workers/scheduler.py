from __future__ import annotations

import multiprocessing
import os
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ProcessPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def resolve_worker_count(workers: int | str | None) -> int:
    """Resolve the public worker setting without silently accepting bad values."""
    if workers is None or workers == "auto":
        return max(1, os.cpu_count() or 1)
    if isinstance(workers, str):
        try:
            workers = int(workers)
        except ValueError as error:
            raise ValueError("workers must be 'auto' or a positive integer") from error
    if isinstance(workers, bool) or workers < 1:
        raise ValueError("workers must be 'auto' or a positive integer")
    return workers


class DeterministicScheduler:
    """Bounded parallel map whose result order is always the input order.

    Only ``max_pending`` futures may be queued.  This provides backpressure for
    long simulations instead of submitting a century's worth of work at once.
    """

    def __init__(self, workers: int | str | None = "auto", max_pending: int | None = None) -> None:
        self.workers = resolve_worker_count(workers)
        self.max_pending = self.workers * 2 if max_pending is None else max_pending
        if self.max_pending < self.workers:
            raise ValueError("max_pending must be at least the worker count")
        self._pool: ProcessPoolExecutor | None = None

    def close(self) -> None:
        """Stop child processes owned by this scheduler."""
        if self._pool is not None:
            self._pool.shutdown()
            self._pool = None

    @property
    def worker_pids(self) -> list[int]:
        """Return live executor PIDs for diagnostics, never for task control."""
        if self._pool is None:
            return []
        # ProcessPoolExecutor has no public process-introspection API. Snapshot
        # its process table defensively: it can change while the pool starts.
        processes = tuple((getattr(self._pool, "_processes", None) or {}).values())
        return sorted(
            process.pid for process in processes
            if process.pid is not None and process.is_alive()
        )

    def __enter__(self) -> DeterministicScheduler:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def map(self, function: Callable[[T], R], values: Iterable[T]) -> list[R]:
        if self.workers == 1:
            return [function(item) for item in values]

        iterator = iter(values)
        initial: list[T] = []
        for _ in range(self.max_pending):
            try:
                initial.append(next(iterator))
            except StopIteration:
                break
        if not initial:
            return []
        results: list[R] = []
        if self._pool is None:
            # Processes, rather than Python threads, are intentional here: the
            # simulation is CPU-bound and must be able to use multiple cores
            # despite CPython's GIL.
            # ``spawn`` is safe when the HTTP server already has threads and is
            # also the native behaviour of Windows BUILD/build.exe deployments.
            self._pool = ProcessPoolExecutor(
                max_workers=min(self.workers, len(initial)),
                mp_context=multiprocessing.get_context("spawn"),
            )
        pending: list[Future[R]] = [self._pool.submit(function, item) for item in initial]
        exhausted = len(initial) < self.max_pending
        while pending or not exhausted:
            while not exhausted and len(pending) < self.max_pending:
                try:
                    pending.append(self._pool.submit(function, next(iterator)))
                except StopIteration:
                    exhausted = True
            if pending:
                # Waiting for the oldest submission (rather than the first
                # completion) both applies backpressure and fixes ordering.
                results.append(pending.pop(0).result())
        return results
