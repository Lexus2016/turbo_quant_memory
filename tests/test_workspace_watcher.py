"""Leased file watcher: git baseline, debounced journaling, ignore rules, lease handover and failure containment."""

from __future__ import annotations

import json
import os
import random
import shutil
import string
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from watchdog.events import DirDeletedEvent, DirMovedEvent
from watchdog.observers.api import BaseObserver
from watchdog.observers.polling import PollingObserver

from turbo_memory_mcp.workspace import runtime as runtime_module
from turbo_memory_mcp.workspace import watcher as watcher_module
from turbo_memory_mcp.workspace.db import CoordinationDB
from turbo_memory_mcp.workspace.journal import Journal, JournalLimits
from turbo_memory_mcp.workspace.models import LeaseRow, VersionRecord
from turbo_memory_mcp.workspace.paths import resolve_workspace
from turbo_memory_mcp.workspace.runtime import TICKER_THREAD_NAME, WorkspaceRuntime
from turbo_memory_mcp.workspace.watcher import WATCHER_THREAD_NAME, WorkspaceWatcher

GHOST_SESSION = "ghost00000001"


def _polling() -> BaseObserver:
    return PollingObserver(timeout=0.1)


def _quiet_polling() -> BaseObserver:
    """An observer that reports nothing during a test; only explicitly queued paths are processed."""

    return PollingObserver(timeout=60)


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


@pytest.fixture
def standalone(
    repo: Path, env: dict[str, str], tmp_path: Path
) -> Iterator[tuple[WorkspaceWatcher, Journal, CoordinationDB]]:
    """A watcher over a real journal without any runtime, for tests that drive the flush directly."""

    workspace = resolve_workspace(repo, environ=env)
    db = CoordinationDB.open(tmp_path / "coord.sqlite")
    journal = Journal(workspace, db, tmp_path / "blobs", JournalLimits())
    watcher = WorkspaceWatcher(
        workspace,
        journal,
        attribute=lambda _path: ("unknown", None, None),
        observer_factory=_quiet_polling,
        content_under=lambda prefix: db.content_paths_under(workspace.root_key, prefix),
    )
    yield watcher, journal, db
    watcher.stop()
    db.close()


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
    """Two sessions in one tree. Ticks drive the lease: the second session to tick first takes it."""

    first = _start(runtimes, repo, env)
    second = _start(runtimes, repo, env)
    second.tick()
    first.tick()
    assert _owners(runtimes) == [second]
    return first, second


def _owners(runtimes: list[WorkspaceRuntime]) -> list[WorkspaceRuntime]:
    """The sessions that run a watcher. Every session reports the lease owner's state, so this checks the watcher."""

    return [runtime for runtime in runtimes if runtime.watcher is not None]


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


