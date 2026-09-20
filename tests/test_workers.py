import json
import os
import unittest

from src.simulation import Simulation
from src.simulation.workers import DeterministicScheduler, Phase, TradeOrder, random_stream, resolve_orders


def _square(value):
    return value * value


def _process_id(_value):
    return os.getpid()


class WorkerPipelineTests(unittest.TestCase):
    def test_world_is_bit_identical_for_several_worker_counts(self):
        encoded = []
        for workers in (1, 2, 4, 7):
            world = Simulation(seed=2718, workers=workers).step(36)
            encoded.append(json.dumps(world.to_dict(), sort_keys=True, separators=(",", ":")))
        self.assertEqual([encoded[0]] * len(encoded), encoded)

    def test_random_stream_is_decision_local(self):
        first = random_stream(1, 2, Phase.PRODUCTION, "a", "output").random()
        repeat = random_stream(1, 2, Phase.PRODUCTION, "a", "output").random()
        another_decision = random_stream(1, 2, Phase.PRODUCTION, "a", "investment").random()
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, another_decision)

    def test_scheduler_has_backpressure_and_preserves_order(self):
        scheduler = DeterministicScheduler(workers=2, max_pending=2)
        self.assertEqual([9, 4, 1], scheduler.map(_square, (value for value in (3, 2, 1))))
        self.assertTrue(all(pid != os.getpid() for pid in scheduler.map(_process_id, range(2))))
        scheduler.close()
        with self.assertRaises(ValueError):
            DeterministicScheduler(workers=2, max_pending=1)

    def test_trade_resolver_uses_distance_and_stable_tie_breaker(self):
        orders = (
            TradeOrder("buyer", 5, 3),
            TradeOrder("far", -5, 1),
            TradeOrder("near", -5, 1),
        )
        distances = {("far", "buyer"): 10, ("near", "buyer"): 2}
        shipments = resolve_orders(orders, lambda seller, buyer: distances[(seller, buyer)])
        self.assertEqual("near", shipments[0].seller_id)
        self.assertEqual(5, shipments[0].quantity)

    def test_history_and_events_are_bounded(self):
        simulation = Simulation(seed=8, workers=2, history_limit=3, event_limit=2)
        simulation.step(8)
        self.assertEqual(3, len(simulation.history))
        self.assertLessEqual(len(simulation.events), 2)

    def test_worker_configuration_validation(self):
        self.assertGreaterEqual(DeterministicScheduler("auto").workers, 1)
        self.assertEqual(3, DeterministicScheduler("3").workers)
        for invalid in (0, -1, "many"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                DeterministicScheduler(invalid)
