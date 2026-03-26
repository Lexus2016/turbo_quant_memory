"""Stable payload contracts for the Phase 5 MCP tool surface."""

from __future__ import annotations

from typing import Any, Mapping

from . import __version__

PRODUCT_NAME = "Turbo Quant Memory for AI Agents"
PACKAGE_NAME = "turbo-memory-mcp"
SERVER_ID = "tqmemory"
RUNTIME_COMMAND = "turbo-memory-mcp serve"
REPOSITORY_URL = "https://github.com/Lexus2016/turbo_quant_memory"
TRANSPORT = "stdio"
DEFAULT_WRITE_SCOPE = "project"
DEFAULT_QUERY_MODE = "hybrid"
QUERY_MODES = ("project", "global", "hybrid")
INDEX_MODES = ("full", "incremental")
HYDRATE_MODES = ("default", "related")
PHASE_1_TOOL_NAMES = ("health", "server_info", "list_scopes", "self_test")
PHASE_2_TOOL_NAMES = PHASE_1_TOOL_NAMES + ("remember_note", "promote_note", "search_memory")
PHASE_3_TOOL_NAMES = PHASE_2_TOOL_NAMES + ("index_paths",)
PHASE_4_TOOL_NAMES = PHASE_1_TOOL_NAMES + ("remember_note", "promote_note", "semantic_search", "index_paths")
PHASE_5_TOOL_NAMES = PHASE_1_TOOL_NAMES + (
    "remember_note",
    "promote_note",
    "deprecate_note",
    "semantic_search",
    "hydrate",
    "index_paths",
)


