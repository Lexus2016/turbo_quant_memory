"""Phase 2 stdio MCP server for Turbo Quant Memory."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover - compatibility for current stable SDK
    from mcp.server.fastmcp import FastMCP as MCPServer

from .contracts import (
    DEFAULT_QUERY_MODE,
    DEFAULT_WRITE_SCOPE,
    PHASE_1_TOOL_NAMES,
    PHASE_2_TOOL_NAMES,
    PRODUCT_NAME,
    QUERY_MODES,
    SERVER_ID,
    build_health_payload,
    build_note_item_payload,
    build_note_write_payload,
    build_scope_payload,
    build_search_payload,
    build_self_test_payload,
    build_server_info_payload,
)
from .identity import ProjectIdentity, resolve_project_identity
from .store import GLOBAL_SCOPE, MemoryStore, PROJECT_SCOPE, resolve_storage_root

HYBRID_PROJECT_BIAS = 0.15
MAX_SEARCH_LIMIT = 20
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def build_server() -> MCPServer:
    mcp = MCPServer(
        SERVER_ID,
        instructions=(
            "Use remember_note(..., scope=\"project\") to store project notes, "
            "promote reusable knowledge into global scope, and search "
            "project/global/hybrid memory."
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
    def search_memory(
        query: str,
        scope: str = DEFAULT_QUERY_MODE,
        limit: int = 5,
    ) -> dict[str, object]:
        return search_memory_impl(query, scope=scope, limit=limit)

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
        raise ValueError(f"remember_note only supports scope='{PROJECT_SCOPE}' in Phase 2.")
    if not title.strip():
        raise ValueError("remember_note requires a non-empty title.")
    if not content.strip():
        raise ValueError("remember_note requires non-empty content.")

    _, store = build_runtime_context(cwd=cwd, environ=environ)
    note = store.write_project_note(title, content, tags=tags, source_refs=source_refs)
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
    return build_note_write_payload(
        note,
        source_path=str(store.note_source_path(note)),
        action="promoted",
        content_preview=build_content_preview(note["content"]),
    )


def search_memory_impl(
    query: str,
    *,
    scope: str = DEFAULT_QUERY_MODE,
    limit: int = 5,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    query_text = query.strip()
    if not query_text:
        raise ValueError("search_memory requires a non-empty query.")

    resolved_scope = _normalize_scope(scope)
    if resolved_scope not in QUERY_MODES:
        raise ValueError(f"Unsupported query scope: {scope}")

    _, store = build_runtime_context(cwd=cwd, environ=environ)
    normalized_limit = _normalize_limit(limit)
    ranked_notes = _rank_notes(store, query_text, resolved_scope)
    items = [
        build_note_item_payload(
            ranked["note"],
            source_path=str(store.note_source_path(ranked["note"])),
            confidence=ranked["confidence"],
            can_hydrate=True,
            content_preview=build_content_preview(ranked["note"]["content"]),
        )
        for ranked in ranked_notes[:normalized_limit]
    ]
    return build_search_payload(query=query_text, scope=resolved_scope, items=items)


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


def _rank_notes(store: MemoryStore, query: str, scope: str) -> list[dict[str, Any]]:
    query_terms = tuple(dict.fromkeys(_tokenize(query)))
    query_text = query.lower()

    ranked: list[dict[str, Any]] = []
    for note in _notes_for_scope(store, scope):
        confidence = _score_note(note, query_terms, query_text)
        if confidence <= 0:
            continue
        project_preference = 0 if note["scope"] == PROJECT_SCOPE else 1
        effective_score = confidence
        if scope == DEFAULT_QUERY_MODE and note["scope"] == PROJECT_SCOPE:
            effective_score += HYBRID_PROJECT_BIAS
        ranked.append(
            {
                "note": note,
                "confidence": min(confidence, 1.0),
                "effective_score": effective_score,
                "project_preference": project_preference,
                "updated_epoch": _updated_epoch(note["updated_at"]),
                "item_identity": str(note["note_id"]),
            }
        )

    ranked.sort(
        key=lambda item: (
            -item["effective_score"],
            item["project_preference"],
            -item["updated_epoch"],
            item["item_identity"],
        )
    )
    return ranked


def _notes_for_scope(store: MemoryStore, scope: str) -> list[dict[str, Any]]:
    if scope == PROJECT_SCOPE:
        return store.list_notes(PROJECT_SCOPE)
    if scope == GLOBAL_SCOPE:
        return store.list_notes(GLOBAL_SCOPE)
    return [*store.list_notes(PROJECT_SCOPE), *store.list_notes(GLOBAL_SCOPE)]


def _score_note(note: Mapping[str, Any], query_terms: tuple[str, ...], query_text: str) -> float:
    if not query_terms:
        return 0.0

    title = str(note.get("title", ""))
    content = str(note.get("content", ""))
    tags = " ".join(str(tag) for tag in note.get("tags", []))
    full_text = f"{title} {content} {tags}".lower()
    note_tokens = set(_tokenize(full_text))
    title_tokens = set(_tokenize(title.lower()))
    tag_tokens = set(_tokenize(tags.lower()))

    overlap = sum(1 for term in query_terms if term in note_tokens)
    title_overlap = sum(1 for term in query_terms if term in title_tokens)
    tag_overlap = sum(1 for term in query_terms if term in tag_tokens)
    phrase_bonus = 0.2 if query_text in full_text else 0.0

    if overlap == 0 and phrase_bonus == 0:
        return 0.0

    denominator = max(len(query_terms), 1)
    base_score = overlap / denominator
    title_bonus = 0.2 * (title_overlap / denominator)
    tag_bonus = 0.1 * (tag_overlap / denominator)
    return min(base_score + title_bonus + tag_bonus + phrase_bonus, 1.0)


def _updated_epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _tokenize(value: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(value)]


def _normalize_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_SEARCH_LIMIT))


def _normalize_scope(scope: str) -> str:
    return scope.strip().lower()


__all__ = [
    "HYBRID_PROJECT_BIAS",
    "MAX_SEARCH_LIMIT",
    "MCPServer",
    "PHASE_1_TOOL_NAMES",
    "PHASE_2_TOOL_NAMES",
    "PRODUCT_NAME",
    "SERVER_ID",
    "build_server",
    "build_runtime_context",
    "build_current_project_payload",
    "build_content_preview",
    "promote_note_impl",
    "remember_note_impl",
    "run_stdio_server",
    "search_memory_impl",
    "self_test_impl",
    "server_info_impl",
]
