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
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from watchdog.observers import Observer

from turbo_memory_mcp.store import resolve_storage_root
from turbo_memory_mcp.workspace import models
from turbo_memory_mcp.workspace import status as status_views
from turbo_memory_mcp.workspace.db import CoordinationDB, CoordinationLockBusy, CoordinationUnavailable, lock_deadline
from turbo_memory_mcp.workspace.journal import Journal, JournalLimits
from turbo_memory_mcp.workspace.liveness import is_process_alive, process_start_time
from turbo_memory_mcp.workspace.models import EventRow, SessionRow, Workspace
from turbo_memory_mcp.workspace.paths import blobs_dir, coord_db_path, is_disabled, resolve_workspace, to_relative
from turbo_memory_mcp.workspace.watcher import WorkspaceWatcher

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver

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
# Shutdown's database work (lease release, session end, close) shares one deadline of this length.
_SHUTDOWN_DB_WAIT_S: Final = 2.0
_CLOSE_THREAD_NAME: Final = "tqmemory-workspace-close"
# How long the deferred closer waits for a busy database lock before it gives up.
_DEFERRED_LOCK_WAIT_S: Final = 30.0
# The lease owner runs the journal's retention and size eviction at most this often.
EVICT_INTERVAL_S: Final = 3600.0
# After a failed watcher start, the session waits this long before the next attempt (no lease, no baseline).
WATCH_RETRY_S: Final = 60.0
_OVER_CAP_META: Final = "journal.over_cap:"
_JOURNAL_OFF: Final = "off"
_JOURNAL_WATCHING: Final = "watching"
_JOURNAL_UNAVAILABLE: Final = "unavailable"


