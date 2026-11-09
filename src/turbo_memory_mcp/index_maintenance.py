"""Bounded, owner-only LanceDB maintenance for derived retrieval indexes.

Індекси є відновлюваним кешем. Обслуговування не змінює початкові нотатки.
"""

from __future__ import annotations

import shutil
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .locks import GLOBAL_LOCK_KEY, ScopeLocks, project_lock_key

RETENTION = timedelta(days=7)
DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60
_LOCK_TIMEOUT_SECONDS = 0.01


def iter_index_paths(storage_root: Path) -> Iterator[tuple[str, str | None, Path]]:
    global_path = storage_root / "global" / "retrieval"
    if (
        not global_path.is_symlink()
        and (global_path / "items.lance").is_dir()
        and not (global_path / "items.lance").is_symlink()
    ):
        yield "global", None, global_path
    projects = storage_root / "projects"
    if projects.is_dir():
        for project in sorted(projects.iterdir()):
            path = project / "retrieval"
            if (
                project.is_dir()
                and not project.is_symlink()
                and path.is_dir()
                and not path.is_symlink()
                and (path / "items.lance").is_dir()
                and not (path / "items.lance").is_symlink()
            ):
                yield "project", project.name, path


def _open_table(path: Path) -> Any:
    import lancedb

    return lancedb.connect(str(path)).open_table("items")


def _index_coverage(table: Any) -> tuple[int, int, bool]:
    for index in table.list_indices():
        if str(index.index_type).upper() == "FTS":
            stats = table.index_stats(index.name)
            if stats is not None:
                return int(stats.num_indexed_rows), int(stats.num_unindexed_rows), True
            return 0, int(table.count_rows()), True
    return 0, int(table.count_rows()), False


def _has_expired_version(table: Any, now: datetime) -> bool:
    cutoff = now - RETENTION
    current = int(table.version)
    for version in table.list_versions():
        if int(version.get("version", -1)) == current:
            continue  # The live version is never garbage. / Поточна версія не є сміттям.
        stamp = version.get("timestamp")
        if isinstance(stamp, datetime):
            # LanceDB returns naive local time. / LanceDB повертає місцевий час без пояса.
            stamp = stamp.astimezone(UTC)
            if stamp < cutoff:
                return True
    return False


def _allocated_bytes(path: Path) -> int:
    """Estimate occupied bytes portably. / Оцінюйте зайняте місце переносно."""
    total = 0
    for file in path.rglob("*"):
        if file.is_file():
            stat = file.stat()
            blocks = getattr(stat, "st_blocks", None)
            total += blocks * 512 if blocks is not None else stat.st_size
    return total


def index_status(storage_root: Path) -> dict[str, int]:
    """Read-only summary; no table creation. / Стан без запису чи створення таблиць."""
    result = {
        "tables": 0,
        "rows": 0,
        "fts_tables": 0,
        "indexed_rows": 0,
        "unindexed_rows": 0,
        "expired_tables": 0,
        "allocated_bytes": 0,
        "errors": 0,
    }
    now = datetime.now(UTC)
    for _scope, _project_id, path in iter_index_paths(storage_root):
        try:
            table = _open_table(path)
            rows = int(table.count_rows())
            indexed, unindexed, has_fts = _index_coverage(table)
            result["tables"] += 1
            result["rows"] += rows
            result["fts_tables"] += int(has_fts)
            result["indexed_rows"] += indexed
            result["unindexed_rows"] += unindexed
            result["expired_tables"] += int(_has_expired_version(table, now))
            result["allocated_bytes"] += _allocated_bytes(path)
        except Exception as exc:
            result["errors"] += 1
            print(
                f"[tqmemory] index status failed / збій перевірки індексу {path}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    return result


def optimize_indexes(storage_root: Path, locks: ScopeLocks, stop: threading.Event) -> dict[str, int]:
    """Optimize eligible tables under MCP locks. / Обслуговуйте таблиці під блокуванням MCP."""
    result = {"checked": 0, "optimized": 0, "skipped": 0, "busy": 0, "low_space": 0, "errors": 0}
    now = datetime.now(UTC)
    for scope, project_id, path in iter_index_paths(storage_root):
        if stop.is_set():
            break
        key = GLOBAL_LOCK_KEY if scope == "global" else project_lock_key(str(project_id))
        try:
            with locks.hold([key], timeout=_LOCK_TIMEOUT_SECONDS, tool="index_maintenance"):
                table = _open_table(path)
                result["checked"] += 1
                rows = int(table.count_rows())
                _indexed, unindexed, has_fts = _index_coverage(table)
                expired = _has_expired_version(table, now)
                if not unindexed and not expired:
                    result["skipped"] += 1
                    continue
                allocated = _allocated_bytes(path)
                # Compaction may keep both generations. / Ущільнення може тимчасово зберігати обидва покоління.
                if shutil.disk_usage(path).free < 2 * allocated + 128 * 1024 * 1024:
                    result["low_space"] += 1
                    continue
                if rows and not has_fts:
                    from .retrieval_index import _fts_index_kwargs

                    table.create_fts_index("content_search", replace=False, **_fts_index_kwargs())
                table.optimize(cleanup_older_than=RETENTION, delete_unverified=False)
                result["optimized"] += 1
        except Exception as exc:
            from .locks import DispatchBusy

            if isinstance(exc, DispatchBusy):
                result["busy"] += 1
            else:
                result["errors"] += 1
                print(
                    f"[tqmemory] index maintenance failed / збій обслуговування {path}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
    return result


class IndexMaintenance:
    """Run now, then every six hours. / Запускайте одразу, далі кожні шість годин."""

    def __init__(self, storage_root: Path, locks: ScopeLocks, *, interval_seconds: float = DEFAULT_INTERVAL_SECONDS):
        self.storage_root = storage_root
        self.locks = locks
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="tq-index-maintenance", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()  # Keep ownership until done. / Зберігайте володіння до завершення.
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                summary = optimize_indexes(self.storage_root, self.locks, self._stop)
                print(f"[tqmemory] index maintenance / обслуговування індексів: {summary}", file=sys.stderr, flush=True)
            except Exception as exc:
                print(
                    f"[tqmemory] index maintenance cycle failed / збій циклу: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
            if self._stop.wait(self.interval_seconds):
                break


__all__ = ["IndexMaintenance", "index_status", "optimize_indexes"]
