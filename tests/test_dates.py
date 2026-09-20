import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from pathlib import Path

from src.persistence import load_snapshot, save_snapshot
from src.server import create_server
from src.simulation import Simulation
from src.simulation.model import SimulationDate, World


class SimulationDateTests(unittest.TestCase):
    def test_calendar_boundaries_and_world_clock(self):
        self.assertEqual(str(SimulationDate.parse("10.3\\3.4.4300").add_ticks(1)), "10.3\\1.1.4301")
        world = World.create(start_date="01.1\\1.1.4300")
        simulation = Simulation(world=world, workers=1)
        simulation.step(13)
        self.assertEqual(str(world.current_date), "01.1\\2.1.4301")

    def test_dates_are_saved_explicitly(self):
        simulation = Simulation(workers=1)
        simulation.step(2)
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/date.json"
            save_snapshot(simulation.world, path)
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            restored = load_snapshot(path)
        self.assertEqual(payload["world"]["initial_date"], "01.1\\1.1.4300")
        self.assertEqual(payload["world"]["current_date"], "01.1\\3.1.4300")
        self.assertEqual(restored.current_date, simulation.world.current_date)


class TargetDateTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/api/test/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.server.app.simulation.close()

    def post(self, value):
        request = Request(self.base + "/simulation/run", data=json.dumps(value).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        return json.load(urlopen(request, timeout=2))

    def test_target_date_is_relative_to_world_epoch(self):
        task = self.post({"targetDate": "01.1\\2.1.4300"})["task"]
        self.assertEqual(task["targetTick"], 1)

    def test_past_and_overflow_dates_are_rejected(self):
        self.server.app.step(2)
        with self.assertRaises(HTTPError) as past:
            self.post({"targetDate": "01.1\\2.1.4300"})
        self.assertEqual(past.exception.code, 400)
        with self.assertRaises(HTTPError) as overflow:
            self.post({"targetDate": "01.1\\1.1.999999999999999999"})
        self.assertEqual(overflow.exception.code, 400)
