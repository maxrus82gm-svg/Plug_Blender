"""One bounded request to the explicitly registered local Blender session."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
import socket
import tempfile
import time

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 4096
FEEDBACK_LOG_LIMIT = 200
FEEDBACK_LOG_READ_BYTES = 2 * 1024 * 1024
FEEDBACK_LOG_PATH = Path(__file__).resolve().parents[1] / ".runtime" / "agent_feedback.jsonl"


def default_session_file():
    return Path(tempfile.gettempdir()) / "astro_modeler" / "session.json"


def _normalize_feedback_entry(entry):
    if not isinstance(entry, dict):
        return None
    if set(entry) == {"time", "status", "summary", "details"}:
        entry = {
            "first_time": entry["time"], "last_time": entry["time"], "repeat_count": 1,
            "status": entry["status"], "summary": entry["summary"], "details": entry["details"],
        }
    if set(entry) != {"first_time", "last_time", "repeat_count", "status", "summary", "details"}:
        return None
    if (not all(isinstance(entry[key], str)
                for key in ("first_time", "last_time", "status", "summary", "details"))
            or entry["status"] not in {"OK", "WARNING", "BLOCKED"}
            or type(entry["repeat_count"]) is not int or entry["repeat_count"] < 1):
        return None
    return entry


def _recent_feedback_entries(path):
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            if size > FEEDBACK_LOG_READ_BYTES:
                stream.seek(size - FEEDBACK_LOG_READ_BYTES)
                stream.readline()  # Discard a possibly partial first line.
            else:
                stream.seek(0)
            lines = stream.readlines()
    except FileNotFoundError:
        return []
    entries = []
    for line in lines[-FEEDBACK_LOG_LIMIT:]:
        try:
            entry = _normalize_feedback_entry(json.loads(line.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if entry is not None:
            entries.append(entry)
    return entries[-(FEEDBACK_LOG_LIMIT - 1):]


def _record_feedback(status, summary, details, path=None, timestamp=None):
    """Persist only confirmed notes; failures never change tool delivery status."""
    path = Path(path or FEEDBACK_LOG_PATH)
    timestamp = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    entry = {
        "first_time": timestamp,
        "last_time": timestamp,
        "repeat_count": 1,
        "status": status,
        "summary": summary,
        "details": details,
    }
    entries = _recent_feedback_entries(path)
    if entries and all(entries[-1][key] == entry[key] for key in ("status", "summary", "details")):
        entries[-1]["last_time"] = timestamp
        entries[-1]["repeat_count"] += 1
    else:
        entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            for item in entries[-FEEDBACK_LOG_LIMIT:]:
                stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _request(command, session_file=None, arguments=None):
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
        request = {"command": command, "token": session["token"]}
        request.update(arguments or {})
        wire = (json.dumps(request, allow_nan=False, ensure_ascii=False) + "\n").encode("utf-8")
        if len(wire) > MAX_REQUEST_BYTES:
            return {"success": False, "message": "Request exceeds 4096 UTF-8 bytes; shorten the note. Nothing sent to Blender."}
        deadline = time.monotonic() + 5.0
        with socket.create_connection(("127.0.0.1", session["port"]), timeout=5.0) as client:
            client.sendall(wire)
            response = b""
            while b"\n" not in response:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Bridge response timed out")
                client.settimeout(remaining)
                chunk = client.recv(65536)
                if not chunk or len(response) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ValueError("Missing or oversized bridge response")
                response += chunk
        result = json.loads(response.split(b"\n", 1)[0].decode("utf-8"))
        if (not isinstance(result, dict) or type(result.get("success")) is not bool
                or not isinstance(result.get("message"), str)
                or (result["success"] and command in {"create_cube", "create_box_at_cursor"} and not isinstance(result.get("object_name"), str))
                or (result["success"] and command == "get_selected_context" and not isinstance(result.get("context"), dict))):
            raise ValueError("Invalid bridge result")
        return result
    except (OSError, ValueError):
        if command == "post_modeling_note":
            return {"success": False, "message": "Feedback delivery failed or is unconfirmed; inspect the Blender panel before resending."}
        if command == "get_selected_context":
            return {"success": False, "message": "Could not read selected context: Blender connection failed, timed out, or returned an invalid response."}
        # Creating an object is not idempotent. Never retry after a lost response.
        return {"success": False, "object_name": None, "message": "Blender connection failed or timed out; the outcome may be unknown. Inspect the scene before retrying, then reconnect Astro Modeler."}


def create_cube(session_file=None):
    result = _request("create_cube", session_file)
    return {"success": result["success"], "object_name": result.get("object_name"), "message": result["message"]}


def get_selected_context(session_file=None):
    result = _request("get_selected_context", session_file)
    return {"success": result["success"], "context": result.get("context"), "message": result["message"]}


def create_box_at_cursor(size_x, size_y, size_z, session_file=None):
    try:
        valid = all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (size_x, size_y, size_z))
    except OverflowError:
        valid = False
    if not valid:
        return {"success": False, "object_name": None, "message": "Box sizes must be finite positive numbers in Blender units."}
    result = _request("create_box_at_cursor", session_file, dict(size_x=size_x, size_y=size_y, size_z=size_z))
    return {"success": result["success"], "object_name": result.get("object_name"), "message": result["message"]}


def post_modeling_note(status, summary, details="", session_file=None, feedback_log=None):
    if (not isinstance(status, str) or status not in {"OK", "WARNING", "BLOCKED"}
            or not isinstance(summary, str) or not summary.strip() or len(summary) > 240
            or not isinstance(details, str) or len(details) > 1800):
        return {"success": False, "message": "Use OK/WARNING/BLOCKED, a nonblank summary (max 240 characters), and details (max 1800 characters)."}
    result = _request("post_modeling_note", session_file, dict(status=status, summary=summary, details=details))
    if result["success"]:
        try:
            _record_feedback(status, summary, details, feedback_log)
        except (OSError, UnicodeError):
            # Blender already confirmed delivery; the secondary diagnostic log
            # must not turn a visible successful note into a failed tool call.
            pass
    return {"success": result["success"], "message": result["message"]}
