"""Per-project coordination database: schema, concurrency, leases and self-healing."""

from __future__ import annotations

import multiprocessing
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from turbo_memory_mcp.workspace import models
from turbo_memory_mcp.workspace.db import CoordinationDB, CoordinationUnavailable
from turbo_memory_mcp.workspace.models import LeaseRow, SessionRow

WORKSPACE = "/repo/project"
_QUARANTINE_GLOB = "coord.sqlite.corrupt-*"


def _session(session_id: str, workspace: str = WORKSPACE) -> SessionRow:
    now = time.time()
    return SessionRow(
        session_id=session_id,
        workspace=workspace,
        client_name="codex",
        client_version="1.0",
        pid=1234,
        proc_start=now - 10,
        started_at=now,
        last_activity_at=now,
        feature=None,
        areas=(),
        details=None,
    )


def _writer(db_path: str, index: int) -> None:
    """Top-level so a spawn child can import it; each child opens its own connection."""

    db = CoordinationDB.open(Path(db_path))
    try:
        for i in range(50):
            db.insert_session(_session(f"s-{index}-{i}"))
            db.append_event(WORKSPACE, models.WORK_ANNOUNCED, now=time.time(), session_id=f"s-{index}-{i}")
    finally:
        db.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "workspace" / "coord.sqlite"


@pytest.fixture
def db(db_path: Path) -> Iterator[CoordinationDB]:
    instance = CoordinationDB.open(db_path)
    yield instance
    instance.close()


def test_open_creates_schema_and_wal(db: CoordinationDB) -> None:
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {row[0] for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"meta", "sessions", "events", "cursors", "leases", "versions"} <= tables
    assert db.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "1"


def test_concurrent_processes_write_without_errors(db_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_writer, args=(str(db_path), index)) for index in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)
    assert [process.exitcode for process in processes] == [0, 0, 0, 0]

    db = CoordinationDB.open(db_path)
    try:
        assert len(db.list_sessions(WORKSPACE)) == 200
        assert db.max_event_id(WORKSPACE) == 200
    finally:
        db.close()


def test_lease_is_exclusive_and_taken_over_from_dead_owner(db: CoordinationDB) -> None:
    now = time.time()
    assert db.try_acquire_lease(WORKSPACE, "A", 1, None, now, owner_alive=lambda row: True) is True
    assert db.try_acquire_lease(WORKSPACE, "B", 2, None, now, owner_alive=lambda row: True) is False
    assert db.try_acquire_lease(WORKSPACE, "B", 2, None, now, owner_alive=lambda row: False) is True

    owner = db.lease_owner(WORKSPACE)
    assert isinstance(owner, LeaseRow)
    assert owner.session_id == "B"

    db.release_lease(WORKSPACE, "B")
    assert db.lease_owner(WORKSPACE) is None


def test_corrupt_file_is_quarantined_and_replaced(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database" * 100)

    db = CoordinationDB.open(db_path)
    try:
        tables = {row[0] for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "sessions" in tables
    finally:
        db.close()
    assert len(list(db_path.parent.glob(_QUARANTINE_GLOB))) == 1

    db_path.write_bytes(b"still not a database" * 100)
    CoordinationDB.open(db_path).close()
    assert len(list(db_path.parent.glob(_QUARANTINE_GLOB))) == 1


def test_unusable_location_raises_coordination_unavailable(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    with pytest.raises(CoordinationUnavailable):
        CoordinationDB.open(blocker / "coord.sqlite")


def test_events_after_and_cursor(db: CoordinationDB) -> None:
    now = time.time()
    first = db.append_event(WORKSPACE, models.WORK_ANNOUNCED, now=now, session_id="A", detail={"n": 1})
    second = db.append_event(WORKSPACE, models.FILE_CHANGED, now=now, session_id="A", path="src/a.py")
    third = db.append_event(WORKSPACE, models.FILE_DELETED, now=now, session_id="B", path="src/b.py")

    events = db.events_after(WORKSPACE, first)
    assert [event.event_id for event in events] == [second, third]
    assert events[0].path == "src/a.py"
    assert db.events_after(WORKSPACE, first)[0].detail == {}
    assert db.events_after(WORKSPACE, 0)[0].detail == {"n": 1}

    assert db.get_cursor("A") is None
    db.set_cursor("A", second)
    db.set_cursor("A", third)
    assert db.get_cursor("A") == third
    assert db.get_cursor("nobody") is None


def test_session_and_intent_round_trip(db: CoordinationDB) -> None:
    db.insert_session(_session("A"))
    db.update_session_client("A", "claude-code", "2.1")
    db.set_intent("A", "billing", ["src/billing", "tests"], "refund flow")
    db.touch_session("A", time.time() + 5)

    (row,) = db.list_sessions(WORKSPACE)
    assert row.client_name == "claude-code"
    assert row.client_version == "2.1"
    assert row.feature == "billing"
    assert row.areas == ("src/billing", "tests")
    assert row.details == "refund flow"

    db.clear_intent("A")
    (row,) = db.list_sessions(WORKSPACE)
    assert row.feature is None
    assert row.areas == ()

    db.delete_session("A")
    assert db.list_sessions(WORKSPACE) == []


def test_prune_events_removes_only_older_rows(db: CoordinationDB) -> None:
    db.append_event(WORKSPACE, models.SESSION_STARTED, now=100.0)
    kept = db.append_event(WORKSPACE, models.SESSION_ENDED, now=300.0)
    assert db.prune_events(older_than=200.0) == 1
    assert [event.event_id for event in db.events_after(WORKSPACE, 0)] == [kept]


def test_event_kind_constants() -> None:
    assert models.SESSION_STARTED == "session_started"
    assert models.SESSION_ENDED == "session_ended"
    assert models.WORK_ANNOUNCED == "work_announced"
    assert models.WORK_FINISHED == "work_finished"
    assert models.FILE_CHANGED == "file_changed"
    assert models.FILE_DELETED == "file_deleted"


def test_sqlite_error_codes_are_exposed() -> None:
    # Self-healing keys off these codes; Python 3.11+ exposes them as module constants.
    assert sys.version_info >= (3, 11)
    assert sqlite3.SQLITE_NOTADB == 26
    assert sqlite3.SQLITE_CORRUPT == 11


def test_lock_contention_never_quarantines_a_healthy_database(db_path: Path) -> None:
    CoordinationDB.open(db_path).close()
    holder = sqlite3.connect(db_path, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoordinationUnavailable):
            CoordinationDB.open(db_path, busy_timeout_ms=50)
        holder.execute("ROLLBACK")
    finally:
        holder.close()
    assert list(db_path.parent.glob(_QUARANTINE_GLOB)) == []
    CoordinationDB.open(db_path).close()
