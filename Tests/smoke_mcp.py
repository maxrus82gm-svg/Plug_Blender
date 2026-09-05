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
                assert {tool.name for tool in tools} == {"create_cube", "get_selected_context"}
                for tool in tools:
                    assert tool.inputSchema.get("properties", {}) == {}
                    assert tool.annotations.readOnlyHint == (tool.name == "get_selected_context")
                    result = await session.call_tool(tool.name, {})
                    assert result.structuredContent["success"] is False
                    assert "No connected Blender" in result.structuredContent["message"]
                    if tool.name == "get_selected_context":
                        assert result.structuredContent["context"] is None
                print("PASS: SDK STDIO initialize, two-tool discovery, read-only hint, empty input schemas, no-session errors")


if __name__ == "__main__":
    asyncio.run(main())
