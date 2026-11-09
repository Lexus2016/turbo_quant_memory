from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import lancedb
import pytest

from turbo_memory_mcp.index_maintenance import (
    IndexMaintenance,
    _allocated_bytes,
    _index_coverage,
    index_status,
    optimize_indexes,
)
from turbo_memory_mcp.locks import ScopeLocks


def test_maintenance_indexes_unindexed_rows_without_changing_live_rows(tmp_path: Path) -> None:
    path = tmp_path / "projects" / "test-project" / "retrieval"
    path.mkdir(parents=True)
    table = lancedb.connect(str(path)).create_table(
        "items", data=[{"item_id": "one", "content_search": "memory lookup", "vector": [1.0, 0.0]}]
    )
    table.create_fts_index("content_search", use_tantivy=False)
    table.add([{"item_id": "two", "content_search": "project recall", "vector": [0.0, 1.0]}])
    before = index_status(tmp_path)
    assert before["tables"] == 1
    assert before["unindexed_rows"] > 0
    assert table.count_rows() == 2

    result = optimize_indexes(tmp_path, ScopeLocks(), threading.Event())
    after = index_status(tmp_path)

    assert result["optimized"] == 1
    assert result["errors"] == 0
    assert after["indexed_rows"] == 2
    assert after["unindexed_rows"] == 0
    assert table.count_rows() == 2
    assert optimize_indexes(tmp_path, ScopeLocks(), threading.Event())["optimized"] == 0


def test_scheduler_runs_at_start_and_repeats(monkeypatch, tmp_path: Path) -> None:
    calls = threading.Event()
    count = 0

    def fake_optimize(_root, _locks, _stop):
        nonlocal count
        count += 1
        calls.set()
        return {"checked": 0, "optimized": 0, "skipped": 0, "busy": 0, "errors": 0}

    monkeypatch.setattr("turbo_memory_mcp.index_maintenance.optimize_indexes", fake_optimize)
    maintenance = IndexMaintenance(tmp_path, ScopeLocks(), interval_seconds=0.02)
    maintenance.start()
    assert calls.wait(1)
    calls.clear()
    assert calls.wait(1)
    maintenance.stop()
    assert count >= 2


def test_maintenance_skips_when_disk_space_is_low(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "projects" / "test-project" / "retrieval"
    path.mkdir(parents=True)
    table = lancedb.connect(str(path)).create_table(
        "items", data=[{"item_id": "one", "content_search": "memory lookup", "vector": [1.0, 0.0]}]
    )
    monkeypatch.setattr("turbo_memory_mcp.index_maintenance.shutil.disk_usage", lambda _path: SimpleNamespace(free=0))

    result = optimize_indexes(tmp_path, ScopeLocks(), threading.Event())

    assert result["low_space"] == 1
    assert result["optimized"] == 0
    assert table.count_rows() == 1


def test_maintenance_does_not_follow_project_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "retrieval"
    outside.mkdir(parents=True)
    lancedb.connect(str(outside)).create_table(
        "items", data=[{"item_id": "one", "content_search": "memory", "vector": [1.0, 0.0]}]
    )
    projects = tmp_path / "projects"
    projects.mkdir()
    try:
        (projects / "linked-project").symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")

    assert index_status(tmp_path)["tables"] == 0
    assert optimize_indexes(tmp_path, ScopeLocks(), threading.Event())["checked"] == 0


def test_existing_fts_is_not_recreated_when_stats_are_unavailable() -> None:
    class Table:
        def list_indices(self):
            return [SimpleNamespace(index_type="FTS", name="fts")]

        def index_stats(self, _name):
            return None

        def count_rows(self):
            return 5

    assert _index_coverage(Table()) == (0, 5, True)


def test_empty_table_with_expired_versions_is_optimized(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "projects" / "test-project" / "retrieval"
    path.mkdir(parents=True)
    table = lancedb.connect(str(path)).create_table(
        "items", data=[{"item_id": "one", "content_search": "memory", "vector": [1.0, 0.0]}]
    )
    table.delete("item_id = 'one'")
    assert table.count_rows() == 0
    monkeypatch.setattr("turbo_memory_mcp.index_maintenance._has_expired_version", lambda *_args: True)

    result = optimize_indexes(tmp_path, ScopeLocks(), threading.Event())

    assert result["optimized"] == 1
    assert result["errors"] == 0
    assert table.count_rows() == 0


def test_disk_accounting_falls_back_to_logical_size_without_st_blocks() -> None:
    class File:
        def is_file(self):
            return True

        def stat(self):
            return SimpleNamespace(st_size=123)

    class Directory:
        def rglob(self, _pattern):
            return [File()]

    assert _allocated_bytes(Directory()) == 123


def test_maintenance_removes_versions_older_than_retention(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "global" / "retrieval"
    path.mkdir(parents=True)
    table = lancedb.connect(str(path)).create_table(
        "items", data=[{"item_id": "one", "content_search": "memory", "vector": [1.0, 0.0]}]
    )
    table.add([{"item_id": "two", "content_search": "recall", "vector": [0.0, 1.0]}])
    previous_versions = {version["version"] for version in table.list_versions()}
    assert len(previous_versions) == 2
    monkeypatch.setattr("turbo_memory_mcp.index_maintenance.RETENTION", timedelta(seconds=0))
    time.sleep(0.02)

    result = optimize_indexes(tmp_path, ScopeLocks(), threading.Event())

    assert result["optimized"] == 1
    assert result["errors"] == 0
    assert previous_versions.isdisjoint(version["version"] for version in table.list_versions())
    assert table.count_rows() == 2
