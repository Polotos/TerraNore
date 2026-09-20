"""Explicit ownership of the mutable, operational world state."""

from dataclasses import dataclass

from src.simulation.model import World


@dataclass
class OperationalState:
    world: World
    branch_id: str = "main"
    read_only: bool = False

    def assert_writable(self) -> None:
        if self.read_only:
            raise PermissionError("snapshot is read-only; create a branch to continue")
