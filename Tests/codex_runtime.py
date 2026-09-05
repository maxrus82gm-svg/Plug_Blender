"""Use the installed Codex app-server MCP client, without an LLM turn.

An ephemeral protocol test context is discarded on exit. This does not create
a user-facing task or change saved Codex settings. Requires a running session
started by Tests/blender_runtime.py. Exactly two create_cube calls are made.
"""

import asyncio
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"


async def main():
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI is not available on PATH.")
    config = {
        "command": str(ROOT / ".venv/Scripts/python.exe"),
        "args": [str(ROOT / "MCP/server.py"), "--session-file", str(RUNTIME / "astro-session.json")],
        "cwd": str(ROOT), "required": True,
    }
    log_path = RUNTIME / "codex-app-server.log"
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(executable, "app-server", "--stdio", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log, cwd=ROOT)
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
            assert names == ["create_cube"], names
            evidence = {"client": "installed Codex app-server", "ephemeral": True, "model_turns": 0, "tools": names, "calls": []}
            for _ in range(2):
                result = await send("mcpServer/tool/call", {"threadId": context_id, "server": "astro_modeler", "tool": "create_cube", "arguments": {}})
                evidence["calls"].append(result)
                # Persist each response before considering the next non-idempotent call.
                (RUNTIME / "codex-result.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
                print(json.dumps(result, ensure_ascii=False), flush=True)
                structured = result.get("structuredContent") or result.get("result", {}).get("structuredContent")
                if structured is None:
                    contents = result.get("content", [])
                    structured = json.loads(next(item["text"] for item in contents if item.get("type") == "text"))
                if not structured.get("success"):
                    raise RuntimeError("Blender did not confirm success. No automatic retry.")
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()


if __name__ == "__main__":
    asyncio.run(main())
