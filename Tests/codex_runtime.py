"""Use the installed Codex app-server MCP client, without an LLM turn.

An ephemeral protocol test context is discarded on exit. This does not create
a user-facing task or change saved Codex settings. Requires a running session
started by Tests/blender_runtime.py. By default, two create_cube calls are made.
With --selected-context, read the selected-context fixture twice instead.
"""

import asyncio
import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"


async def main(selected_context=False, box=False, note=False):
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI is not available on PATH.")
    session_name = "selected-session.json" if selected_context else "astro-session.json"
    result_name = "codex-selected-result.json" if selected_context else "codex-result.json"
    tool_name = "get_selected_context" if selected_context else "create_cube"
    if box:
        session_name, result_name, tool_name = "box-session.json", "codex-box-result.json", "create_box_at_cursor"
    if note:
        session_name, result_name, tool_name = "box-session.json", "codex-note-result.json", "post_modeling_note"
    config = {
        "command": str(ROOT / ".venv/Scripts/python.exe"),
        "args": [str(ROOT / "MCP/server.py"), "--session-file", str(RUNTIME / session_name),
                 "--feedback-log", str(RUNTIME / "box-agent-feedback.jsonl")],
        "cwd": str(ROOT), "required": True,
    }
    log_path = RUNTIME / "codex-app-server.log"
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(executable, "app-server", "--stdio",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
            cwd=ROOT, limit=1024 * 1024)
        counter = 0

        async def send(method, params, notification=False):
            nonlocal counter
            counter += 1
            packet = {"jsonrpc": "2.0", "method": method, "params": params}
            if not notification:
                packet["id"] = counter
            process.stdin.write((json.dumps(packet) + "\n").encode())
            await process.stdin.drain()
            if notification:
                return None
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=45)
                if not line:
                    raise RuntimeError("Codex app-server exited. Inspect the local runtime log.")
                response = json.loads(line)
                if response.get("id") == packet["id"]:
                    if "error" in response:
                        raise RuntimeError(response["error"])
                    return response["result"]

        try:
            await send("initialize", {"clientInfo": {"name": "astro_modeler_runtime_test", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
            await send("initialized", {}, notification=True)
            context = await send("thread/start", {"cwd": str(ROOT), "ephemeral": True, "config": {"mcp_servers": {"astro_modeler": config}}})
            context_id = context["thread"]["id"]
            status = await send("mcpServerStatus/list", {"threadId": context_id})
            entries = status.get("data", [])
            server = next(entry for entry in entries if entry["name"] == "astro_modeler")
            names = list(server["tools"])
            assert set(names) == {"create_cube", "get_selected_context", "create_box_at_cursor", "post_modeling_note", "inspect_selected_modifier_changes"}, names
            evidence = {"client": "installed Codex app-server", "ephemeral": True, "model_turns": 0, "tools": names, "calls": []}
            for _ in range(1 if box or note else 2):
                result = await send("mcpServer/tool/call", {"threadId": context_id, "server": "astro_modeler", "tool": tool_name, "arguments": {"status": "WARNING", "summary": "Проверка канала Codex", "details": "Не хватает локального измерения толщины."} if note else ({"size_x": 20, "size_y": 10, "size_z": 5} if box else {})})
                evidence["calls"].append(result)
                # Persist each response before considering the next non-idempotent call.
                (RUNTIME / result_name).write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
                if not selected_context:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                structured = result.get("structuredContent") or result.get("result", {}).get("structuredContent")
                if structured is None:
                    contents = result.get("content", [])
                    structured = json.loads(next(item["text"] for item in contents if item.get("type") == "text"))
                if not structured.get("success"):
                    raise RuntimeError("Blender did not confirm success. No automatic retry.")
                if selected_context:
                    print(json.dumps({"success": True, "active_object": structured["context"]["active_object"], "selected_names": [obj["name"] for obj in structured["context"]["selected_objects"]]}, ensure_ascii=False), flush=True)
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--selected-context", action="store_true", help="Read the selected-context fixture twice; do not create cubes")
    parser.add_argument("--box", action="store_true", help="Create one Box in the isolated box fixture")
    parser.add_argument("--note", action="store_true", help="Post one note in the same isolated box fixture")
    args = parser.parse_args()
    asyncio.run(main(args.selected_context, args.box, args.note))
