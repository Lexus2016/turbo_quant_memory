from __future__ import annotations

from typing import Any

import pytest

from turbo_memory_mcp.server import (
    _repair_global_retrieval_if_needed,
    _repair_project_retrieval_if_needed,
)
from turbo_memory_mcp.store import GLOBAL_SCOPE, PROJECT_SCOPE


class _FakeIndex:
    """Records how a repair call touched the index.

    Exposes both the OLD api (count_rows/sync_project/sync_global) and the
    NEW diff api (existing_item_ids/delete_items/sync_*_notes/sync_project_blocks)
    so a repair written against either contract executes without AttributeError
    and the assertions — not a crash — decide pass/fail.
    """

    def __init__(self, *, project: set[str] | None = None, global_: set[str] | None = None) -> None:
        self._existing = {PROJECT_SCOPE: set(project or set()), GLOBAL_SCOPE: set(global_ or set())}
        self.full_resyncs = 0
        self.deleted: list[str] = []
        self.note_upserts: list[str] = []
        self.block_upserts: list[str] = []

    # --- old contract ---
    def count_rows(self, scope: str) -> int:
        return len(self._existing[scope])

    def sync_project(self) -> None:
        self.full_resyncs += 1

    def sync_global(self) -> None:
        self.full_resyncs += 1

    # --- new diff contract ---
    def existing_item_ids(self, scope: str) -> set[str]:
        return set(self._existing[scope])

    def delete_items(self, scope: str, item_ids: Any) -> None:
        self.deleted.extend(str(i) for i in item_ids)

    def sync_project_notes(self, note_ids: Any) -> None:
        self.note_upserts.extend(str(i) for i in note_ids)

    def sync_global_notes(self, note_ids: Any) -> None:
        self.note_upserts.extend(str(i) for i in note_ids)

    def sync_project_blocks(self, block_ids: Any) -> None:
        self.block_upserts.extend(str(i) for i in block_ids)


class _FakeStore:
    def __init__(self, *, note_ids: list[str], block_ids: list[str], scope: str = PROJECT_SCOPE) -> None:
        self._note_ids = note_ids
        self._block_ids = block_ids
        self._scope = scope

    def list_notes(self, scope: str, *_a: Any, **_k: Any) -> list[dict[str, Any]]:
        if scope != self._scope:
            return []
        return [{"note_id": nid, "note_status": "active"} for nid in self._note_ids]

    def list_markdown_blocks(self, *_a: Any, **_k: Any) -> list[dict[str, Any]]:
        return [{"block_id": bid} for bid in self._block_ids]


def test_repair_project_reconciles_missing_id_without_full_resync(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # expected {n1,n2,n3,b1,b2}; index is missing n3 -> drift by one row.
    store = _FakeStore(note_ids=["n1", "n2", "n3"], block_ids=["b1", "b2"])
    index = _FakeIndex(project={"n1", "n2", "b1", "b2"})

    _repair_project_retrieval_if_needed(store, index)

    assert index.full_resyncs == 0, "repair must not re-embed the whole corpus for a 1-row drift"
    assert index.note_upserts == ["n3"]
    assert index.block_upserts == []
    assert index.deleted == []
    err = capsys.readouterr().err
    assert "project retrieval drift" in err
    assert "reconciling by id" in err


def test_repair_project_detects_stale_id_even_when_count_matches() -> None:
    # expected {n1,n2,n3,b1,b2} (5); index has a stale 'ghost' instead of n3 (also 5).
    # Count-only repair is blind here; a correct repair reconciles by id.
    store = _FakeStore(note_ids=["n1", "n2", "n3"], block_ids=["b1", "b2"])
    index = _FakeIndex(project={"n1", "n2", "b1", "b2", "ghost"})

    _repair_project_retrieval_if_needed(store, index)

    assert index.full_resyncs == 0
    assert index.deleted == ["ghost"]
    assert index.note_upserts == ["n3"]


def test_repair_project_is_a_noop_when_ids_match() -> None:
    store = _FakeStore(note_ids=["n1", "n2"], block_ids=["b1"])
    index = _FakeIndex(project={"n1", "n2", "b1"})

    _repair_project_retrieval_if_needed(store, index)

    assert index.full_resyncs == 0
    assert index.deleted == []
    assert index.note_upserts == []
    assert index.block_upserts == []


def test_repair_global_reconciles_by_id_without_full_resync() -> None:
    store = _FakeStore(note_ids=["g1", "g2", "g3"], block_ids=[], scope=GLOBAL_SCOPE)
    index = _FakeIndex(global_={"g1", "stale"})

    _repair_global_retrieval_if_needed(store, index)

    assert index.full_resyncs == 0
    assert index.deleted == ["stale"]
    assert sorted(index.note_upserts) == ["g2", "g3"]
