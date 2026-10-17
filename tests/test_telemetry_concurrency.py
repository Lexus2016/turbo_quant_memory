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
