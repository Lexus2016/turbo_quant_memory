"""Tests for the query-free `recent_context` session-bootstrap tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from turbo_memory_mcp.server import (
    promote_note_impl,
    recent_context_impl,
    remember_note_impl,
)


class _KeywordEmbedder:
    KEYWORDS = ("auth", "refresh", "login", "global", "project", "pattern", "handoff")

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
    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder",
        return_value=_KeywordEmbedder(),
    ):
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


def test_recent_context_includes_handoff_by_default(tmp_path: Path) -> None:
    """The whole point: a handoff (episodic) must surface here, unlike in search."""
    env = _test_env(tmp_path)
    remember_note_impl("A decision", "chose option X for reasons", kind="decision", environ=env)
    remember_note_impl("Session handoff", "paused mid auth refresh login", kind="handoff", environ=env)

    payload = recent_context_impl(environ=env)
    assert payload["status"] == "ok"
    assert payload["mode"] == "recent_context"
    titles = [it["title"] for it in payload["items"]]
    assert "Session handoff" in titles  # episodic, would be hidden by semantic_search
    assert "A decision" in titles


def test_recent_context_orders_newest_first(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    for i in range(3):
        remember_note_impl(f"Note {i}", f"body number {i}", kind="lesson", environ=env)

    payload = recent_context_impl(environ=env)
    updated = [str(it["updated_at"]) for it in payload["items"]]
    assert updated == sorted(updated, reverse=True)  # monotonic non-increasing


def test_recent_context_tier_filter_excludes_episodic(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    remember_note_impl("A lesson", "durable knowledge", kind="lesson", environ=env)
    remember_note_impl("Session handoff", "episodic bridge", kind="handoff", environ=env)

    payload = recent_context_impl(tier_filter=["durable"], environ=env)
    titles = [it["title"] for it in payload["items"]]
    assert "A lesson" in titles
    assert "Session handoff" not in titles
    assert payload["tier_filter"] == ["durable"]


def test_recent_context_respects_limit(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    for i in range(4):
        remember_note_impl(f"Note {i}", f"content {i}", kind="lesson", environ=env)

    payload = recent_context_impl(limit=2, environ=env)
    assert payload["result_count"] == 2


def test_recent_context_invalid_tier_rejected(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    remember_note_impl("A lesson", "body", kind="lesson", environ=env)
    with pytest.raises(ValueError):
        recent_context_impl(tier_filter=["bogus"], environ=env)


def test_recent_context_global_scope(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Global pattern", "reusable cross project pattern", kind="pattern", environ=env
    )
    promote_note_impl(stored["item"]["item_id"], environ=env)

    payload = recent_context_impl(scope="global", environ=env)
    titles = [it["title"] for it in payload["items"]]
    assert "Global pattern" in titles
    assert all(it["scope"] == "global" for it in payload["items"])


def test_recent_context_empty_store(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    payload = recent_context_impl(environ=env)
    assert payload["status"] == "ok"
    assert payload["result_count"] == 0
    assert payload["items"] == []
