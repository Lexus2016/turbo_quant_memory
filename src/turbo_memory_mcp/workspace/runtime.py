"""Session runtime: registers this MCP process in its workspace and keeps presence current.

Each tqmemory process is one agent session. ``WorkspaceRuntime.start`` inserts a session row
into the workspace's coordination database and a background ticker prunes rows whose process
has exited. Read methods only read rows, so they stay cheap on every tool response; process
liveness is checked in ``tick`` alone.

Every public method fails open: a coordination error is recorded in ``degraded_reason`` and
an empty value is returned instead of an exception.
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
_SHUTDOWN_JOIN_S: Final = 2.0
_SESSION_ID_CHARS: Final = 12


class WorkspaceRuntime:
    """Presence handle for one agent session in one git working tree.

    ``degraded_reason`` holds the most recent coordination failure, or None while
    coordination works. It is not cleared by later successes.
    """

    def __init__(self, workspace: Workspace, session_id: str) -> None:
        self.workspace = workspace
        self.session_id = session_id
        self.db: CoordinationDB | None = None
        self.degraded_reason: str | None = None
        self._lifecycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._client_noted = False
        # Serializes ticks in this process, so the ticker and a direct caller never prune the same row twice.
        self._tick_lock = threading.Lock()

    @classmethod
    def start(
        cls,
        cwd: Path | str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> WorkspaceRuntime | None:
        """Register a session for the working tree of ``cwd``; None when the kill switch is on."""

        if is_disabled(environ):
            return None
        runtime = cls(resolve_workspace(cwd, environ), uuid.uuid4().hex[:_SESSION_ID_CHARS])
        try:
            db_file = coord_db_path(resolve_storage_root(environ), runtime.workspace.project_id)
            runtime.db = CoordinationDB.open(db_file)
        except (CoordinationUnavailable, ValueError) as exc:
            runtime._degrade(exc)
            return runtime
        try:
            runtime._register()
        except CoordinationUnavailable as exc:
            runtime._degrade(exc)
            runtime._withdraw()
            return runtime
        runtime._start_ticker()
        return runtime

    def note_client(self, name: str | None, version: str | None) -> None:
        """Record the client's name and version once, the first time a name is known."""

        db = self.db
        if not name or self._client_noted or db is None:
            return
        try:
            db.update_session_client(self.session_id, name, version or "")
        except CoordinationUnavailable as exc:
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
        except CoordinationUnavailable as exc:
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
                    db.delete_session(row.session_id)
                    db.append_event(
                        root_key,
                        models.SESSION_ENDED,
                        now=stamp,
                        session_id=row.session_id,
                        detail={"client": row.client_name, "feature": row.feature},
                    )
                db.prune_events(stamp - EVENT_RETENTION_S)
                self._on_tick(live)
            except CoordinationUnavailable as exc:
                self._degrade(exc)

    def live_sessions(self) -> list[SessionRow]:
        """Sessions of this workspace as last pruned by ``tick``, including this one."""

        db = self.db
        if db is None:
            return []
        try:
            return db.list_sessions(self.workspace.root_key)
        except CoordinationUnavailable as exc:
            self._degrade(exc)
            return []

    def others(self) -> list[SessionRow]:
        """``live_sessions`` without this session."""

        return [row for row in self.live_sessions() if row.session_id != self.session_id]

    def shutdown(self) -> None:
        """Stop the ticker, remove this session and close the database. Safe to call repeatedly."""

        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(_SHUTDOWN_JOIN_S)
        db = self.db
        if db is None:
            return
        try:
            db.delete_session(self.session_id)
            db.append_event(
                self.workspace.root_key,
                models.SESSION_ENDED,
                now=time.time(),
                session_id=self.session_id,
            )
        except CoordinationUnavailable as exc:
            self._degrade(exc)
        finally:
            self._close_db()

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
        with self._lifecycle_lock:
            if self._closed:
                return
            self._thread = thread
        thread.start()

    def _run_ticker(self) -> None:
        while not self._stop.wait(TICK_INTERVAL_S):
            try:
                self.tick()
            except Exception:
                # The ticker must survive any failure; the next interval retries.
                logger.warning("workspace ticker failed", exc_info=True)

    def _on_tick(self, live_sessions: list[SessionRow]) -> None:
        """Hook run after each tick with the sessions still alive. Runs under the tick lock, so it must not call tick."""

    def _degrade(self, exc: Exception) -> None:
        self.degraded_reason = str(exc) or type(exc).__name__

    def _withdraw(self) -> None:
        """Remove a half-registered session row, best effort, so a live PID does not leave a ghost."""

        db = self.db
        if db is not None:
            try:
                db.delete_session(self.session_id)
            except CoordinationUnavailable as exc:
                self._degrade(exc)
        self._close_db()

    def _close_db(self) -> None:
        db = self.db
        self.db = None
        if db is None:
            return
        try:
            db.close()
        except CoordinationUnavailable as exc:
            self._degrade(exc)
