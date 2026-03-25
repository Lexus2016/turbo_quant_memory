"""Phase 1 stdio MCP server for Turbo Quant Memory."""

from __future__ import annotations

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover - compatibility for current stable SDK
    from mcp.server.fastmcp import FastMCP as MCPServer

from .contracts import (
    PHASE_1_TOOL_NAMES,
    SERVER_ID,
    build_health_payload,
    build_scope_payload,
    build_self_test_payload,
    build_server_info_payload,
)


def build_server() -> MCPServer:
    mcp = MCPServer(
        SERVER_ID,
        instructions=(
            "Use the Phase 1 introspection tools to inspect the local runtime "
            "contract for Turbo Quant Memory."
        ),
        json_response=True,
    )

    @mcp.tool()
    def health() -> dict[str, object]:
        return build_health_payload()

    @mcp.tool()
    def server_info() -> dict[str, object]:
        return build_server_info_payload()

    @mcp.tool()
    def list_scopes() -> dict[str, object]:
        return build_scope_payload()

    @mcp.tool()
    def self_test() -> dict[str, object]:
        return build_self_test_payload()

    return mcp


def run_stdio_server() -> None:
    build_server().run(transport="stdio")


__all__ = ["MCPServer", "PHASE_1_TOOL_NAMES", "PRODUCT_NAME", "SERVER_ID", "build_server", "run_stdio_server"]
