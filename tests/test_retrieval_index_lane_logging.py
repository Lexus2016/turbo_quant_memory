from __future__ import annotations

from typing import Any

import pytest

from turbo_memory_mcp.retrieval_index import _safe_fts_search, _safe_vector_search


class _BoomTable:
    """A LanceDB-shaped table whose search always raises."""

    def search(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("lance boom")

    def create_fts_index(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("no fts index")


def test_vector_lane_logs_and_returns_empty(capsys: pytest.CaptureFixture[str]) -> None:
    rows = _safe_vector_search(_BoomTable(), [0.1, 0.2], 5, None)
    assert rows == []
    assert "vector search lane failed" in capsys.readouterr().err


def test_fts_lane_logs_and_returns_empty(capsys: pytest.CaptureFixture[str]) -> None:
    rows = _safe_fts_search(_BoomTable(), "some query", 5, None)
    assert rows == []
    assert "fts search lane failed" in capsys.readouterr().err
