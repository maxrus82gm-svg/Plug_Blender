"""Bounded, nonblocking local bridge. poll() is called by Blender's main thread."""

import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import time

HOST = "127.0.0.1"
PORT = 55881
MAX_BYTES = 4096
MAX_RESPONSE_BYTES = 1024 * 1024
IO_CHUNK = 65536
MAX_CLIENTS = 4
CLIENT_TIMEOUT = 5.0


def default_session_file():
    return Path(tempfile.gettempdir()) / "astro_modeler" / "session.json"


class Bridge:
    def __init__(self, create_cube, session_file=None, port=PORT, get_selected_context=None):
        self.create_cube = create_cube
        self.get_selected_context = get_selected_context
        self.session_file = Path(session_file or default_session_file()).resolve()
        self.port = port
        self.listener = None
        self.clients = {}
        self.token = None

    def start(self):
        if self.listener is not None:
            raise RuntimeError("Astro Modeler is already running.")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Exclusive bind prevents a second Blender session taking this endpoint.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((HOST, self.port))
            listener.listen(MAX_CLIENTS)
            listener.setblocking(False)
            self.token = secrets.token_hex(32)
            self.session_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = {
                "version": 1, "host": HOST, "port": listener.getsockname()[1],
                "token": self.token,
            }
            temporary = self.session_file.with_name(f".session-{secrets.token_hex(8)}.tmp")
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(descriptor, stream)
                os.replace(temporary, self.session_file)
            finally:
                temporary.unlink(missing_ok=True)
            self.listener = listener
        except Exception:
            listener.close()
            self.token = None
            raise

    def _close_client(self, client):
        self.clients.pop(client, None)
        client.close()

    def _request(self, raw):
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict) or set(request) != {"command", "token"}:
                raise ValueError("Expected only command and token.")
            token = request["token"]
            if not isinstance(token, str) or not secrets.compare_digest(token, self.token):
                raise ValueError("Invalid session token. Restart the integration connection.")
            if request["command"] == "get_selected_context":
                if self.get_selected_context is None:
                    raise RuntimeError("Selected context unavailable. Update the Astro Modeler add-on.")
                return {"success": True, "context": self.get_selected_context(), "message": "Selected context read."}
            if request["command"] != "create_cube":
                raise ValueError("Only create_cube and get_selected_context are supported.")
            name = self.create_cube()
            return {"success": True, "object_name": name, "message": "Cube created in the current scene."}
        except Exception as exc:
            # Never echo the request/token or provide an arbitrary Python operation.
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Invalid request or Blender operation failed."
            return {"success": False, "object_name": None, "message": message}

    def poll(self):
        if self.listener is None:
            return
        try:
            client, _ = self.listener.accept()
        except BlockingIOError:
            pass
        else:
            client.setblocking(False)
            if len(self.clients) >= MAX_CLIENTS:
                client.close()
            else:
                self.clients[client] = {"input": b"", "output": None, "deadline": time.monotonic() + CLIENT_TIMEOUT}
        # A maximum of four small reads/writes and one scene operation per timer tick.
        executed = False
        for client, state in list(self.clients.items()):
            try:
                if time.monotonic() >= state["deadline"]:
                    self._close_client(client)
                    continue
                if state["output"] is None:
                    if executed:
                        continue
                    chunk = client.recv(MAX_BYTES + 1)
                    if not chunk:
                        self._close_client(client)
                        continue
                    state["input"] += chunk
                    if len(state["input"]) > MAX_BYTES:
                        self._close_client(client)
                        continue
                    if b"\n" not in state["input"]:
                        continue
                    line, extra = state["input"].split(b"\n", 1)
                    if extra.strip():
                        self._close_client(client)
                        continue
                    result = self._request(line)
                    executed = True
                    try:
                        output = (json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
                    except (ValueError, TypeError):
                        output = b'{"success":false,"message":"Non-finite or invalid Blender context; no context returned."}\n'
                    if len(output) > MAX_RESPONSE_BYTES:
                        # Never silently truncate the selected object list.
                        output = b'{"success":false,"message":"Context exceeds 1 MiB; select fewer objects."}\n'
                    state["output"] = output
                sent = client.send(state["output"][:IO_CHUNK])
                state["output"] = state["output"][sent:]
                if not state["output"]:
                    self._close_client(client)
            except BlockingIOError:
                continue
            except OSError:
                self._close_client(client)

    def stop(self):
        for client in list(self.clients):
            self._close_client(client)
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        try:
            descriptor = json.loads(self.session_file.read_text(encoding="utf-8"))
            if self.token and descriptor.get("token") == self.token:
                self.session_file.unlink()
        except (OSError, ValueError, AttributeError):
            pass
        self.token = None
