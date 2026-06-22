from __future__ import annotations

from typing import Any

import pytest

import turbo_memory_mcp.server as server_module
from turbo_memory_mcp.server import (
    _rebuild_scope_index_after_error,
    _rebuild_scope_index_for_format_change,
)
from turbo_memory_mcp.store import GLOBAL_SCOPE, PROJECT_SCOPE

# NOTE: drift-repair reconciliation (_repair_project/global_retrieval_if_needed)
# is covered by test_index_drift_repair.py. This file covers the two paths that
# legitimately still do a full re-embed: a format-version rebuild and the
# post-error rebuild fallback.


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


def test_format_change_rebuild_logs_and_syncs_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = {"project": 0, "global": 0}

    class _FakeRetrievalIndex:
        def __init__(self, _store: Any) -> None:
            pass

        def sync_project(self) -> None:
            calls["project"] += 1

        def sync_global(self) -> None:
            calls["global"] += 1

    monkeypatch.setattr(server_module, "RetrievalIndex", _FakeRetrievalIndex)

    _rebuild_scope_index_for_format_change(object(), PROJECT_SCOPE)
    assert calls == {"project": 1, "global": 0}
    err = capsys.readouterr().err
    assert "project retrieval index out of date" in err
    assert "full re-embed" in err

    _rebuild_scope_index_for_format_change(object(), GLOBAL_SCOPE)
    assert calls == {"project": 1, "global": 1}
    assert "global retrieval index out of date" in capsys.readouterr().err


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
