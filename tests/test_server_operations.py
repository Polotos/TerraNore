import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.server import create_server


class ServerOperationTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/api/test/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, path, data=None):
        request = Request(
            self.base + path,
            data=json.dumps(data or {}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return json.load(urlopen(request, timeout=3))

    def get(self, path, **query):
        suffix = "?" + urlencode(query) if query else ""
        return json.load(urlopen(self.base + path + suffix, timeout=3))

    def test_revision_navigation_lod_and_validation(self):
        state = self.get("/state")
        revision = state["revision"]
        children = self.get("/nodes/world/children")
        self.assertEqual(len(children["items"]), 4)
        card = self.get("/objects/region-1")
        self.assertEqual(card["object"]["id"], "region-1")
        changed = self.post("/objects/region-1/lod", {"level": "lod-2"})
        self.assertGreater(changed["revision"], revision)
        self.assertEqual(changed["world"]["regions"][0]["detail_level"], "lod-2")

        with self.assertRaises(HTTPError) as error:
            self.get("/timeseries", **{"from": 0, "to": 10001})
        self.assertEqual(error.exception.code, 413)
        with self.assertRaises(HTTPError) as error:
            self.post("/lod", {"regionId": "missing", "level": "lod-0"})
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as error:
            self.post("/lod", {"regionId": "region-1", "level": "ultra"})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.get("/state")["revision"], changed["revision"])

    def test_equal_period_results_survive_real_lod_transition_over_http(self):
        baseline = self.post("/simulation/step", {"ticks": 6})
        expected = {
            "population": baseline["summary"]["population"],
            "treasury": baseline["summary"]["treasury"],
            "production": baseline["summary"]["production"],
        }

        self.post("/reset", {"seed": 42})
        before = self.get("/state")["revision"]
        changed = self.post("/objects/region-1/lod", {"level": "lod-2"})
        self.assertEqual(changed["world"]["regions"][0]["detail_level"], "lod-2")
        self.assertEqual(changed["revision"], before + 1)
        actual = self.post("/simulation/step", {"ticks": 6})
        self.assertEqual(actual["summary"], {**expected, "year": 0, "month": 7})

    def test_task_pause_resume_cancel_and_queries(self):
        created = self.post("/simulation/run", {"targetTick": 100})["task"]
        paused = self.post("/simulation/pause", {"taskId": created["id"]})["task"]
        self.assertEqual(paused["status"], "paused")
        resumed = self.post("/simulation/resume", {"taskId": created["id"]})["task"]
        self.assertIn(resumed["status"], ("paused", "running"))
        cancelled = self.post("/simulation/cancel", {
            "taskId": created["id"], "cancellationToken": created["cancellationToken"]
        })["task"]
        self.assertEqual(cancelled["status"], "cancelled")

        self.post("/simulation/step", {"ticks": 2})
        self.assertTrue(self.get("/timeseries", **{"from": 0, "to": 100})["items"])
        self.assertIn("delta", self.get("/compare", left=0, right=2))
        self.assertIn("items", self.get("/events", **{"from": 0, "to": 2}))
        self.assertIn("items", self.get("/anomalies", **{"from": 0, "to": 2}))

    def test_snapshots_and_save_load(self):
        snapshot = self.post("/snapshots")["snapshot"]
        opened = self.post(f"/snapshots/{snapshot['id']}/open")
        self.assertTrue(opened["readOnly"])
        with self.assertRaises(HTTPError) as error:
            self.post("/simulation/step")
        self.assertEqual(error.exception.code, 409)

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "world.json")
            self.post("/save", {"path": path})
            loaded = self.post("/load", {"path": path})
        self.assertFalse(loaded["readOnly"])

    def test_snapshot_continuation_requires_named_branch(self):
        snapshot = self.post("/snapshots", {"id": "fork-point"})["snapshot"]
        self.post(f"/snapshots/{snapshot['id']}/open")
        branched = self.post(f"/snapshots/{snapshot['id']}/branch", {"branchId": "what-if"})
        self.assertFalse(branched["readOnly"])
        self.assertEqual(branched["branchId"], "what-if")
        advanced = self.post("/simulation/step")
        self.assertEqual(advanced["world"]["tick"], snapshot["tick"] + 1)

    def test_non_loopback_bind_is_rejected(self):
        with self.assertRaises(ValueError):
            create_server("0.0.0.0")
