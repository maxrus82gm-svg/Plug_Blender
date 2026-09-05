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
                assert [tool.name for tool in tools] == ["create_cube"]
                assert tools[0].inputSchema.get("properties", {}) == {}
                result = await session.call_tool("create_cube", {})
                assert result.structuredContent["success"] is False
                assert "No connected Blender" in result.structuredContent["message"]
                print("PASS: SDK STDIO initialize, one-tool discovery, empty input schema, no-session error")


if __name__ == "__main__":
    asyncio.run(main())
