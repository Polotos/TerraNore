import unittest

from src.simulation import Simulation
from src.simulation.lod import AUTO, DetailSelector, LOD


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
        DetailSelector().set_level(world, "region-1", "lod-2")
        self.assertEqual(world.simulation_node("region-1").lod_override, LOD.ENTERPRISE)
        DetailSelector().set_level(world, "region-1", "lod-1")
        self.assertEqual(world.simulation_node("region-1").lod_override, LOD.PLANET)
        DetailSelector().set_level(world, "region-1", "lod-0")
        self.assertEqual(world.simulation_node("region-1").lod_override, LOD.AGGREGATE)
        DetailSelector().set_level(world, "region-1", "auto")
        self.assertIs(world.simulation_node("region-1").lod_override, AUTO)
        with self.assertRaises(ValueError):
            DetailSelector().set_level(world, "region-1", "impossible")
