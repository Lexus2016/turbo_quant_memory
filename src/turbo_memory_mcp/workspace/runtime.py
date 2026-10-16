"""Session runtime: registers this MCP process in its workspace and keeps presence current.

Each tqmemory process is one agent session. ``WorkspaceRuntime.start`` inserts a session row
into the workspace's coordination database and a background ticker prunes rows whose process
has exited. Read methods only read rows, so they stay cheap on every tool response; process
liveness is checked in ``tick`` alone.

Every public method fails open: any failure is recorded in ``degraded_reason`` and an empty
value is returned instead of an exception. ``start`` never raises; it returns None only when
the kill switch is on.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from turbo_memory_mcp.store import resolve_storage_root
from turbo_memory_mcp.workspace import models
from turbo_memory_mcp.workspace import status as status_views
from turbo_memory_mcp.workspace.db import CoordinationDB, CoordinationUnavailable
from turbo_memory_mcp.workspace.liveness import is_process_alive, process_start_time
from turbo_memory_mcp.workspace.models import EventRow, SessionRow, Workspace
from turbo_memory_mcp.workspace.paths import coord_db_path, is_disabled, resolve_workspace, to_relative

logger = logging.getLogger(__name__)

TICK_INTERVAL_S: Final = 5.0
EVENT_RETENTION_S: Final = 86400.0
TICKER_THREAD_NAME: Final = "tqmemory-workspace-ticker"
# Upper bound for shutdown to wait for the ticker thread and for an in-flight tick.
_SHUTDOWN_WAIT_S: Final = 10.0
_END_ATTEMPTS: Final = 3
_END_RETRY_S: Final = 0.1
# Repeats of the same failure kind are logged at most this often.
_WARN_REPEAT_S: Final = 600.0
_SESSION_ID_CHARS: Final = 12
_UNRESOLVED_REFUSAL: Final = "workspace could not be resolved"
_UNAVAILABLE_REASON: Final = "workspace coordination is not available"
# Digest reads events in pages of this size and at most DIGEST_SCAN_LIMIT per call; the rest waits for the next call.
DIGEST_PAGE_SIZE: Final = 200
DIGEST_SCAN_LIMIT: Final = 5000
DIGEST_MORE_HINT: Final = "call workspace_status for details"


class WorkspaceRuntime:
    """Presence handle for one agent session in one git working tree.

    ``degraded_reason`` holds the most recent failure, or None. The next fully successful
    ``tick`` clears it. Failures are logged, but repeats of one kind at most every 10 minutes.
    """

    def __init__(self, workspace: Workspace, session_id: str) -> None:
        self.workspace = workspace
        self.session_id = session_id
        self.db: CoordinationDB | None = None
        self.degraded_reason: str | None = None
        self._lifecycle_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._client_noted = False
        # Serializes ticks in this process and guards the database close, so no tick runs on a closed db.
        self._tick_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        # Sessions live at the last tick; digest() reads this snapshot instead of probing processes.
        self._presence: tuple[SessionRow, ...] = ()
        # Serializes the check-then-set of this session's cursor, so it never moves backwards.
        self._cursor_lock = threading.Lock()
        self._warn_lock = threading.Lock()
        self._warned_at: dict[str, float] = {}

    @classmethod
    def start(
        cls,
        cwd: Path | str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> WorkspaceRuntime | None:
        """Register a session for the working tree of ``cwd``; None when the kill switch is on."""

        if is_disabled(environ):
            return None
        session_id = uuid.uuid4().hex[:_SESSION_ID_CHARS]
        try:
            workspace = resolve_workspace(cwd, environ)
        except Exception as exc:
            runtime = cls(_unresolved_workspace(cwd), session_id)
            runtime._degrade(exc)
            return runtime
        runtime = cls(workspace, session_id)
        try:
            runtime.db = CoordinationDB.open(coord_db_path(resolve_storage_root(environ), workspace.project_id))
            runtime._register()
            runtime._start_ticker()
        except Exception as exc:
            runtime._degrade(exc)
            runtime._withdraw()
        return runtime

    def note_client(self, name: str | None, version: str | None) -> None:
        """Record the client's name and version once, the first time a name is known."""

        if not name:
            return
        with self._client_lock:
            db = self.db
            if self._client_noted or db is None:
                return
            try:
                db.update_session_client(self.session_id, name, version or "")
            except Exception as exc:
                self._degrade(exc)
                return
            self._client_noted = True

    def touch(self) -> None:
        """Refresh this session's last-activity timestamp."""

        db = self.db
        if db is None:
            return
        try:
            db.touch_session(self.session_id, time.time())
        except Exception as exc:
            self._degrade(exc)

    def tick(self, now: float | None = None) -> None:
        """Prune sessions of this workspace whose process has exited, then prune old events."""

        with self._tick_lock:
            db = self.db
            if db is None:
                return
            stamp = time.time() if now is None else now
            root_key = self.workspace.root_key
            try:
                live: list[SessionRow] = []
                for row in db.list_sessions(root_key):
                    if is_process_alive(row.pid, row.proc_start):
                        live.append(row)
                        continue
                    db.end_session(root_key, row.session_id, now=stamp, detail=_end_detail(row))
                self._presence = tuple(live)
                db.prune_events(stamp - EVENT_RETENTION_S)
                self._on_tick(live)
            except Exception as exc:
                self._degrade(exc)
                return
            self.degraded_reason = None

    def live_sessions(self) -> list[SessionRow]:
        """Sessions of this workspace as last pruned by ``tick``, including this one."""

        db = self.db
        if db is None:
            return []
        try:
            return db.list_sessions(self.workspace.root_key)
        except Exception as exc:
            self._degrade(exc)
            return []

    def others(self) -> list[SessionRow]:
        """``live_sessions`` without this session."""

        return [row for row in self.live_sessions() if row.session_id != self.session_id]

    def announce_work(
        self,
        feature: str,
        areas: Sequence[str] | None = None,
        details: str | None = None,
    ) -> dict[str, Any]:
        """Record what this session works on and report overlaps with other sessions.

        Raises ``ValueError`` when an area lies outside the workspace; every other failure degrades.
        """

        normalized = _normalize_areas(self.workspace, areas or ())
        db = self.db
        if db is None:
            return self._unavailable()
        root_key = self.workspace.root_key
        try:
            now = time.time()
            db.announce_intent(
                root_key,
                self.session_id,
                feature,
                normalized,
                details,
                now=now,
                detail={"feature": feature, "areas": list(normalized)},
            )
            rows = db.list_sessions(root_key)
            changes, _ = _recent_changes(db, root_key, now, areas=normalized)
            response: dict[str, Any] = {
                "status": "ok",
                "feature": feature,
                "areas": list(normalized),
                "overlaps": status_views.overlap_views(normalized, _peers(rows, self.session_id)),
                "recent_changes_in_areas": status_views.change_views(changes, _by_id(rows)),
            }
        except Exception as exc:
            self._degrade(exc)
            return self._unavailable()
        return response

    def finish_work(self) -> dict[str, Any]:
        """Clear this session's announced work and record that it finished."""

        db = self.db
        if db is None:
            return self._unavailable()
        try:
            db.finish_intent(self.workspace.root_key, self.session_id, now=time.time())
        except Exception as exc:
            self._degrade(exc)
            return self._unavailable()
        return {"status": "ok"}

    def status(self) -> dict[str, Any]:
        """Workspace overview: this session, its peers, overlaps and recent file changes.

        Advances this session's event cursor to the newest event, so later reads start after it.
        """

        db = self.db
        if db is None:
            return self._unavailable()
        root_key = self.workspace.root_key
        try:
            now = time.time()
            own = db.get_session(self.session_id)
            rows = db.list_sessions(root_key)
            changes, watermark = _recent_changes(db, root_key, now, areas=None)
            areas = own.areas if own is not None else ()
            peers = _peers(rows, self.session_id)
            response: dict[str, Any] = {
                "status": "ok",
                "workspace": root_key,
                "you": {
                    "session_id": self.session_id,
                    "client": own.client_name if own is not None else "unknown",
                    "feature": own.feature if own is not None else None,
                    "areas": list(areas),
                },
                "sessions": [status_views.session_view(row) for row in peers],
                "recent_changes": status_views.change_views(changes, _by_id(rows)),
                "overlaps": status_views.overlap_views(areas, peers),
                "journal": {"state": "off", "reason": None},
                "coverage": status_views.COVERAGE_TEXT,
            }
            # Last step: a response that failed to build must not move the cursor past unseen events.
            self._advance_cursor(db, watermark)
        except Exception as exc:
            self._degrade(exc)
            return self._unavailable()
        return response

    def digest(self) -> dict[str, Any] | None:
        """New lines about other sessions and file changes since this session's last read, or None.

        Runs on every memory tool response, so it never probes processes: whether others are live comes
        from the presence snapshot that ``tick`` keeps. Alone, the cursor moves to the newest event without
        reading the backlog, so a peer that joins later sees only its own arrival. Shares the cursor with
        ``status``. Never raises.
        """

        db = self.db
        if db is None:
            return None
        root_key = self.workspace.root_key
        try:
            cursor = db.get_cursor(self.session_id)
            if cursor is None or not self._has_live_peers():
                self._advance_cursor(db, db.max_event_id(root_key))
                return None
            me = db.get_session(self.session_id)
            if me is None:
                self._advance_cursor(db, db.max_event_id(root_key))
                return None
            events, newest = self._read_events_after(db, root_key, cursor)
            self._advance_cursor(db, newest)
            sessions = {row.session_id: row for row in self._presence}
            lines = status_views.build_digest(events, me, sessions)
        except Exception as exc:
            self._log_failure(exc)
            return None
        if not lines:
            return None
        return {"new": lines, "more": DIGEST_MORE_HINT}

    def shutdown(self) -> None:
        """Stop the ticker, remove this session and close the database. Safe to call repeatedly.

        Waits up to ``_SHUTDOWN_WAIT_S`` for an in-flight tick. If that wait times out, nothing is
        cleaned up and the session stays registered; a later ``shutdown`` call retries the cleanup.
        """

        with self._lifecycle_lock:
            if self._closed:
                return
            thread = self._thread
        self._stop.set()
        deadline = time.monotonic() + _SHUTDOWN_WAIT_S
        if thread is not None and thread is not threading.current_thread():
            thread.join(_remaining(deadline))
        if not self._tick_lock.acquire(timeout=_remaining(deadline)):
            self._degrade(CoordinationUnavailable("shutdown timed out waiting for an active tick"))
            return
        try:
            try:
                self._end_own_session()
            finally:
                self._close_db()
        finally:
            self._tick_lock.release()
        # Only now is cleanup finished, so only now may a later call be a no-op.
        with self._lifecycle_lock:
            self._closed = True

    def _register(self) -> None:
        db = self.db
        if db is None:  # pragma: no cover - start() opens the database before registering
            raise CoordinationUnavailable("coordination database is not open")
        root_key = self.workspace.root_key
        now = time.time()
        pid = os.getpid()
        db.insert_session(
            SessionRow(
                session_id=self.session_id,
                workspace=root_key,
                client_name="unknown",
                client_version="",
                pid=pid,
                proc_start=process_start_time(pid),
                started_at=now,
                last_activity_at=now,
                feature=None,
                areas=(),
                details=None,
            )
        )
        db.append_event(root_key, models.SESSION_STARTED, now=now, session_id=self.session_id)
        # The new session starts after every existing event, so it receives no backlog.
        db.set_cursor(self.session_id, db.max_event_id(root_key))
        self.tick()

    def _start_ticker(self) -> None:
        thread = threading.Thread(target=self._run_ticker, name=TICKER_THREAD_NAME, daemon=True)
        thread.start()
        with self._lifecycle_lock:
            self._thread = thread

    def _run_ticker(self) -> None:
        while not self._stop.wait(TICK_INTERVAL_S):
            try:
                self.tick()
            except Exception as exc:
                # The ticker must survive any failure; the next interval retries.
                self._degrade(exc)

    def _end_own_session(self) -> None:
        """Record this session's graceful end, retrying briefly so a transient lock leaves no phantom row."""

        for attempt in range(_END_ATTEMPTS):
            db = self.db
            if db is None:
                return
            try:
                db.end_session(
                    self.workspace.root_key,
                    self.session_id,
                    now=time.time(),
                    detail=self._own_detail(db),
                )
                return
            except Exception as exc:
                self._degrade(exc)
                if attempt + 1 < _END_ATTEMPTS:
                    time.sleep(_END_RETRY_S)

    def _own_detail(self, db: CoordinationDB) -> dict[str, object]:
        """This session's client and feature for the end event; empty when they cannot be read.

        Only this row is decoded, and a failure here never prevents ending the session.
        """

        try:
            row = db.get_session(self.session_id)
        except Exception as exc:
            self._degrade(exc)
            return {}
        return {} if row is None else _end_detail(row)

    def _has_live_peers(self) -> bool:
        return any(row.session_id != self.session_id for row in self._presence)

    def _read_events_after(self, db: CoordinationDB, root_key: str, cursor: int) -> tuple[list[EventRow], int]:
        """Events after ``cursor`` in pages, up to DIGEST_SCAN_LIMIT, and the highest id read (``cursor`` if none)."""

        events: list[EventRow] = []
        after = cursor
        while len(events) < DIGEST_SCAN_LIMIT:
            requested = min(DIGEST_PAGE_SIZE, DIGEST_SCAN_LIMIT - len(events))
            page = db.events_after(root_key, after, limit=requested)
            events.extend(page)
            if not page:
                break
            after = page[-1].event_id
            if len(page) < requested:
                break
        return events, after

    def _advance_cursor(self, db: CoordinationDB, target: int) -> None:
        """Move this session's event cursor forward to ``target``; a lower or equal target changes nothing."""

        with self._cursor_lock:
            current = db.get_cursor(self.session_id)
            if current is None or target > current:
                db.set_cursor(self.session_id, target)

    def _on_tick(self, live_sessions: list[SessionRow]) -> None:
        """Hook run after each tick with the sessions still alive. Runs under the tick lock, so it must not call tick."""

    def _degrade(self, exc: Exception) -> None:
        self.degraded_reason = str(exc) or type(exc).__name__
        self._log_failure(exc)

    def _unavailable(self) -> dict[str, Any]:
        """The empty value returned by a public method whose work could not be done."""

        return {"status": "degraded", "reason": self.degraded_reason or _UNAVAILABLE_REASON}

    def _log_failure(self, exc: Exception, now: float | None = None) -> None:
        """Log the first failure of each kind, then repeats at most once per ``_WARN_REPEAT_S``."""

        try:
            kind = type(exc).__name__
            stamp = time.monotonic() if now is None else now
            with self._warn_lock:
                last = self._warned_at.get(kind)
                if last is not None and stamp - last < _WARN_REPEAT_S:
                    return
                self._warned_at[kind] = stamp
            logger.warning("workspace coordination degraded (%s)", kind, exc_info=exc)
        except Exception:
            # A failing log handler must never turn a degraded state into an exception for the caller.
            return

    def _withdraw(self) -> None:
        """Remove a half-registered session row, best effort, so a live PID does not leave a ghost."""

        db = self.db
        if db is not None:
            try:
                db.delete_session(self.session_id)
            except Exception as exc:
                self._degrade(exc)
        self._close_db()

    def _close_db(self) -> None:
        db = self.db
        self.db = None
        if db is None:
            return
        try:
            db.close()
        except Exception as exc:
            self._degrade(exc)


