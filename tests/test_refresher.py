"""Bounded search-time refresh with a background worker (2026-10-17).

A search used to bring its project's index fully up to date before
answering, under the dispatch lock: a large change (a new agent worktree, a
first index) made that one search, and every other call of the project,
time out. A search now does a bounded amount of refresh work inline and
hands the rest to IndexRefresher, which runs batches under the project lock.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from turbo_memory_mcp import server
from turbo_memory_mcp.locks import ScopeLocks, project_lock_key
from turbo_memory_mcp.refresher import IndexRefresher, RefreshProgress


def _env(tmp_path: Path, **extra: str) -> tuple[Path, dict[str, str]]:
    project_root = tmp_path / "repo"
    (project_root / "docs").mkdir(parents=True)
    env = {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "project-alpha",
        "TQMEMORY_PROJECT_NAME": "Alpha Project",
        **extra,
    }
    return project_root, env


def _write_docs(project_root: Path, count: int, body: str = "Original body") -> list[Path]:
    paths = [project_root / "docs" / f"doc-{index:02d}.md" for index in range(count)]
    for path in paths:
        path.write_text(f"# {path.stem}\n\n{body} of {path.stem}.", encoding="utf-8")
    return paths


@pytest.fixture(autouse=True)
def _drain_default_refresher() -> Iterator[None]:
    yield
    assert server.DEFAULT_REFRESHER.wait_idle(30)


def test_large_change_search_reports_refresh_then_converges(tmp_path: Path) -> None:
    project_root, env = _env(tmp_path, TQMEMORY_INLINE_REFRESH_FILES="5")
    server.index_paths_impl(paths=[str(project_root / "docs")], mode="full", cwd=project_root, environ=env)
    _write_docs(project_root, 30)

    first = server.semantic_search_impl("body", cwd=project_root, environ=env)

    assert first["index_refresh"]["state"] == "refreshing"
    assert first["index_refresh"]["pending_files"] == 25
    assert server.DEFAULT_REFRESHER.wait_idle(30)
    _, store = server.build_runtime_context(cwd=project_root, environ=env)
    assert len(store.list_markdown_file_manifests()) == 30
    second = server.semantic_search_impl("body", cwd=project_root, environ=env)
    assert "index_refresh" not in second


def test_search_skips_inline_refresh_while_background_job_runs(tmp_path: Path) -> None:
    project_root, env = _env(tmp_path)
    _write_docs(project_root, 2)
    server.index_paths_impl(paths=[str(project_root / "docs")], mode="full", cwd=project_root, environ=env)
    status = {"state": "refreshing", "pending_files": 7, "pending_items": 0}

    with (
        patch.object(server.DEFAULT_REFRESHER, "is_active", return_value=True),
        patch.object(server.DEFAULT_REFRESHER, "status", return_value=status),
        patch.object(server, "index_paths_with_sync_plan", side_effect=AssertionError("inline refresh")),
    ):
        payload = server.semantic_search_impl("body", cwd=project_root, environ=env)

    assert payload["index_refresh"] == status


class _SpyLocks(ScopeLocks):
    def __init__(self) -> None:
        super().__init__()
        self.held: list[list[str]] = []

    @contextmanager
    def hold(self, keys: Sequence[str], *, timeout: float, tool: str) -> Iterator[None]:
        self.held.append(list(keys))
        with super().hold(keys, timeout=timeout, tool=tool):
            yield


def test_worker_takes_only_the_project_lock() -> None:
    locks = _SpyLocks()
    refresher = IndexRefresher(locks, lambda cwd, environ, files, items: RefreshProgress())

    refresher.schedule("alpha", cwd=None, environ=None)

    assert refresher.wait_idle(5)
    assert locks.held == [[project_lock_key("alpha")]]
    refresher.shutdown()


def test_background_refresh_picks_up_files_edited_mid_job(tmp_path: Path) -> None:
    project_root, env = _env(tmp_path)
    server.index_paths_impl(paths=[str(project_root / "docs")], mode="full", cwd=project_root, environ=env)
    paths = _write_docs(project_root, 6)
    batches = 0

    def _run(cwd: Any, environ: Any, max_files: int, max_items: int) -> RefreshProgress:
        nonlocal batches
        progress = server._run_refresh_batch(cwd, environ, max_files, max_items)
        batches += 1
        if batches == 1:
            paths[1].write_text("# doc-01\n\nRewritten while the job was running.", encoding="utf-8")
        return progress

    refresher = IndexRefresher(ScopeLocks(), _run, batch_files=2)
    refresher.schedule("project-alpha", cwd=str(project_root), environ=env)

    assert refresher.wait_idle(30)
    refresher.shutdown()
    _, store = server.build_runtime_context(cwd=project_root, environ=env)
    doc1 = [b["content_raw"] for b in store.list_markdown_blocks() if b["source_path"] == "doc-01.md"]
    assert doc1 and all("Rewritten while the job was running" in content for content in doc1)
    assert len(store.list_markdown_file_manifests()) == 6


def test_refresher_shutdown_returns_while_a_job_is_pending() -> None:
    entered = threading.Event()
    release = threading.Event()

    def _blocking(cwd: Any, environ: Any, files: int, items: int) -> RefreshProgress:
        entered.set()
        release.wait(10)
        return RefreshProgress()

    refresher = IndexRefresher(ScopeLocks(), _blocking)
    refresher.schedule("alpha", cwd=None, environ=None)
    assert entered.wait(5)
    try:
        start = time.monotonic()
        refresher.shutdown(timeout=1.0)
        assert time.monotonic() - start < 2.0
    finally:
        release.set()


def test_refresher_starts_no_thread_until_scheduled() -> None:
    refresher = IndexRefresher(ScopeLocks(), lambda cwd, environ, files, items: RefreshProgress())
    assert refresher._thread is None

    refresher.schedule("alpha", cwd=None, environ=None)

    assert refresher._thread is not None
    assert refresher._thread.name == IndexRefresher.THREAD_NAME
    assert refresher.wait_idle(5)
    refresher.shutdown()


def test_refresher_ends_job_after_batch_error() -> None:
    def _broken(cwd: Any, environ: Any, files: int, items: int) -> RefreshProgress:
        raise RuntimeError("batch failed")

    refresher = IndexRefresher(ScopeLocks(), _broken)
    refresher.schedule("alpha", cwd=None, environ=None)

    assert refresher.wait_idle(5)
    assert refresher.is_active("alpha") is False
    refresher.shutdown()


def test_refresher_stops_a_job_that_makes_no_progress() -> None:
    """Rows that can never be mirrored (corrupt blocks) must not keep the
    worker busy forever."""
    calls = 0

    def _stuck(cwd: Any, environ: Any, files: int, items: int) -> RefreshProgress:
        nonlocal calls
        calls += 1
        return RefreshProgress(remaining_items=5)

    refresher = IndexRefresher(ScopeLocks(), _stuck)
    refresher.schedule("alpha", cwd=None, environ=None)

    assert refresher.wait_idle(5)
    assert calls == 2
    refresher.shutdown()


def test_status_reports_the_scheduling_progress() -> None:
    release = threading.Event()

    def _blocking(cwd: Any, environ: Any, files: int, items: int) -> RefreshProgress:
        release.wait(10)
        return RefreshProgress()

    refresher = IndexRefresher(ScopeLocks(), _blocking)
    refresher.schedule("alpha", cwd=None, environ=None, progress=RefreshProgress(remaining_files=4, remaining_items=9))
    try:
        assert refresher.is_active("alpha")
        assert refresher.status("alpha") == {"state": "refreshing", "pending_files": 4, "pending_items": 9}
        assert refresher.status("beta") is None
    finally:
        release.set()
        assert refresher.wait_idle(5)
        refresher.shutdown()


# --- final review fixes (2026-10-17) ---


def _mirror_content(project_root: Path, env: dict[str, str], source_path: str) -> list[str]:
    _, store = server.build_runtime_context(cwd=project_root, environ=env)
    table = server.RetrievalIndex(store)._open_scope_table("project")
    if table is None:
        return []
    return [
        str(row["content_search"])
        for row in table.to_arrow().to_pylist()
        if row.get("source_path") == source_path
    ]


def test_inline_refresh_embeds_at_most_the_row_budget_of_one_huge_file(tmp_path: Path) -> None:
    """The row budget must cover the blocks of changed files, not only the
    repair: one file with hundreds of sections held the lock for minutes."""
    project_root, env = _env(tmp_path, TQMEMORY_INLINE_REFRESH_ITEMS="40")
    _write_docs(project_root, 1)
    server.index_paths_impl(paths=[str(project_root / "docs")], mode="full", cwd=project_root, environ=env)
    huge = project_root / "docs" / "huge.md"
    huge.write_text("".join(f"## Section {index}\n\nBody {index}.\n\n" for index in range(200)), encoding="utf-8")
    embedded: list[int] = []
    real_write = server.RetrievalIndex._write_chunk

    def spy(self: Any, scope: str, rows: list[dict[str, Any]], **kwargs: Any) -> Any:
        # Only what the search itself embeds: the background worker it starts
        # may already be running under this patch.
        if threading.current_thread() is threading.main_thread():
            embedded.append(len(rows))
        return real_write(self, scope, rows, **kwargs)

    with patch.object(server.RetrievalIndex, "_write_chunk", autospec=True, side_effect=spy):
        payload = server.semantic_search_impl("section", cwd=project_root, environ=env)

    assert sum(embedded) <= 40
    assert payload["index_refresh"]["pending_items"] >= 160
    assert server.DEFAULT_REFRESHER.wait_idle(60)
    assert len(_mirror_content(project_root, env, "huge.md")) >= 200


def test_interrupted_index_run_is_mirrored_by_a_later_refresh(tmp_path: Path) -> None:
    """Manifests are written per file; if the run dies before the mirror is
    updated, a later refresh must still mirror the new content."""
    project_root, env = _env(tmp_path)
    paths = _write_docs(project_root, 2)
    server.index_paths_impl(paths=[str(project_root / "docs")], mode="full", cwd=project_root, environ=env)
    paths[0].write_text("# doc-00\n\nEdited body that must reach the mirror.", encoding="utf-8")
    _, store = server.build_runtime_context(cwd=project_root, environ=env)

    # The process dies after ingestion wrote the manifests, before the mirror.
    with (
        patch.object(server, "_apply_project_index_sync_plan", side_effect=RuntimeError("killed")),
        pytest.raises(RuntimeError),
    ):
        server.refresh_project_batch(store, max_files=None, max_items=None)

    server.refresh_project_batch(store, max_files=None, max_items=None)

    mirrored = _mirror_content(project_root, env, "doc-00.md")
    assert mirrored and all("Edited body that must reach the mirror" in text for text in mirrored)


def test_primary_stops_the_refresher_before_releasing_the_daemon_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Another process may become primary the moment the lock is released;
    this process's background batch must be over by then."""
    order: list[str] = []

    class _Listener:
        def stop(self) -> None:
            order.append("listener.stop")

    monkeypatch.setattr(server.DEFAULT_REFRESHER, "shutdown", lambda timeout=2.0: order.append("refresher.shutdown"))
    monkeypatch.setattr(server, "release_daemon_lock", lambda endpoint: order.append("release_daemon_lock"))

    server._stop_primary(_Listener(), object(), None)  # type: ignore[arg-type]

    assert order == ["listener.stop", "refresher.shutdown", "release_daemon_lock"]


def test_promoted_proxy_stops_the_refresher_before_releasing_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    class _Listener:
        def stop(self) -> None:
            order.append("listener.stop")

    monkeypatch.setattr(server.DEFAULT_REFRESHER, "shutdown", lambda timeout=2.0: order.append("refresher.shutdown"))
    monkeypatch.setattr(server, "release_daemon_lock", lambda endpoint: order.append("release_daemon_lock"))
    runtime = server.ProxyRuntime(object())  # type: ignore[arg-type]
    runtime._listener = _Listener()  # type: ignore[assignment]
    runtime._endpoint = object()  # type: ignore[assignment]
    runtime._client = None

    runtime.shutdown()

    assert order == ["listener.stop", "refresher.shutdown", "release_daemon_lock"]


def test_refresher_restarts_after_a_shutdown_without_a_worker() -> None:
    ran = threading.Event()

    def _done(cwd: Any, environ: Any, files: int, items: int) -> RefreshProgress:
        ran.set()
        return RefreshProgress()

    refresher = IndexRefresher(ScopeLocks(), _done)
    refresher.shutdown()
    refresher.schedule("alpha", cwd=None, environ=None)

    assert ran.wait(5)
    assert refresher.wait_idle(5)
    refresher.shutdown()
