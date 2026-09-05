"""STDIO MCP server. stdout is reserved for the official SDK protocol."""

import argparse
from typing import TypedDict

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from blender_client import create_cube as request_cube


class CubeResult(TypedDict):
    success: bool
    object_name: str | None
    message: str


def make_server(session_file=None):
    server = FastMCP(
        "Astro Modeler",
        instructions="Create one cube in the explicitly connected Blender session. Only create_cube is available. Never automatically retry an uncertain result; ask the user to inspect the scene.",
    )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    def create_cube() -> CubeResult:
        """Create a 2-unit cube at the world origin in the connected Blender scene. Requires Object Mode."""
        return request_cube(session_file)

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Astro Modeler STDIO MCP server")
    parser.add_argument("--session-file", help="Optional explicit session descriptor for development/testing")
    args = parser.parse_args()
    make_server(args.session_file).run(transport="stdio")