class WorkspaceRuntime:
    """Presence handle for one agent session in one git working tree.

    ``degraded_reason`` holds the most recent failure, or None. The next fully successful
    ``tick`` clears it. Failures are logged, but repeats of one kind at most every 10 minutes.
    """

    def __init__(
        self,
        workspace: Workspace,
        session_id: str,
        *,
        observer_factory: Callable[[], BaseObserver] | None = None,
    ) -> None:
        self.workspace = workspace
        self.session_id = session_id
        self.db: CoordinationDB | None = None
        self.degraded_reason: str | None = None
        # Built in ``start`` when the workspace may be journaled; None otherwise.
        self.journal: Journal | None = None
        self._observer_factory: Callable[[], BaseObserver] = observer_factory or Observer
        self._watcher: WorkspaceWatcher | None = None
        self._journal_error: str | None = None
        self._storage_root: Path | None = None
        self._watch_error: str | None = None
        self._watch_retry_at: float | None = None
        self._last_evict: float | None = None
        self._pid = os.getpid()
        self._proc_start: float | None = None
        self._lifecycle_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._client_noted = False
        # Serializes ticks in this process and guards the database close, so no tick runs on a closed db.
        self._tick_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        # Serializes the check-then-set of this session's cursor, so it never moves backwards.
        self._cursor_lock = threading.Lock()
        self._warn_lock = threading.Lock()
        self._warned_at: dict[str, float] = {}

    @classmethod
    def start(
        cls,
        cwd: Path | str | None = None,
        environ: Mapping[str, str] | None = None,
        *,
        observer_factory: Callable[[], BaseObserver] | None = None,
    ) -> WorkspaceRuntime | None:
        """Register a session for the working tree of ``cwd``; None when the kill switch is on.

        ``observer_factory`` replaces the platform file observer; tests pass a polling observer.
        """

        if is_disabled(environ):
            return None
        session_id = uuid.uuid4().hex[:_SESSION_ID_CHARS]
        try:
            workspace = resolve_workspace(cwd, environ)
        except Exception as exc:
            runtime = cls(_unresolved_workspace(cwd), session_id, observer_factory=observer_factory)
            runtime._degrade(exc)
            return runtime
        runtime = cls(workspace, session_id, observer_factory=observer_factory)
        try:
            storage_root = resolve_storage_root(environ)
            runtime._storage_root = storage_root
            runtime.db = CoordinationDB.open(coord_db_path(storage_root, workspace.project_id))
            runtime._build_journal(storage_root, environ)
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
        """Prune dead sessions and old events, then run the watcher lease decisions.

        The ticker calls this every ``TICK_INTERVAL_S``. Starting a session never runs the watcher: only a tick does.
        """

        self._tick(now, watch=True)

    def _tick(self, now: float | None, *, watch: bool) -> None:
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
                self._on_tick(live, watch=watch)
            except Exception as exc:
                self._degrade(exc)
                return
            self.degraded_reason = None

    @property
    def watcher(self) -> WorkspaceWatcher | None:
        """The running file watcher, present only while this session holds the workspace lease."""

        return self._watcher

    def journal_state(self) -> dict[str, Any]:
        """Workspace-level journal state, derived from the live lease, so every session reports the same owner.

        ``watching`` when a live session holds the lease; ``unavailable`` when the workspace may not be journaled,
        or when this session's watcher failed and it is backing off; ``off`` otherwise. ``over_cap`` is the flag the
        lease owner published after its last eviction.
        """

        owner: str | None = None
        if self.workspace.journal_refusal is not None or self.journal is None:
            state = _JOURNAL_UNAVAILABLE
            reason = self.workspace.journal_refusal or self._journal_error or _UNAVAILABLE_REASON
        else:
            owner = self._live_owner()
            if owner is not None:
                state, reason = _JOURNAL_WATCHING, None
            elif self._in_backoff():
                state, reason = _JOURNAL_UNAVAILABLE, self._watch_error
            else:
                state, reason = _JOURNAL_OFF, None
        return {
            "state": state,
            "reason": reason,
            "owner_session": owner,
            "over_cap": self._over_cap(),
            "this_session_owns": owner is not None and owner == self.session_id,
        }

    def run_journal_eviction(self) -> None:
        """Evict journal versions past retention or over the size cap, then publish the over-cap flag.

        The flag is written to the shared database, so every session reports the same value. Runs on the lease
        owner (the ticker calls it at most once per ``EVICT_INTERVAL_S``); a failure is logged, never raised.
        """

        journal = self.journal
        db = self.db
        if journal is None or db is None:
            return
        self._last_evict = time.monotonic()
        try:
            journal.evict()
            db.set_meta(_over_cap_key(self.workspace.root_key), "1" if journal.over_cap else "0")
        except Exception as exc:
            self._log_failure(exc)

    def describe(self) -> dict[str, Any]:
        """Coordination state for health and server_info. Reads rows only; never raises."""

        live: int | None
        try:
            live = len(self.live_sessions())
        except Exception:
            live = None
        # Read after the rows: a failed read sets degraded_reason, and the state must then say so.
        state = "degraded" if self.degraded_reason else "ok"
        db_path: str | None
        try:
            storage_root = self._storage_root if self._storage_root is not None else resolve_storage_root()
            db_path = str(coord_db_path(storage_root, self.workspace.project_id))
        except Exception:
            db_path = None
        return {
            "enabled": True,
            "state": state,
            "reason": self.degraded_reason,
            "workspace": self.workspace.root_key,
            "db_path": db_path,
            "live_sessions": live,
            "journal": self.journal_state(),
            "coverage": status_views.COVERAGE_TEXT,
        }

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
                "journal": self.journal_state(),
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

        Runs on every memory tool response. "Alone" is a plain read of the sessions table, with no
        process probes (the ticker prunes dead rows). Alone, the cursor moves to the newest event without
        reading the backlog. Otherwise events are read in bounded pages and the cursor advances to the
        highest event actually read, even when a later page fails. Shares the cursor with ``status``.
        Never raises.
        """

        db = self.db
        if db is None:
            return None
        root_key = self.workspace.root_key
        try:
            cursor = db.get_cursor(self.session_id)
            rows = db.list_sessions(root_key)
            me = next((row for row in rows if row.session_id == self.session_id), None)
            alone = not any(row.session_id != self.session_id for row in rows)
            if cursor is None or me is None or alone:
                self._advance_cursor(db, db.max_event_id(root_key))
                return None
            events, newest = self._read_events_after(db, root_key, cursor)
            self._advance_cursor(db, newest)
            lines = status_views.build_digest(events, me, {row.session_id: row for row in rows})
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
            # Every database lock taken below is bounded; a lock held by a stuck writer is skipped, not waited on.
            with lock_deadline(time.monotonic() + _SHUTDOWN_DB_WAIT_S):
                try:
                    self._release_watch()
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
        self._proc_start = process_start_time(pid)
        db.insert_session(
            SessionRow(
                session_id=self.session_id,
                workspace=root_key,
                client_name="unknown",
                client_version="",
                pid=pid,
                proc_start=self._proc_start,
                started_at=now,
                last_activity_at=now,
                feature=None,
                areas=(),
                details=None,
            )
        )
        db.append_event(
            root_key,
            models.SESSION_STARTED,
            now=now,
            session_id=self.session_id,
            detail={"client": "unknown"},
        )
        # The new session starts after every existing event, so it receives no backlog.
        db.set_cursor(self.session_id, db.max_event_id(root_key))
        self._tick(None, watch=False)

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

    def _read_events_after(self, db: CoordinationDB, root_key: str, cursor: int) -> tuple[list[EventRow], int]:
        """Events after ``cursor`` in pages, up to DIGEST_SCAN_LIMIT, and the highest id read (``cursor`` if none)."""

        events: list[EventRow] = []
        after = cursor
        while len(events) < DIGEST_SCAN_LIMIT:
            requested = min(DIGEST_PAGE_SIZE, DIGEST_SCAN_LIMIT - len(events))
            try:
                page = db.events_after(root_key, after, limit=requested)
            except Exception as exc:
                # Deliberate: the prefix already read is shown and the cursor advances past it. Returning None
                # here would silently drop events that were read; the failed page is retried by the next call.
                self._log_failure(exc)
                break
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

    def _build_journal(self, storage_root: Path, environ: Mapping[str, str] | None) -> None:
        """Open the journal for a workspace that may be journaled, and rebuild its index if it is empty.

        A failure leaves ``journal`` None and the journal unavailable; registration still succeeds.
        """

        db = self.db
        if self.workspace.journal_refusal is not None or db is None:
            return
        try:
            journal = Journal(
                self.workspace,
                db,
                blobs_dir(storage_root, self.workspace.project_id),
                JournalLimits.from_environ(environ),
            )
            # Before any watcher exists, so the index is repaired before the first write.
            journal.rebuild_index_if_empty()
        except Exception as exc:
            self._journal_error = str(exc) or type(exc).__name__
            self._log_failure(exc)
            return
        self.journal = journal

    def _on_tick(self, live_sessions: list[SessionRow], *, watch: bool = True) -> None:
        """Lease and watcher decisions, run after each tick with the sessions still alive. Must not call tick.

        ``watch=False`` (registration) only prunes. Decisions are made under the database lock inside the lease
        call; the watcher is started or stopped only after that call has returned, because its flush thread takes
        the same lock.
        """

        db = self.db
        journal = self.journal
        if db is None:
            return
        if self.workspace.journal_refusal is not None or journal is None:
            self._stop_watcher()
            return
        if not watch:
            return
        root_key = self.workspace.root_key
        if len(live_sessions) < 2:
            self._stop_watcher()
            db.release_lease(root_key, self.session_id)
            return
        if self._watcher is not None:
            reason = self._watcher.unhealthy_reason()
            if reason is not None:
                self._fail_watch(reason)
                return
        if self._watcher is None and self._in_backoff():
            # No lease and no baseline while backing off after a failed start.
            return
        # tick has already pruned dead rows, so membership in ``live_sessions`` is the liveness test. Probing the
        # process here would spawn a subprocess while the database lock is held.
        alive_ids = {row.session_id for row in live_sessions}
        acquired = db.try_acquire_lease(
            root_key,
            self.session_id,
            self._pid,
            self._proc_start,
            time.time(),
            owner_alive=lambda row: row.session_id in alive_ids and is_process_alive(row.pid, row.proc_start),
        )
        if not acquired:
            self._stop_watcher()
            return
        if self._watcher is None:
            try:
                self._start_watcher(journal)
            except Exception as exc:
                self._log_failure(exc)
                self._fail_watch(str(exc) or type(exc).__name__)
                return
        self._maybe_evict()

    def _start_watcher(self, journal: Journal) -> None:
        db = self.db
        if db is None:
            raise CoordinationUnavailable("coordination database is not open")
        root_key = self.workspace.root_key
        watcher = WorkspaceWatcher(
            self.workspace,
            journal,
            attribute=lambda path: status_views.infer_attribution(path, self.live_sessions()),
            observer_factory=self._observer_factory,
            content_under=lambda prefix: db.content_paths_under(root_key, prefix),
        )
        watcher.start()
        self._watcher = watcher
        self._watch_error = None
        self._watch_retry_at = None

    def _stop_watcher(self) -> None:
        watcher = self._watcher
        self._watcher = None
        if watcher is not None:
            watcher.stop()

    def _fail_watch(self, reason: str) -> None:
        """Stop the watcher, give up the lease and back off before the next attempt."""

        self._stop_watcher()
        db = self.db
        if db is not None:
            try:
                db.release_lease(self.workspace.root_key, self.session_id)
            except Exception as exc:
                self._log_failure(exc)
        self._watch_error = reason
        self._watch_retry_at = time.monotonic() + WATCH_RETRY_S

    def _in_backoff(self) -> bool:
        return self._watch_retry_at is not None and time.monotonic() < self._watch_retry_at

    def _live_owner(self) -> str | None:
        """The session id holding the lease, when that session is still live; None otherwise. Reads rows only."""

        db = self.db
        if db is None:
            return None
        try:
            lease = db.lease_owner(self.workspace.root_key)
            if lease is None:
                return None
            if any(row.session_id == lease.session_id for row in db.list_sessions(self.workspace.root_key)):
                return lease.session_id
        except Exception as exc:
            self._log_failure(exc)
        return None

    def _over_cap(self) -> bool:
        db = self.db
        if db is None:
            return False
        try:
            return db.get_meta(_over_cap_key(self.workspace.root_key)) == "1"
        except Exception as exc:
            self._log_failure(exc)
            return False

    def _release_watch(self) -> None:
        """Stop the watcher and give up the lease; used on shutdown. Failures degrade without raising."""

        self._stop_watcher()
        db = self.db
        if db is None:
            return
        try:
            db.release_lease(self.workspace.root_key, self.session_id)
        except Exception as exc:
            self._degrade(exc)

    def _maybe_evict(self) -> None:
        now = time.monotonic()
        if self._last_evict is not None and now - self._last_evict < EVICT_INTERVAL_S:
            return
        self.run_journal_eviction()

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

        self._stop_watcher()
        db = self.db
        if db is not None:
            try:
                db.release_lease(self.workspace.root_key, self.session_id)
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
        except CoordinationLockBusy as exc:
            # The lock holder finishes on its own; a daemon thread releases the lease, ends the session and closes
            # the connection once the lock is free, so peers never wait on a row this process no longer serves.
            self._log_failure(exc)
            worker = threading.Thread(
                target=_finish_when_free,
                args=(db, self.workspace.root_key, self.session_id),
                name=_CLOSE_THREAD_NAME,
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            self._degrade(exc)


def _finish_when_free(db: CoordinationDB, root_key: str, session_id: str) -> None:
    """Release the lease, end the session row and close the database once the lock frees.

    Gives up with a log after ``_DEFERRED_LOCK_WAIT_S``. The lock is taken once for all three steps.
    """

    try:
        with lock_deadline(time.monotonic() + _DEFERRED_LOCK_WAIT_S):
            db.release_lease(root_key, session_id)
            row = db.get_session(session_id)
            if row is not None:
                db.end_session(root_key, session_id, now=time.time(), detail=_end_detail(row))
            db.close()
    except Exception:
        logger.warning("workspace coordination gave up finishing session %s after shutdown", session_id, exc_info=True)


def _over_cap_key(root_key: str) -> str:
    return f"{_OVER_CAP_META}{root_key}"


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
