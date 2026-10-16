"""Compact status digest: silence when alone, one line per event, bounded output and one shared cursor."""

from __future__ import annotations

import os
import subprocess
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
