from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from turbo_memory_mcp.server import (
    build_runtime_context,
    deprecate_note_impl,
    index_paths_impl,
    remember_note_impl,
    promote_note_impl,
    semantic_search_impl,
)
from turbo_memory_mcp.store import MARKDOWN_FORMAT_VERSION, sha256_text


class KeywordEmbedder:
    KEYWORDS = (
        "auth",
        "refresh",
        "rotation",
        "session",
        "cache",
        "ambiguous",
        "token",
        "login",
        "project",
        "global",
        "install",
        "package",
        "runtime",
    )

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vector = [0.0] * 384
            for index, keyword in enumerate(self.KEYWORDS):
                vector[index] = 1.0 if keyword in lowered else 0.0
            vectors.append(vector)
        return vectors


@pytest.fixture(autouse=True)
def _fake_embedder() -> None:
    with patch("turbo_memory_mcp.retrieval_index.build_default_embedder", return_value=KeywordEmbedder()):
        yield


def _test_env(tmp_path: Path) -> dict[str, str]:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    return {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "project-alpha",
        "TQMEMORY_PROJECT_NAME": "Alpha Project",
    }


def _seed_markdown_block(tmp_path: Path, env: dict[str, str], *, block_id: str, text: str, source_path: str) -> None:
    _, store = build_runtime_context(cwd=tmp_path / "repo", environ=env)
    store.write_markdown_root(
        {
            "root_id": "docs-root",
            "path": str((tmp_path / "repo" / "docs").resolve()),
            "path_hash": "docs-root-hash",
        }
    )
    store.write_markdown_block(
        {
            "block_id": block_id,
            "root_id": "docs-root",
            "source_path": source_path,
            "heading_path": ["Architecture", "Auth"],
            "chunk_index": 0,
            "content_raw": text,
            "block_checksum": sha256_text(text),
            "source_checksum": f"checksum-{block_id}",
        }
    )


