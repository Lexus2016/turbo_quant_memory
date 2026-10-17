"""Usage stats are one file shared by every project (2026-10-17).

With one dispatch lock per project, searches in different projects record
usage concurrently; the read-modify-write must not lose counts.
"""

from __future__ import annotations

import threading
from pathlib import Path

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.store import MemoryStore
from turbo_memory_mcp.telemetry import load_usage_stats, record_semantic_search_usage


def _store(tmp_path: Path, project_id: str) -> MemoryStore:
    identity = ProjectIdentity(
        project_id=project_id,
        project_name=project_id,
        project_root=tmp_path / project_id,
        identity_source=f"local/{project_id}",
        identity_kind="local_path",
        remote_url=None,
    )
    return MemoryStore(identity, storage_root=tmp_path / "central-store")


def test_concurrent_usage_records_are_not_lost(tmp_path: Path) -> None:
    stores = [_store(tmp_path, "alphaproject0001"), _store(tmp_path, "betaproject00001")]
    calls_per_thread = 25
    start = threading.Barrier(8)

    def _record(store: MemoryStore) -> None:
        start.wait(5)
        for _ in range(calls_per_thread):
            record_semantic_search_usage(
                store,
                project_id=store.project.project_id,
                project_name=store.project.project_name,
                response_payload={"items": []},
                raw_source_bytes=0,
            )

    threads = [threading.Thread(target=_record, args=(stores[index % 2],)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert load_usage_stats(stores[0])["totals"]["search_calls"] == 8 * calls_per_thread


def test_usage_snapshot_waits_for_a_running_record(tmp_path: Path) -> None:
    """load_usage_stats may rewrite the file (format migration), so the
    snapshot path must not run inside another call's read-modify-write."""
    from turbo_memory_mcp import telemetry

    store = _store(tmp_path, "alphaproject0001")
    done = threading.Event()

    def _snapshot() -> None:
        telemetry.build_usage_snapshot(store, project_id="alphaproject0001", project_name="alpha")
        done.set()

    with telemetry._USAGE_STATS_LOCK:
        thread = threading.Thread(target=_snapshot, daemon=True)
        thread.start()
        assert not done.wait(0.3)
    assert done.wait(5)
    thread.join(5)
