"""Leased file watcher: git baseline, debounced journaling, ignore rules, lease handover and failure containment."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from watchdog.observers.api import BaseObserver
from watchdog.observers.polling import PollingObserver

from turbo_memory_mcp.workspace import watcher as watcher_module
from turbo_memory_mcp.workspace.models import LeaseRow, VersionRecord
from turbo_memory_mcp.workspace.paths import resolve_workspace
from turbo_memory_mcp.workspace.runtime import TICKER_THREAD_NAME, WorkspaceRuntime
from turbo_memory_mcp.workspace.watcher import WATCHER_THREAD_NAME, WorkspaceWatcher

GHOST_SESSION = "ghost00000001"


def _polling() -> BaseObserver:
    return PollingObserver(timeout=0.1)


def _live(name: str) -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == name and thread.is_alive()]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    isolated = {key: value for key, value in os.environ.items() if not key.startswith("TQMEMORY_")}
    isolated["TQMEMORY_HOME"] = str(tmp_path / "tqmemory-home")
    return isolated


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tracked.txt").write_text("committed\n", encoding="utf-8")
    (root / "clean.txt").write_text("clean\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def runtimes() -> Iterator[list[WorkspaceRuntime]]:
    """Track every runtime a test starts; each is shut down and no ticker or watcher thread may outlive the test."""

    started: list[WorkspaceRuntime] = []
    yield started
    for runtime in started:
        runtime.shutdown()
    assert _live(TICKER_THREAD_NAME) == []
    assert _live(WATCHER_THREAD_NAME) == []


def _start(
    runtimes: list[WorkspaceRuntime],
    cwd: Path,
    env: dict[str, str],
    factory: Callable[[], BaseObserver] = _polling,
) -> WorkspaceRuntime:
    runtime = WorkspaceRuntime.start(cwd=cwd, environ=env, observer_factory=factory)
    assert runtime is not None
    runtimes.append(runtime)
    return runtime


def _pair(
    runtimes: list[WorkspaceRuntime], repo: Path, env: dict[str, str]
) -> tuple[WorkspaceRuntime, WorkspaceRuntime]:
    """Two sessions in one tree; the second to register holds the lease and runs the watcher."""

    first = _start(runtimes, repo, env)
    second = _start(runtimes, repo, env)
    assert _owners(runtimes) == [second]
    return first, second


def _owners(runtimes: list[WorkspaceRuntime]) -> list[WorkspaceRuntime]:
    return [runtime for runtime in runtimes if runtime.journal_state()["state"] == "watching"]


def _lease(runtime: WorkspaceRuntime) -> LeaseRow | None:
    assert runtime.db is not None
    return runtime.db.lease_owner(runtime.workspace.root_key)


def _history(runtime: WorkspaceRuntime, rel: str) -> list[VersionRecord]:
    assert runtime.journal is not None
    return runtime.journal.history(rel, limit=100)


def _recorded_paths(runtime: WorkspaceRuntime) -> set[str]:
    db = runtime.db
    assert db is not None
    with db.lock:
        rows = db.conn.execute("SELECT DISTINCT path FROM versions").fetchall()
    return {row[0] for row in rows}


def _settle(runtimes: list[WorkspaceRuntime], predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    """Flush every watcher until ``predicate`` holds; the observers deliver events on their own threads."""

    deadline = time.monotonic() + timeout
    while True:
        for runtime in runtimes:
            if runtime.watcher is not None:
                runtime.watcher.flush_now()
        if predicate():
            return
        if time.monotonic() >= deadline:
            pytest.fail("condition not reached before the timeout")
        time.sleep(0.05)


def _quiet(runtimes: list[WorkspaceRuntime], seconds: float = 0.6) -> None:
    """Let a burst finish arriving (the debounce is 0.3 s), then flush what is left."""

    time.sleep(seconds)
    for runtime in runtimes:
        if runtime.watcher is not None:
            runtime.watcher.flush_now()


def test_baseline_snapshots_dirty_and_untracked_files_only(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    (repo / "tracked.txt").write_text("changed now\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("new\n", encoding="utf-8")

    _, second = _pair(runtimes, repo, env)

    assert _history(second, "tracked.txt")[0].kind == "content"
    assert _history(second, "untracked.txt")[0].kind == "content"
    assert _history(second, "clean.txt") == []
    assert _history(second, "src/a.py") == []


def test_change_is_recorded_with_inferred_attribution(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    _, second = _pair(runtimes, repo, env)
    second.announce_work("docs", ["src"])

    (repo / "src" / "a.py").write_text("x = 2  # edited\n", encoding="utf-8")
    _settle(runtimes, lambda: bool(_history(second, "src/a.py")))

    version = _history(second, "src/a.py")[0]
    assert version.attribution == "inferred"
    assert version.session_id == second.session_id


def test_ignored_files_are_not_recorded(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    (repo / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    (repo / ".tqmemoryignore").write_text("# private area\nprivate/*\n", encoding="utf-8")
    _, second = _pair(runtimes, repo, env)

    (repo / "debug.log").write_text("noise\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "out.txt").write_text("artifact\n", encoding="utf-8")
    (repo / "private").mkdir()
    (repo / "private" / "notes.txt").write_text("private\n", encoding="utf-8")
    (repo / ".git" / "probe.txt").write_text("git internals\n", encoding="utf-8")
    # A control file written in the same burst: once it is recorded, the ignored paths had their chance.
    (repo / "visible.txt").write_text("hello\n", encoding="utf-8")
    _settle(runtimes, lambda: bool(_history(second, "visible.txt")))
    _quiet(runtimes)

    recorded = _recorded_paths(second)
    assert "visible.txt" in recorded
    assert "debug.log" not in recorded
    assert "build/out.txt" not in recorded
    assert "private/notes.txt" not in recorded
    assert not any(path == ".git" or path.startswith(".git/") for path in recorded)


def test_delete_and_rename_are_journaled(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    _, second = _pair(runtimes, repo, env)

    (repo / "gone.txt").write_text("bye\n", encoding="utf-8")
    _settle(runtimes, lambda: bool(_history(second, "gone.txt")))
    (repo / "gone.txt").unlink()
    _settle(runtimes, lambda: any(v.kind == "deletion" for v in _history(second, "gone.txt")))

    (repo / "old.py").write_text("print(1)\n", encoding="utf-8")
    _settle(runtimes, lambda: bool(_history(second, "old.py")))
    (repo / "old.py").rename(repo / "new.py")
    _settle(
        runtimes,
        lambda: any(v.kind == "deletion" for v in _history(second, "old.py")) and bool(_history(second, "new.py")),
    )
    assert _history(second, "new.py")[0].kind == "content"


def test_watcher_starts_with_second_session_and_stops_when_alone(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    first = _start(runtimes, repo, env)
    assert first.journal_state()["state"] == "off"

    second = _start(runtimes, repo, env)
    first.tick()
    second.tick()

    assert [runtime.session_id for runtime in _owners(runtimes)] == [second.session_id]
    assert first.journal_state()["state"] == "off"
    assert first.journal_state()["owner_session"] == second.session_id

    second.shutdown()
    first.tick()

    assert first.journal_state()["state"] == "off"
    assert _lease(first) is None


def test_lease_taken_over_after_owner_death(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    first, second = _pair(runtimes, repo, env)
    assert first.db is not None
    # The lease now names a session whose process has exited; the table has no row for it.
    assert first.db.try_acquire_lease(
        first.workspace.root_key,
        GHOST_SESSION,
        _dead_pid(),
        -1_000_000.0,
        time.time(),
        owner_alive=lambda _row: False,
    )
    assert _lease(first) is not None
    assert _lease(first).session_id == GHOST_SESSION  # type: ignore[union-attr]

    first.tick()

    assert first.journal_state()["state"] == "watching"
    assert _lease(first) is not None
    assert _lease(first).session_id == first.session_id  # type: ignore[union-attr]
    second.tick()
    assert second.journal_state()["state"] == "off"
    assert second.watcher is None


def test_burst_of_files_stays_within_limits(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    env["TQMEMORY_JOURNAL_MAX_BYTES"] = "2048"
    first, second = _pair(runtimes, repo, env)

    burst = repo / "burst"
    burst.mkdir()
    for index in range(300):
        (burst / f"f{index:03d}.txt").write_text(f"file {index}\n" * 8, encoding="utf-8")

    def burst_recorded() -> bool:
        return sum(1 for path in _recorded_paths(second) if path.startswith("burst/")) == 300

    _settle(runtimes, burst_recorded, timeout=60.0)

    assert second.journal is not None
    assert second.journal.evict() == 0
    assert second.journal.over_cap is True

    digest = first.digest()
    assert digest is not None
    assert len([line for line in digest["new"] if "files changed" in line]) == 1


def test_refused_workspace_never_watches(tmp_path: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "x.txt").write_text("x\n", encoding="utf-8")

    first = _start(runtimes, plain, env)
    second = _start(runtimes, plain, env)

    for runtime in (first, second):
        state = runtime.journal_state()
        assert state["state"] == "unavailable"
        assert state["reason"] == "not a git repository"
        assert runtime.watcher is None
    assert _lease(first) is None


def test_failing_path_does_not_stop_later_changes(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, second = _pair(runtimes, repo, env)
    assert second.journal is not None
    journal = second.journal
    original_record = journal.record

    def flaky_record(rel_path: str, **kwargs: Any) -> VersionRecord | None:
        if rel_path == "bad.txt":
            raise ValueError("cannot journal this path")
        if rel_path == "worse.txt":
            raise OSError("disk went away")
        return original_record(rel_path, **kwargs)

    monkeypatch.setattr(journal, "record", flaky_record)
    (repo / "bad.txt").write_text("1\n", encoding="utf-8")
    (repo / "worse.txt").write_text("2\n", encoding="utf-8")
    (repo / "good.txt").write_text("3\n", encoding="utf-8")
    _settle(runtimes, lambda: bool(_history(second, "good.txt")))

    assert _history(second, "bad.txt") == []
    assert _history(second, "worse.txt") == []

    (repo / "later.txt").write_text("4\n", encoding="utf-8")
    _settle(runtimes, lambda: bool(_history(second, "later.txt")))


def test_watchdog_start_failure_is_contained(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    def broken() -> BaseObserver:
        raise OSError("inotify watch limit reached")

    _start(runtimes, repo, env)
    second = _start(runtimes, repo, env, factory=broken)

    state = second.journal_state()
    assert state["state"] == "unavailable"
    assert "inotify watch limit reached" in str(state["reason"])
    assert state["owner_session"] is None
    assert _lease(second) is None
    assert second.watcher is None
    assert second.announce_work("docs", ["src"])["status"] == "ok"
    assert second.status()["status"] == "ok"
    assert second.status()["journal"]["state"] == "unavailable"
    second.digest()


class _BlockingJournal:
    """Stands in for a journal whose first write never returns until released."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def record(self, rel_path: str, **kwargs: Any) -> None:
        self.entered.set()
        self.release.wait(30)
        return None

    def record_deletion(self, rel_path: str, **kwargs: Any) -> None:
        return None


def test_watcher_stop_is_bounded_and_idempotent(
    repo: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watcher_module, "_JOIN_TIMEOUT_S", 0.2)
    journal = _BlockingJournal()
    watcher = WorkspaceWatcher(
        resolve_workspace(repo, environ=env),
        journal,  # type: ignore[arg-type]
        attribute=lambda _path: ("unknown", None),
        debounce_s=0.05,
        observer_factory=_polling,
    )
    try:
        watcher.start()
        (repo / "blocked.txt").write_text("x\n", encoding="utf-8")
        assert journal.entered.wait(10)

        started = time.monotonic()
        watcher.stop()
        assert time.monotonic() - started < 5.0
        watcher.stop()

        threads = [thread for thread in threading.enumerate() if thread.name == WATCHER_THREAD_NAME]
        assert all(thread.daemon for thread in threads)
    finally:
        journal.release.set()
        watcher.stop()
