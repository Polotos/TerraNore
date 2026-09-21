"""Regression coverage for Windows-style spawned simulation workers."""

import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


class SpawnedServerTests(unittest.TestCase):
    def test_server_module_uses_workers_without_spawning_more_servers(self):
        # ProcessPoolExecutor uses ``spawn`` explicitly, including on Unix. This
        # reproduces Windows' import of the main module in each worker.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]

        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "src.server", "--port", str(port), "--no-browser"],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                base = f"http://127.0.0.1:{port}/api/test/v1"
                deadline = time.monotonic() + 10
                while True:
                    try:
                        urlopen(base + "/state", timeout=0.25).close()
                        break
                    except URLError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            self.fail("server did not start")
                        time.sleep(0.05)

                configuration = {
                    "seed": 42,
                    "systemCount": 2,
                    "settlementCount": 4,
                    "startDate": "2200-01-01",
                    "accuracyProfile": "balanced",
                    "workers": 2,
                }
                create = Request(
                    base + "/worlds",
                    data=json.dumps(configuration).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                created = json.load(urlopen(create, timeout=10))
                self.assertEqual(created["world"]["workers"], 2)

                tick = Request(
                    base + "/tick",
                    data=b'{"ticks":1}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                advanced = json.load(urlopen(tick, timeout=20))
                self.assertEqual(advanced["world"]["tick"], 1)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            log.seek(0)
            output = log.read()

        # A spawned worker imports src.server.__main__ as __mp_main__. Only the
        # original process may print the server banner; children must remain
        # ProcessPool workers rather than starting additional HTTP servers.
        self.assertEqual(output.count("TerraNore Test запущен:"), 1, output)


if __name__ == "__main__":
    unittest.main()
