"""Phase 4 stdio MCP server for Turbo Quant Memory."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover - compatibility for current stable SDK
    from mcp.server.fastmcp import FastMCP as MCPServer

from .contracts import (
    DEFAULT_QUERY_MODE,
    PHASE_4_TOOL_NAMES,
    PRODUCT_NAME,
    QUERY_MODES,
    SERVER_ID,
    build_health_payload,
    build_note_item_payload,
    build_note_write_payload,
    build_scope_payload,
    build_self_test_payload,
    build_server_info_payload,
)
from .identity import ProjectIdentity, resolve_project_identity
from .ingestion import index_paths
from .retrieval import semantic_search, sync_global_retrieval, sync_project_retrieval
from .store import GLOBAL_SCOPE, MemoryStore, PROJECT_SCOPE, resolve_storage_root


def build_server() -> MCPServer:
    mcp = MCPServer(
        SERVER_ID,
        instructions=(
            "Use remember_note(..., scope=\"project\") to store project notes, "
            "promote reusable knowledge into global scope, retrieve compact "
            "project/global/hybrid memory, and index Markdown roots "
            "through index_paths(...)."
        ),
        json_response=True,
        log_level="ERROR",
    )

    @mcp.tool()
    def health() -> dict[str, object]:
        return build_health_payload()

    @mcp.tool()
    def server_info() -> dict[str, object]:
        return server_info_impl()

    @mcp.tool()
    def list_scopes() -> dict[str, object]:
        return build_scope_payload()

    @mcp.tool()
    def self_test() -> dict[str, object]:
        return self_test_impl()

    @mcp.tool()
    def remember_note(
        title: str,
        content: str,
        tags: list[str] | None = None,
        source_refs: list[str] | None = None,
        scope: str = "project",
    ) -> dict[str, object]:
        return remember_note_impl(title, content, tags=tags, source_refs=source_refs, scope=scope)

    @mcp.tool()
    def promote_note(note_id: str) -> dict[str, object]:
        return promote_note_impl(note_id)

    @mcp.tool()
    def semantic_search(
        query: str,
        scope: str = DEFAULT_QUERY_MODE,
        limit: int = 5,
    ) -> dict[str, object]:
        return semantic_search_impl(query, scope=scope, limit=limit)

    @mcp.tool()
    def index_paths(
        paths: list[str] | None = None,
        mode: str = "incremental",
    ) -> dict[str, object]:
        return index_paths_impl(paths=paths, mode=mode)

    return mcp


def run_stdio_server() -> None:
    build_server().run(transport="stdio")


def server_info_impl(
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    project, store = build_runtime_context(cwd=cwd, environ=environ)
    return build_server_info_payload(
        storage_root=str(store.storage_root),
        current_project=build_current_project_payload(project),
    )


def self_test_impl(
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    project, store = build_runtime_context(cwd=cwd, environ=environ)
    return build_self_test_payload(
        storage_root=str(store.storage_root),
        current_project=build_current_project_payload(project),
    )


def remember_note_impl(
    title: str,
    content: str,
    *,
    tags: list[str] | None = None,
    source_refs: list[str] | None = None,
    scope: str = "project",
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    resolved_scope = _normalize_scope(scope)
    if resolved_scope == GLOBAL_SCOPE:
        raise ValueError("Direct global writes are disabled; write to project scope and use promote_note.")
    if resolved_scope != PROJECT_SCOPE:
        raise ValueError(f"remember_note only supports scope='{PROJECT_SCOPE}' in this server.")
    if not title.strip():
        raise ValueError("remember_note requires a non-empty title.")
    if not content.strip():
        raise ValueError("remember_note requires non-empty content.")

    _, store = build_runtime_context(cwd=cwd, environ=environ)
    note = store.write_project_note(title, content, tags=tags, source_refs=source_refs)
    sync_project_retrieval(store)
    return build_note_write_payload(
        note,
        source_path=str(store.note_source_path(note)),
        action="stored",
        content_preview=build_content_preview(note["content"]),
    )


def promote_note_impl(
    note_id: str,
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    resolved_note_id = note_id.strip()
    if not resolved_note_id:
        raise ValueError("promote_note requires a non-empty note_id.")

    _, store = build_runtime_context(cwd=cwd, environ=environ)
    note = store.promote_note(resolved_note_id)
    sync_global_retrieval(store)
    return build_note_write_payload(
        note,
        source_path=str(store.note_source_path(note)),
        action="promoted",
        content_preview=build_content_preview(note["content"]),
    )


def semantic_search_impl(
    query: str,
    *,
    scope: str = DEFAULT_QUERY_MODE,
    limit: int = 5,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    return semantic_search(store, query, scope=scope, limit=limit)


def index_paths_impl(
    paths: list[str] | None = None,
    *,
    mode: str = "incremental",
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    runtime_cwd = Path(cwd).expanduser().resolve() if cwd is not None else store.project.project_root
    payload = index_paths(store, paths=paths, mode=mode, cwd=runtime_cwd)
    sync_project_retrieval(store)
    return payload


def build_runtime_context(
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[ProjectIdentity, MemoryStore]:
    project = resolve_project_identity(cwd=cwd, environ=environ)
    store = MemoryStore(project, storage_root=resolve_storage_root(environ))
    return project, store


def build_current_project_payload(project: ProjectIdentity) -> dict[str, object]:
    return {
        "project_id": project.project_id,
        "project_name": project.project_name,
        "project_root": str(project.project_root),
        "identity_kind": project.identity_kind,
    }


def build_content_preview(content: str, limit: int = 160) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _normalize_scope(scope: str) -> str:
    return scope.strip().lower()


__all__ = [
    "MCPServer",
    "PHASE_4_TOOL_NAMES",
    "PRODUCT_NAME",
    "SERVER_ID",
    "build_server",
    "build_runtime_context",
    "build_current_project_payload",
    "build_content_preview",
    "index_paths_impl",
    "promote_note_impl",
    "remember_note_impl",
    "run_stdio_server",
    "semantic_search_impl",
    "self_test_impl",
    "server_info_impl",
]
