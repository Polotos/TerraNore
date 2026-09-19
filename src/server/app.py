from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from src.persistence import TimeSeries
from src.simulation import Simulation
from src.simulation.lod import DetailSelector

API = "/api/test/v1"
UI_ROOT = Path(__file__).parents[1] / "ui"


class AppState:
    def __init__(self, seed: int = 42) -> None:
        self.simulation = Simulation(seed)
        self.series = TimeSeries()
        self.series.capture(self.simulation.world)
        self.lock = threading.Lock()

    def payload(self) -> dict:
        world = self.simulation.world
        return {
            "product": "TerraNore Test",
            "saveFormat": "test-save-v1",
            "world": world.to_dict(),
            "summary": {
                "year": world.tick // Simulation.TICKS_PER_YEAR,
                "month": world.tick % Simulation.TICKS_PER_YEAR + 1,
                "population": sum(item.population for item in world.regions),
                "treasury": round(sum(item.economy.treasury for item in world.regions), 2),
                "production": round(sum(item.economy.production for item in world.regions), 2),
            },
            "series": self.series.points[-120:],
        }


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class TestRequestHandler(BaseHTTPRequestHandler):
    server_version = "TerraNoreTest/0.1"

    @property
    def app(self) -> AppState:
        return self.server.app  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == f"{API}/state":
            _json(self, 200, self.app.payload())
        elif path == f"{API}/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
            self._websocket()
        else:
            self._static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            size = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(size) or b"{}")
            with self.app.lock:
                if path == f"{API}/tick":
                    ticks = min(1200, max(1, int(data.get("ticks", 1))))
                    self.app.simulation.step(ticks)
                    self.app.series.capture(self.app.simulation.world)
                elif path == f"{API}/reset":
                    self.server.app = AppState(int(data.get("seed", 42)))  # type: ignore[attr-defined]
                elif path == f"{API}/lod":
                    DetailSelector().set_level(self.app.simulation.world, data["regionId"], data["level"])
                else:
                    _json(self, 404, {"error": "unknown test endpoint"})
                    return
            _json(self, 200, self.server.app.payload())  # type: ignore[attr-defined]
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            _json(self, 400, {"error": str(error)})

    def _static(self, path: str) -> None:
        relative = "index.html" if path == "/" else path.lstrip("/")
        candidate = (UI_ROOT / relative).resolve()
        if UI_ROOT.resolve() not in candidate.parents or not candidate.is_file():
            self.send_error(404)
            return
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(candidate)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400)
            return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        data = json.dumps(self.app.payload(), ensure_ascii=False).encode()
        header = b"\x81" + (bytes([len(data)]) if len(data) < 126 else b"\x7e" + struct.pack("!H", len(data)))
        self.connection.sendall(header + data)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[TerraNore Test] {self.address_string()} - {format % args}")


def create_server(host: str = "127.0.0.1", port: int = 0, seed: int = 42) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), TestRequestHandler)
    server.app = AppState(seed)  # type: ignore[attr-defined]
    return server
