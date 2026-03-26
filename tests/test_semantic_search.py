from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from turbo_memory_mcp.server import build_runtime_context, remember_note_impl, promote_note_impl, semantic_search_impl
from turbo_memory_mcp.store import sha256_text


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
