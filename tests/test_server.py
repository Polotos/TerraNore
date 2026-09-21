import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.server import create_server
from src.simulation import Simulation


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_ui_and_api(self):
        html = urlopen(self.base + "/", timeout=2).read().decode()
        self.assertIn("TerraNore — генератор мира", html)
        state = json.load(urlopen(self.base + "/api/test/v1/state", timeout=2))
        self.assertEqual(state["product"], "TerraNore")
        request = Request(self.base + "/api/test/v1/tick", data=b'{"ticks":12}', headers={"Content-Type":"application/json"}, method="POST")
        updated = json.load(urlopen(request, timeout=2))
        self.assertEqual(updated["world"]["tick"], 12)

    def test_world_creation_uses_and_returns_validated_configuration(self):
        configuration = {
            "seed": 91, "systemCount": 3, "settlementCount": 7,
            "startDate": "2312-04-05", "accuracyProfile": "research", "workers": 2,
        }
        request = Request(
            self.base + "/api/test/v1/worlds", data=json.dumps(configuration).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        state = json.load(urlopen(request, timeout=2))
        self.assertEqual(state["world"]["systemCount"], 3)
        self.assertEqual(state["world"]["settlementCount"], 7)
        self.assertEqual(state["world"]["startDate"], "2312-04-05")
        self.assertEqual(state["world"]["accuracyProfile"], "research")
        self.assertEqual(state["world"]["workers"], 2)
        self.assertEqual(len(state["world"]["regions"]), 7)
        self.assertEqual(self.server.app.simulation.scheduler.workers, 2)

    def test_world_creation_rejects_invalid_ranges(self):
        invalid = {
            "seed": 1, "systemCount": 0, "settlementCount": 1,
            "startDate": "2200-01-01", "accuracyProfile": "balanced", "workers": 1,
        }
        request = Request(
            self.base + "/api/test/v1/worlds", data=json.dumps(invalid).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=2)
        self.assertEqual(raised.exception.code, 400)

    def test_event_log_keeps_receiving_events_after_bounded_buffer_overflows(self):
        self.server.app.simulation.close()
        self.server.app.simulation = Simulation(seed=8, workers=1, event_limit=1)

        self.server.app.step(8)

        records = self.server.app.event_log.records
        self.assertGreater(len(records), 8)
        self.assertEqual(8, records[-1].tick)
        self.assertEqual(1, len(self.server.app.simulation.events))
