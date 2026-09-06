"""Real MCP SDK + Codex calls to the isolated installed-ZIP GUI box fixture."""

import asyncio
import json
from pathlib import Path
import socket
import sys
import time
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
DESCRIPTOR = RUNTIME / "box-session.json"


async def control(action, **kwargs):
    nonce = uuid.uuid4().hex
    temporary = RUNTIME / "box-control.tmp"
    temporary.write_text(json.dumps(dict(nonce=nonce, action=action, **kwargs)), encoding="utf-8")
    temporary.replace(RUNTIME / "box-control.json")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            result = json.loads((RUNTIME / "box-state.json").read_text(encoding="utf-8"))
            if result["nonce"] == nonce:
                assert result["success"], result
                return {k: v for k, v in result.items() if k != "nonce"}
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        await asyncio.sleep(0.1)
    raise TimeoutError("Isolated Blender fixture did not acknowledge control")


def nonfinite_bridge(value):
    descriptor = json.loads(DESCRIPTOR.read_text(encoding="utf-8"))
    with socket.create_connection(("127.0.0.1", descriptor["port"]), timeout=5) as client:
        client.sendall((json.dumps({"command": "create_box_at_cursor", "token": descriptor["token"],
                                   "size_x": value, "size_y": 2, "size_z": 3}) + "\n").encode())
        response = b""
        while b"\n" not in response:
            chunk = client.recv(4096)
            if not chunk:
                raise RuntimeError("Missing response; no retry")
            response += chunk
    return json.loads(response)


async def main():
    params = StdioServerParameters(command=sys.executable,
        args=[str(ROOT / "MCP/server.py"), "--session-file", str(DESCRIPTOR)])
    evidence = {"cases": [], "source": "real GUI Blender, installed ZIP, SDK STDIO"}
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert names == {"create_cube", "get_selected_context", "create_box_at_cursor"}
            for label, position, rotation, sizes in (
                ("origin", [0, 0, 0], [0, 0, 0], [20, 10, 5]),
                ("translated", [7, -3, 2.5], [0, 0, 0], [3, 4, 7]),
                ("cursor_rotation_ignored", [-4, 6, 2], [.3, -.7, 1.2], [2, 5, 9]),
                ("repeat_and_unit_scale_ignored", [-4, 6, 2], [.3, -.7, 1.2], [.25, 2.5, 12]),
            ):
                before = await control("configure", position=position, rotation=rotation, unit_scale=.001)
                result = await session.call_tool("create_box_at_cursor", dict(zip(("size_x", "size_y", "size_z"), sizes)))
                assert result.structuredContent["success"], result
                after = await control("snapshot")
                assert after["objects"] == before["objects"] + 1
                context = (await session.call_tool("get_selected_context", {})).structuredContent
                assert context["success"] and context["context"]["active_object"]["name"] == result.structuredContent["object_name"]
                evidence["cases"].append(label)
            before = await control("snapshot")
            for value in (0, -1, True, "2"):
                result = await session.call_tool("create_box_at_cursor", {"size_x": value, "size_y": 2, "size_z": 3})
                assert result.isError, result
            for value in (float("nan"), float("inf"), -float("inf")):
                assert not (await asyncio.to_thread(nonfinite_bridge, value))["success"]
            # Finite inputs outside Blender float32 mesh range fail in Blender itself.
            for value in (1e40, 1e-50):
                result = await session.call_tool("create_box_at_cursor", {"size_x": value, "size_y": 2, "size_z": 3})
                assert not result.structuredContent["success"]
            assert await control("snapshot") == before
            evidence["cases"].append("invalid/nonfinite/range: no object or mesh allocated")
            before = await control("configure", position=[1, 2, 3], edit=True)
            result = await session.call_tool("create_box_at_cursor", {"size_x": 2, "size_y": 3, "size_z": 4})
            assert not result.structuredContent["success"] and "Object Mode" in result.structuredContent["message"]
            assert await control("snapshot") == before
            evidence["cases"].append("Edit Mode rejected unchanged")
            before = await control("configure", position=[8, -3, 1], rotation=[.4, .7, -.5])
            process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / "Tests/codex_runtime.py"), "--box")
            assert await process.wait() == 0
            after = await control("snapshot")
            assert after["objects"] == before["objects"] + 1 and after["box_calls"] == before["box_calls"] + 1
            evidence["cases"].append("installed Codex app-server: one Box, independent fixture checks")
            result = await session.call_tool("create_cube", {})
            assert result.structuredContent["success"]
            await control("cube_check")
            context = (await session.call_tool("get_selected_context", {})).structuredContent
            assert context["context"]["active_object"]["name"] == result.structuredContent["object_name"]
            evidence["cases"].append("Create Cube / Selected Context regression")
    await control("finish")
    evidence["cases"].append("ZIP install/enable, stop/restart, load disconnect, disable cleanup")
    evidence["events"] = json.loads((RUNTIME / "box-events.json").read_text(encoding="utf-8"))
    evidence["success"] = True
    (RUNTIME / "box-result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"success": True, "cases": evidence["cases"], "boxes_verified": len(evidence["events"])}))


if __name__ == "__main__":
    asyncio.run(main())
