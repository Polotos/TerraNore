from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


class DeterministicScheduler:
    """Runs independent work in parallel and always returns input order."""

    def __init__(self, workers: int = 1) -> None:
        self.workers = max(1, workers)

    def map(self, function: Callable[[T], R], values: Iterable[T]) -> list[R]:
        items = list(values)
        if self.workers == 1:
            return [function(item) for item in items]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            return list(pool.map(function, items))
