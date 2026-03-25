"""Stable payload contracts for the Phase 1 MCP tool surface."""

from __future__ import annotations

from . import __version__

PRODUCT_NAME = "Turbo Quant Memory for AI Agents"
PACKAGE_NAME = "turbo-memory-mcp"
SERVER_ID = "tqmemory"
RUNTIME_COMMAND = "turbo-memory-mcp serve"
TRANSPORT = "stdio"
SCOPE_NOTE = "Real storage arrives in later phases."
PHASE_1_TOOL_NAMES = ("health", "server_info", "list_scopes", "self_test")


def build_install_contract() -> dict[str, dict[str, str]]:
    return {
        "primary": {
            "tool": "uv",
            "command": f"uv run {RUNTIME_COMMAND}",
        },
        "fallback": {
            "tool": "pip",
            "command": "python -m turbo_memory_mcp serve",
        },
    }


def build_supported_client_tiers() -> dict[str, list[str]]:
    return {
        "tier_1": ["Claude Code", "Codex", "Cursor", "OpenCode"],
        "tier_2": ["Antigravity"],
    }


def build_contract_snapshot() -> dict[str, object]:
    return {
        "product_name": PRODUCT_NAME,
        "server_id": SERVER_ID,
        "package_name": PACKAGE_NAME,
        "version": __version__,
        "runtime_command": RUNTIME_COMMAND,
        "transport": TRANSPORT,
        "install": build_install_contract(),
        "client_tiers": build_supported_client_tiers(),
    }


def build_health_payload() -> dict[str, object]:
    payload = build_contract_snapshot()
    return {
        "status": "ok",
        "transport": payload["transport"],
        "server_id": payload["server_id"],
        "package_name": payload["package_name"],
        "version": payload["version"],
    }


def build_server_info_payload() -> dict[str, object]:
    return build_contract_snapshot()


def build_scope_payload() -> dict[str, object]:
    scopes = []
    for name in ("project", "global", "hybrid"):
        scopes.append(
            {
                "name": name,
                "status": "planned",
                "note": SCOPE_NOTE,
            }
        )
    return {
        "status": "ok",
        "server_id": SERVER_ID,
        "scopes": scopes,
    }


def build_self_test_payload() -> dict[str, object]:
    payload = build_contract_snapshot()
    return {
        "status": "ok",
        "tool_count": len(PHASE_1_TOOL_NAMES),
        "tool_names": list(PHASE_1_TOOL_NAMES),
        "server_id": payload["server_id"],
        "package_name": payload["package_name"],
        "runtime_command": payload["runtime_command"],
        "transport": payload["transport"],
        "install": payload["install"],
        "client_tiers": payload["client_tiers"],
    }
