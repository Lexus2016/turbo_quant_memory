"""Per-project coordination database shared by every agent session on one machine.

Several tqmemory processes open the same SQLite file in WAL mode. Within one process a
single ``CoordinationDB`` is shared by threads, so every statement runs under ``lock``.
A corrupt file is quarantined and replaced by a fresh database instead of failing every call;
healing is serialized across processes by an OS lock on a side file.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from turbo_memory_mcp.workspace.models import (
    SESSION_ENDED,
    WORK_ANNOUNCED,
    WORK_FINISHED,
    EventRow,
    LeaseRow,
    SessionRow,
)

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_SCHEMA_VERSION = "1"
_QUARANTINE_MARK = ".corrupt-"
_SIDE_SUFFIXES = ("", "-wal", "-shm")
_CORRUPTION_CODES = frozenset({sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB})
_PRIMARY_RESULT_MASK = 0xFF
_WAL_ATTEMPTS = 3
_WAL_RETRY_SECONDS = 0.05
_MOVE_ATTEMPTS = 5
_MOVE_RETRY_SECONDS = 0.1
_STEM_ATTEMPTS = 1000
_HEAL_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_SECONDS = 0.05

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
  client_name TEXT NOT NULL DEFAULT 'unknown', client_version TEXT NOT NULL DEFAULT '',
  pid INTEGER NOT NULL, proc_start REAL, started_at REAL NOT NULL, last_activity_at REAL NOT NULL,
  feature TEXT, areas TEXT NOT NULL DEFAULT '[]', details TEXT);
CREATE INDEX IF NOT EXISTS sessions_ws ON sessions(workspace);
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL, kind TEXT NOT NULL,
  session_id TEXT, path TEXT, detail TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_ws ON events(workspace, event_id);
CREATE TABLE IF NOT EXISTS cursors (session_id TEXT PRIMARY KEY, event_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS leases (
  workspace TEXT PRIMARY KEY, session_id TEXT NOT NULL, pid INTEGER NOT NULL,
  proc_start REAL, acquired_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS versions (
  version_id INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL, path TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('content', 'deletion')), sha256 TEXT, size INTEGER NOT NULL DEFAULT 0,
  observed_at REAL NOT NULL, attribution TEXT NOT NULL, session_id TEXT);
CREATE INDEX IF NOT EXISTS versions_path ON versions(workspace, path, version_id);
"""


class CoordinationUnavailable(Exception):
    """The coordination database cannot be used; callers must fail open."""


class _Corrupt(Exception):
    """Internal signal: the file on disk is not a healthy SQLite database."""


