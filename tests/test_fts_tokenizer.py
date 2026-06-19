from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from turbo_memory_mcp.retrieval_index import (
    DEFAULT_FTS_LANGUAGE,
    RetrievalIndex,
    _ensure_fts_index,
    _fts_index_kwargs,
    _rebuild_fts_index,
    _resolve_fts_language,
    _safe_fts_search,
)
from turbo_memory_mcp.server import build_runtime_context, remember_note_impl


# --------------------------------------------------------------------------- #
# _resolve_fts_language / _fts_index_kwargs (pure, no LanceDB needed)
# --------------------------------------------------------------------------- #


def test_resolve_language_defaults_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TQMEMORY_FTS_LANGUAGE", raising=False)
    assert _resolve_fts_language() == "English"
    assert DEFAULT_FTS_LANGUAGE == "English"


def test_resolve_language_accepts_supported_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TQMEMORY_FTS_LANGUAGE", "russian")
    assert _resolve_fts_language() == "Russian"


def test_resolve_language_invalid_falls_back_with_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TQMEMORY_FTS_LANGUAGE", "Klingon")
    assert _resolve_fts_language() == "English"
    assert "not a supported FTS stemmer language" in capsys.readouterr().err


def test_resolve_language_ukrainian_is_unsupported_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Guards the documented reality: LanceDB/Snowball has no Ukrainian stemmer,
    # so naively wiring it would silently disable FTS. We must fall back instead.
    monkeypatch.setenv("TQMEMORY_FTS_LANGUAGE", "Ukrainian")
    assert _resolve_fts_language() == "English"
    assert "not a supported FTS stemmer language" in capsys.readouterr().err


def test_fts_index_kwargs_default_is_explicit_and_english(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TQMEMORY_FTS_LANGUAGE", raising=False)
    kwargs = _fts_index_kwargs()
    # Explicitly pinned so a future LanceDB default change can't drift retrieval.
    assert kwargs == {
        "use_tantivy": False,
        "base_tokenizer": "simple",
        "language": "English",
        "lower_case": True,
        "stem": True,
        "remove_stop_words": True,
        "ascii_folding": True,
        "with_position": False,
    }


def test_fts_index_kwargs_honors_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TQMEMORY_FTS_LANGUAGE", "Russian")
    assert _fts_index_kwargs()["language"] == "Russian"


# --------------------------------------------------------------------------- #
# Integration: real LanceDB FTS index behavior
# --------------------------------------------------------------------------- #


def _make_table(tmp_path: Path, rows: list[dict[str, str]]):
    import lancedb

    db = lancedb.connect(str(tmp_path / "db"))
    return db.create_table("items", rows)


def test_default_fts_matches_cyrillic_exact_and_english_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TQMEMORY_FTS_LANGUAGE", raising=False)
    table = _make_table(
        tmp_path,
        [
            {"item_id": "ua", "content_search": "Привіт світ повнотекстовий пошук"},
            {"item_id": "en", "content_search": "running tests with tokens"},
        ],
    )
    _ensure_fts_index(table)

    def ids(query: str) -> list[str]:
        return [r["item_id"] for r in _safe_fts_search(table, query, 5, None)]

    # Cyrillic exact token matches out of the box (simple tokenizer).
    assert ids("повнотекстовий") == ["ua"]
    # Cyrillic is case-folded (lower_case) and survives ascii_folding intact.
    assert ids("ПОШУК") == ["ua"]
    # English stemming still works under the default (run -> running).
    assert ids("run") == ["en"]


def test_russian_stemming_matches_cyrillic_inflection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TQMEMORY_FTS_LANGUAGE", "Russian")
    table = _make_table(
        tmp_path,
        [{"item_id": "ru", "content_search": "работа с документами"}],
    )
    # rebuild (replace=True) is the path that applies a changed language to a
    # table whose index would otherwise be created once and never replaced.
    _rebuild_fts_index(table)

    hits = [r["item_id"] for r in _safe_fts_search(table, "документ", 5, None)]
    # Under the default English stemmer this inflected form does NOT match;
    # Russian stemming reduces "документами" -> "документ".
    assert hits == ["ru"]


# --------------------------------------------------------------------------- #
# RetrievalIndex.rebuild_fts (the documented apply-a-language-change path)
# --------------------------------------------------------------------------- #


class _KeywordEmbedder:
    """384-dim deterministic embedder so a real LanceDB table can be synced
    without loading the heavy multilingual model."""

    KEYWORDS = ("auth", "token", "rotation", "session", "cache", "deploy")

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vector = [0.0] * 384
            for index, keyword in enumerate(self.KEYWORDS):
                vector[index] = 1.0 if keyword in lowered else 0.0
            vectors.append(vector)
        return vectors


@pytest.fixture
def _fake_embedder() -> None:
    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder",
        return_value=_KeywordEmbedder(),
    ):
        yield


def _store_env(tmp_path: Path) -> dict[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    return {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(repo),
        "TQMEMORY_PROJECT_ID": "project-fts",
        "TQMEMORY_PROJECT_NAME": "FTS Test",
    }


def test_rebuild_fts_returns_false_when_table_absent(tmp_path: Path) -> None:
    env = _store_env(tmp_path)
    _, store = build_runtime_context(cwd=tmp_path / "repo", environ=env)
    index = RetrievalIndex(store)
    # No notes synced yet -> no global table on disk -> nothing to rebuild.
    assert index.rebuild_fts("global") is False


def test_rebuild_fts_rebuilds_and_keeps_fts_working(
    tmp_path: Path, _fake_embedder: None
) -> None:
    env = _store_env(tmp_path)
    repo = tmp_path / "repo"
    remember_note_impl(
        "Auth rotation", "auth token rotation", kind="lesson", cwd=repo, environ=env
    )
    _, store = build_runtime_context(cwd=repo, environ=env)
    index = RetrievalIndex(store)

    assert index.rebuild_fts("project") is True
    table = index._open_scope_table("project")
    hits = [r["item_id"] for r in _safe_fts_search(table, "rotation", 5, None)]
    assert len(hits) >= 1
