"""SDK/Codex end-to-end checks against the explicitly launched GUI fixture."""

import asyncio
import json
import math
from pathlib import Path
import sys
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"


async def select_case(name):
    nonce = str(uuid.uuid4())
    temporary = RUNTIME / "selected-control.tmp"
    temporary.write_text(json.dumps({"nonce": nonce, "case": name}), encoding="utf-8")
    temporary.replace(RUNTIME / "selected-control.json")
    for _ in range(300):
        path = RUNTIME / "selected-state.json"
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if state["nonce"] == nonce:
                return state
        await asyncio.sleep(0.05)
    raise RuntimeError(f"Blender did not prepare {name}")


def close(actual, expected):
    if isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected):
            close(a, e)
    else:
        assert math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6), (actual, expected)


def verify(result, state):
    assert result["success"], result
    context = result["context"]
    facts = state["facts"]
    assert context["mode"] == facts["mode"]
    expected_active = {"name": facts["active"], "type": facts["active_type"]} if facts["active"] else None
    assert context["active_object"] == expected_active
    assert [obj["name"] for obj in context["selected_objects"]] == list(facts["selected"])
    for obj in context["selected_objects"]:
        assert set(obj) == {"name", "type", "matrix_world"}
        expected = facts["selected"][obj["name"]]
        assert obj["type"] == expected["type"]
        close(obj["matrix_world"], expected["matrix"][:3])
        if obj["name"] == "ContextEmpty" and state["case"] != "zero_scale":
            # Known pivot / rotated local axes / scale, independent of serialization.
            close(obj["matrix_world"], [[0, -3, 0, -4], [2, 0, 0, 6], [0, 0, 4, 2]])
    close(context["3d_cursor"]["position"], [7, -3, 2.5])
    quaternion = context["3d_cursor"]["orientation_wxyz"]
    if quaternion[0] < 0:
        quaternion = [-v for v in quaternion]
    close(quaternion, [math.sqrt(0.5), math.sqrt(0.5), 0, 0])
    wire = json.dumps(context)
    assert not any(f'"{key}"' in wire for key in ("vertices", "edges", "faces", "normals", "UV", "attributes", "point_clouds"))


async def main():
    evidence = {"cases": {}, "codex": False, "create_cube": []}
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "MCP/server.py"), "--session-file", str(RUNTIME / "selected-session.json")])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert set(tools) == {"create_cube", "get_selected_context"}
            assert tools["get_selected_context"].annotations.readOnlyHint
            results = {}
            for name in ("none", "none_active", "single", "multiple", "zero_scale", "edit_mode", "many", "heavy"):
                state = await select_case(name)
                assert state["background"] is False, "This check requires an open GUI Blender"
                result = (await session.call_tool("get_selected_context", {})).structuredContent
                verify(result, state)
                repeated = (await session.call_tool("get_selected_context", {})).structuredContent
                assert repeated == result
                results[name] = result
                evidence["cases"][name] = {"selected_count": len(result["context"]["selected_objects"]), "response_bytes": len(json.dumps(result).encode()), "facts": state["facts"]}
            assert results["single"] == results["heavy"], "Mesh density changed context payload"
            assert evidence["cases"]["heavy"]["facts"]["selected"]["ContextMesh"]["vertices"] == 1_000_000
            assert evidence["cases"]["many"]["response_bytes"] > 4096

            state = await select_case("multiple")
            process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / "Tests/codex_runtime.py"), "--selected-context", cwd=ROOT)
            assert await asyncio.wait_for(process.wait(), 60) == 0
            codex = json.loads((RUNTIME / "codex-selected-result.json").read_text(encoding="utf-8"))
            assert len(codex["calls"]) == 2
            for call in codex["calls"]:
                verify(call["structuredContent"], state)
            evidence["codex"] = True

            count = state["facts"]["object_count"]
            for _ in range(2):
                result = (await session.call_tool("create_cube", {})).structuredContent
                assert result["success"], "Uncertain create_cube result; do not retry automatically"
                evidence["create_cube"].append(result["object_name"])
                context = (await session.call_tool("get_selected_context", {})).structuredContent["context"]
                assert context["active_object"]["name"] == result["object_name"]
                close(context["selected_objects"][0]["matrix_world"], [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]])
            final_state = json.loads((RUNTIME / "selected-state.json").read_text(encoding="utf-8"))
            assert final_state["facts"]["object_count"] == count + 2
            assert final_state["read_only_calls_verified"] >= 20
            evidence["final_state"] = final_state
    (RUNTIME / "selected-result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    await select_case("finish")
    print("PASS: GUI Blender; 8 selection cases + repeats; 1M vertices produce identical payload; installed Codex read twice; two create_cube calls; main-thread/read-only guards")


if __name__ == "__main__":
    asyncio.run(main())
