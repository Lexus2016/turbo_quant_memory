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
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from turbo_memory_mcp.store import resolve_storage_root
from turbo_memory_mcp.workspace import models
from turbo_memory_mcp.workspace.db import CoordinationDB, CoordinationUnavailable
from turbo_memory_mcp.workspace.liveness import is_process_alive, process_start_time
from turbo_memory_mcp.workspace.models import SessionRow, Workspace
from turbo_memory_mcp.workspace.paths import coord_db_path, is_disabled, resolve_workspace

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

    def _on_tick(self, live_sessions: list[SessionRow]) -> None:
        """Hook run after each tick with the sessions still alive. Runs under the tick lock, so it must not call tick."""

    def _degrade(self, exc: Exception) -> None:
        self.degraded_reason = str(exc) or type(exc).__name__
        self._log_failure(exc)

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


def _unresolved_workspace(cwd: Path | str | None) -> Workspace:
    root = Path(cwd) if cwd is not None else Path(os.curdir)
    return Workspace(
        root=root,
        root_key=root.as_posix(),
        project_id="",
        is_git=False,
        journal_refusal=_UNRESOLVED_REFUSAL,
    )
