import json
import threading
import unittest
from urllib.request import Request, urlopen

from src.server import create_server


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
        self.assertIn("TerraNore Test", html)
        state = json.load(urlopen(self.base + "/api/test/v1/state", timeout=2))
        self.assertEqual(state["product"], "TerraNore Test")
        request = Request(self.base + "/api/test/v1/tick", data=b'{"ticks":12}', headers={"Content-Type":"application/json"}, method="POST")
        updated = json.load(urlopen(request, timeout=2))
        self.assertEqual(updated["world"]["tick"], 12)
