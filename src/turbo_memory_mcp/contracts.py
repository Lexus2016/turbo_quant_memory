"""Stable payload contracts for the Phase 3 MCP tool surface."""

from __future__ import annotations

from typing import Any, Mapping

from . import __version__

PRODUCT_NAME = "Turbo Quant Memory for AI Agents"
PACKAGE_NAME = "turbo-memory-mcp"
SERVER_ID = "tqmemory"
RUNTIME_COMMAND = "turbo-memory-mcp serve"
TRANSPORT = "stdio"
DEFAULT_WRITE_SCOPE = "project"
DEFAULT_QUERY_MODE = "hybrid"
QUERY_MODES = ("project", "global", "hybrid")
INDEX_MODES = ("full", "incremental")
PHASE_1_TOOL_NAMES = ("health", "server_info", "list_scopes", "self_test")
PHASE_2_TOOL_NAMES = PHASE_1_TOOL_NAMES + ("remember_note", "promote_note", "search_memory")
PHASE_3_TOOL_NAMES = PHASE_2_TOOL_NAMES + ("index_paths",)


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


def build_contract_snapshot(
    *,
    storage_root: str | None = None,
    current_project: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "product_name": PRODUCT_NAME,
        "server_id": SERVER_ID,
        "package_name": PACKAGE_NAME,
        "version": __version__,
        "runtime_command": RUNTIME_COMMAND,
        "transport": TRANSPORT,
        "install": build_install_contract(),
        "client_tiers": build_supported_client_tiers(),
        "default_write_scope": DEFAULT_WRITE_SCOPE,
        "default_query_mode": DEFAULT_QUERY_MODE,
        "query_modes": list(QUERY_MODES),
        "index_modes": list(INDEX_MODES),
    }
    if storage_root is not None:
        payload["storage_root"] = storage_root
    if current_project is not None:
        payload["current_project"] = dict(current_project)
    return payload


def build_health_payload() -> dict[str, object]:
    payload = build_contract_snapshot()
    return {
        "status": "ok",
        "transport": payload["transport"],
        "server_id": payload["server_id"],
        "package_name": payload["package_name"],
        "version": payload["version"],
    }


def build_server_info_payload(
    *,
    storage_root: str,
    current_project: Mapping[str, Any],
) -> dict[str, object]:
    return build_contract_snapshot(storage_root=storage_root, current_project=current_project)


def build_scope_payload() -> dict[str, object]:
    scopes = [
        {
            "name": "project",
            "status": "active",
            "writes": "default",
            "note": "Repository-local notes with automatic current-project identity.",
        },
        {
            "name": "global",
            "status": "active",
            "writes": "promotion_only",
            "note": "Reusable notes promoted explicitly from project scope.",
        },
        {
            "name": "hybrid",
            "status": "active",
            "writes": "read_only",
            "note": "Merged project/global retrieval with a strong project bias.",
        },
    ]
    return {
        "status": "ok",
        "server_id": SERVER_ID,
        "default_write_scope": DEFAULT_WRITE_SCOPE,
        "default_query_mode": DEFAULT_QUERY_MODE,
        "query_modes": list(QUERY_MODES),
        "scopes": scopes,
    }


def build_note_item_payload(
    note: Mapping[str, Any],
    *,
    source_path: str,
    confidence: float,
    can_hydrate: bool,
    content_preview: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": note["title"],
        "content_preview": content_preview,
        "tags": list(note.get("tags", [])),
        "scope": note["scope"],
        "project_id": note["project_id"],
        "project_name": note["project_name"],
        "source_kind": note["source_kind"],
        "item_id": note["note_id"],
        "source_path": source_path,
        "updated_at": note["updated_at"],
        "confidence": round(float(confidence), 3),
        "can_hydrate": can_hydrate,
    }
    if note.get("promoted_from"):
        payload["promoted_from"] = dict(note["promoted_from"])
    return payload


def build_note_write_payload(
    note: Mapping[str, Any],
    *,
    source_path: str,
    action: str,
    content_preview: str,
) -> dict[str, object]:
    return {
        "status": "ok",
        "action": action,
        "item": build_note_item_payload(
            note,
            source_path=source_path,
            confidence=1.0,
            can_hydrate=True,
            content_preview=content_preview,
        ),
    }


def build_search_payload(
    *,
    query: str,
    scope: str,
    items: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "status": "ok",
        "query": query,
        "scope": scope,
        "result_count": len(items),
        "items": items,
    }


def build_indexing_payload(
    *,
    mode: str,
    registered_roots: list[Mapping[str, str]],
    indexed_files: int,
    changed_files: int,
    skipped_files: int,
    deleted_files: int,
    block_count: int,
) -> dict[str, object]:
    return {
        "status": "ok",
        "mode": mode,
        "registered_roots": [dict(root) for root in registered_roots],
        "indexed_files": indexed_files,
        "changed_files": changed_files,
        "skipped_files": skipped_files,
        "deleted_files": deleted_files,
        "block_count": block_count,
    }


def build_self_test_payload(
    *,
    storage_root: str,
    current_project: Mapping[str, Any],
) -> dict[str, object]:
    payload = build_contract_snapshot(storage_root=storage_root, current_project=current_project)
    return {
        "status": "ok",
        "tool_count": len(PHASE_3_TOOL_NAMES),
        "tool_names": list(PHASE_3_TOOL_NAMES),
        "server_id": payload["server_id"],
        "package_name": payload["package_name"],
        "runtime_command": payload["runtime_command"],
        "transport": payload["transport"],
        "install": payload["install"],
        "client_tiers": payload["client_tiers"],
        "storage_root": payload["storage_root"],
        "current_project": payload["current_project"],
        "namespace_contract": {
            "default_write_scope": payload["default_write_scope"],
            "default_query_mode": payload["default_query_mode"],
            "query_modes": payload["query_modes"],
            "index_modes": payload["index_modes"],
        },
    }


__all__ = [
    "DEFAULT_QUERY_MODE",
    "DEFAULT_WRITE_SCOPE",
    "INDEX_MODES",
    "PACKAGE_NAME",
    "PHASE_1_TOOL_NAMES",
    "PHASE_2_TOOL_NAMES",
    "PHASE_3_TOOL_NAMES",
    "PRODUCT_NAME",
    "QUERY_MODES",
    "RUNTIME_COMMAND",
    "SERVER_ID",
    "TRANSPORT",
    "build_contract_snapshot",
    "build_health_payload",
    "build_indexing_payload",
    "build_install_contract",
    "build_note_item_payload",
    "build_note_write_payload",
    "build_scope_payload",
    "build_search_payload",
    "build_self_test_payload",
    "build_server_info_payload",
    "build_supported_client_tiers",
]
