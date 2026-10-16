"""Per-project coordination database: schema, concurrency, leases, self-healing and the error contract."""

from __future__ import annotations

import multiprocessing
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from turbo_memory_mcp.workspace import db as db_module
from turbo_memory_mcp.workspace import models
from turbo_memory_mcp.workspace.db import CoordinationDB, CoordinationUnavailable
from turbo_memory_mcp.workspace.models import LeaseRow, SessionRow

WORKSPACE = "/repo/project"
_QUARANTINE_GLOB = "coord.sqlite.corrupt-*"
_GARBAGE = b"not a database" * 100


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


def _quarantined(db_path: Path) -> list[Path]:
    return sorted(db_path.parent.glob(_QUARANTINE_GLOB))


def test_open_creates_schema_and_wal(db: CoordinationDB) -> None:
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {row[0] for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"meta", "sessions", "events", "cursors", "leases", "versions"} <= tables
    assert db.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "1"


def test_concurrent_processes_write_without_errors(db_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_writer, args=(str(db_path), index)) for index in range(4)]
    exitcodes: list[int | None] = []
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=120)
        exitcodes = [process.exitcode for process in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
            process.join(timeout=10)
    assert exitcodes == [0, 0, 0, 0]

    db = CoordinationDB.open(db_path)
    try:
        assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
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
    db_path.write_bytes(_GARBAGE)

    db = CoordinationDB.open(db_path)
    try:
        tables = {row[0] for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "sessions" in tables
    finally:
        db.close()
    assert len(_quarantined(db_path)) == 1

    db_path.write_bytes(_GARBAGE + b"again")
    CoordinationDB.open(db_path).close()
    assert len(_quarantined(db_path)) == 1


def test_unusable_location_raises_coordination_unavailable(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    with pytest.raises(CoordinationUnavailable):
        CoordinationDB.open(blocker / "coord.sqlite")


def test_opener_arriving_mid_heal_waits_and_keeps_the_healed_database(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Healer A has moved the corrupt main file aside; opener B must block on the heal lock, not connect."""

    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(_GARBAGE)
    real_replace = os.replace
    observed: dict[str, Any] = {}
    openers: list[threading.Thread] = []

    def open_from_another_thread() -> None:
        try:
            db = CoordinationDB.open(db_path)
            try:
                observed["sessions"] = db.list_sessions(WORKSPACE)
            finally:
                db.close()
        except BaseException as exc:  # reported to the test thread below
            observed["error"] = exc

    def replace_then_race(src: Any, dst: Any) -> None:
        real_replace(src, dst)
        if Path(src) == db_path and not openers:
            thread = threading.Thread(target=open_from_another_thread)
            openers.append(thread)
            thread.start()
            time.sleep(0.3)
            observed["blocked_mid_heal"] = thread.is_alive()

    monkeypatch.setattr(db_module.os, "replace", replace_then_race)
    CoordinationDB.open(db_path).close()
    openers[0].join(timeout=10)

    assert observed.get("blocked_mid_heal") is True
    assert "error" not in observed
    assert observed["sessions"] == []
    quarantined = _quarantined(db_path)
    assert quarantined
    assert all(path.read_bytes() == _GARBAGE for path in quarantined)


def test_fresh_database_is_created_when_the_fifth_move_attempt_succeeds(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(_GARBAGE)
    real_replace = os.replace
    failures = {"left": 4}

    def blocked_four_times(src: Any, dst: Any) -> None:
        if Path(src) == db_path and failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError("sharing violation")
        real_replace(src, dst)

    monkeypatch.setattr(db_module.os, "replace", blocked_four_times)
    db = CoordinationDB.open(db_path)
    try:
        assert db.list_sessions(WORKSPACE) == []
    finally:
        db.close()
    assert failures["left"] == 0
    assert len(_quarantined(db_path)) == 1


def test_sidecar_blocked_for_move_and_unlink_is_retried_until_healed(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(_GARBAGE)
    wal = db_path.with_name("coord.sqlite-wal")
    wal.write_bytes(b"stale wal")
    real_replace = os.replace
    real_unlink = os.unlink
    blocked = {"replace": 2, "unlink": 1}

    def blocked_wal_replace(src: Any, dst: Any) -> None:
        if str(src).endswith("-wal") and blocked["replace"] > 0:
            blocked["replace"] -= 1
            raise PermissionError("sharing violation")
        real_replace(src, dst)

    def blocked_wal_unlink(path: Any) -> None:
        if str(path).endswith("-wal") and blocked["unlink"] > 0:
            blocked["unlink"] -= 1
            raise PermissionError("sharing violation")
        real_unlink(path)

    monkeypatch.setattr(db_module.os, "replace", blocked_wal_replace)
    monkeypatch.setattr(db_module.os, "unlink", blocked_wal_unlink)
    # Called directly: SQLite discards an invalid WAL during its own open, so the heal must see it first.
    db_module._quarantine_and_recreate(db_path, 250).close()

    assert blocked == {"replace": 0, "unlink": 0}
    assert not wal.exists() or wal.read_bytes() != b"stale wal"
    assert any(path.read_bytes() == _GARBAGE for path in _quarantined(db_path))


def test_presence_probe_failure_is_retried_then_healed(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(_GARBAGE)
    real_exists = Path.exists
    failures = {"left": 2}
    sleeps: list[float] = []

    def flaky_exists(self: Path) -> bool:
        if self == db_path and failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError("stat refused")
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", flaky_exists)
    monkeypatch.setattr(db_module.time, "sleep", sleeps.append)

    db = CoordinationDB.open(db_path)
    try:
        assert db.list_sessions(WORKSPACE) == []
    finally:
        db.close()
    assert failures["left"] == 0
    assert sleeps == [db_module._MOVE_RETRY_SECONDS]
    assert len(_quarantined(db_path)) == 1


def test_permanent_probe_failure_exhausts_into_coordination_unavailable(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(_GARBAGE)

    real_exists = Path.exists

    def refused(self: Path) -> bool:
        # Every path named like the database (main file, sidecars, quarantine stems) fails to stat.
        if self.name.startswith(db_path.name):
            raise PermissionError("stat refused")
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", refused)
    with pytest.raises(CoordinationUnavailable):
        CoordinationDB.open(db_path)


def test_healthy_database_held_open_by_another_connection_is_never_quarantined(db_path: Path) -> None:
    first = CoordinationDB.open(db_path)
    first.insert_session(_session("A"))
    holder = sqlite3.connect(db_path, isolation_level=None)
    try:
        holder.execute("BEGIN")
        holder.execute("SELECT COUNT(*) FROM sessions").fetchone()
        CoordinationDB.open(db_path).close()
    finally:
        holder.close()
    assert _quarantined(db_path) == []
    assert [row.session_id for row in first.list_sessions(WORKSPACE)] == ["A"]
    first.close()


def test_heal_lock_timeout_raises_without_touching_the_damaged_file(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(_GARBAGE)
    monkeypatch.setattr(db_module, "_HEAL_LOCK_TIMEOUT_SECONDS", 0.2)
    fd = os.open(db_path.with_name(f"{db_path.name}.heal-lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        db_module._lock_fd(fd)
        with pytest.raises(CoordinationUnavailable):
            CoordinationDB.open(db_path)
    finally:
        db_module._unlock_fd(fd)
        os.close(fd)
    assert db_path.read_bytes() == _GARBAGE
    assert _quarantined(db_path) == []


def test_quarantine_retries_a_blocked_move_and_gives_up_after_bounded_attempts(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(_GARBAGE)
    real_replace = os.replace
    failures = {"left": 2}

    def flaky_replace(src: Any, dst: Any) -> None:
        if Path(src) == db_path and failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError("sharing violation")
        real_replace(src, dst)

    monkeypatch.setattr(db_module.os, "replace", flaky_replace)
    CoordinationDB.open(db_path).close()
    assert failures["left"] == 0
    assert len(_quarantined(db_path)) == 1

    db_path.write_bytes(_GARBAGE)

    def always_locked(src: Any, dst: Any) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(db_module.os, "replace", always_locked)
    with pytest.raises(CoordinationUnavailable):
        CoordinationDB.open(db_path)
    assert db_path.read_bytes() == _GARBAGE


def test_quarantine_stems_never_collide_and_only_the_newest_group_survives(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path.parent.mkdir(parents=True)
    (db_path.parent / "coord.sqlite.corrupt-junk").write_bytes(b"leftover")
    (db_path.parent / "coord.sqlite.corrupt-5-wal").write_bytes(b"old wal")
    monkeypatch.setattr(db_module.time, "time_ns", lambda: 1_000)

    db_path.write_bytes(_GARBAGE)
    CoordinationDB.open(db_path).close()
    db_path.write_bytes(_GARBAGE)
    CoordinationDB.open(db_path).close()

    quarantined = _quarantined(db_path)
    assert len(quarantined) == 1
    assert quarantined[0].name.removeprefix("coord.sqlite.corrupt-").isdigit()


def test_garbage_database_with_stale_wal_heals_to_a_fresh_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(_GARBAGE)
    db_path.with_name("coord.sqlite-wal").write_bytes(b"stale wal")

    db = CoordinationDB.open(db_path)
    try:
        assert db.list_sessions(WORKSPACE) == []
        assert db.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        db.close()
    # The damaged bytes are preserved in quarantine, never reused for the fresh database.
    assert any(path.read_bytes() == _GARBAGE for path in _quarantined(db_path))


def test_sidecar_that_cannot_be_moved_is_unlinked(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(_GARBAGE)
    wal = db_path.with_name("coord.sqlite-wal")
    wal.write_bytes(b"stale wal")
    real_replace = os.replace

    def refuse_wal(src: Any, dst: Any) -> None:
        if str(src).endswith("-wal"):
            raise PermissionError("sharing violation")
        real_replace(src, dst)

    monkeypatch.setattr(db_module.os, "replace", refuse_wal)
    CoordinationDB.open(db_path).close()
    assert not wal.exists() or wal.read_bytes() != b"stale wal"
    assert len(_quarantined(db_path)) == 1


def test_prune_failure_never_fails_open(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(_GARBAGE)

    def broken_glob(self: Path, pattern: str) -> Iterator[Path]:
        raise OSError("listing refused")

    monkeypatch.setattr(Path, "glob", broken_glob)
    CoordinationDB.open(db_path).close()


def test_unsupported_schema_version_is_refused_and_left_untouched(db_path: Path) -> None:
    CoordinationDB.open(db_path).close()
    raw = sqlite3.connect(db_path, isolation_level=None)
    try:
        raw.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    finally:
        raw.close()

    with pytest.raises(CoordinationUnavailable):
        CoordinationDB.open(db_path)
    assert _quarantined(db_path) == []
    raw = sqlite3.connect(db_path, isolation_level=None)
    try:
        assert raw.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "2"
    finally:
        raw.close()


def test_extended_corruption_codes_are_recognized() -> None:
    class ExtendedCorruption(sqlite3.DatabaseError):
        sqlite_errorcode = 779  # SQLITE_CORRUPT_INDEX: primary code 11

    class NotADatabase(sqlite3.DatabaseError):
        sqlite_errorcode = 26  # SQLITE_NOTADB

    class Locked(sqlite3.OperationalError):
        sqlite_errorcode = 5  # SQLITE_BUSY: not corruption

    assert db_module._is_corruption(ExtendedCorruption("index damaged"))
    assert db_module._is_corruption(NotADatabase("file is not a database"))
    assert not db_module._is_corruption(Locked("locked"))
    assert not db_module._is_corruption(sqlite3.DatabaseError("no code"))


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


def test_delete_session_also_removes_its_cursor(db: CoordinationDB) -> None:
    db.insert_session(_session("A"))
    db.set_cursor("A", 7)
    db.delete_session("A")
    assert db.get_cursor("A") is None


def test_prune_events_removes_only_older_rows(db: CoordinationDB) -> None:
    db.append_event(WORKSPACE, models.SESSION_STARTED, now=100.0)
    kept = db.append_event(WORKSPACE, models.SESSION_ENDED, now=300.0)
    assert db.prune_events(older_than=200.0) == 1
    assert [event.event_id for event in db.events_after(WORKSPACE, 0)] == [kept]


def test_close_is_idempotent_and_wraps_sqlite_errors(db_path: Path) -> None:
    db = CoordinationDB.open(db_path)
    real = db.conn

    class BrokenConnection:
        def close(self) -> None:
            raise sqlite3.OperationalError("disk I/O error")

    db.conn = cast("sqlite3.Connection", BrokenConnection())
    try:
        with pytest.raises(CoordinationUnavailable):
            db.close()
        db.close()
    finally:
        real.close()


def test_close_twice_is_a_no_op(db_path: Path) -> None:
    db = CoordinationDB.open(db_path)
    db.close()
    db.close()


def test_event_kind_constants() -> None:
    assert models.SESSION_STARTED == "session_started"
    assert models.SESSION_ENDED == "session_ended"
    assert models.WORK_ANNOUNCED == "work_announced"
    assert models.WORK_FINISHED == "work_finished"
    assert models.FILE_CHANGED == "file_changed"
    assert models.FILE_DELETED == "file_deleted"


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
    assert _quarantined(db_path) == []
    CoordinationDB.open(db_path).close()