def test_semantic_search_project_scope_returns_markdown_first_balanced_card(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    _seed_markdown_block(
        tmp_path,
        env,
        block_id="block-auth-rotation",
        text="Auth refresh rotation keeps session cache stable for project login flows.",
        source_path="docs/auth.md",
    )
    remember_note_impl(
        "Auth Flow Note",
        "Project auth refresh rotation note for session cache.",
        kind="lesson",
        tags=["auth", "session"],
        environ=env,
    )

    payload = semantic_search_impl("auth refresh rotation session cache", scope="project", limit=5, environ=env)

    assert payload["status"] == "ok"
    assert payload["scope"] == "project"
    assert payload["result_count"] >= 2
    assert payload["items"][0]["source_kind"] == "markdown"
    assert payload["items"][0]["block_id"] == "block-auth-rotation"
    assert payload["items"][0]["compressed_summary"]
    assert 1 <= len(payload["items"][0]["key_points"]) <= 3
    assert "content_raw" not in payload["items"][0]
    assert "content_search" not in payload["items"][0]


def test_semantic_search_global_scope_preserves_promoted_from_provenance(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Global Pattern",
        "Reusable session cache pattern for global memory.",
        kind="pattern",
        tags=["session", "cache"],
        environ=env,
    )
    promote_note_impl(stored["item"]["item_id"], environ=env)

    payload = semantic_search_impl("session cache pattern", scope="global", limit=5, environ=env)

    assert payload["status"] == "ok"
    assert payload["scope"] == "global"
    assert payload["items"][0]["source_kind"] == "memory_note"
    assert payload["items"][0]["note_kind"] == "pattern"
    assert payload["items"][0]["promoted_from"]["scope"] == "project"
    assert payload["items"][0]["title"] == "Global Pattern"


def test_semantic_search_hybrid_prefers_project_scope_when_relevance_is_close(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Project Login Cache",
        "Project login auth refresh cache.",
        kind="lesson",
        tags=["auth", "login"],
        environ=env,
    )
    promote_note_impl(stored["item"]["item_id"], environ=env)

    payload = semantic_search_impl("auth login refresh cache", scope="hybrid", limit=5, environ=env)

    assert payload["status"] == "ok"
    assert payload["scope"] == "hybrid"
    assert payload["result_count"] >= 2
    assert [item["scope"] for item in payload["items"][:2]] == ["project", "global"]


def test_semantic_search_marks_ambiguous_token_results_with_warning(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    _seed_markdown_block(
        tmp_path,
        env,
        block_id="ambiguous-token-alpha",
        text="Ambiguous token handling for auth refresh token alpha.",
        source_path="docs/alpha.md",
    )
    _seed_markdown_block(
        tmp_path,
        env,
        block_id="ambiguous-token-beta",
        text="Ambiguous token handling for auth refresh token beta.",
        source_path="docs/beta.md",
    )

    payload = semantic_search_impl("ambiguous token", scope="project", limit=5, environ=env)

    assert payload["status"] == "ok"
    assert payload["confidence_state"] == "ambiguous"
    assert "hydrate before acting" in payload["warning"]
    assert payload["items"][0]["confidence_state"] == "ambiguous"


def test_semantic_search_ignores_archived_notes(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    archived = remember_note_impl(
        "Old Runtime",
        "Use uv run turbo-memory-mcp serve.",
        kind="lesson",
        tags=["runtime"],
        environ=env,
    )
    remember_note_impl(
        "Current Runtime",
        "Use turbo-memory-mcp serve after installation.",
        kind="lesson",
        tags=["runtime"],
        environ=env,
    )
    deprecate_note_impl(
        archived["item"]["item_id"],
        scope="project",
        reason="Deprecated dev-only runtime contract.",
        environ=env,
    )

    payload = semantic_search_impl("runtime install serve", scope="project", limit=5, environ=env)

    assert payload["result_count"] == 1
    assert payload["items"][0]["title"] == "Current Runtime"
    assert payload["items"][0]["note_status"] == "active"


def test_semantic_search_refreshes_stale_markdown_before_query(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    project_root = Path(env["TQMEMORY_PROJECT_ROOT"])
    docs = project_root / "docs"
    docs.mkdir()
    auth_doc = docs / "auth.md"
    auth_doc.write_text("# Auth\n\nAlpha refresh memory.", encoding="utf-8")

    index_paths_impl(paths=[str(docs)], mode="full", cwd=project_root, environ=env)
    auth_doc.write_text("# Auth\n\nBeta refresh memory.", encoding="utf-8")

    payload = semantic_search_impl("beta refresh memory", scope="project", limit=5, cwd=project_root, environ=env)

    assert payload["result_count"] >= 1
    assert "Beta refresh memory" in payload["items"][0]["compressed_summary"]


def test_semantic_search_lexical_bonus_supports_unicode_queries(tmp_path: Path) -> None:
    env = _test_env(tmp_path)

    class FlatEmbedder:
        def encode(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 384 for _ in texts]

    with patch("turbo_memory_mcp.retrieval_index.build_default_embedder", return_value=FlatEmbedder()):
        remember_note_impl(
            "Оновлення Сесії",
            "Критична практика для оновлення сесії.",
            kind="pattern",
            tags=["сесія"],
            environ=env,
        )
        remember_note_impl(
            "Deployment Note",
            "Infrastructure fallback only.",
            kind="lesson",
            tags=["deploy"],
            environ=env,
        )

        payload = semantic_search_impl("оновлення сесії", scope="project", limit=5, environ=env)

    assert payload["items"][0]["title"] == "Оновлення Сесії"


def test_semantic_search_emits_usage_milestone_for_large_savings(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    large_content = "auth refresh " * 500
    remember_note_impl(
        "Large Auth Note",
        large_content,
        kind="pattern",
        tags=["auth", "refresh"],
        environ=env,
    )

    payload = {}
    for _ in range(10):
        payload = semantic_search_impl("auth refresh", scope="project", limit=5, environ=env)
    stats_path = Path(env["TQMEMORY_HOME"]) / "telemetry" / "usage.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8"))

    assert payload["impact_milestone"]["kind"] == "retrievals"
    assert stats["totals"]["search_calls"] == 10


def test_semantic_search_full_reindexes_when_markdown_manifest_format_is_stale(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    project_root = Path(env["TQMEMORY_PROJECT_ROOT"])
    docs = project_root / "docs"
    docs.mkdir()
    auth_doc = docs / "auth.md"
    auth_doc.write_text("# Auth\n\nAlpha refresh memory.", encoding="utf-8")

    index_paths_impl(paths=[str(docs)], mode="full", cwd=project_root, environ=env)
    manifest_path = Path(env["TQMEMORY_HOME"]) / "projects" / "project-alpha" / "markdown" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format_version"] = 0
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload = semantic_search_impl("alpha refresh memory", scope="project", limit=5, environ=env)
    repaired_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["result_count"] >= 1
    assert repaired_manifest["format_version"] == MARKDOWN_FORMAT_VERSION
