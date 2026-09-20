from __future__ import annotations

import hashlib
import random


def random_stream(world_seed: int, tick: int, phase: int, object_id: str, decision_type: str) -> random.Random:
    """Create a schedule-independent RNG stream for one decision."""
    material = f"{world_seed}\0{tick}\0{phase}\0{object_id}\0{decision_type}".encode()
    seed = int.from_bytes(hashlib.sha256(material).digest(), "big")
    return random.Random(seed)
