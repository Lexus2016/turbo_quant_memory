"""Compact status digest: silence when alone, one line per event, bounded output and one shared cursor."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from turbo_memory_mcp.workspace import models
from turbo_memory_mcp.workspace import runtime as runtime_module
from turbo_memory_mcp.workspace.db import CoordinationUnavailable
from turbo_memory_mcp.workspace.runtime import WorkspaceRuntime


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    isolated = {key: value for key, value in os.environ.items() if not key.startswith("TQMEMORY_")}
    isolated["TQMEMORY_HOME"] = str(tmp_path / "tqmemory-home")
    return isolated


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    return path


@pytest.fixture
def runtimes() -> Iterator[list[WorkspaceRuntime]]:
    started: list[WorkspaceRuntime] = []
    yield started
    for runtime in started:
        runtime.shutdown()


def _start(
    started: list[WorkspaceRuntime],
    repo: Path,
    env: dict[str, str],
    client: str | None = None,
) -> WorkspaceRuntime:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    started.append(runtime)
    if client is not None:
        runtime.note_client(client, "1.0")
    return runtime


def _settle(runtime: WorkspaceRuntime) -> None:
    """Refresh the presence snapshot, as the ticker does every interval; digest() reads that snapshot."""

    runtime.tick()


def _files(
    runtime: WorkspaceRuntime,
    paths: list[str],
    kind: str = models.FILE_CHANGED,
    detail: dict[str, object] | None = None,
) -> None:
    assert runtime.db is not None
    for path in paths:
        runtime.db.append_event(runtime.workspace.root_key, kind, now=time.time(), path=path, detail=detail or {})


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _lines_with(digest: dict[str, object] | None, prefix: str) -> list[str]:
    assert digest is not None
    lines = digest["new"]
    assert isinstance(lines, list)
    return [line for line in lines if line.startswith(prefix)]


# Required behaviour ---------------------------------------------------------


def test_digest_silent_when_alone(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    runtime = _start(runtimes, repo, env)
    _files(runtime, ["src/a.py", "src/b.py"])
    _settle(runtime)

    assert runtime.digest() is None


def test_digest_appears_once_per_event(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    viewer = _start(runtimes, repo, env)
    _start(runtimes, repo, env, client="codex")
    _settle(viewer)

    first = viewer.digest()

    assert first is not None
    assert any("codex" in line for line in first["new"])
    assert viewer.digest() is None


def test_digest_caps_at_three_lines(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    viewer = _start(runtimes, repo, env)
    for index in range(5):
        _start(runtimes, repo, env, client=f"peer{index}")
    _settle(viewer)

    digest = viewer.digest()

    assert digest is not None
    assert len(digest["new"]) == 3
    assert digest["new"][-1] == "+3 more updates"


def test_file_burst_collapses_to_one_line(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    viewer.digest()  # consumes the peer's arrival so only the burst remains
    _files(peer, [f"src/file_{index}.py" for index in range(2000)])

    digest = viewer.digest()

    assert len(_lines_with(digest, "2000 files changed:")) == 1


def test_digest_filters_by_my_areas(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    peer = _start(runtimes, repo, env, client="codex")
    viewer = _start(runtimes, repo, env)
    viewer.announce_work("ranking", ["src"])
    _settle(viewer)

    _files(peer, ["docs/x.md"])
    assert viewer.digest() is None

    _files(peer, ["src/y.py"])
    assert _lines_with(viewer.digest(), "1 files changed: src/y.py")


def test_digest_skips_changes_inferred_to_me(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    peer = _start(runtimes, repo, env, client="codex")
    viewer = _start(runtimes, repo, env)
    viewer.announce_work("ranking", ["src"])
    _settle(viewer)

    _files(peer, ["src/y.py"], detail={"attribution": "inferred", "session_id": viewer.session_id})
    assert viewer.digest() is None

    _files(peer, ["src/z.py"], detail={"attribution": "inferred", "session_id": peer.session_id})
    assert _lines_with(viewer.digest(), "1 files changed: src/z.py")


# Controller rulings ---------------------------------------------------------


def test_alone_digest_reads_no_events_and_moves_cursor_to_newest(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _start(runtimes, repo, env)
    assert runtime.db is not None
    _files(runtime, [f"src/f{index}.py" for index in range(50)])

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a lone session must not read the event backlog")

    monkeypatch.setattr(runtime.db, "events_after", forbidden)
    _settle(runtime)

    assert runtime.digest() is None
    assert runtime.db.get_cursor(runtime.session_id) == runtime.db.max_event_id(runtime.workspace.root_key)


def test_solo_backlog_does_not_flood_first_digest_after_a_peer_appears(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = _start(runtimes, repo, env)
    _files(runtime, [f"src/f{index}.py" for index in range(50)])
    _settle(runtime)
    assert runtime.digest() is None

    _start(runtimes, repo, env, client="codex")
    _settle(runtime)
    digest = runtime.digest()

    assert digest is not None
    assert len(digest["new"]) == 1
    assert "codex" in digest["new"][0]


def test_digest_pages_through_the_whole_backlog(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    viewer.digest()
    _files(peer, [f"src/f{index}.py" for index in range(450)])

    digest = viewer.digest()

    assert _lines_with(digest, "450 files changed:")


def test_scan_cap_bounds_one_call_and_resumes_on_the_next(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime_module, "DIGEST_SCAN_LIMIT", 250)
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    viewer.digest()
    assert viewer.db is not None
    root = viewer.workspace.root_key
    before = viewer.db.max_event_id(root)
    _files(peer, [f"src/f{index}.py" for index in range(450)])

    first = viewer.digest()

    assert _lines_with(first, "250 files changed:")
    # The cursor stops at the newest event actually read (the 250th), not at the database's newest.
    assert viewer.db.get_cursor(viewer.session_id) == before + 250
    assert _lines_with(viewer.digest(), "200 files changed:")
    assert viewer.digest() is None


def test_file_line_counts_distinct_paths_newest_first(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    viewer.digest()
    # a changed five times and d was deleted: four distinct paths, newest first a, d, c, b.
    _files(peer, ["src/a.py", "src/b.py", "src/a.py", "src/c.py", "src/a.py"])
    _files(peer, ["src/d.py"], kind=models.FILE_DELETED)
    _files(peer, ["src/a.py", "src/a.py", "src/a.py"])

    assert _lines_with(viewer.digest(), "4 files changed: src/a.py, src/d.py, src/c.py …") == [
        "4 files changed: src/a.py, src/d.py, src/c.py …"
    ]

    _files(peer, ["src/x.py", "src/y.py"])

    assert _lines_with(viewer.digest(), "2 files changed: src/y.py, src/x.py") == [
        "2 files changed: src/y.py, src/x.py"
    ]


def test_digest_fails_open(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    assert viewer.db is not None

    def broken(*_args: object, **_kwargs: object) -> None:
        raise CoordinationUnavailable("database is gone")

    monkeypatch.setattr(viewer.db, "events_after", broken)
    _files(peer, ["src/a.py"])

    assert viewer.digest() is None


def test_digest_makes_no_liveness_probe(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer = _start(runtimes, repo, env)
    _start(runtimes, repo, env, client="codex")
    _settle(viewer)

    def probe(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("digest() must not probe process liveness")

    monkeypatch.setattr(runtime_module, "is_process_alive", probe)

    assert viewer.digest() is not None


def test_digest_and_status_share_one_cursor_that_never_moves_back(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    assert viewer.db is not None
    root = viewer.workspace.root_key
    viewer.digest()

    _files(peer, ["src/a.py"])
    viewer.status()
    assert viewer.digest() is None

    ahead = viewer.db.max_event_id(root) + 100
    viewer.db.set_cursor(viewer.session_id, ahead)
    _files(peer, ["src/b.py"])
    viewer.digest()
    viewer.status()

    assert viewer.db.get_cursor(viewer.session_id) == ahead


# Fix round 1 ----------------------------------------------------------------


def _event_details(runtime: WorkspaceRuntime, kind: str) -> list[dict[str, object]]:
    assert runtime.db is not None
    events = runtime.db.events_after(runtime.workspace.root_key, 0, limit=10_000)
    return [event.detail for event in events if event.kind == kind]


def test_finished_work_line_names_the_feature(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    viewer.digest()
    peer.announce_work("ranking")
    peer.finish_work()

    lines = viewer.digest()

    assert lines is not None
    assert "codex finished ranking" in lines["new"]
    assert _event_details(peer, models.WORK_FINISHED)[-1] == {"client": "codex", "feature": "ranking"}


def test_departed_peer_keeps_client_and_feature(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    viewer = _start(runtimes, repo, env)
    leaver = _start(runtimes, repo, env, client="codex")
    _start(runtimes, repo, env)
    leaver.announce_work("ranking")
    assert leaver.db is not None
    # Simulate the process dying: the ticker of another session prunes the row.
    with leaver.db.lock:
        leaver.db.conn.execute("UPDATE sessions SET pid = ? WHERE session_id = ?", (_dead_pid(), leaver.session_id))
    _settle(viewer)
    assert leaver.session_id not in {row.session_id for row in viewer.live_sessions()}

    digest = viewer.digest()

    assert digest is not None
    assert "codex announced ranking" in digest["new"]
    assert not any("another session" in line for line in digest["new"])


def test_arrival_before_any_tick_is_not_lost(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    viewer = _start(runtimes, repo, env)
    _start(runtimes, repo, env, client="codex")

    digest = viewer.digest()

    assert digest is not None
    assert "codex joined" in digest["new"]


def test_later_page_failure_shows_the_read_prefix_and_advances_past_it(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    viewer.digest()
    assert viewer.db is not None
    root = viewer.workspace.root_key
    before = viewer.db.max_event_id(root)
    _files(peer, [f"src/f{index}.py" for index in range(250)])
    original = viewer.db.events_after

    def second_page_fails(workspace: str, after_id: int, limit: int = 200) -> list[models.EventRow]:
        if after_id != before:
            raise CoordinationUnavailable("page lost")
        return original(workspace, after_id, limit=limit)

    monkeypatch.setattr(viewer.db, "events_after", second_page_fails)

    digest = viewer.digest()

    assert _lines_with(digest, "200 files changed:")
    assert viewer.db.get_cursor(viewer.session_id) == before + 200


def test_malformed_event_row_does_not_block_later_events(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    viewer.digest()
    assert viewer.db is not None
    root = viewer.workspace.root_key
    with viewer.db.lock:
        viewer.db.conn.execute(
            "INSERT INTO events (workspace, kind, session_id, path, detail, created_at)"
            " VALUES (?, ?, NULL, 'src/bad.py', '{bad', 1.0)",
            (root, models.FILE_CHANGED),
        )
    bad_id = viewer.db.max_event_id(root)

    viewer.digest()
    assert viewer.db.get_cursor(viewer.session_id) == bad_id

    _files(peer, ["src/ok.py"])
    digest = viewer.digest()

    assert digest is not None
    assert any("src/ok.py" in line for line in digest["new"])


def test_file_dedup_keeps_first_occurrence_order_at_scale(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    viewer.digest()
    _files(peer, [f"src/f{index % 1500}.py" for index in range(3000)])

    digest = viewer.digest()

    assert _lines_with(digest, "1500 files changed: src/f1499.py, src/f1498.py, src/f1497.py …") != []


# Fix round 2 ----------------------------------------------------------------


def test_arrival_of_a_departed_peer_names_its_client(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    _start(runtimes, repo, env)
    viewer = _start(runtimes, repo, env)
    leaver = _start(runtimes, repo, env)  # client unknown at start, as before MCP initialize
    leaver.note_client("codex", "1.0")
    leaver.announce_work("ranking")
    assert leaver.db is not None
    with leaver.db.lock:
        leaver.db.conn.execute("UPDATE sessions SET pid = ? WHERE session_id = ?", (_dead_pid(), leaver.session_id))
    _settle(viewer)

    digest = viewer.digest()

    assert digest is not None
    assert "codex joined" in digest["new"]
    assert "codex announced ranking" in digest["new"]
    assert not any("another session" in line for line in digest["new"])


def test_arrival_line_shows_a_feature_recorded_on_the_event(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    viewer = _start(runtimes, repo, env)
    _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    viewer.digest()
    assert viewer.db is not None
    viewer.db.append_event(
        viewer.workspace.root_key,
        models.SESSION_STARTED,
        now=time.time(),
        session_id="ghost0000001",
        detail={"client": "codex", "feature": "ranking"},
    )

    digest = viewer.digest()

    assert digest is not None
    assert "codex joined (ranking)" in digest["new"]


def test_work_event_without_a_feature_falls_back_to_the_session_row(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    viewer = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env, client="codex")
    _settle(viewer)
    viewer.digest()
    peer.announce_work("ranking")  # the row now records feature "ranking"
    viewer.digest()
    assert viewer.db is not None
    viewer.db.append_event(
        viewer.workspace.root_key,
        models.WORK_ANNOUNCED,
        now=time.time(),
        session_id=peer.session_id,
        detail={},
    )

    digest = viewer.digest()

    assert digest is not None
    assert "codex announced ranking" in digest["new"]
