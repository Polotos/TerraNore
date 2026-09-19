import tempfile
import unittest
from pathlib import Path

from src.persistence import FORMAT, load_snapshot, save_snapshot
from src.simulation import Simulation


class PersistenceTests(unittest.TestCase):
    def test_snapshot_round_trip(self):
        simulation = Simulation(seed=9)
        expected = simulation.step(5)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.test-save.json"
            save_snapshot(expected, path)
            actual = load_snapshot(path)
        self.assertEqual(FORMAT, "test-save-v1")
        self.assertEqual(expected.to_dict(), actual.to_dict())
