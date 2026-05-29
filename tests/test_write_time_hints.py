from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from turbo_memory_mcp.retrieval_index import RetrievalIndex
from turbo_memory_mcp.server import (
    build_runtime_context,
    index_paths_impl,
    remember_note_impl,
)


class KeywordEmbedder:
    """Deterministic stand-in for the sentence-transformer: a note's vector is
    the one-hot presence of these keywords, so notes sharing the same keyword
    set get identical vectors (cosine distance 0 -> similarity 1.0)."""

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
    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder",
        return_value=KeywordEmbedder(),
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


def _remember(env: dict[str, str], cwd: Path, title: str, content: str, kind: str) -> dict:
    return remember_note_impl(title, content, kind=kind, cwd=cwd, environ=env)


def test_first_note_has_no_similarity_hints(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    cwd = tmp_path / "repo"
    payload = _remember(env, cwd, "Alpha", "auth refresh rotation token handling", "lesson")
    assert "similar_notes" not in payload


def test_near_duplicate_same_kind_is_supersede_candidate(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    cwd = tmp_path / "repo"
    first = _remember(env, cwd, "Alpha", "auth refresh rotation token handling", "lesson")
    first_id = first["item"]["item_id"]

    second = _remember(env, cwd, "Beta", "token rotation on auth refresh flow", "lesson")
    assert "similar_notes" in second
    hints = second["similar_notes"]
    match = next(h for h in hints if h["item_id"] == first_id)
    assert match["suggestion"] == "supersede_candidate"
    assert match["score"] >= 0.88
    assert match["note_kind"] == "lesson"


def test_near_duplicate_different_kind_is_review(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    cwd = tmp_path / "repo"
    lesson = _remember(env, cwd, "Alpha", "auth refresh rotation token handling", "lesson")
    lesson_id = lesson["item"]["item_id"]

    decision = _remember(env, cwd, "Gamma", "auth refresh rotation token policy", "decision")
    assert "similar_notes" in decision
    match = next(h for h in decision["similar_notes"] if h["item_id"] == lesson_id)
    # Same content but a different kind: never auto-supersede, only flag for review.
    assert match["suggestion"] == "review_for_conflict"


def test_unrelated_note_has_no_hints(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    cwd = tmp_path / "repo"
    _remember(env, cwd, "Alpha", "auth refresh rotation token handling", "lesson")
    payload = _remember(env, cwd, "Delta", "cache session warming", "lesson")
    assert "similar_notes" not in payload


def test_markdown_blocks_never_appear_as_note_hints(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    cwd = tmp_path / "repo"
    docs = cwd / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "# Guide\n\nauth refresh rotation token rotation guide\n", encoding="utf-8"
    )
    index_paths_impl(paths=[str(docs)], mode="full", cwd=cwd, environ=env)

    # Positive control: the markdown block IS indexed and highly similar, so the
    # raw (unfiltered) vector search surfaces it. Without the source_kind guard the
    # block would leak into similar_notes.
    _, store = build_runtime_context(cwd=cwd, environ=env)
    raw = RetrievalIndex(store).find_similar(
        "auth refresh rotation token handling", "project"
    )
    assert any(row["source_kind"] != "memory_note" for row in raw)

    # The note hint path must only ever surface notes -> the block is excluded,
    # and since no other notes exist, similar_notes is absent entirely.
    payload = _remember(env, cwd, "Alpha", "auth refresh rotation token handling", "lesson")
    assert "similar_notes" not in payload
