"""Session runtime: registration, presence, pruning of dead sessions and the fail-open contract."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from turbo_memory_mcp.workspace import models
from turbo_memory_mcp.workspace import runtime as runtime_module
from turbo_memory_mcp.workspace.db import CoordinationDB, CoordinationUnavailable
from turbo_memory_mcp.workspace.models import SessionRow, Workspace
from turbo_memory_mcp.workspace.paths import coord_db_path, resolve_workspace, workspace_storage_dir
from turbo_memory_mcp.workspace.runtime import TICKER_THREAD_NAME, WorkspaceRuntime

CHILD = (
    "import time\n"
    "from turbo_memory_mcp.workspace.runtime import WorkspaceRuntime\n"
    "runtime = WorkspaceRuntime.start()\n"
    "print(runtime.session_id, flush=True)\n"
    "time.sleep(60)\n"
)


def _live_tickers() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == TICKER_THREAD_NAME and thread.is_alive()]


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "tqmemory-home"


@pytest.fixture
def env(home: Path) -> dict[str, str]:
    isolated = {key: value for key, value in os.environ.items() if not key.startswith("TQMEMORY_")}
    isolated["TQMEMORY_HOME"] = str(home)
    return isolated


def _make_repo(base: Path, name: str) -> Path:
    repo = base / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _make_repo(tmp_path, "repo")


@pytest.fixture
def runtimes() -> Iterator[list[WorkspaceRuntime]]:
    """Track every runtime a test starts so each one is shut down and no ticker thread leaks."""

    started: list[WorkspaceRuntime] = []
    yield started
    for runtime in started:
        runtime.shutdown()
    assert _live_tickers() == []


@pytest.fixture
def children() -> Iterator[list[subprocess.Popen[str]]]:
    procs: list[subprocess.Popen[str]] = []
    yield procs
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)


CHILD_REPORT_TIMEOUT_S = 30.0


def _spawn(children: list[subprocess.Popen[str]], repo: Path, env: dict[str, str]) -> tuple[subprocess.Popen[str], str]:
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    children.append(proc)
    assert proc.stdout is not None
    lines: list[str] = []
    # Read on a helper thread so a child that never reports fails the test instead of hanging it.
    reader = threading.Thread(target=lambda: lines.append(proc.stdout.readline()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    reader.join(CHILD_REPORT_TIMEOUT_S)
    line = lines[0].strip() if lines and not reader.is_alive() else ""
    if not line:
        proc.kill()
        pytest.fail("child did not report its session id in time")
    return proc, line


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _ghost(workspace_key: str) -> SessionRow:
    now = time.time()
    return SessionRow(
        session_id="ghost00000001",
        workspace=workspace_key,
        client_name="codex",
        client_version="1.0",
        pid=_dead_pid(),
        # A start time no real process can have: a reused PID then fails the start-time check too.
        proc_start=-1_000_000.0,
        started_at=now,
        last_activity_at=now,
        feature="announced",
        areas=(),
        details=None,
    )


def _ids(rows: list[SessionRow]) -> set[str]:
    return {row.session_id for row in rows}


def test_three_processes_see_each_other(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], children: list[subprocess.Popen[str]]
) -> None:
    _, first = _spawn(children, repo, env)
    _, second = _spawn(children, repo, env)
    parent = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert parent is not None
    runtimes.append(parent)

    assert {first, second, parent.session_id} <= _ids(parent.live_sessions())
    assert parent.others() and {first, second} <= _ids(parent.others())
    assert parent.session_id not in _ids(parent.others())


def test_killed_session_disappears_with_ended_event(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], children: list[subprocess.Popen[str]]
) -> None:
    victim, victim_id = _spawn(children, repo, env)
    parent = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert parent is not None
    runtimes.append(parent)
    assert victim_id in _ids(parent.live_sessions())

    victim.kill()
    victim.wait()
    parent.tick()

    assert victim_id not in _ids(parent.live_sessions())
    assert parent.db is not None
    events = parent.db.events_after(parent.workspace.root_key, 0)
    ended = [event for event in events if event.kind == models.SESSION_ENDED]
    assert any(event.session_id == victim_id for event in ended)


def test_runtimes_in_different_workspaces_do_not_see_each_other(
    tmp_path: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    first = WorkspaceRuntime.start(cwd=_make_repo(tmp_path, "alpha"), environ=env)
    second = WorkspaceRuntime.start(cwd=_make_repo(tmp_path, "beta"), environ=env)
    assert first is not None and second is not None
    runtimes.extend([first, second])

    assert _ids(first.live_sessions()) == {first.session_id}
    assert _ids(second.live_sessions()) == {second.session_id}


def test_failed_registration_leaves_no_row_behind(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_append = CoordinationDB.append_event

    def failing_append(self: CoordinationDB, workspace: str, kind: str, **kwargs: object) -> int:
        if kind == models.SESSION_STARTED:
            raise CoordinationUnavailable("disk full")
        return original_append(self, workspace, kind, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(CoordinationDB, "append_event", failing_append)
    broken = WorkspaceRuntime.start(cwd=repo, environ=env)
    monkeypatch.undo()

    assert broken is not None
    assert broken.degraded_reason == "disk full"
    observer = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert observer is not None
    runtimes.append(observer)
    assert _ids(observer.live_sessions()) == {observer.session_id}


def test_disabled_switch_returns_none(repo: Path, env: dict[str, str]) -> None:
    env["TQMEMORY_WORKSPACE_DISABLE"] = "TRUE"
    assert WorkspaceRuntime.start(cwd=repo, environ=env) is None


def test_degraded_when_storage_unusable(repo: Path, env: dict[str, str], home: Path) -> None:
    project_id = resolve_workspace(repo, env).project_id
    blocked = workspace_storage_dir(home, project_id)
    blocked.parent.mkdir(parents=True)
    blocked.write_text("not a directory", encoding="utf-8")

    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)

    assert runtime is not None
    assert runtime.degraded_reason
    assert runtime.live_sessions() == []
    assert runtime.others() == []
    runtime.note_client("codex", "1.0")
    runtime.touch()
    runtime.tick()
    runtime.shutdown()
    runtime.shutdown()


def test_corrupt_db_self_heals(repo: Path, env: dict[str, str], home: Path, runtimes: list[WorkspaceRuntime]) -> None:
    project_id = resolve_workspace(repo, env).project_id
    db_file = coord_db_path(home, project_id)
    db_file.parent.mkdir(parents=True)
    db_file.write_bytes(b"not a database" * 100)

    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)

    assert runtime is not None
    runtimes.append(runtime)
    assert runtime.degraded_reason is None
    assert _ids(runtime.live_sessions()) == {runtime.session_id}


def test_shutdown_is_idempotent_and_removes_session(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    leaving = WorkspaceRuntime.start(cwd=repo, environ=env)
    staying = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert leaving is not None and staying is not None
    runtimes.append(staying)

    leaving.shutdown()
    leaving.shutdown()

    assert _ids(staying.live_sessions()) == {staying.session_id}
    assert staying.db is not None
    assert len(_live_tickers()) == 1


def test_shutdown_stops_ticker_promptly(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime_module, "TICK_INTERVAL_S", 3600.0)
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)
    assert len(_live_tickers()) == 1

    started = time.monotonic()
    runtime.shutdown()

    assert time.monotonic() - started < 2.0
    assert _live_tickers() == []


def test_ticker_prunes_dead_sessions_in_background(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime_module, "TICK_INTERVAL_S", 0.05)
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)
    assert runtime.db is not None
    ghost = _ghost(runtime.workspace.root_key)
    runtime.db.insert_session(ghost)

    deadline = time.monotonic() + 10.0
    while ghost.session_id in _ids(runtime.live_sessions()) and time.monotonic() < deadline:
        threading.Event().wait(0.02)

    assert ghost.session_id not in _ids(runtime.live_sessions())


def test_live_sessions_reads_rows_without_liveness_checks(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep the real ticker from pruning the ghost before the read is asserted.
    monkeypatch.setattr(runtime_module, "TICK_INTERVAL_S", 3600.0)
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)
    assert runtime.db is not None
    ghost = _ghost(runtime.workspace.root_key)
    runtime.db.insert_session(ghost)

    assert ghost.session_id in _ids(runtime.live_sessions())

    runtime.tick()

    assert ghost.session_id not in _ids(runtime.live_sessions())


def test_note_client_and_touch_update_own_row(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)

    runtime.note_client(None, None)
    runtime.note_client("codex", "2.1")
    runtime.touch()

    (row,) = runtime.live_sessions()
    assert (row.client_name, row.client_version) == ("codex", "2.1")
    assert row.last_activity_at >= row.started_at


def test_failures_after_start_fail_open(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)
    assert runtime.db is not None
    runtime.db.conn.close()

    assert runtime.live_sessions() == []
    assert runtime.degraded_reason
    runtime.note_client("codex", "1")
    runtime.touch()
    runtime.tick()
    runtime.shutdown()
    assert runtime.db is None


def test_start_opens_shared_coordination_file(
    repo: Path, env: dict[str, str], home: Path, runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)

    assert isinstance(runtime.db, CoordinationDB)
    assert coord_db_path(home, runtime.workspace.project_id).exists()


def test_resolution_failure_returns_a_degraded_runtime(
    repo: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> Workspace:
        raise RuntimeError("identity exploded")

    monkeypatch.setattr(runtime_module, "resolve_workspace", boom)
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    monkeypatch.undo()

    assert runtime is not None
    assert runtime.db is None
    assert runtime.degraded_reason == "identity exploded"
    assert runtime.workspace.journal_refusal == "workspace could not be resolved"
    assert runtime.live_sessions() == []


def test_ticker_start_failure_withdraws_the_row(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_self: threading.Thread) -> None:
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)
    broken = WorkspaceRuntime.start(cwd=repo, environ=env)
    monkeypatch.undo()

    assert broken is not None
    assert broken.degraded_reason == "cannot start thread"
    observer = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert observer is not None
    runtimes.append(observer)
    assert _ids(observer.live_sessions()) == {observer.session_id}


def test_shutdown_waits_for_an_active_tick(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime_module, "TICK_INTERVAL_S", 3600.0)
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)
    entered = threading.Event()
    release = threading.Event()

    def blocking_hook(_live: list[SessionRow]) -> None:
        entered.set()
        release.wait(10.0)

    runtime._on_tick = blocking_hook  # type: ignore[method-assign]
    ticking = threading.Thread(target=runtime.tick, daemon=True)
    ticking.start()
    assert entered.wait(10.0)

    closer = threading.Thread(target=runtime.shutdown, daemon=True)
    closer.start()
    closer.join(0.3)

    assert closer.is_alive(), "shutdown must wait for the in-flight tick"
    assert runtime.db is not None, "the database must not close under a running tick"
    release.set()
    closer.join(10.0)
    ticking.join(10.0)
    assert not closer.is_alive()
    observer = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert observer is not None
    runtimes.append(observer)
    assert runtime.session_id not in _ids(observer.live_sessions())


def test_shutdown_retries_a_transient_failure_before_closing(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)
    original_end = CoordinationDB.end_session
    attempts: list[int] = []

    def flaky_end(self: CoordinationDB, *args: object, **kwargs: object) -> bool:
        attempts.append(1)
        if len(attempts) <= 2:
            raise CoordinationUnavailable("database is locked")
        return original_end(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(CoordinationDB, "end_session", flaky_end)
    runtime.shutdown()
    monkeypatch.undo()

    assert len(attempts) == 3
    assert runtime.db is None
    observer = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert observer is not None
    runtimes.append(observer)
    assert _ids(observer.live_sessions()) == {observer.session_id}
    assert observer.db is not None
    ended = [e for e in observer.db.events_after(observer.workspace.root_key, 0) if e.kind == models.SESSION_ENDED]
    assert [e.session_id for e in ended] == [runtime.session_id]


def test_shutdown_gives_up_after_bounded_retries_and_closes(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)
    attempts: list[int] = []

    def always_fails(self: CoordinationDB, *args: object, **kwargs: object) -> bool:
        attempts.append(1)
        raise CoordinationUnavailable("database is locked")

    monkeypatch.setattr(CoordinationDB, "end_session", always_fails)
    runtime.shutdown()

    assert len(attempts) == 3
    assert runtime.db is None
    assert runtime.degraded_reason == "database is locked"


def test_shutdown_event_carries_the_same_detail_as_pruning(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    leaving = WorkspaceRuntime.start(cwd=repo, environ=env)
    observer = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert leaving is not None and observer is not None
    runtimes.append(observer)
    leaving.note_client("codex", "2.1")
    leaving.shutdown()

    assert observer.db is not None
    ended = [e for e in observer.db.events_after(observer.workspace.root_key, 0) if e.kind == models.SESSION_ENDED]
    assert [(e.session_id, e.detail) for e in ended] == [(leaving.session_id, {"client": "codex", "feature": None})]


def test_note_client_writes_only_the_first_known_name(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)
    writes: list[str] = []
    original_update = CoordinationDB.update_session_client

    def counting_update(self: CoordinationDB, session_id: str, name: str, version: str) -> None:
        writes.append(name)
        original_update(self, session_id, name, version)

    monkeypatch.setattr(CoordinationDB, "update_session_client", counting_update)
    threads = [threading.Thread(target=runtime.note_client, args=(f"client-{index}", "1.0")) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10.0)

    assert len(writes) == 1


def test_malformed_session_row_degrades_instead_of_raising(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)
    assert runtime.db is not None
    with runtime.db.lock:
        runtime.db.conn.execute("UPDATE sessions SET areas = 'not json' WHERE session_id = ?", (runtime.session_id,))

    assert runtime.live_sessions() == []
    assert runtime.degraded_reason
    runtime.tick()
    runtime.touch()


def test_degraded_reason_clears_after_a_successful_tick(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)
    runtime._degrade(CoordinationUnavailable("transient"))
    assert runtime.degraded_reason == "transient"

    runtime.tick()

    assert runtime.degraded_reason is None


def test_repeated_failures_are_logged_at_most_every_ten_minutes(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], caplog: pytest.LogCaptureFixture
) -> None:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    runtimes.append(runtime)

    with caplog.at_level(logging.WARNING, logger="turbo_memory_mcp.workspace.runtime"):
        runtime._log_failure(CoordinationUnavailable("a"), now=0.0)
        runtime._log_failure(CoordinationUnavailable("b"), now=100.0)
        runtime._log_failure(OSError("c"), now=100.0)
        runtime._log_failure(CoordinationUnavailable("d"), now=601.0)

    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "workspace coordination degraded (CoordinationUnavailable)",
        "workspace coordination degraded (OSError)",
        "workspace coordination degraded (CoordinationUnavailable)",
    ]
