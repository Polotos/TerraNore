import base64
import json
import os
import socket
import struct
import threading
import unittest
from urllib.request import Request, urlopen

from src.server import create_server
from src.server.app import _websocket_frame


def receive_frame(connection):
    first, second = connection.recv(2)
    size = second & 0x7F
    if size == 126:
        size = struct.unpack("!H", connection.recv(2))[0]
    elif size == 127:
        size = struct.unpack("!Q", connection.recv(8))[0]
    payload = b""
    while len(payload) < size:
        payload += connection.recv(size - len(payload))
    return first & 0x0F, payload


def masked_frame(opcode, payload=b""):
    mask = b"mask"
    size = len(payload)
    if size < 126:
        header = bytes((0x80 | opcode, 0x80 | size))
    elif size <= 0xFFFF:
        header = bytes((0x80 | opcode, 0xFE)) + struct.pack("!H", size)
    else:
        header = bytes((0x80 | opcode, 0xFF)) + struct.pack("!Q", size)
    encoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return header + mask + encoded


class WebSocketServerTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def connect(self):
        connection = socket.create_connection(self.server.server_address, timeout=2)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            "GET /api/test/v1/ws HTTP/1.1\r\nHost: localhost\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        connection.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += connection.recv(1)
        self.assertIn(b"101 Switching Protocols", response)
        return connection

    def test_connection_stays_open_and_receives_compact_tick_notifications(self):
        connection = self.connect()
        opcode, initial = receive_frame(connection)
        self.assertEqual(opcode, 1)
        self.assertEqual(set(json.loads(initial)), {
            "revision", "currentDate", "activeTask", "summary", "changedObjectIds",
        })

        request = Request(
            f"http://127.0.0.1:{self.server.server_address[1]}/api/test/v1/tick",
            data=b'{"ticks":2}', headers={"Content-Type": "application/json"}, method="POST",
        )
        json.load(urlopen(request, timeout=2))
        first = json.loads(receive_frame(connection)[1])
        second = json.loads(receive_frame(connection)[1])
        self.assertEqual((first["revision"], second["revision"]), (2, 3))
        self.assertEqual(second["currentDate"], "2200-03")
        self.assertTrue(second["changedObjectIds"])

        connection.sendall(masked_frame(0x9, b"health"))
        self.assertEqual(receive_frame(connection), (0xA, b"health"))
        connection.sendall(masked_frame(0x8, struct.pack("!H", 1000)))
        self.assertEqual(receive_frame(connection), (0x8, struct.pack("!H", 1000)))
        connection.close()

    def test_server_frames_use_all_rfc_length_encodings(self):
        self.assertEqual(_websocket_frame(1, b"a" * 125)[1], 125)
        self.assertEqual(_websocket_frame(1, b"a" * 126)[1:4], b"\x7e\x00\x7e")
        frame = _websocket_frame(1, b"a" * 65536)
        self.assertEqual(frame[1], 127)
        self.assertEqual(struct.unpack("!Q", frame[2:10])[0], 65536)


if __name__ == "__main__":
    unittest.main()