def build_install_contract() -> dict[str, dict[str, str]]:
    release_ref = f"v{__version__}"
    git_install_ref = f"git+{REPOSITORY_URL}@{release_ref}"
    return {
        "primary": {
            "tool": "uv",
            "command": f"uv tool install {git_install_ref}",
        },
        "fallback": {
            "tool": "pip",
            "command": f"python -m pip install {git_install_ref}",
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
        "hydrate_modes": list(HYDRATE_MODES),
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
    storage_stats: Mapping[str, Any] | None = None,
    index_status: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    payload = build_contract_snapshot(storage_root=storage_root, current_project=current_project)
    if storage_stats is not None:
        payload["storage_stats"] = dict(storage_stats)
    if index_status is not None:
        payload["index_status"] = dict(index_status)
    return payload


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
        "note_kind": note["note_kind"],
        "note_status": note.get("note_status", "active"),
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
    if note.get("deprecated_at"):
        payload["deprecated_at"] = note["deprecated_at"]
    if note.get("deprecation_reason"):
        payload["deprecation_reason"] = note["deprecation_reason"]
    if note.get("superseded_by"):
        payload["superseded_by"] = dict(note["superseded_by"])
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
    confidence_state: str | None = None,
    warning: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "ok",
        "query": query,
        "scope": scope,
        "result_count": len(items),
        "items": items,
    }
    if confidence_state is not None:
        payload["confidence_state"] = confidence_state
    if warning is not None:
        payload["warning"] = warning
    return payload


def build_semantic_item_payload(item: Mapping[str, Any]) -> dict[str, object]:
    payload: dict[str, object] = {
        "scope": item["scope"],
        "project_id": item["project_id"],
        "project_name": item["project_name"],
        "source_kind": item["source_kind"],
        "item_id": item["item_id"],
        "source_path": item["source_path"],
        "title": item["title"],
        "heading_path": list(item.get("heading_path", [])),
        "updated_at": item["updated_at"],
        "score": round(float(item["score"]), 3),
        "confidence": round(float(item["confidence"]), 3),
        "confidence_state": item["confidence_state"],
        "compressed_summary": item["compressed_summary"],
        "key_points": list(item.get("key_points", [])),
        "can_hydrate": bool(item["can_hydrate"]),
    }
    if item.get("block_id"):
        payload["block_id"] = item["block_id"]
    if item.get("warning"):
        payload["warning"] = item["warning"]
    if item.get("note_kind"):
        payload["note_kind"] = item["note_kind"]
    if item.get("note_status"):
        payload["note_status"] = item["note_status"]
    if item.get("promoted_from"):
        payload["promoted_from"] = dict(item["promoted_from"])
    return payload


def build_hydrated_markdown_item_payload(
    block: Mapping[str, Any],
    *,
    project_name: str,
) -> dict[str, object]:
    heading_path = list(block.get("heading_path", []))
    title = heading_path[-1] if heading_path else str(block["source_path"])
    return {
        "scope": block["scope"],
        "project_id": block["project_id"],
        "project_name": project_name,
        "source_kind": block["source_kind"],
        "item_id": block["block_id"],
        "block_id": block["block_id"],
        "source_path": block["source_path"],
        "title": title,
        "heading_path": heading_path,
        "updated_at": block["updated_at"],
        "content": block["content_raw"],
    }


def build_hydrated_note_item_payload(
    note: Mapping[str, Any],
    *,
    source_path: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "scope": note["scope"],
        "project_id": note["project_id"],
        "project_name": note["project_name"],
        "source_kind": note["source_kind"],
        "item_id": note["note_id"],
        "title": note["title"],
        "note_kind": note["note_kind"],
        "note_status": note.get("note_status", "active"),
        "source_path": source_path,
        "updated_at": note["updated_at"],
        "content": note["content"],
        "tags": list(note.get("tags", [])),
        "source_refs": list(note.get("source_refs", [])),
    }
    if note.get("promoted_from"):
        payload["promoted_from"] = dict(note["promoted_from"])
    if note.get("deprecated_at"):
        payload["deprecated_at"] = note["deprecated_at"]
    if note.get("deprecation_reason"):
        payload["deprecation_reason"] = note["deprecation_reason"]
    if note.get("superseded_by"):
        payload["superseded_by"] = dict(note["superseded_by"])
    return payload


def build_hydration_payload(
    *,
    mode: str,
    item: Mapping[str, Any],
    neighbors_before: list[Mapping[str, Any]],
    neighbors_after: list[Mapping[str, Any]],
    neighbor_window: Mapping[str, int],
) -> dict[str, object]:
    return {
        "status": "ok",
        "mode": mode,
        "scope": item["scope"],
        "source_kind": item["source_kind"],
        "item": dict(item),
        "neighbors_before": [dict(neighbor) for neighbor in neighbors_before],
        "neighbors_after": [dict(neighbor) for neighbor in neighbors_after],
        "neighbor_window": {
            "before": int(neighbor_window["before"]),
            "after": int(neighbor_window["after"]),
        },
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
        "tool_count": len(PHASE_5_TOOL_NAMES),
        "tool_names": list(PHASE_5_TOOL_NAMES),
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
            "hydrate_modes": payload["hydrate_modes"],
        },
    }


__all__ = [
    "DEFAULT_QUERY_MODE",
    "DEFAULT_WRITE_SCOPE",
    "HYDRATE_MODES",
    "INDEX_MODES",
    "PACKAGE_NAME",
    "PHASE_1_TOOL_NAMES",
    "PHASE_2_TOOL_NAMES",
    "PHASE_3_TOOL_NAMES",
    "PHASE_4_TOOL_NAMES",
    "PHASE_5_TOOL_NAMES",
    "PRODUCT_NAME",
    "QUERY_MODES",
    "RUNTIME_COMMAND",
    "SERVER_ID",
    "TRANSPORT",
    "build_contract_snapshot",
    "build_health_payload",
    "build_hydrated_markdown_item_payload",
    "build_hydrated_note_item_payload",
    "build_hydration_payload",
    "build_indexing_payload",
    "build_install_contract",
    "build_note_item_payload",
    "build_note_write_payload",
    "build_scope_payload",
    "build_search_payload",
    "build_semantic_item_payload",
    "build_self_test_payload",
    "build_server_info_payload",
    "build_supported_client_tiers",
]