def _end_detail(row: SessionRow) -> dict[str, object]:
    return {"client": row.client_name, "feature": row.feature}


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _normalize_areas(workspace: Workspace, areas: Sequence[str]) -> tuple[str, ...]:
    """Workspace-relative POSIX form of each area, deduplicated in order; raises ValueError for an outside path."""

    normalized: list[str] = []
    for area in areas:
        relative = to_relative(workspace, area.replace("\\", "/"))
        if relative not in normalized:
            normalized.append(relative)
    return tuple(normalized)


def _recent_changes(
    db: CoordinationDB,
    root_key: str,
    now: float,
    *,
    areas: Sequence[str] | None,
) -> tuple[list[EventRow], int]:
    """Newest file changes in the status window under ``areas``, and the watermark they were read against.

    The watermark is read first; every page is bounded by it, so the result is a consistent prefix of the log.
    """

    watermark = db.max_event_id(root_key)
    changes = status_views.collect_changes(
        lambda upper, before: db.events_window(
            root_key,
            status_views.CHANGE_KINDS,
            upper_id=upper,
            before_id=before,
            limit=status_views.CHANGE_PAGE_SIZE,
        ),
        upper_id=watermark,
        since=now - status_views.STATUS_RECENT_WINDOW_S,
        areas=areas,
    )
    return changes, watermark


def _by_id(rows: Sequence[SessionRow]) -> dict[str, SessionRow]:
    return {row.session_id: row for row in rows}


def _peers(rows: Sequence[SessionRow], session_id: str) -> list[SessionRow]:
    return [row for row in rows if row.session_id != session_id]


def _unresolved_workspace(cwd: Path | str | None) -> Workspace:
    root = Path(cwd) if cwd is not None else Path(os.curdir)
    return Workspace(
        root=root,
        root_key=root.as_posix(),
        project_id="",
        is_git=False,
        journal_refusal=_UNRESOLVED_REFUSAL,
    )
