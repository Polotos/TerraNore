import tempfile
import unittest
from pathlib import Path

from src.persistence import (
    EventLog, EventRecord, FORMAT, HistoryPolicy, SnapshotStore, TimeSeries,
    load_snapshot, save_snapshot,
)
from src.simulation import Simulation


class PersistenceTests(unittest.TestCase):
    def test_snapshot_round_trip(self):
        simulation = Simulation(seed=9, workers=2)
        simulation.world.start_date = "2345-06-07"
        simulation.world.accuracy_profile = "research"
        expected = simulation.step(5)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.test-save.json"
            save_snapshot(expected, path)
            actual = load_snapshot(path)
        self.assertEqual(FORMAT, "test-save-v1")
        self.assertEqual(expected.to_dict(), actual.to_dict())
        self.assertEqual(actual.start_date, "2345-06-07")
        self.assertEqual(actual.accuracy_profile, "research")

    def test_multilevel_history_preserves_pins_and_significant_dates(self):
        series = TimeSeries(HistoryPolicy(full_months=2, monthly_years=1, quarterly_years=2))
        simulation = Simulation(seed=1)
        for tick in range(37):
            if tick:
                simulation.step()
            series.capture(simulation.world)
        series.pin_interval(4, 5)
        series.mark_significant(7)
        series.compact(36)

        ticks = [point["tick"] for point in series.points]
        self.assertIn(4, ticks)
        self.assertIn(5, ticks)
        self.assertIn(7, ticks)
        self.assertNotIn(8, ticks)  # old unpinned monthly data is compacted
        self.assertTrue(all(tick in ticks for tick in (34, 35, 36)))
        self.assertEqual(set(series.metadata), {"population", "treasury"})

    def test_quarter_bucket_preserves_a_short_shortage_as_period_minimum(self):
        metadata = {
            "stocks": {"name": "Stocks", "unit": "goods", "aggregation": "snapshot"},
            "population": {"name": "Population", "unit": "people", "aggregation": "snapshot"},
            "production": {"name": "Production", "unit": "goods", "aggregation": "sum"},
        }
        series = TimeSeries(
            HistoryPolicy(full_months=0, monthly_years=0, quarterly_years=2),
            metadata=metadata,
        )
        # The one-month shortage is not on either quarterly boundary.
        for tick, stock in enumerate((100, 4, 100)):
            series.append(tick, {"stocks": stock, "population": 50, "production": 10})
        series.compact(30)

        bucket = series.buckets[0]
        self.assertEqual((bucket["start_tick"], bucket["end_tick"], bucket["count"]), (0, 2, 3))
        self.assertEqual(bucket["metrics"]["stocks"], {
            "first": 100, "last": 100, "min": 4, "max": 100,
            "average": 68.0, "value": 100,
        })
        self.assertEqual(bucket["metrics"]["production"]["sum"], 30)
        self.assertEqual(bucket["metrics"]["production"]["value"], 30)

    def test_pinned_and_significant_samples_are_not_bucketed(self):
        series = TimeSeries(HistoryPolicy(0, 0, 2))
        for tick in range(6):
            series.append(tick, {"population": tick, "treasury": tick})
        series.pin_interval(1, 2)
        series.mark_significant(4)
        series.compact(30)

        self.assertEqual([point["tick"] for point in series.points], [1, 2, 4])
        self.assertTrue(all(bucket["start_tick"] not in {1, 2, 4} for bucket in series.buckets))

    def test_event_journal_keeps_significant_dates_and_causal_chain(self):
        journal = EventLog()
        journal.append(EventRecord("cause", 3, "route-changed", object_id="route-1"))
        journal.append(EventRecord("effect", 4, "shortage", causes=("cause",)))

        self.assertTrue(journal.get("cause").significant)
        self.assertEqual(journal.causal_chain("effect")[0]["id"], "cause")
        self.assertEqual([item["tick"] for item in journal.between(0, 10)], [3, 4])

    def test_snapshot_store_is_copy_on_write_and_branches_explicitly(self):
        world = Simulation(seed=2).world
        store = SnapshotStore(world, interval=2)
        manual = store.create(world, snapshot_id="manual")
        self.assertEqual(manual.handle, f"{store.world_id}:manual")
        self.assertIs(store.resolve_handle(manual.handle), manual)
        self.assertEqual(store.snapshots["initial"].blob_id, manual.blob_id)
        world.tick = 2
        automatic = store.create_if_due(world)
        self.assertEqual(automatic.reason, "interval")

        viewed = store.open("manual")
        viewed.tick = 99
        self.assertEqual(store.open("manual").tick, 0)
        branch = store.branch("manual", "experiment")
        branch.tick = 1
        self.assertIn("experiment", store.branches)
        self.assertEqual(store.open("manual").tick, 0)

    def test_snapshot_store_rejects_snapshot_from_another_world(self):
        first = SnapshotStore(Simulation(seed=1).world)
        second = SnapshotStore(Simulation(seed=2).world)
        second.snapshots["initial"] = first.snapshots["initial"]

        with self.assertRaisesRegex(ValueError, "another world"):
            second.open("initial")
        with self.assertRaisesRegex(ValueError, "another world"):
            second.branch("initial", "foreign")
        with self.assertRaisesRegex(ValueError, "another world"):
            second.resolve_handle(first.snapshots["initial"].handle)
