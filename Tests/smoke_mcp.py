"""Exercise real SDK STDIO initialization, discovery and a no-session tool call."""

import asyncio
from pathlib import Path
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    with tempfile.TemporaryDirectory() as directory:
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "MCP/server.py"), "--session-file", str(Path(directory) / "missing.json")])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert {tool.name for tool in tools} == {"create_cube", "get_selected_context", "create_box_at_cursor", "post_modeling_note", "inspect_selected_modifier_changes"}
                for tool in tools:
                    arguments = {"size_x": 20, "size_y": 10, "size_z": 5} if tool.name == "create_box_at_cursor" else {}
                    if tool.name == "post_modeling_note":
                        arguments = {"status": "OK", "summary": "Тест", "details": ""}
                    assert set(tool.inputSchema.get("properties", {})) == set(arguments)
                    assert tool.annotations.readOnlyHint == (tool.name in {
                        "get_selected_context", "inspect_selected_modifier_changes"})
                    result = await session.call_tool(tool.name, arguments)
                    assert result.structuredContent["success"] is False
                    assert "No connected Blender" in result.structuredContent["message"]
                    if tool.name == "get_selected_context":
                        assert result.structuredContent["context"] is None
                    if tool.name == "inspect_selected_modifier_changes":
                        assert result.structuredContent["inspection"] is None
                print("PASS: SDK STDIO initialize, five-tool discovery, read-only hints, input schemas, no-session errors")


if __name__ == "__main__":
    asyncio.run(main())
