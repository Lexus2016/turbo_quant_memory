from __future__ import annotations

import asyncio
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from turbo_memory_mcp import __version__
from turbo_memory_mcp.contracts import build_install_contract
from turbo_memory_mcp.contracts import CURRENT_TOOL_NAMES
from turbo_memory_mcp.identity import resolve_project_identity
from turbo_memory_mcp.server import build_current_project_payload
from turbo_memory_mcp.store import resolve_storage_root

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOL_NAMES = [
    "health",
    "server_info",
    "list_scopes",
    "self_test",
    "remember_note",
    "promote_note",
    "deprecate_note",
    "semantic_search",
    "hydrate",
    "index_paths",
    "lint_knowledge_base",
]


def _result_payload(result: Any) -> dict[str, Any]:
    if getattr(result, "structuredContent", None):
        return dict(result.structuredContent)

    text_content = result.content[0].text
    return json.loads(text_content)


async def _collect_server_contract() -> dict[str, Any]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "turbo_memory_mcp", "serve"],
        cwd=PROJECT_ROOT,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()

            return {
                "tool_names": [tool.name for tool in tools.tools],
                "health": _result_payload(await session.call_tool("health")),
                "server_info": _result_payload(await session.call_tool("server_info")),
                "list_scopes": _result_payload(await session.call_tool("list_scopes")),
                "self_test": _result_payload(await session.call_tool("self_test")),
            }


@lru_cache(maxsize=1)
def collect_server_contract() -> dict[str, Any]:
    return asyncio.run(_collect_server_contract())


def test_tool_catalog_is_exact() -> None:
    contract = collect_server_contract()

    assert list(CURRENT_TOOL_NAMES) == EXPECTED_TOOL_NAMES
    assert contract["tool_names"] == EXPECTED_TOOL_NAMES


def test_health_payload_matches_runtime_contract() -> None:
    payload = collect_server_contract()["health"]

    assert payload["status"] == "ok"
    assert payload["transport"] == "stdio"
    assert payload["server_id"] == "tqmemory"


def test_server_info_payload_fields() -> None:
    payload = collect_server_contract()["server_info"]
    expected_project = build_current_project_payload(resolve_project_identity(cwd=PROJECT_ROOT))
    install_contract = build_install_contract()

    assert payload["product_name"] == "Turbo Quant Memory for AI Agents"
    assert payload["package_name"] == "turbo-memory-mcp"
    assert payload["version"] == __version__
    assert payload["runtime_command"] == "turbo-memory-mcp serve"
    assert payload["install"]["primary"]["tool"] == "uv"
    assert payload["install"]["primary"]["command"] == install_contract["primary"]["command"]
    assert payload["install"]["fallback"]["tool"] == "pip"
    assert payload["install"]["fallback"]["command"] == install_contract["fallback"]["command"]
    assert payload["client_tiers"]["tier_1"] == [
        "Claude Code",
        "Codex",
        "Cursor",
        "OpenCode",
    ]
    assert payload["client_tiers"]["tier_2"] == ["Antigravity"]
    assert payload["default_write_scope"] == "project"
    assert payload["default_query_mode"] == "project"
    assert payload["query_modes"] == ["project", "global", "hybrid"]
    assert payload["storage_root"] == str(resolve_storage_root())
    assert payload["current_project"] == expected_project
    assert payload["hydrate_modes"] == ["default", "related"]
    assert set(payload["storage_stats"]) == {"project", "global"}
    assert set(payload["index_status"]) == {"project", "global"}
    assert payload["index_status"]["project"]["freshness"] in {"empty", "not_indexed", "fresh", "stale"}
    assert payload["index_status"]["global"]["freshness"] in {"empty", "fresh", "stale"}


def test_live_scope_contract_exposes_active_namespace_modes() -> None:
    payload = collect_server_contract()["list_scopes"]
    scopes = payload["scopes"]

    assert [scope["name"] for scope in scopes] == ["project", "global", "hybrid"]
    assert [scope["status"] for scope in scopes] == ["active", "active", "active"]
    assert [scope["writes"] for scope in scopes] == ["default", "promotion_only", "read_only"]
    assert payload["default_write_scope"] == "project"
    assert payload["default_query_mode"] == "project"


def test_self_test_summarises_namespace_contract() -> None:
    payload = collect_server_contract()["self_test"]
    install_contract = build_install_contract()

    assert payload["status"] == "ok"
    assert payload["tool_count"] == 11
    assert payload["tool_names"] == EXPECTED_TOOL_NAMES
    assert payload["runtime_command"] == "turbo-memory-mcp serve"
    assert payload["package_name"] == "turbo-memory-mcp"
    assert payload["version"] == __version__
    assert payload["install"]["primary"]["command"] == install_contract["primary"]["command"]
    assert payload["install"]["fallback"]["command"] == install_contract["fallback"]["command"]
    assert payload["client_tiers"]["tier_1"] == [
        "Claude Code",
        "Codex",
        "Cursor",
        "OpenCode",
    ]
    assert payload["storage_root"] == str(resolve_storage_root())
    assert payload["namespace_contract"]["default_write_scope"] == "project"
    assert payload["namespace_contract"]["default_query_mode"] == "project"
    assert payload["namespace_contract"]["query_modes"] == ["project", "global", "hybrid"]
    assert payload["namespace_contract"]["index_modes"] == ["full", "incremental"]
    assert payload["namespace_contract"]["hydrate_modes"] == ["default", "related"]
