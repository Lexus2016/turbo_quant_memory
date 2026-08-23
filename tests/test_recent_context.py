"""Tests for the query-free `recent_context` session-bootstrap tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from turbo_memory_mcp.server import (
    build_runtime_context,
    deprecate_note_impl,
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


# --- hybrid project-first partitioning + promoted-pair dedupe (2026-08-23) ---

import json


def _age_note(env: dict[str, str], note: dict, *, updated_at: str) -> None:
    """Backdate a note's updated_at directly in its JSON on disk.

    The write path deliberately never accepts backdated timestamps; the
    codebase's documented test pattern is patching the JSON after write.
    Accepts both raw records (note_id) and tool payloads (item_id).
    """
    _, store = build_runtime_context(environ=env)
    record = {
        "note_id": note.get("note_id") or note.get("item_id"),
        "scope": note.get("scope", "project"),
        "project_id": note.get("project_id"),
    }
    path = store.note_source_path(record)
    data = json.loads(path.read_text())
    data["updated_at"] = updated_at
    path.write_text(json.dumps(data))


def _hybrid_env(tmp_path: Path) -> dict[str, str]:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    return {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "project-alpha",
        "TQMEMORY_PROJECT_NAME": "Alpha Project",
    }


def test_hybrid_hot_globals_cannot_evict_project_notes(tmp_path: Path) -> None:
    """Regression for the measured live failure: 8 fresher other-project
    promotions used to fill every slot and the current project's own handoff
    vanished. Hybrid must fill from project scope first."""
    env = _hybrid_env(tmp_path)
    stored = remember_note_impl(
        "Project handoff", "paused mid auth work", kind="handoff", environ=env
    )
    _age_note(env, stored["item"], updated_at="2026-08-01T00:00:00+00:00")
    # A second project note so the window is not a single item.
    second = remember_note_impl("Older lesson", "durable knowledge", kind="lesson", environ=env)
    _age_note(env, second["item"], updated_at="2026-07-01T00:00:00+00:00")
    # Fresher global notes from other projects: promote then retire the
    # local originals so they exist ONLY as global copies.
    for i in range(8):
        g = remember_note_impl(f"Other project note {i}", f"body {i}", kind="lesson", environ=env)
        promote_note_impl(g["item"]["item_id"], environ=env)
        deprecate_note_impl(g["item"]["item_id"], environ=env)
        _age_note(env, {"note_id": g["item"]["item_id"], "scope": "global"},
                  updated_at=f"2026-08-20T00:00:0{i}+00:00")

    payload = recent_context_impl(scope="hybrid", limit=10, environ=env)

    titles = [it["title"] for it in payload["items"]]
    assert "Project handoff" in titles
    scopes = [it["scope"] for it in payload["items"]]
    assert scopes[0] == "project" and scopes[1] == "project"
    assert all(s == "global" for s in scopes[2:])


def test_hybrid_backfills_from_global_when_project_sparse(tmp_path: Path) -> None:
    env = _hybrid_env(tmp_path)
    p1 = remember_note_impl("Only project note", "body", kind="lesson", environ=env)
    _age_note(env, p1["item"], updated_at="2026-07-01T00:00:00+00:00")
    # Global notes can only be created via promote; deprecate each original so
    # the project window stays sparse and the backfill path actually runs.
    for i in range(3):
        s = remember_note_impl(f"Global news {i}", f"body {i}", kind="lesson", environ=env)
        promote_note_impl(s["item"]["item_id"], environ=env)
        deprecate_note_impl(s["item"]["item_id"], environ=env)

    payload = recent_context_impl(scope="hybrid", limit=4, environ=env)

    items = payload["items"]
    assert len(items) == 4
    assert items[0]["scope"] == "project"
    assert [it["scope"] for it in items[1:]] == ["global"] * 3


def test_hybrid_promoted_pair_appears_once_as_project_original(tmp_path: Path) -> None:
    """A promotion shares the original's note_id; when the original occupies a
    slot the global copy must not consume another one."""
    env = _hybrid_env(tmp_path)
    stored = remember_note_impl(
        "Promotable decision", "chose X because Y", kind="decision", environ=env
    )
    promote_note_impl(stored["item"]["item_id"], environ=env)

    payload = recent_context_impl(scope="hybrid", limit=10, environ=env)

    surviving = [it for it in payload["items"] if it["title"] == "Promotable decision"]
    assert len(surviving) == 1
    assert surviving[0]["scope"] == "project"


def test_hybrid_foreign_promotion_survives_backfill(tmp_path: Path) -> None:
    """Promotions of OTHER projects are born-global content here — they must
    not be suppressed by the dedupe."""
    env = _hybrid_env(tmp_path)
    stored = remember_note_impl(
        "Local decision", "local choice", kind="decision", environ=env
    )
    promoted = promote_note_impl(stored["item"]["item_id"], environ=env)
    # Simulate a foreign project's promotion: rewrite the copy's provenance.
    _, store = build_runtime_context(environ=env)
    path = store.global_note_path(promoted["item"]["item_id"])
    data = json.loads(path.read_text())
    data["promoted_from"]["project_id"] = "some-other-project"
    data["promoted_from"]["note_id"] = "foreign-note-id"
    path.write_text(json.dumps(data))

    payload = recent_context_impl(scope="hybrid", limit=10, environ=env)

    titles = [it["title"] for it in payload["items"]]
    assert "Local decision" in titles


def test_single_scopes_unchanged_by_partitioning(tmp_path: Path) -> None:
    env = _hybrid_env(tmp_path)
    stored = remember_note_impl("P note", "body", kind="lesson", environ=env)
    _age_note(env, stored["item"], updated_at="2026-01-01T00:00:00+00:00")
    g1 = remember_note_impl("G note newer", "body", kind="lesson", environ=env)
    promote_note_impl(g1["item"]["item_id"], environ=env)
    deprecate_note_impl(g1["item"]["item_id"], environ=env)

    project_payload = recent_context_impl(scope="project", limit=10, environ=env)
    global_payload = recent_context_impl(scope="global", limit=10, environ=env)

    assert [it["title"] for it in project_payload["items"]] == ["P note"]
    assert any(it["title"] == "G note newer" for it in global_payload["items"])
