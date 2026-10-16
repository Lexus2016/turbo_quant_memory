"""Announced work, overlap reporting, status and inferred attribution."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from turbo_memory_mcp.workspace import db as db_module
from turbo_memory_mcp.workspace import models
from turbo_memory_mcp.workspace.db import CoordinationUnavailable
from turbo_memory_mcp.workspace.models import EventRow, SessionRow
from turbo_memory_mcp.workspace.runtime import WorkspaceRuntime
from turbo_memory_mcp.workspace.status import (
    COVERAGE_TEXT,
    STATUS_RECENT_WINDOW_S,
    area_matches,
    areas_overlap,
    change_views,
    collect_changes,
    infer_attribution,
    shared_areas,
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


def _change(event_id: int, path: str, detail: dict[str, Any] | None = None, created_at: float = 0.0) -> EventRow:
    return EventRow(
        event_id=event_id,
        workspace="/ws",
        kind=models.FILE_CHANGED,
        session_id=None,
        path=path,
        detail=detail or {"attribution": "unknown"},
        created_at=created_at,
    )


# Pure helpers ---------------------------------------------------------------


def test_area_matches_prefix_and_glob() -> None:
    assert area_matches("src/retrieval/x.py", "src/retrieval")
    assert not area_matches("src/retrieval_old.py", "src/retrieval")
    assert area_matches("src/a.py", "src/*.py")


def test_whole_tree_area_matches_every_path() -> None:
    assert area_matches("src/retrieval.py", ".")
    assert area_matches("README.md", ".")
    assert infer_attribution("lib/x.py", [_row("c1", "codex", "ranking", (".",))]) == ("inferred", "c1")
    assert areas_overlap(".", "src/a.py")
    assert areas_overlap("src/a.py", ".")
    assert shared_areas(["."], ["src"]) == ["src"]


def test_overlap_is_symmetric_for_containment() -> None:
    assert areas_overlap("src", "src/a.py")
    assert areas_overlap("src/a.py", "src")
    assert shared_areas(["src"], ["src/a.py"]) == ["src/a.py"]
    assert shared_areas(["src/a.py"], ["src"]) == ["src/a.py"]


def test_plain_siblings_with_shared_text_do_not_overlap() -> None:
    assert not areas_overlap("src/a", "src/ab")
    assert not areas_overlap("src/ab", "src/a")


def test_glob_pairs_with_intersecting_prefixes_overlap_in_both_directions() -> None:
    assert areas_overlap("src/*.py", "src/r*")
    assert areas_overlap("src/r*", "src/*.py")
    assert shared_areas(["src/*.py"], ["src/r*"]) == ["src/r*"]
    assert shared_areas(["src/r*"], ["src/*.py"]) == ["src/r*"]


def test_infer_attribution_requires_exactly_one_match() -> None:
    codex = _row("c1", "codex", "ranking", ("src",))
    claude = _row("c2", "claude", "docs", ("docs",))
    assert infer_attribution("src/a.py", [codex, claude]) == ("inferred", "c1")
    assert infer_attribution("src/a.py", [codex, _row("c3", "x", "y", ("src/a.py",))]) == ("unknown", None)
    assert infer_attribution("lib/a.py", [codex, claude]) == ("unknown", None)


def test_inferred_writer_never_stated_as_fact() -> None:
    codex = _row("c1", "codex", "ranking", ("src",))
    hint = writer_hint("inferred", codex)
    assert hint.startswith("inside ")
    assert "announced area" in hint
    assert " changed" not in hint
    assert writer_hint("unknown", None) == "writer unknown"
    assert writer_hint("restore", None) == "restored by an agent"


def test_writer_hint_uses_stored_client_name_and_optional_feature() -> None:
    assert writer_hint("inferred", _row("c1", "Codex", "ranking", ("src",))) == (
        "inside Codex's announced area (ranking)"
    )
    assert writer_hint("inferred", _row("c1", "Codex", "", ("src",))) == "inside Codex's announced area"
    assert writer_hint("inferred", _row("c1", "Codex", None, ("src",))) == "inside Codex's announced area"
    assert writer_hint("inferred", _row("c1", "unknown", "x", ("src",))) == "inside unknown's announced area (x)"


def test_change_hint_prefers_recorded_feature_over_current_feature() -> None:
    session = _row("c1", "codex", "now", ("src",))
    event = _change(1, "src/a.py", {"attribution": "inferred", "session_id": "c1", "feature": "then"})

    views = change_views([event], {"c1": session})

    assert views[0]["writer_hint"] == "inside codex's announced area (then)"


def test_collect_changes_stops_at_scan_limit() -> None:
    pages: list[tuple[int, int]] = []

    def fetch(upper: int, before: int) -> list[EventRow]:
        pages.append((upper, before))
        return [_change(before - offset, "lib/x.py") for offset in (1, 2)]

    matches = collect_changes(fetch, upper_id=100, since=0.0, areas=["src"], scan_limit=4, page_size=2)

    assert matches == []
    assert len(pages) == 2


def test_collect_changes_ignores_events_older_than_window() -> None:
    old = _change(9, "src/a.py", created_at=5.0)
    matches = collect_changes(lambda _u, _b: [old], upper_id=9, since=10.0, areas=None)
    assert matches == []


# Runtime --------------------------------------------------------------------


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


def test_overlap_is_reported_to_the_broader_announcer_too(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    broad = _start(runtimes, repo, env)
    narrow = _start(runtimes, repo, env)
    narrow.announce_work("docs", ["src/retrieval.py"])
    broad.announce_work("ranking", ["src"])

    overlaps = broad.status()["overlaps"]

    assert [(view["session_id"], view["shared_areas"]) for view in overlaps] == [
        (narrow.session_id, ["src/retrieval.py"])
    ]


def test_announce_normalizes_windows_separators(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = _start(runtimes, repo, env)

    result = runtime.announce_work("ranking", ["src\\retrieval.py"])

    assert result["areas"] == ["src/retrieval.py"]


def test_announce_whole_tree_sees_changes_and_overlaps(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = _start(runtimes, repo, env)
    other = _start(runtimes, repo, env)
    other.announce_work("docs", ["docs"])
    assert runtime.db is not None
    runtime.db.append_event(
        runtime.workspace.root_key,
        models.FILE_CHANGED,
        now=time.time(),
        path="lib/anything.py",
        detail={"attribution": "unknown"},
    )

    result = runtime.announce_work("refactor", ["."])

    assert [change["path"] for change in result["recent_changes_in_areas"]] == ["lib/anything.py"]
    assert [view["shared_areas"] for view in result["overlaps"]] == [["docs"]]


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
    assert changes[0]["writer_hint"] == "inside codex's announced area (ranking)"


def test_announce_finds_change_behind_many_unrelated_changes(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = _start(runtimes, repo, env)
    assert runtime.db is not None
    root = runtime.workspace.root_key
    now = time.time()
    runtime.db.append_event(root, models.FILE_CHANGED, now=now, path="src/retrieval.py", detail={})
    for _ in range(200):
        runtime.db.append_event(root, models.FILE_CHANGED, now=now, path="lib/other.py", detail={})

    result = runtime.announce_work("ranking", ["src"])

    assert [change["path"] for change in result["recent_changes_in_areas"]] == ["src/retrieval.py"]


def test_status_cursor_never_skips_event_appended_during_read(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _start(runtimes, repo, env)
    db = runtime.db
    assert db is not None
    root = runtime.workspace.root_key
    original = db.max_event_id
    watermarks: list[int] = []

    def racing_max_event_id(workspace: str) -> int:
        # An event lands between the watermark read and the event read.
        watermark = original(workspace)
        watermarks.append(watermark)
        db.append_event(workspace, models.FILE_CHANGED, now=time.time(), path="src/late.py", detail={})
        return watermark

    monkeypatch.setattr(db, "max_event_id", racing_max_event_id)
    runtime.status()

    assert db.get_cursor(runtime.session_id) == watermarks[-1]
    unseen = [event.path for event in db.events_after(root, watermarks[-1])]
    assert "src/late.py" in unseen


def test_status_does_not_advance_cursor_when_response_cannot_be_built(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]
) -> None:
    runtime = _start(runtimes, repo, env)
    assert runtime.db is not None
    before = runtime.db.get_cursor(runtime.session_id)
    # A timestamp beyond datetime's range cannot be rendered, so building the response fails.
    runtime.db.append_event(
        runtime.workspace.root_key,
        models.FILE_CHANGED,
        now=1e18,
        path="src/huge.py",
        detail={"attribution": "unknown"},
    )

    result = runtime.status()

    assert result["status"] == "degraded"
    assert runtime.db.get_cursor(runtime.session_id) == before


def test_finish_work_clears_announced_work(repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime]) -> None:
    first = _start(runtimes, repo, env)
    second = _start(runtimes, repo, env)
    first.announce_work("ranking", ["src"])
    assert second.announce_work("docs", ["src"])["overlaps"]

    assert first.finish_work() == {"status": "ok"}

    assert second.announce_work("docs", ["src"])["overlaps"] == []
    assert first.status()["you"]["feature"] is None


def test_announce_intent_and_event_commit_together(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _start(runtimes, repo, env)
    assert runtime.announce_work("ranking", ["src"])["status"] == "ok"

    def broken(*_args: object, **_kwargs: object) -> None:
        raise CoordinationUnavailable("event write failed")

    monkeypatch.setattr(db_module, "_insert_event", broken)
    result = runtime.announce_work("docs", ["lib"])

    assert result["status"] == "degraded"
    assert runtime.live_sessions()[0].feature == "ranking"


def test_announce_and_status_fail_open_when_database_fails(
    repo: Path, env: dict[str, str], runtimes: list[WorkspaceRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _start(runtimes, repo, env)
    assert runtime.db is not None

    def broken(*_args: object, **_kwargs: object) -> None:
        raise CoordinationUnavailable("disk gone")

    monkeypatch.setattr(runtime.db, "announce_intent", broken)
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
