from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOL_NAMES = ["health", "server_info", "list_scopes", "self_test"]
EXPECTED_SCOPES = ["project", "global", "hybrid"]


def result_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if structured:
        return dict(structured)

    if not getattr(result, "content", None):
        raise AssertionError("Tool call returned no content.")

    text = getattr(result.content[0], "text", None)
    if text is None:
        raise AssertionError("Tool call returned no text payload.")

    return json.loads(text)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def run_smoke() -> list[str]:
    params = StdioServerParameters(
        command="uv",
        args=["run", "turbo-memory-mcp", "serve"],
        cwd=PROJECT_ROOT,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]
            expect(tool_names == EXPECTED_TOOL_NAMES, f"Unexpected tool catalog: {tool_names}")

            health = result_payload(await session.call_tool("health"))
            server_info = result_payload(await session.call_tool("server_info"))
            list_scopes = result_payload(await session.call_tool("list_scopes"))
            self_test = result_payload(await session.call_tool("self_test"))

    expect(health["status"] == "ok", f"health.status mismatch: {health}")
    expect(health["transport"] == "stdio", f"health.transport mismatch: {health}")
    expect(health["server_id"] == "tqmemory", f"health.server_id mismatch: {health}")

    expect(
        server_info["runtime_command"] == "turbo-memory-mcp serve",
        f"server_info.runtime_command mismatch: {server_info}",
    )
    expect(
        server_info["client_tiers"]["tier_1"] == ["Claude Code", "Codex", "Cursor", "OpenCode"],
        f"server_info.client_tiers.tier_1 mismatch: {server_info}",
    )
    expect(
        server_info["client_tiers"]["tier_2"] == ["Antigravity"],
        f"server_info.client_tiers.tier_2 mismatch: {server_info}",
    )

    scopes = [scope["name"] for scope in list_scopes["scopes"]]
    expect(scopes == EXPECTED_SCOPES, f"list_scopes mismatch: {list_scopes}")

    expect(self_test["status"] == "ok", f"self_test.status mismatch: {self_test}")
    expect(self_test["tool_count"] == 4, f"self_test.tool_count mismatch: {self_test}")
    expect(self_test["tool_names"] == EXPECTED_TOOL_NAMES, f"self_test.tool_names mismatch: {self_test}")

    return [
        f"PASS tool catalog: {', '.join(tool_names)}",
        f"PASS health: {health['status']} over {health['transport']}",
        f"PASS server_info: {server_info['runtime_command']}",
        f"PASS list_scopes: {', '.join(scopes)}",
        f"PASS self_test: {self_test['tool_count']} tools",
    ]


def main() -> int:
    try:
        messages = asyncio.run(run_smoke())
    except Exception as exc:  # pragma: no cover - script-level failure path
        print(f"FAIL smoke test: {exc}", file=sys.stderr)
        return 1

    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
