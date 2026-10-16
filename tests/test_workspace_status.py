"""Announced work, overlap reporting, status and inferred attribution."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from turbo_memory_mcp.workspace import models
from turbo_memory_mcp.workspace.db import CoordinationUnavailable
from turbo_memory_mcp.workspace.models import SessionRow
from turbo_memory_mcp.workspace.runtime import WorkspaceRuntime
from turbo_memory_mcp.workspace.status import (
    COVERAGE_TEXT,
    STATUS_RECENT_WINDOW_S,
    area_matches,
    infer_attribution,
    writer_hint,
)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    isolated = {key: value for key, value in os.environ.items() if not key.startswith("TQMEMORY_")}
    isolated["TQMEMORY_HOME"] = str(tmp_path / "tqmemory-home")
    return isolated


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    (path / "src").mkdir(parents=True)
    (path / "src" / "retrieval.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    return path


@pytest.fixture
def runtimes() -> Iterator[list[WorkspaceRuntime]]:
    started: list[WorkspaceRuntime] = []
    yield started
    for runtime in started:
        runtime.shutdown()


def _start(started: list[WorkspaceRuntime], repo: Path, env: dict[str, str]) -> WorkspaceRuntime:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    started.append(runtime)
    return runtime


def _row(session_id: str, client: str, feature: str | None, areas: tuple[str, ...]) -> SessionRow:
    return SessionRow(
        session_id=session_id,
        workspace="/ws",
        client_name=client,
        client_version="",
        pid=1,
        proc_start=None,
        started_at=0.0,
        last_activity_at=0.0,
        feature=feature,
        areas=areas,
        details=None,
    )


def test_area_matches_prefix_and_glob() -> None:
    assert area_matches("src/retrieval/x.py", "src/retrieval")
    assert not area_matches("src/retrieval_old.py", "src/retrieval")
    assert area_matches("src/a.py", "src/*.py")


def test_infer_attribution_requires_exactly_one_match() -> None:
    codex = _row("c1", "codex", "ranking", ("src",))
    claude = _row("c2", "claude", "docs", ("docs",))
    assert infer_attribution("src/a.py", [codex, claude]) == ("inferred", "c1")
    assert infer_attribution("src/a.py", [codex, _row("c3", "x", "y", ("src/a.py",))]) == ("unknown", None)
    assert infer_attribution("lib/a.py", [codex, claude]) == ("unknown", None)


def test_inferred_writer_never_stated_as_fact() -> None:
    codex = _row("c1", "codex", "ranking", ("src",))
    hint = writer_hint("inferred", codex)
    assert "announced area" in hint
    assert not hint.lower().startswith("codex")
    assert writer_hint("unknown", None) == "writer unknown"
    assert writer_hint("restore", None) == "restored by an agent"


def test_announce_reports_overlap_with_other_session(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    first = _start(runtimes, repo, env)
    second = _start(runtimes, repo, env)
    first.note_client("codex", "1.0")
    assert first.announce_work("ranking", ["src"])["status"] == "ok"

    result: dict[str, Any] = second.announce_work("docs", ["src/retrieval.py"])

    assert result["status"] == "ok"
    assert result["overlaps"] == [
        {
            "session_id": first.session_id,
            "client": "codex",
            "feature": "ranking",
            "shared_areas": ["src/retrieval.py"],
        }
    ]


def test_announce_rejects_area_outside_workspace(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = _start(runtimes, repo, env)
    with pytest.raises(ValueError):
        runtime.announce_work("ranking", ["../x"])
    assert runtime.live_sessions()[0].feature is None


def test_status_lists_other_sessions_and_coverage_text(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    first = _start(runtimes, repo, env)
    second = _start(runtimes, repo, env)
    second.announce_work("docs", ["docs"])

    status = first.status()

    assert [session["feature"] for session in status["sessions"]] == ["docs"]
    assert status["coverage"] == COVERAGE_TEXT
    assert status["journal"] == {"state": "off", "reason": None}


def test_status_reports_recent_change_with_inferred_writer(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    writer = _start(runtimes, repo, env)
    viewer = _start(runtimes, repo, env)
    writer.note_client("codex", "1.0")
    writer.announce_work("ranking", ["src"])
    assert viewer.db is not None
    viewer.db.append_event(
        viewer.workspace.root_key,
        models.FILE_CHANGED,
        now=time.time(),
        path="src/retrieval.py",
        detail={"attribution": "inferred", "session_id": writer.session_id, "version_id": 1},
    )

    changes = viewer.status()["recent_changes"]

    assert len(changes) == 1
    assert changes[0]["path"] == "src/retrieval.py"
    assert changes[0]["attribution"] == "inferred"
    assert "announced area" in changes[0]["writer_hint"]
    assert not changes[0]["writer_hint"].lower().startswith("codex")


def test_finish_work_clears_announced_work(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    first = _start(runtimes, repo, env)
    second = _start(runtimes, repo, env)
    first.announce_work("ranking", ["src"])
    assert second.announce_work("docs", ["src"])["overlaps"]

    assert first.finish_work() == {"status": "ok"}

    assert second.announce_work("docs", ["src"])["overlaps"] == []
    assert first.status()["you"]["feature"] is None


def test_announce_and_status_fail_open_when_database_fails(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _start(runtimes, repo, env)
    assert runtime.db is not None

    def broken(*_args: object, **_kwargs: object) -> None:
        raise CoordinationUnavailable("disk gone")

    monkeypatch.setattr(runtime.db, "set_intent", broken)
    monkeypatch.setattr(runtime.db, "list_sessions", broken)

    assert runtime.announce_work("ranking", ["src"])["status"] == "degraded"
    assert runtime.status()["status"] == "degraded"
    assert runtime.degraded_reason == "disk gone"


def test_methods_return_degraded_after_shutdown(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = _start(runtimes, repo, env)
    runtime.shutdown()

    assert runtime.announce_work("ranking")["status"] == "degraded"
    assert runtime.finish_work()["status"] == "degraded"
    assert runtime.status()["status"] == "degraded"


def test_status_omits_changes_older_than_window(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = _start(runtimes, repo, env)
    assert runtime.db is not None
    stale = time.time() - STATUS_RECENT_WINDOW_S - 60
    runtime.db.append_event(
        runtime.workspace.root_key,
        models.FILE_CHANGED,
        now=stale,
        path="src/old.py",
        detail={"attribution": "unknown", "version_id": 1},
    )

    assert runtime.status()["recent_changes"] == []
