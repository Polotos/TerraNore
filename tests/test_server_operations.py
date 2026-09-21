import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.server import create_server


class ServerOperationTests(unittest.TestCase):
    def setUp(self):
        self.saves = tempfile.TemporaryDirectory()
        self.server = create_server(saves_dir=self.saves.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/api/test/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.saves.cleanup()

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
        self.assertEqual(changed["object"]["id"], "region-1")
        self.assertEqual(changed["object"]["manualLod"], "lod-2")
        self.assertEqual(changed["object"]["effectiveLod"], "lod-2")

        automatic = self.post("/objects/region-1/lod", {"level": "auto"})
        self.assertEqual(automatic["object"]["manualLod"], "auto")
        self.assertEqual(automatic["object"]["effectiveLod"], "lod-0")

        with self.assertRaises(HTTPError) as error:
            self.get("/timeseries", **{"from": 0, "to": 10001})
        self.assertEqual(error.exception.code, 413)
        with self.assertRaises(HTTPError) as error:
            self.post("/lod", {"regionId": "missing", "level": "lod-0"})
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as error:
            self.post("/lod", {"regionId": "region-1", "level": "ultra"})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.get("/state")["revision"], automatic["revision"])

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
        self.assertIsInstance(created["completed"], int)
        active = self.get("/state")["activeTask"]
        self.assertEqual(active["id"], created["id"])
        self.assertEqual(active["cancellationToken"], created["cancellationToken"])
        control = {"taskId": created["id"], "cancellationToken": created["cancellationToken"]}
        paused = self.post("/simulation/pause", control)["task"]
        self.assertEqual(paused["status"], "paused")
        with self.assertRaises(HTTPError) as error:
            self.post("/simulation/step", {"ticks": 1})
        self.assertEqual(error.exception.code, 409)
        resumed = self.post("/simulation/resume", control)["task"]
        self.assertIn(resumed["status"], ("paused", "running"))
        cancelled = self.post("/simulation/cancel", {
            "taskId": created["id"], "cancellationToken": created["cancellationToken"]
        })["task"]
        self.assertEqual(cancelled["status"], "cancelled")

        self.post("/simulation/step", {"ticks": 2})
        self.assertTrue(self.get("/timeseries", **{"from": 0, "to": 100})["items"])
        object_points = self.get(
            "/timeseries", **{"from": 0, "to": 2, "objectId": "region-1", "metric": "production"}
        )["items"]
        self.assertEqual([point["tick"] for point in object_points], [0, 1, 2])
        self.assertTrue(all(set(point) == {"tick", "production"} for point in object_points))
        self.assertIn("delta", self.get("/compare", left=0, right=2))
        self.assertIn("items", self.get("/events", **{"from": 0, "to": 2}))
        self.assertIn("items", self.get("/anomalies", **{"from": 0, "to": 2}))

    def test_task_controls_require_matching_non_empty_token(self):
        for operation in ("pause", "resume", "cancel"):
            with self.subTest(operation=operation):
                created = self.post("/simulation/run", {"targetTick": 1_000_000})["task"]
                credentials = {
                    "taskId": created["id"],
                    "cancellationToken": created["cancellationToken"],
                }
                if operation == "resume":
                    self.post("/simulation/pause", credentials)

                with self.assertRaises(HTTPError) as error:
                    self.post(f"/simulation/{operation}", {
                        "cancellationToken": created["cancellationToken"],
                    })
                self.assertEqual(error.exception.code, 400)

                for token, expected_status in ((None, 400), ("", 400), ("incorrect-token", 403)):
                    request = {"taskId": created["id"]}
                    if token is not None:
                        request["cancellationToken"] = token
                    with self.subTest(operation=operation, token=token):
                        with self.assertRaises(HTTPError) as error:
                            self.post(f"/simulation/{operation}", request)
                        self.assertEqual(error.exception.code, expected_status)

                result = self.post(f"/simulation/{operation}", credentials)["task"]
                expected = {"pause": "paused", "resume": "running", "cancel": "cancelled"}[operation]
                self.assertIn(result["status"], (expected, "paused") if operation == "resume" else (expected,))

                if operation != "cancel":
                    self.post("/simulation/cancel", credentials)

    def test_timeseries_returns_compacted_buckets(self):
        # Use the synchronous operation so the assertion cannot race a run task.
        self.post("/simulation/step", {"ticks": 130})
        payload = self.get(
            "/timeseries", **{"from": 0, "to": 10, "objectId": "region-1", "metric": "production"}
        )

        self.assertTrue(payload["items"])
        bucket = payload["items"][0]
        self.assertEqual(
            set(bucket), {"start_tick", "end_tick", "count", "production", "metrics"}
        )
        self.assertIn("sum", bucket["metrics"]["production"])
        self.assertEqual(bucket["production"], bucket["metrics"]["production"]["value"])

    def test_snapshots_and_save_load(self):
        snapshot = self.post("/snapshots")["snapshot"]
        opened = self.post(f"/snapshots/{snapshot['id']}/open")
        self.assertTrue(opened["readOnly"])
        with self.assertRaises(HTTPError) as error:
            self.post("/simulation/step")
        self.assertEqual(error.exception.code, 409)

        saved = self.post("/save", {"name": "campaign/world.json"})
        self.assertEqual(saved["name"], "campaign/world.json")
        loaded = self.post("/load", {"name": "campaign/world.json"})
        self.assertFalse(loaded["readOnly"])

    def test_save_paths_are_confined_and_developer_import_is_opt_in(self):
        for name in ("../outside.json", "..\\outside.json", "/tmp/outside.json", "C:\\outside.json"):
            with self.subTest(name=name), self.assertRaises(HTTPError) as error:
                self.post("/save", {"name": name})
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.post("/save", {"path": "legacy.json"})
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.post("/developer/import", {"path": "/tmp/external.json"})
        self.assertEqual(error.exception.code, 404)

    def test_snapshot_continuation_requires_named_branch(self):
        snapshot = self.post("/snapshots", {"id": "fork-point"})["snapshot"]
        self.post(f"/snapshots/{snapshot['id']}/open")
        branched = self.post(f"/snapshots/{snapshot['id']}/branch", {"branchId": "what-if"})
        self.assertFalse(branched["readOnly"])
        self.assertEqual(branched["branchId"], "what-if")
        advanced = self.post("/simulation/step")
        self.assertEqual(advanced["world"]["tick"], snapshot["tick"] + 1)

    def test_reset_starts_a_new_snapshot_store(self):
        stale = self.post("/snapshots", {"id": "world-a"})["snapshot"]

        self.post("/reset", {"seed": 99})
        current = self.post("/snapshots", {"id": "world-a"})["snapshot"]

        self.assertNotEqual(stale["id"], current["id"])

        with self.assertRaises(HTTPError) as error:
            self.post(f"/snapshots/{stale['id']}/open")
        self.assertEqual(error.exception.code, 409)
        with self.assertRaises(HTTPError) as error:
            self.post(f"/snapshots/{stale['id']}/branch", {"branchId": "stale-world"})
        self.assertEqual(error.exception.code, 409)
        opened = self.post(f"/snapshots/{current['id']}/open")
        self.assertEqual(opened["world"]["seed"], 99)

    def test_non_loopback_bind_is_rejected(self):
        with self.assertRaises(ValueError):
            create_server("0.0.0.0")
