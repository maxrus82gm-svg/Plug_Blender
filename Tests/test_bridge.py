"""Protocol/connection checks without importing bpy or changing a Blender scene."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
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
from blender_client import create_cube, get_selected_context, create_box_at_cursor


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

    def test_context_read_is_separate_from_create_and_accepts_no_extra_fields(self):
        context = {"mode": "OBJECT", "active_object": None, "selected_objects": [], "3d_cursor": {"position": [0, 0, 0], "orientation_wxyz": [1, 0, 0, 0]}}
        self.bridge.get_selected_context = lambda: context
        for _ in range(2):
            result = self.exchange(self.request(command="get_selected_context"))
            self.assertTrue(result["success"])
            self.assertEqual(result["context"], context)
        for changes in ({"token": "bad"}, {"objects": "all"}):
            self.assertFalse(self.exchange(self.request(command="get_selected_context", **changes))["success"])
        self.assertEqual(self.names, [])

    def test_real_client_reads_context_larger_than_old_4k_limit(self):
        objects = [{"name": f"Object.{index:03}", "type": "EMPTY", "matrix_world": [[1, 0, 0, index], [0, 1, 0, 0], [0, 0, 1, 0]]} for index in range(100)]
        context = {"mode": "OBJECT", "active_object": None, "selected_objects": objects, "3d_cursor": {"position": [0, 0, 0], "orientation_wxyz": [1, 0, 0, 0]}}
        self.bridge.get_selected_context = lambda: context
        self.assertGreater(len(json.dumps(context)), 4096)
        # This external Python test uses a client thread; the Blender add-on does not.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(get_selected_context, self.path)
            deadline = time.monotonic() + 3
            while not future.done() and time.monotonic() < deadline:
                self.bridge.poll()
                time.sleep(0.001)
            result = future.result(timeout=2)
        self.assertTrue(result["success"])
        self.assertEqual(result["context"], context)
        self.assertEqual(self.names, [])

    def test_oversized_or_nonfinite_context_returns_error_without_partial_list(self):
        for context in ({"value": "x" * bridge_module.MAX_RESPONSE_BYTES}, {"value": float("nan")}):
            self.bridge.get_selected_context = lambda: context
            result = self.exchange(self.request(command="get_selected_context"))
            self.assertFalse(result["success"])
            self.assertNotIn("context", result)
        self.assertEqual(self.names, [])

    def test_context_unavailable_and_missing_session_are_clear_errors(self):
        result = self.exchange(self.request(command="get_selected_context"))
        self.assertFalse(result["success"])
        self.assertIn("Update", result["message"])
        self.bridge.stop()
        result = get_selected_context(self.path)
        self.assertFalse(result["success"])
        self.assertIsNone(result["context"])
        self.assertNotIn("object_name", result)

    def test_box_arguments_are_validated_before_callback(self):
        calls = []
        self.bridge.create_box_at_cursor = lambda *sizes: calls.append(sizes) or "Box"
        valid = self.request(command="create_box_at_cursor", size_x=20, size_y=10, size_z=5)
        self.assertTrue(self.exchange(valid)["success"])
        self.assertEqual(calls, [(20, 10, 5)])
        for axis in ("size_x", "size_y", "size_z"):
            for value in (0, -1, float("nan"), float("inf"), -float("inf"), True, "2", None):
                self.assertFalse(self.exchange({**valid, axis: value})["success"])
        for change in ({"token": "bad"}, {"extra": 1}):
            self.assertFalse(self.exchange({**valid, **change})["success"])
        missing = dict(valid)
        del missing["size_z"]
        self.assertFalse(self.exchange(missing)["success"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.names, [])

    def test_box_client_roundtrip_and_local_validation(self):
        self.bridge.create_box_at_cursor = lambda x, y, z: "Box"
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(create_box_at_cursor, 3, 4, 5, self.path)
            deadline = time.monotonic() + 3
            while not future.done() and time.monotonic() < deadline:
                self.bridge.poll()
                time.sleep(0.001)
            self.assertEqual(future.result(timeout=2)["object_name"], "Box")
        for value in (0, -1, float("nan"), float("inf"), True, "2", 10 ** 400):
            result = create_box_at_cursor(value, 2, 3, self.path)
            self.assertFalse(result["success"])
            self.assertIn("finite positive", result["message"])


if __name__ == "__main__":
    unittest.main()
