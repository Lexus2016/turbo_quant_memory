"""Per-project coordination database shared by every agent session on one machine.

Several tqmemory processes open the same SQLite file in WAL mode. Within one process a
single ``CoordinationDB`` is shared by threads, so every statement runs under ``lock``.
A corrupt file is quarantined and replaced by a fresh database instead of failing every call.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from turbo_memory_mcp.workspace.models import EventRow, LeaseRow, SessionRow

_SCHEMA_VERSION = "1"
_SIDE_SUFFIXES = ("", "-wal", "-shm")
_CORRUPTION_CODES = frozenset({sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB})

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
    """Thread-safe handle to the coordination database; open it with :meth:`open`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.lock = threading.Lock()

    @classmethod
    def open(cls, path: Path, *, busy_timeout_ms: int = 250) -> CoordinationDB:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CoordinationUnavailable(f"cannot create coordination directory: {exc}") from exc
        try:
            return cls(_open_connection(path, busy_timeout_ms))
        except _Corrupt:
            pass
        except sqlite3.Error as exc:
            raise CoordinationUnavailable(f"cannot open coordination database: {exc}") from exc

        _quarantine(path)
        try:
            return cls(_open_connection(path, busy_timeout_ms))
        except (_Corrupt, sqlite3.Error) as exc:
            raise CoordinationUnavailable(f"cannot create coordination database: {exc}") from exc

    def close(self) -> None:
        with self.lock:
            self.conn.close()

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

    def list_sessions(self, workspace: str) -> list[SessionRow]:
        with self._use() as conn:
            rows = conn.execute(
                "SELECT session_id, workspace, client_name, client_version, pid, proc_start, started_at,"
                " last_activity_at, feature, areas, details FROM sessions WHERE workspace = ?"
                " ORDER BY started_at, session_id",
                (workspace,),
            ).fetchall()
        return [
            SessionRow(
                session_id=row[0],
                workspace=row[1],
                client_name=row[2],
                client_version=row[3],
                pid=row[4],
                proc_start=row[5],
                started_at=row[6],
                last_activity_at=row[7],
                feature=row[8],
                areas=tuple(json.loads(row[9])),
                details=row[10],
            )
            for row in rows
        ]

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
                "SELECT event_id, workspace, kind, session_id, path, detail, created_at FROM events"
                " WHERE workspace = ? AND event_id > ? ORDER BY event_id LIMIT ?",
                (workspace, after_id, limit),
            ).fetchall()
        return [
            EventRow(
                event_id=row[0],
                workspace=row[1],
                kind=row[2],
                session_id=row[3],
                path=row[4],
                detail=json.loads(row[5]),
                created_at=row[6],
            )
            for row in rows
        ]

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
            if getattr(exc, "sqlite_errorcode", None) in _CORRUPTION_CODES:
                raise _Corrupt(str(exc)) from exc
            raise
        if [tuple(row) for row in result] != [("ok",)]:
            raise _Corrupt("quick_check reported damage")
        conn.execute("PRAGMA journal_mode = WAL")
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


def _quarantine(path: Path) -> None:
    """Move a damaged database (and its WAL/SHM side files) aside, then drop older quarantined copies."""

    stamp = int(time.time())
    target = f"{path.name}.corrupt-{stamp}"
    for suffix in _SIDE_SUFFIXES:
        source = path.with_name(path.name + suffix)
        try:
            os.replace(source, path.with_name(target + suffix))
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CoordinationUnavailable(f"cannot quarantine damaged coordination database: {exc}") from exc
    _prune_quarantine(path)


def _prune_quarantine(path: Path) -> None:
    quarantined: dict[Path, int] = {}
    for candidate in path.parent.glob(f"{path.name}.corrupt-*"):
        digits = candidate.name.removeprefix(f"{path.name}.corrupt-").split("-", 1)[0]
        if digits.isdigit():
            quarantined[candidate] = int(digits)
    if not quarantined:
        return
    newest = max(quarantined.values())
    for candidate, stamp in quarantined.items():
        if stamp < newest:
            try:
                candidate.unlink()
            except OSError:
                pass
