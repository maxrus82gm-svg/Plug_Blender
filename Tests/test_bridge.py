"""Protocol/connection checks without importing bpy or changing a Blender scene."""

import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("astro_bridge", ROOT / "Plugins/AstroModeler/astro_modeler/bridge.py")
bridge_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge_module)
sys.path.insert(0, str(ROOT / "MCP"))
from blender_client import create_cube


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "session.json"
        self.names = []

        def operation():
            name = f"Cube.{len(self.names) + 1:03d}"
            self.names.append(name)
            return name

        self.bridge = bridge_module.Bridge(operation, self.path, port=0)
        self.bridge.start()
        self.session = json.loads(self.path.read_text(encoding="utf-8"))

    def tearDown(self):
        self.bridge.stop()
        self.temp.cleanup()

    def exchange(self, request, fragmented=False):
        with socket.create_connection(("127.0.0.1", self.session["port"])) as client:
            wire = (json.dumps(request) + "\n").encode()
            if fragmented:
                client.sendall(wire[:8])
                self.bridge.poll()
                self.assertEqual(self.names, [])
                client.sendall(wire[8:])
            else:
                client.sendall(wire)
            client.setblocking(False)
            result = b""
            deadline = time.monotonic() + 2
            while b"\n" not in result and time.monotonic() < deadline:
                self.bridge.poll()
                try:
                    result += client.recv(4096)
                except BlockingIOError:
                    time.sleep(0.001)
            return json.loads(result)

    def request(self, **changes):
        return {"command": "create_cube", "token": self.session["token"], **changes}

    def test_create_cube_and_repeated_call(self):
        for expected in ("Cube.001", "Cube.002"):
            result = self.exchange(self.request())
            self.assertTrue(result["success"])
            self.assertEqual(result["object_name"], expected)

    def test_fragmented_message(self):
        self.assertTrue(self.exchange(self.request(), fragmented=True)["success"])

    def test_wrong_token_unknown_command_and_extra_fields_do_not_execute(self):
        for request in (self.request(token="bad"), self.request(command="execute_blender_python"), self.request(code="ignored")):
            self.assertFalse(self.exchange(request)["success"])
        self.assertEqual(self.names, [])

    def test_callback_failure(self):
        def failure():
            raise RuntimeError("Switch Blender to Object Mode.")
        self.bridge.create_cube = failure
        result = self.exchange(self.request())
        self.assertFalse(result["success"])
        self.assertIn("Object Mode", result["message"])

    def test_second_session_cannot_claim_endpoint(self):
        other = bridge_module.Bridge(lambda: "Bad", self.path, port=self.session["port"])
        with self.assertRaises(OSError):
            other.start()
        self.assertEqual(json.loads(self.path.read_text()), self.session)

    def test_stalled_and_oversized_clients_are_bounded(self):
        with socket.create_connection(("127.0.0.1", self.session["port"])) as client:
            self.bridge.poll()
            state = next(iter(self.bridge.clients.values()))
            state["deadline"] = time.monotonic() - 1
            self.bridge.poll()
            self.assertEqual(len(self.bridge.clients), 0)
        with socket.create_connection(("127.0.0.1", self.session["port"])) as client:
            client.sendall(b"x" * (bridge_module.MAX_BYTES + 1))
            self.bridge.poll()
            self.assertEqual(len(self.bridge.clients), 0)
        self.assertEqual(self.names, [])

    def test_stop_removes_own_descriptor(self):
        self.bridge.stop()
        self.assertFalse(self.path.exists())
        self.assertFalse(create_cube(self.path)["success"])

    def test_descriptor_cannot_redirect_client_off_loopback(self):
        self.session["host"] = "example.com"
        self.path.write_text(json.dumps(self.session))
        self.assertFalse(create_cube(self.path)["success"])
        self.assertEqual(self.names, [])


if __name__ == "__main__":
    unittest.main()
