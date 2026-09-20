import tempfile
import unittest
from pathlib import Path

from tests.scenarios import RESOURCES, ScenarioRunner, create_fixed_world
from tests.scenarios.performance import profile_scenario


class FixedWorldScenarioTests(unittest.TestCase):
    def run_checked(self, months, *, workers=1, lod=2, switches=None):
        runner = ScenarioRunner(create_fixed_world(), workers, lod)
        runner.apply_automatic_lod(lod)
        runner.run(months, switches, lambda item: item.assert_invariants())
        return runner

    def test_fixture_shape_and_all_execution_modes(self):
        world = create_fixed_world()
        self.assertEqual((1, 4, 12, 4),
                         (1, len(world.systems), len(world.settlements), len(RESOURCES)))
        sequential = self.run_checked(36, workers=1).metrics()
        for workers in (2, 4):
            self.assertEqual(sequential, self.run_checked(36, workers=workers).metrics())
        for lod in (0, 1):
            self.run_checked(36, lod=lod)
        switched = self.run_checked(36, lod=0, switches={6: 1, 18: 2, 30: 0})
        self.assertEqual(0, switched.default_lod)

    def test_manual_lod_lock_survives_automatic_switches(self):
        runner = ScenarioRunner(create_fixed_world(), default_lod=0)
        pinned = runner.world.settlements[:3]
        for settlement in pinned:
            runner.set_lod(settlement.id, 2, lock=True)
        runner.run(24, {3: 1, 8: 0, 16: 2})
        self.assertTrue(all((settlement.lod, settlement.lod_lock) == (2, 2)
                            for settlement in pinned))

    def test_goods_output_is_limited_by_available_inputs(self):
        settlement = create_fixed_world().settlements[0]
        settlement.capacity["goods"] = 100
        settlement.capacity["ore"] = 0
        settlement.capacity["fuel"] = 0
        settlement.stocks["ore"] = 3
        settlement.stocks["fuel"] = 10
        plan = ScenarioRunner._plan(settlement)
        self.assertEqual(1, dict(plan.production)["goods"])
        self.assertEqual({"ore": 2, "fuel": 1},
                         {key: value for key, value in plan.input_use if value})

    def test_century_and_save_load_are_identical(self):
        continuous = self.run_checked(1200)
        first = self.run_checked(600)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "half.json"
            size = first.save(path)
            resumed = ScenarioRunner.load(path, workers=4)
            resumed.run(600, checkpoint=lambda item: item.assert_invariants())
        self.assertGreater(size, 0)
        self.assertEqual(continuous.world, resumed.world)
        self.assertEqual(continuous.ledger, resumed.ledger)
        self.assertEqual(continuous.events, resumed.events)

    def test_low_lod_accuracy_budget_and_causal_types(self):
        reference = self.run_checked(120, lod=2)
        expected = reference.metrics()
        reference_edges = {(event.kind, bool(event.causes)) for event in reference.events}
        for lod in (0, 1):
            actual_runner = self.run_checked(120, lod=lod)
            actual = actual_runner.metrics()
            for key in ("population", "trade", "infrastructure"):
                tolerance = max(1, abs(expected[key]) * 0.02)
                self.assertLessEqual(abs(actual[key] - expected[key]), tolerance)
            for key in ("production", "consumption", "stocks"):
                for resource in RESOURCES:
                    tolerance = max(1, abs(expected[key][resource]) * 0.02)
                    self.assertLessEqual(abs(actual[key][resource] - expected[key][resource]), tolerance)
            self.assertEqual(expected["crisis_dates"], actual["crisis_dates"])
            self.assertEqual(reference_edges,
                             {(event.kind, bool(event.causes)) for event in actual_runner.events})

    def test_diagnostics_answer_acceptance_questions(self):
        diagnostics = self.run_checked(24).diagnostics()
        self.assertEqual({
            "resource_sources", "goods_destinations", "critical_routes", "bottlenecks",
            "buildings", "closures", "deficits", "consequences", "movements",
        }, set(diagnostics))
        self.assertTrue(diagnostics["resource_sources"])
        self.assertTrue(diagnostics["movements"])

    def test_performance_report_schema_and_ui_probe(self):
        report = profile_scenario(years=1, worker_counts=(1, 2), ui_probe=lambda: None)
        self.assertEqual({
            "years", "total_seconds", "ticks_per_second", "peak_memory_bytes", "save_bytes",
            "history_bytes", "speedup", "phase_seconds", "phase_fraction", "ui_latency_seconds",
        }, set(report))
        self.assertGreater(report["ticks_per_second"], 0)


if __name__ == "__main__":
    unittest.main()
