from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from turbo_memory_mcp.identity import resolve_project_identity
from turbo_memory_mcp.server import build_current_project_payload

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOL_NAMES = [
    "health",
    "server_info",
    "list_scopes",
    "self_test",
    "remember_note",
    "promote_note",
    "search_memory",
]
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
    with TemporaryDirectory(prefix="tqmemory-smoke-") as temp_dir:
        storage_root = Path(temp_dir) / "memory-home"
        resolved_storage_root = storage_root.resolve()
        server_env = {
            **os.environ,
            "TQMEMORY_HOME": str(storage_root),
            "TQMEMORY_PROJECT_ROOT": str(PROJECT_ROOT),
        }
        expected_project = build_current_project_payload(
            resolve_project_identity(cwd=PROJECT_ROOT, environ=server_env)
        )
        params = StdioServerParameters(
            command="uv",
            args=["run", "turbo-memory-mcp", "serve"],
            cwd=PROJECT_ROOT,
            env=server_env,
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
                remembered = result_payload(
                    await session.call_tool(
                        "remember_note",
                        {
                            "title": "Smoke Note",
                            "content": "Phase 2 namespace smoke checks project and global memory.",
                            "tags": ["smoke", "phase2"],
                        },
                    )
                )
                promoted = result_payload(
                    await session.call_tool("promote_note", {"note_id": remembered["item"]["item_id"]})
                )
                hybrid_search = result_payload(
                    await session.call_tool(
                        "search_memory",
                        {
                            "query": "namespace smoke",
                            "scope": "hybrid",
                            "limit": 5,
                        },
                    )
                )

    expect(health["status"] == "ok", f"health.status mismatch: {health}")
    expect(health["transport"] == "stdio", f"health.transport mismatch: {health}")
    expect(health["server_id"] == "tqmemory", f"health.server_id mismatch: {health}")

    expect(
        server_info["runtime_command"] == "turbo-memory-mcp serve",
        f"server_info.runtime_command mismatch: {server_info}",
    )
    expect(
        server_info["storage_root"] == str(resolved_storage_root),
        f"server_info.storage_root mismatch: {server_info}",
    )
    expect(server_info["current_project"] == expected_project, f"server_info.current_project mismatch: {server_info}")
    expect(server_info["query_modes"] == EXPECTED_SCOPES, f"server_info.query_modes mismatch: {server_info}")

    scopes = [scope["name"] for scope in list_scopes["scopes"]]
    expect(scopes == EXPECTED_SCOPES, f"list_scopes mismatch: {list_scopes}")
    expect(list_scopes["default_write_scope"] == "project", f"list_scopes.default_write_scope mismatch: {list_scopes}")
    expect(list_scopes["default_query_mode"] == "hybrid", f"list_scopes.default_query_mode mismatch: {list_scopes}")

    expect(self_test["status"] == "ok", f"self_test.status mismatch: {self_test}")
    expect(self_test["tool_count"] == 7, f"self_test.tool_count mismatch: {self_test}")
    expect(self_test["tool_names"] == EXPECTED_TOOL_NAMES, f"self_test.tool_names mismatch: {self_test}")
    expect(
        self_test["namespace_contract"]["query_modes"] == EXPECTED_SCOPES,
        f"self_test.namespace_contract mismatch: {self_test}",
    )

    expect(remembered["item"]["scope"] == "project", f"remember_note scope mismatch: {remembered}")
    expect(remembered["item"]["project_id"] == expected_project["project_id"], f"remember_note project mismatch: {remembered}")
    expect(promoted["item"]["scope"] == "global", f"promote_note scope mismatch: {promoted}")
    expect(promoted["item"]["promoted_from"]["scope"] == "project", f"promote_note provenance mismatch: {promoted}")
    expect(hybrid_search["scope"] == "hybrid", f"search_memory scope mismatch: {hybrid_search}")
    expect(hybrid_search["result_count"] >= 2, f"search_memory result_count mismatch: {hybrid_search}")
    expect(
        [item["scope"] for item in hybrid_search["items"][:2]] == ["project", "global"],
        f"search_memory ordering mismatch: {hybrid_search}",
    )

    return [
        f"PASS tool catalog: {', '.join(tool_names)}",
        f"PASS server_info: {server_info['current_project']['project_id']} @ {server_info['storage_root']}",
        f"PASS remember_note: {remembered['item']['item_id']} in {remembered['item']['scope']}",
        f"PASS promote_note: {promoted['item']['item_id']} in {promoted['item']['scope']}",
        f"PASS search_memory: {hybrid_search['items'][0]['scope']} before {hybrid_search['items'][1]['scope']}",
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
