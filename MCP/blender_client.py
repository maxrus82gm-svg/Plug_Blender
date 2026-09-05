"""One bounded request to the explicitly registered local Blender session."""

import json
from pathlib import Path
import socket
import tempfile
import time


def default_session_file():
    return Path(tempfile.gettempdir()) / "astro_modeler" / "session.json"


def create_cube(session_file=None):
    try:
        path = Path(session_file or default_session_file())
        if path.stat().st_size > 4096:
            raise ValueError("Invalid session descriptor size.")
        session = json.loads(path.read_text(encoding="utf-8"))
        if (session.get("version") != 1 or session.get("host") != "127.0.0.1"
                or type(session.get("port")) is not int or not 1 <= session["port"] <= 65535
                or not isinstance(session.get("token"), str) or len(session["token"]) != 64):
            raise ValueError("Invalid local session descriptor. Restart Astro Modeler.")
    except FileNotFoundError:
        return {"success": False, "object_name": None, "message": "No connected Blender session. Click Start Integration in Astro Modeler."}
    except (OSError, ValueError, AttributeError) as exc:
        return {"success": False, "object_name": None, "message": f"Cannot read the Blender session ({type(exc).__name__}). Restart Astro Modeler."}
    try:
        deadline = time.monotonic() + 5.0
        with socket.create_connection(("127.0.0.1", session["port"]), timeout=5.0) as client:
            request = {"command": "create_cube", "token": session["token"]}
            client.sendall((json.dumps(request) + "\n").encode("utf-8"))
            response = b""
            while b"\n" not in response:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Bridge response timed out")
                client.settimeout(remaining)
                chunk = client.recv(4097)
                if not chunk or len(response) + len(chunk) > 4096:
                    raise ValueError("Missing or oversized bridge response")
                response += chunk
        result = json.loads(response.split(b"\n", 1)[0].decode("utf-8"))
        if (not isinstance(result, dict) or type(result.get("success")) is not bool
                or not isinstance(result.get("message"), str)
                or (result["success"] and not isinstance(result.get("object_name"), str))):
            raise ValueError("Invalid bridge result")
        return result
    except (OSError, ValueError):
        # Creating an object is not idempotent. Never retry after a lost response.
        return {"success": False, "object_name": None, "message": "Blender connection failed or timed out; the outcome may be unknown. Inspect the scene before retrying, then reconnect Astro Modeler."}
