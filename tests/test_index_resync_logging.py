from __future__ import annotations

from typing import Any

import pytest

from turbo_memory_mcp.server import (
    _rebuild_scope_index_after_error,
    _repair_global_retrieval_if_needed,
    _repair_project_retrieval_if_needed,
)
from turbo_memory_mcp.store import GLOBAL_SCOPE, PROJECT_SCOPE


class _FakeIndex:
    def __init__(self, count: int) -> None:
        self._count = count
        self.project_synced = 0
        self.global_synced = 0

    def count_rows(self, _scope: str) -> int:
        return self._count

    def sync_project(self) -> None:
        self.project_synced += 1

    def sync_global(self) -> None:
        self.global_synced += 1


class _FakeStore:
    def __init__(self, blocks: int, project_notes: int, global_notes: int) -> None:
        self._blocks = blocks
        self._project_notes = project_notes
        self._global_notes = global_notes

    def list_markdown_blocks(self, *_a: Any, **_k: Any) -> list[Any]:
        return [None] * self._blocks

    def list_notes(self, scope: str, *_a: Any, **_k: Any) -> list[Any]:
        n = self._project_notes if scope == PROJECT_SCOPE else self._global_notes
        return [None] * n


def test_repair_project_logs_and_resyncs_on_drift(capsys: pytest.CaptureFixture[str]) -> None:
    store = _FakeStore(blocks=2, project_notes=3, global_notes=0)  # expected 5
    index = _FakeIndex(count=4)  # drift: have 4, expected 5
    _repair_project_retrieval_if_needed(store, index)
    assert index.project_synced == 1
    err = capsys.readouterr().err
    assert "project retrieval drift" in err
    assert "have 4" in err and "expected 5" in err


def test_repair_project_is_silent_when_counts_match(capsys: pytest.CaptureFixture[str]) -> None:
    store = _FakeStore(blocks=2, project_notes=3, global_notes=0)  # expected 5
    index = _FakeIndex(count=5)  # matches -> no work, no noise
    _repair_project_retrieval_if_needed(store, index)
    assert index.project_synced == 0
    assert capsys.readouterr().err == ""


def test_repair_global_logs_and_resyncs_on_drift(capsys: pytest.CaptureFixture[str]) -> None:
    store = _FakeStore(blocks=0, project_notes=0, global_notes=7)  # expected 7
    index = _FakeIndex(count=2)
    _repair_global_retrieval_if_needed(store, index)
    assert index.global_synced == 1
    assert "global retrieval drift" in capsys.readouterr().err


def test_rebuild_after_error_logs_and_selects_scope(capsys: pytest.CaptureFixture[str]) -> None:
    index = _FakeIndex(count=0)
    boom = RuntimeError("lance spill")

    _rebuild_scope_index_after_error(index, PROJECT_SCOPE, boom)
    assert index.project_synced == 1 and index.global_synced == 0
    err = capsys.readouterr().err
    assert "incremental project index update failed" in err
    assert "lance spill" in err

    _rebuild_scope_index_after_error(index, GLOBAL_SCOPE, boom)
    assert index.global_synced == 1
    assert "incremental global index update failed" in capsys.readouterr().err
