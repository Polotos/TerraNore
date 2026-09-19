import unittest

from src.simulation import Simulation
from src.simulation.lod import DetailSelector


class SimulationTests(unittest.TestCase):
    def test_same_seed_is_deterministic_across_worker_counts(self):
        sequential = Simulation(seed=17, workers=1).run_years(10).to_dict()
        parallel = Simulation(seed=17, workers=4).run_years(10).to_dict()
        self.assertEqual(sequential, parallel)

    def test_century_run_completes(self):
        world = Simulation(seed=42).run_years(100)
        self.assertEqual(world.tick, 1200)
        self.assertTrue(all(region.population > 0 for region in world.regions))

    def test_lod_is_domain_only(self):
        world = Simulation().world
        DetailSelector().set_level(world, "region-1", "detailed")
        self.assertEqual(world.regions[0].detail_level, "detailed")
        with self.assertRaises(ValueError):
            DetailSelector().set_level(world, "region-1", "impossible")