def test_deleting_a_committed_file_is_journaled(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    _, second = _pair(runtimes, repo, env)
    assert second.watcher is not None

    (repo / "src" / "a.py").unlink()
    second.watcher._enqueue([str(repo / "src" / "a.py")])
    second.watcher.flush_now()

    assert _history(second, "src/a.py")[0].kind == "deletion"


def test_watcher_starts_with_second_session_and_stops_when_alone(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    first = _start(runtimes, repo, env)
    assert first.journal_state()["state"] == "off"

    second = _start(runtimes, repo, env)
    second.tick()
    first.tick()

    assert [runtime.session_id for runtime in _owners(runtimes)] == [second.session_id]
    assert first.journal_state()["state"] == "watching"
    assert first.journal_state()["owner_session"] == second.session_id
    assert first.journal_state()["this_session_owns"] is False

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
    lease = _lease(first)
    assert lease is not None
    assert lease.session_id == GHOST_SESSION

    first.tick()

    assert first.journal_state()["state"] == "watching"
    lease = _lease(first)
    assert lease is not None
    assert lease.session_id == first.session_id
    # The lease holder is live, so the other session reports the holder as the owner, not "off".
    second.tick()
    assert second.journal_state()["state"] == "watching"
    assert second.journal_state()["owner_session"] == first.session_id
    assert second.journal_state()["this_session_owns"] is False
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

    second.run_journal_eviction()
    assert second.journal is not None
    assert second.journal.over_cap is True
    assert first.journal_state()["over_cap"] is True

    digest = first.digest()
    assert digest is not None
    assert len([line for line in digest["new"] if "files changed" in line]) == 1


def test_non_owner_reports_owner_and_shared_over_cap(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    env["TQMEMORY_JOURNAL_MAX_BYTES"] = "100"
    first, second = _pair(runtimes, repo, env)
    rng = random.Random(7)
    for index in range(3):
        # Blobs are compressed, so the content must resist compression for the size cap to bite.
        text = "".join(rng.choice(string.ascii_letters) for _ in range(400))
        (repo / f"note{index}.txt").write_text(text, encoding="utf-8")
    _settle(runtimes, lambda: all(f"note{index}.txt" in _recorded_paths(second) for index in range(3)))

    second.run_journal_eviction()

    watching = first.journal_state()
    assert watching["state"] == "watching"
    assert watching["owner_session"] == second.session_id
    assert watching["this_session_owns"] is False
    assert watching["over_cap"] is True
    assert second.journal_state()["this_session_owns"] is True


def test_refused_workspace_never_watches(tmp_path: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "x.txt").write_text("x\n", encoding="utf-8")

    first = _start(runtimes, plain, env)
    second = _start(runtimes, plain, env)
    second.tick()
    first.tick()

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

    first = _start(runtimes, repo, env)
    second = _start(runtimes, repo, env, factory=broken)
    second.tick()

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
    assert first.journal_state()["state"] == "off"


def test_failed_start_backs_off_before_the_next_attempt(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []
    original_start = WorkspaceWatcher.start

    def counting_start(self: WorkspaceWatcher) -> None:
        attempts.append(1)
        original_start(self)

    monkeypatch.setattr(WorkspaceWatcher, "start", counting_start)

    def broken() -> BaseObserver:
        raise OSError("inotify watch limit reached")

    _start(runtimes, repo, env)
    second = _start(runtimes, repo, env, factory=broken)
    second.tick()
    second.tick()

    assert len(attempts) == 1
    assert second.journal_state()["state"] == "unavailable"
    assert _lease(second) is None

    # Once the backoff has elapsed, the next tick tries again.
    third = _start(runtimes, repo, env, factory=broken)
    monkeypatch.setattr(runtime_module, "WATCH_RETRY_S", 0.0)
    third.tick()
    third.tick()
    assert len(attempts) == 3


def test_dead_emitter_releases_lease_and_backs_off(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    created: list[BaseObserver] = []

    def tracked() -> BaseObserver:
        observer = _polling()
        created.append(observer)
        return observer

    _start(runtimes, repo, env)
    second = _start(runtimes, repo, env, factory=tracked)
    second.tick()
    assert second.journal_state()["state"] == "watching"

    created[0].stop()  # the observer thread dies under the running watcher
    created[0].join(5.0)
    second.tick()

    state = second.journal_state()
    assert state["state"] == "unavailable"
    assert "observer" in str(state["reason"])
    assert _lease(second) is None
    assert second.watcher is None

    second.tick()
    assert len(created) == 1
    assert second.journal_state()["state"] == "unavailable"


class _StuckObserver(PollingObserver):
    """An observer whose stop blocks until released, like an emitter that never finishes joining."""

    def __init__(self, release: threading.Event) -> None:
        super().__init__(timeout=0.1)
        self._release = release

    def stop(self) -> None:
        self._release.wait(30)
        super().stop()


def test_stuck_observer_does_not_block_shutdown_or_lease_release(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    release = threading.Event()
    try:
        first = _start(runtimes, repo, env)
        second = _start(runtimes, repo, env, factory=lambda: _StuckObserver(release))
        second.tick()
        first.tick()
        assert second.watcher is not None

        started = time.monotonic()
        second.shutdown()
        elapsed = time.monotonic() - started

        assert elapsed < 4.0
        assert _lease(first) is None
    finally:
        release.set()


def test_watcher_stop_is_bounded_and_idempotent(
    repo: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watcher_module, "_JOIN_TIMEOUT_S", 0.2)
    journal = _BlockingJournal()
    watcher = WorkspaceWatcher(
        resolve_workspace(repo, environ=env),
        journal,  # type: ignore[arg-type]
        attribute=lambda _path: ("unknown", None, None),
        debounce_s=0.05,
        observer_factory=_polling,
    )
    try:
        watcher.start()
        (repo / "blocked.txt").write_text("x\n", encoding="utf-8")
        assert journal.entered.wait(10)

        started = time.monotonic()
        watcher.stop()
        # Two joins at 0.2 s each; the default 2 s joins would take about 4 s, so this bound fails without the patch.
        assert time.monotonic() - started < 1.5
        watcher.stop()

        threads = [thread for thread in threading.enumerate() if thread.name == WATCHER_THREAD_NAME]
        assert all(thread.daemon for thread in threads)
    finally:
        journal.release.set()
        watcher.stop()


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


def test_stop_drains_events_queued_before_it(
    repo: Path, standalone: tuple[WorkspaceWatcher, Journal, CoordinationDB]
) -> None:
    watcher, journal, _db = standalone
    doomed = repo / "doomed.txt"
    doomed.write_text("bye\n", encoding="utf-8")
    journal.record("doomed.txt")
    watcher.start()

    doomed.unlink()
    watcher._enqueue([str(doomed)])
    watcher.stop()

    assert journal.history("doomed.txt", limit=1)[0].kind == "deletion"


def test_git_calls_never_take_optional_locks(
    repo: Path, standalone: tuple[WorkspaceWatcher, Journal, CoordinationDB], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    real_run = subprocess.run

    def spy(args: Any, *args_rest: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((list(args), kwargs))
        return real_run(args, *args_rest, **kwargs)

    monkeypatch.setattr(watcher_module.subprocess, "run", spy)
    watcher, _journal, _db = standalone
    watcher.start()
    (repo / "fresh.txt").write_text("x\n", encoding="utf-8")
    (repo / "gone.txt").write_text("y\n", encoding="utf-8")
    watcher._enqueue([str(repo / "fresh.txt"), str(repo / "gone.txt")])
    watcher.flush_now()
    (repo / "gone.txt").unlink()
    watcher._enqueue([str(repo / "gone.txt")])
    watcher.flush_now()
    watcher.stop()

    git_calls = [(args, kwargs) for args, kwargs in calls if args and args[0] == "git"]
    assert git_calls
    for args, kwargs in git_calls:
        assert args[1] == "--no-optional-locks", args
        assert kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert any("status" in args for args, _ in git_calls)
    assert any("check-ignore" in args for args, _ in git_calls)


def test_transient_file_leaves_no_deletion_and_no_digest_line(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    first, second = _pair(runtimes, repo, env)
    assert second.watcher is not None

    scratch = repo / "scratch.tmp"
    scratch.write_text("temp\n", encoding="utf-8")
    scratch.unlink()
    second.watcher._enqueue([str(scratch)])
    second.watcher.flush_now()

    assert _history(second, "scratch.tmp") == []
    digest = first.digest()
    lines = digest["new"] if digest is not None else []
    assert not any("scratch" in line or "files changed" in line for line in lines)


def test_removed_directory_records_deletions_for_its_journaled_files(
    repo: Path, standalone: tuple[WorkspaceWatcher, Journal, CoordinationDB]
) -> None:
    watcher, journal, _db = standalone
    docs = repo / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("a\n", encoding="utf-8")
    (docs / "b.md").write_text("b\n", encoding="utf-8")
    journal.record("docs/a.md")
    journal.record("docs/b.md")
    shutil.rmtree(docs)

    watcher_module._Handler(watcher).on_any_event(DirDeletedEvent(str(docs)))
    watcher.flush_now()

    assert journal.history("docs/a.md", limit=1)[0].kind == "deletion"
    assert journal.history("docs/b.md", limit=1)[0].kind == "deletion"


def test_start_never_runs_the_baseline_on_the_caller(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocked_baseline(self: WorkspaceWatcher) -> None:
        entered.set()
        release.wait(30)

    monkeypatch.setattr(WorkspaceWatcher, "_baseline", blocked_baseline)
    ticker = threading.Thread(target=lambda: None, daemon=True)
    try:
        _start(runtimes, repo, env)
        started = time.monotonic()
        second = _start(runtimes, repo, env)
        assert time.monotonic() - started < 2.0
        assert not entered.is_set()

        # The ticker's work (here, an explicit tick) is where the baseline runs.
        ticker = threading.Thread(target=second.tick, daemon=True)
        ticker.start()
        assert entered.wait(10)
    finally:
        release.set()
        ticker.join(10)


def test_shutdown_returns_when_a_flush_holds_the_db_lock(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime_module, "_SHUTDOWN_DB_WAIT_S", 0.3)
    _, second = _pair(runtimes, repo, env)
    assert second.db is not None
    held = threading.Event()
    release = threading.Event()

    def hold_db_lock() -> None:
        # Stands in for an abandoned flush helper stuck inside journal.record.
        assert second.db is not None
        with second.db.lock:
            held.set()
            release.wait(30)

    holder = threading.Thread(target=hold_db_lock, daemon=True)
    holder.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        second.shutdown()
        elapsed = time.monotonic() - started
        assert elapsed < 2.0
    finally:
        release.set()
        holder.join(10)


def test_emitter_stopped_under_a_live_observer_is_unhealthy(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    created: list[BaseObserver] = []

    def tracked() -> BaseObserver:
        observer = _polling()
        created.append(observer)
        return observer

    first = _start(runtimes, repo, env)
    second = _start(runtimes, repo, env, factory=tracked)
    second.tick()
    first.tick()
    assert second.journal_state()["state"] == "watching"

    observer = created[0]
    for emitter in observer.emitters:
        emitter.stop()
        emitter.join(5.0)
    assert observer.is_alive()  # the observer keeps running; only its emitter died

    second.tick()

    state = second.journal_state()
    assert state["state"] == "unavailable"
    assert "emitter" in str(state["reason"])
    assert _lease(second) is None
    assert second.watcher is None

    second.tick()
    assert len(created) == 1


def test_moved_out_directory_is_enumerated_even_when_recreated(
    repo: Path, tmp_path: Path, standalone: tuple[WorkspaceWatcher, Journal, CoordinationDB]
) -> None:
    watcher, journal, _db = standalone
    docs = repo / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("a\n", encoding="utf-8")
    journal.record("docs/a.md")
    outside = tmp_path / "outside" / "docs"
    outside.parent.mkdir()
    shutil.move(str(docs), str(outside))
    docs.mkdir()  # the path exists again, empty

    watcher_module._Handler(watcher).on_any_event(DirMovedEvent(str(docs), str(outside)))
    watcher.flush_now()

    assert journal.history("docs/a.md", limit=1)[0].kind == "deletion"


def test_deferred_shutdown_releases_lease_and_session_once_the_lock_frees(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime_module, "_SHUTDOWN_DB_WAIT_S", 0.3)
    first, second = _pair(runtimes, repo, env)
    assert second.db is not None
    held = threading.Event()
    release = threading.Event()

    def hold_db_lock() -> None:
        assert second.db is not None
        with second.db.lock:
            held.set()
            release.wait(30)

    holder = threading.Thread(target=hold_db_lock, daemon=True)
    holder.start()
    try:
        assert held.wait(5)
        second.shutdown()
    finally:
        release.set()
        holder.join(10)

    # The deferred closer finishes once the lock is free: lease and session row go, the database closes.
    deadline = time.monotonic() + 10.0
    while _live(runtime_module._CLOSE_THREAD_NAME) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _live(runtime_module._CLOSE_THREAD_NAME) == []
    assert _lease(first) is None
    assert first.db is not None
    assert first.db.get_session(second.session_id) is None

    # A peer can now take the lease.
    third = _start(runtimes, repo, env)
    third.tick()
    lease = _lease(first)
    assert lease is not None
    assert lease.session_id == third.session_id
    assert third.watcher is not None


def _latest_kind(db: CoordinationDB, workspace: str, path: str) -> str | None:
    with db.lock:
        row = db.conn.execute(
            "SELECT kind FROM versions WHERE workspace = ? AND path = ? ORDER BY version_id DESC LIMIT 1",
            (workspace, path),
        ).fetchone()
    return None if row is None else str(row[0])


def test_directory_replaced_by_symlink_is_not_journaled_through_the_link(
    repo: Path, tmp_path: Path, standalone: tuple[WorkspaceWatcher, Journal, CoordinationDB]
) -> None:
    """A journaled directory replaced by a symlink: the journal resolves paths through links, so nothing is filed
    under a link target's path. The watcher skips the child rather than record a deletion for a file that exists."""

    watcher, journal, db = standalone
    workspace = watcher._workspace.root_key
    inside = repo / "elsewhere"
    inside.mkdir()
    (inside / "a.md").write_text("copy\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "b.md").write_text("copy\n", encoding="utf-8")

    docs = repo / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("a\n", encoding="utf-8")
    (docs / "b.md").write_text("b\n", encoding="utf-8")
    journal.record("docs/a.md")
    journal.record("docs/b.md")
    moved = tmp_path / "moved-away"
    shutil.move(str(docs), str(moved))
    docs.symlink_to(inside, target_is_directory=True)
    watcher_module._Handler(watcher).on_any_event(DirMovedEvent(str(docs), str(moved)))
    watcher.flush_now()

    # No deletion lands under the link target's path, and the real file there is untouched.
    assert _latest_kind(db, workspace, "elsewhere/a.md") is None
    assert _latest_kind(db, workspace, "docs/a.md") == "content"

    docs.unlink()
    docs.symlink_to(outside, target_is_directory=True)
    watcher_module._Handler(watcher).on_any_event(DirMovedEvent(str(docs), str(moved)))
    watcher.flush_now()
    assert _latest_kind(db, workspace, "docs/b.md") == "content"


def _event_features(db: CoordinationDB, path: str) -> list[str | None]:
    """The ``feature`` recorded in the detail of each file-change event for ``path``, oldest first."""

    with db.lock:
        rows = db.conn.execute(
            "SELECT detail FROM events WHERE kind = 'file_changed' AND path = ? ORDER BY event_id", (path,)
        ).fetchall()
    return [json.loads(row[0]).get("feature") for row in rows]


def test_hot_path_never_waits_on_a_busy_database(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    first, second = _pair(runtimes, repo, env)
    second.announce_work("docs", ["src"])
    assert first.db is not None
    held = threading.Event()
    release = threading.Event()

    def hold_db_lock() -> None:
        assert first.db is not None
        with first.db.lock:
            held.set()
            release.wait(30)

    holder = threading.Thread(target=hold_db_lock, daemon=True)
    holder.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        assert first.digest() is None
        first.touch()
        assert time.monotonic() - started < 0.25
    finally:
        release.set()
        holder.join(10)

    # Nothing was lost: the cursor did not move, so the next call delivers the pending line.
    digest = first.digest()
    assert digest is not None
    assert any("docs" in line for line in digest["new"])


def test_change_during_the_baseline_is_recorded(
    repo: Path,
    tmp_path: Path,
    standalone: tuple[WorkspaceWatcher, Journal, CoordinationDB],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, journal, _db = standalone
    original_baseline = WorkspaceWatcher._baseline

    def baseline_then_change(self: WorkspaceWatcher) -> None:
        original_baseline(self)
        # The git snapshot is already taken; this write lands in the gap the observer must cover.
        (repo / "late.txt").write_text("written during the baseline\n", encoding="utf-8")

    monkeypatch.setattr(WorkspaceWatcher, "_baseline", baseline_then_change)
    watcher = WorkspaceWatcher(
        base._workspace,
        journal,
        attribute=lambda _path: ("unknown", None, None),
        observer_factory=_polling,
    )
    try:
        watcher.start()
        deadline = time.monotonic() + 10.0
        while not _history_kinds(journal, "late.txt") and time.monotonic() < deadline:
            watcher.flush_now()
            time.sleep(0.05)
        assert _history_kinds(journal, "late.txt") == ["content"]
    finally:
        watcher.stop()


def _history_kinds(journal: Journal, rel: str) -> list[str]:
    return [version.kind for version in journal.history(rel, limit=100)]


def test_version_keeps_the_feature_of_the_session_it_was_attributed_to(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    _, second = _pair(runtimes, repo, env)
    assert second.db is not None
    second.announce_work("feature-A", ["src"])

    (repo / "src" / "a.py").write_text("x = 3  # for A\n", encoding="utf-8")
    _settle(runtimes, lambda: len(_event_features(second.db, "src/a.py")) >= 1)  # type: ignore[arg-type]

    second.announce_work("feature-B", ["src"])
    (repo / "src" / "a.py").write_text("x = 4  # for B\n", encoding="utf-8")
    _settle(runtimes, lambda: len(_event_features(second.db, "src/a.py")) >= 2)  # type: ignore[arg-type]

    features = _event_features(second.db, "src/a.py")  # type: ignore[arg-type]
    assert features[0] == "feature-A"
    assert features[1] == "feature-B"
