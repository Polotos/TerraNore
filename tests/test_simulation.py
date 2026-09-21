import unittest

from src.simulation import Simulation
from src.simulation.lod import AUTO, DetailSelector, LOD
from src.simulation.model import World


class SimulationTests(unittest.TestCase):
    def test_generated_objects_have_distinct_deterministic_hex_names(self):
        world = World.create(seed=42, system_count=2, settlement_count=3)
        repeated = World.create(seed=42, system_count=2, settlement_count=3)
        self.assertEqual(
            [(r.system_name, r.planet_name, r.name) for r in world.regions],
            [(r.system_name, r.planet_name, r.name) for r in repeated.regions],
        )
        for region in world.regions:
            self.assertRegex(region.system_name, r"^SYS-[0-9A-F]{8}$")
            self.assertRegex(region.planet_name, r"^PLN-[0-9A-F]{8}$")
            self.assertRegex(region.name, r"^REG-[0-9A-F]{8}$")
        self.assertEqual(world.regions[0].system_name, world.regions[2].system_name)
        self.assertNotEqual(world.regions[0].planet_name, world.regions[2].planet_name)
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