class CoordinationDB:
    """Thread-safe handle to the coordination database; open it with :meth:`open`.

    ``try_acquire_lease``'s ``owner_alive`` callback must not re-enter this CoordinationDB,
    because ``lock`` is not reentrant.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.lock = threading.Lock()
        self._closed = False

    @classmethod
    def open(cls, path: Path, *, busy_timeout_ms: int = 250) -> CoordinationDB:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CoordinationUnavailable(f"cannot create coordination directory: {exc}") from exc
        # Every open takes the heal lock, so no process connects to a file another process is moving aside.
        with _heal_lock(path):
            try:
                return cls(_open_connection(path, busy_timeout_ms))
            except _Corrupt:
                pass
            except sqlite3.Error as exc:
                raise CoordinationUnavailable(f"cannot open coordination database: {exc}") from exc
            return cls(_quarantine_and_recreate(path, busy_timeout_ms))

    def close(self) -> None:
        with self.lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.conn.close()
            except sqlite3.Error as exc:
                raise CoordinationUnavailable(f"cannot close coordination database: {exc}") from exc

    @contextmanager
    def _use(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            try:
                yield self.conn
            except sqlite3.Error as exc:
                raise CoordinationUnavailable(str(exc)) from exc

    # Sessions -----------------------------------------------------------------

    def insert_session(self, row: SessionRow) -> None:
        with self._use() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, workspace, client_name, client_version, pid, proc_start,"
                " started_at, last_activity_at, feature, areas, details)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.session_id,
                    row.workspace,
                    row.client_name,
                    row.client_version,
                    row.pid,
                    row.proc_start,
                    row.started_at,
                    row.last_activity_at,
                    row.feature,
                    json.dumps(list(row.areas)),
                    row.details,
                ),
            )

    def update_session_client(self, session_id: str, client_name: str, client_version: str) -> None:
        with self._use() as conn:
            conn.execute(
                "UPDATE sessions SET client_name = ?, client_version = ? WHERE session_id = ?",
                (client_name, client_version, session_id),
            )

    def touch_session(self, session_id: str, now: float) -> None:
        with self._use() as conn:
            conn.execute("UPDATE sessions SET last_activity_at = ? WHERE session_id = ?", (now, session_id))

    def delete_session(self, session_id: str) -> None:
        with self._use() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM cursors WHERE session_id = ?", (session_id,))

    def end_session(
        self,
        workspace: str,
        session_id: str,
        *,
        now: float,
        detail: Mapping[str, Any] | None = None,
    ) -> bool:
        """Remove a session, its cursor and record ``SESSION_ENDED`` in one write transaction.

        Returns False, and appends nothing, when the session row was already gone. So when two
        processes end the same dead session, exactly one of them records the event.
        """

        payload = json.dumps(dict(detail or {}), sort_keys=True, default=str)
        with self._use() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                removed = conn.execute(
                    "DELETE FROM sessions WHERE session_id = ? AND workspace = ?",
                    (session_id, workspace),
                ).rowcount
                if removed == 0:
                    conn.execute("COMMIT")
                    return False
                conn.execute("DELETE FROM cursors WHERE session_id = ?", (session_id,))
                conn.execute(
                    "INSERT INTO events (workspace, kind, session_id, path, detail, created_at)"
                    " VALUES (?, ?, ?, NULL, ?, ?)",
                    (workspace, SESSION_ENDED, session_id, payload, now),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            return True

    def list_sessions(self, workspace: str) -> list[SessionRow]:
        with self._use() as conn:
            rows = conn.execute(
                _SESSION_SELECT + " WHERE workspace = ? ORDER BY started_at, session_id",
                (workspace,),
            ).fetchall()
        return [_session_row(row) for row in rows]

    def get_session(self, session_id: str) -> SessionRow | None:
        """Read one session row; decodes only that row, so a damaged peer row cannot affect it."""

        with self._use() as conn:
            row = conn.execute(_SESSION_SELECT + " WHERE session_id = ?", (session_id,)).fetchone()
        return None if row is None else _session_row(row)

    def set_intent(self, session_id: str, feature: str, areas: Sequence[str], details: str | None) -> None:
        with self._use() as conn:
            conn.execute(
                "UPDATE sessions SET feature = ?, areas = ?, details = ? WHERE session_id = ?",
                (feature, json.dumps(list(areas)), details, session_id),
            )

    def clear_intent(self, session_id: str) -> None:
        with self._use() as conn:
            conn.execute(
                "UPDATE sessions SET feature = NULL, areas = '[]', details = NULL WHERE session_id = ?",
                (session_id,),
            )

    def announce_intent(
        self,
        workspace: str,
        session_id: str,
        feature: str,
        areas: Sequence[str],
        details: str | None,
        *,
        now: float,
        detail: Mapping[str, Any],
    ) -> None:
        """Store the announced work and record ``WORK_ANNOUNCED`` in one write transaction."""

        with self._transaction() as conn:
            client, _ = _session_identity(conn, session_id)
            conn.execute(
                "UPDATE sessions SET feature = ?, areas = ?, details = ? WHERE session_id = ?",
                (feature, json.dumps(list(areas)), details, session_id),
            )
            _insert_event(conn, workspace, WORK_ANNOUNCED, session_id, now, {**detail, "client": client})

    def finish_intent(self, workspace: str, session_id: str, *, now: float) -> None:
        """Clear the announced work and record ``WORK_FINISHED`` in one write transaction."""

        with self._transaction() as conn:
            client, feature = _session_identity(conn, session_id)
            conn.execute(
                "UPDATE sessions SET feature = NULL, areas = '[]', details = NULL WHERE session_id = ?",
                (session_id,),
            )
            _insert_event(conn, workspace, WORK_FINISHED, session_id, now, {"client": client, "feature": feature})

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """A write transaction under the lock: committed when the block finishes, rolled back on any error."""

        with self._use() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    # Events -------------------------------------------------------------------

    def append_event(
        self,
        workspace: str,
        kind: str,
        *,
        now: float,
        session_id: str | None = None,
        path: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> int:
        payload = json.dumps(dict(detail or {}), sort_keys=True, default=str)
        with self._use() as conn:
            cursor = conn.execute(
                "INSERT INTO events (workspace, kind, session_id, path, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (workspace, kind, session_id, path, payload, now),
            )
            event_id = cursor.lastrowid
        if event_id is None:  # pragma: no cover - sqlite always reports the rowid of an INSERT
            raise CoordinationUnavailable("event insert returned no row id")
        return event_id

    def events_after(self, workspace: str, after_id: int, limit: int = 200) -> list[EventRow]:
        with self._use() as conn:
            rows = conn.execute(
                _EVENT_SELECT + " WHERE workspace = ? AND event_id > ? ORDER BY event_id LIMIT ?",
                (workspace, after_id, limit),
            ).fetchall()
        return [_event_row(row) for row in rows]

    def events_window(
        self,
        workspace: str,
        kinds: Sequence[str],
        *,
        upper_id: int,
        before_id: int,
        limit: int,
    ) -> list[EventRow]:
        """Newest-first events of ``kinds`` with ``before_id > event_id`` and ``event_id <= upper_id``.

        Callers read ``upper_id`` first (the watermark) and page down with ``before_id``, so events
        appended during a read are never returned and a cursor set to the watermark never skips one.
        """

        placeholders = ", ".join("?" for _ in kinds)
        with self._use() as conn:
            rows = conn.execute(
                _EVENT_SELECT
                + " WHERE workspace = ? AND event_id <= ? AND event_id < ? AND kind IN ("
                + placeholders
                + ")"
                " ORDER BY event_id DESC LIMIT ?",
                (workspace, upper_id, before_id, *kinds, limit),
            ).fetchall()
        return [_event_row(row) for row in rows]

    def max_event_id(self, workspace: str) -> int:
        with self._use() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(event_id), 0) FROM events WHERE workspace = ?", (workspace,)
            ).fetchone()
        return int(row[0])

    def prune_events(self, older_than: float) -> int:
        with self._use() as conn:
            cursor = conn.execute("DELETE FROM events WHERE created_at < ?", (older_than,))
            return cursor.rowcount

    # Cursors ------------------------------------------------------------------

    def get_cursor(self, session_id: str) -> int | None:
        with self._use() as conn:
            row = conn.execute("SELECT event_id FROM cursors WHERE session_id = ?", (session_id,)).fetchone()
        return None if row is None else int(row[0])

    def set_cursor(self, session_id: str, event_id: int) -> None:
        with self._use() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cursors (session_id, event_id) VALUES (?, ?)",
                (session_id, event_id),
            )

    # Leases -------------------------------------------------------------------

    def lease_owner(self, workspace: str) -> LeaseRow | None:
        with self._use() as conn:
            return self._lease_row(conn, workspace)

    def try_acquire_lease(
        self,
        workspace: str,
        session_id: str,
        pid: int,
        proc_start: float | None,
        now: float,
        owner_alive: Callable[[LeaseRow], bool],
    ) -> bool:
        """Take the workspace lease when it is free, ours already, or held by a dead owner.

        ``owner_alive`` runs while the write transaction is open and must not call back into this
        database, because ``lock`` is not reentrant.
        """

        with self._use() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                owner = self._lease_row(conn, workspace)
                if owner is None or (owner.session_id != session_id and not owner_alive(owner)):
                    conn.execute(
                        "INSERT OR REPLACE INTO leases (workspace, session_id, pid, proc_start, acquired_at)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (workspace, session_id, pid, proc_start, now),
                    )
                    acquired = True
                else:
                    acquired = owner.session_id == session_id
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            return acquired

    def release_lease(self, workspace: str, session_id: str) -> None:
        with self._use() as conn:
            conn.execute("DELETE FROM leases WHERE workspace = ? AND session_id = ?", (workspace, session_id))

    @staticmethod
    def _lease_row(conn: sqlite3.Connection, workspace: str) -> LeaseRow | None:
        row = conn.execute(
            "SELECT workspace, session_id, pid, proc_start, acquired_at FROM leases WHERE workspace = ?",
            (workspace,),
        ).fetchone()
        if row is None:
            return None
        return LeaseRow(
            workspace=row[0],
            session_id=row[1],
            pid=row[2],
            proc_start=row[3],
            acquired_at=row[4],
        )


_SESSION_SELECT = (
    "SELECT session_id, workspace, client_name, client_version, pid, proc_start, started_at,"
    " last_activity_at, feature, areas, details FROM sessions"
)


def _insert_event(
    conn: sqlite3.Connection,
    workspace: str,
    kind: str,
    session_id: str | None,
    now: float,
    detail: Mapping[str, Any],
) -> None:
    payload = json.dumps(dict(detail), sort_keys=True, default=str)
    conn.execute(
        "INSERT INTO events (workspace, kind, session_id, path, detail, created_at) VALUES (?, ?, ?, NULL, ?, ?)",
        (workspace, kind, session_id, payload, now),
    )


_EVENT_SELECT = "SELECT event_id, workspace, kind, session_id, path, detail, created_at FROM events"


def _event_row(row: tuple[Any, ...]) -> EventRow:
    return EventRow(
        event_id=row[0],
        workspace=row[1],
        kind=row[2],
        session_id=row[3],
        path=row[4],
        detail=_lenient_object(row[5]),
        created_at=row[6],
    )


def _session_row(row: tuple[Any, ...]) -> SessionRow:
    return SessionRow(
        session_id=row[0],
        workspace=row[1],
        client_name=row[2],
        client_version=row[3],
        pid=row[4],
        proc_start=row[5],
        started_at=row[6],
        last_activity_at=row[7],
        feature=row[8],
        areas=_lenient_areas(row[9]),
        details=row[10],
    )


def _lenient_areas(text: str) -> tuple[str, ...]:
    """Decode a stored ``areas`` value; anything undecodable or not a list reads as no areas.

    The session row itself still counts, so one damaged row never hides presence or blocks pruning.
    """

    try:
        value = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _lenient_object(text: object) -> dict[str, Any]:
    """Decode a stored event detail; anything undecodable or not an object reads as empty.

    One damaged event row must not block the log: readers see it with no detail and move on.
    """

    if not isinstance(text, str):
        return {}
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def _session_identity(conn: sqlite3.Connection, session_id: str) -> tuple[str, str | None]:
    """The client name and feature stored on a session row; ("unknown", None) when the row is gone."""

    row = conn.execute("SELECT client_name, feature FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        return ("unknown", None)
    return (str(row[0]), row[1])


def _open_connection(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    """Open, verify and migrate one connection; raise ``_Corrupt`` when the file itself is damaged."""

    conn = sqlite3.connect(
        path,
        timeout=busy_timeout_ms / 1000,
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    try:
        try:
            result = conn.execute("PRAGMA quick_check").fetchall()
        except sqlite3.DatabaseError as exc:
            if _is_corruption(exc):
                raise _Corrupt(str(exc)) from exc
            raise
        if [tuple(row) for row in result] != [("ok",)]:
            raise _Corrupt("quick_check reported damage")
        _check_schema_version(conn)
        _enable_wal(conn)
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (_SCHEMA_VERSION,),
        )
    except BaseException:
        conn.close()
        raise
    return conn


def _is_corruption(exc: sqlite3.DatabaseError) -> bool:
    # Extended result codes carry the primary code in the low byte (SQLITE_CORRUPT_INDEX 779 = 11 | 3 << 8).
    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & _PRIMARY_RESULT_MASK) in _CORRUPTION_CODES


def _check_schema_version(conn: sqlite3.Connection) -> None:
    # Checked before the schema is applied, so a database written by another format is never modified.
    has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone()
    if has_meta is None:
        return
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is not None and row[0] != _SCHEMA_VERSION:
        raise CoordinationUnavailable(f"coordination database has unsupported schema version {row[0]!r}")


def _enable_wal(conn: sqlite3.Connection) -> None:
    for attempt in range(_WAL_ATTEMPTS):
        mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if str(mode).lower() == "wal":
            return
        if attempt < _WAL_ATTEMPTS - 1:
            time.sleep(_WAL_RETRY_SECONDS)
    # Still not WAL: the rollback journal keeps the data correct, so the database is used as it is.


def _quarantine_and_recreate(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    """Move the damaged database and its side files aside, then create a fresh one. Runs under the heal lock.

    Each attempt moves every file still at the database path; a sidecar that cannot be moved is unlinked,
    and anything still present afterwards is retried after a short pause.
    """

    stem = _fresh_quarantine_stem(path)
    for attempt in range(_MOVE_ATTEMPTS):
        try:
            _clear_db_files(path, stem)
            pending = _db_files_present(path)
        except OSError:
            pending = True  # a failing probe ends this attempt; the next one re-checks from the start
        if not pending:
            break
        if attempt + 1 < _MOVE_ATTEMPTS:
            time.sleep(_MOVE_RETRY_SECONDS)
    else:
        raise CoordinationUnavailable("damaged coordination database could not be moved aside")
    try:
        conn = _open_connection(path, busy_timeout_ms)
    except (_Corrupt, sqlite3.Error) as exc:
        raise CoordinationUnavailable(f"cannot create coordination database: {exc}") from exc
    _prune_quarantine(path)
    return conn


def _clear_db_files(path: Path, stem: str) -> None:
    """One best-effort pass over the files still at the database path; failures are left for the next attempt."""

    for suffix in _SIDE_SUFFIXES:
        source = path.with_name(path.name + suffix)
        if not source.exists():
            continue
        try:
            os.replace(source, path.with_name(stem + suffix))
        except OSError:
            if suffix:
                # A sidecar that cannot be moved is removed, so a fresh database never sits beside a stale WAL.
                try:
                    os.unlink(source)
                except OSError:
                    pass


def _db_files_present(path: Path) -> bool:
    return any(path.with_name(path.name + suffix).exists() for suffix in _SIDE_SUFFIXES)


@contextmanager
def _heal_lock(path: Path) -> Iterator[None]:
    """Serialize healing across processes with an OS lock on ``coord.sqlite.heal-lock``."""

    try:
        fd = os.open(path.with_name(f"{path.name}.heal-lock"), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise CoordinationUnavailable(f"cannot open coordination heal lock: {exc}") from exc
    try:
        _lock_fd(fd)
        try:
            yield
        finally:
            _unlock_fd(fd)
    finally:
        _close_fd(fd)


def _close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass  # the descriptor is gone either way; nothing is left to clean up


def _lock_fd(fd: int) -> None:
    deadline = time.monotonic() + _HEAL_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            if sys.platform == "win32":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise CoordinationUnavailable("timed out waiting for the coordination heal lock") from exc
            time.sleep(_LOCK_POLL_SECONDS)


def _unlock_fd(fd: int) -> None:
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass  # closing the descriptor releases the lock anyway


def _fresh_quarantine_stem(path: Path) -> str:
    """Return a quarantine stem verified unused. A stem whose probes fail is treated as taken; never returns unverified."""

    stamp = time.time_ns()
    for _ in range(_STEM_ATTEMPTS):
        stem = f"{path.name}{_QUARANTINE_MARK}{stamp}"
        try:
            taken = any(path.with_name(stem + suffix).exists() for suffix in _SIDE_SUFFIXES)
        except OSError:
            taken = True
        if not taken:
            return stem
        stamp += 1
    raise CoordinationUnavailable("no verifiable quarantine name for the damaged coordination database")


def _prune_quarantine(path: Path) -> None:
    """Keep only the newest quarantine group; best-effort, never fails ``open``."""

    prefix = f"{path.name}{_QUARANTINE_MARK}"
    try:
        entries = list(path.parent.glob(f"{prefix}*"))
        groups = {_quarantine_group(entry.name) for entry in entries}
        numeric = [group for group in groups if group.removeprefix(prefix).isdigit()]
        keep = max(numeric, key=lambda group: int(group.removeprefix(prefix))) if numeric else None
        for entry in entries:
            if _quarantine_group(entry.name) == keep:
                continue
            try:
                entry.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _quarantine_group(name: str) -> str:
    """Return the stem shared by a quarantined database and its ``-wal``/``-shm`` side files."""

    for suffix in ("-wal", "-shm"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name
