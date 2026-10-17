"""Background completion of search-time index refreshes.

A search brings its project's index up to date before answering, but only up
to a budget; the rest of the work lands here. One worker thread per process
runs the pending projects in batches, each batch under that project's lock,
so a search or a write in the same project waits for one batch at most.

The worker keeps no state about what is done: every batch rediscovers the
pending work from file manifests and the mirror diff. A restart, a failover
or a failed batch therefore loses nothing; the next search reschedules
whatever is still stale.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .locks import ScopeLocks, project_lock_key


@dataclass(frozen=True)
class RefreshProgress:
    """Work a refresh batch left for later."""

    remaining_files: int = 0
    remaining_items: int = 0

    @property
    def pending(self) -> bool:
        return self.remaining_files > 0 or self.remaining_items > 0

    @property
    def total(self) -> int:
        return self.remaining_files + self.remaining_items


# (cwd, environ, max_files, max_items) -> what that batch left.
BatchRunner = Callable[[str | None, Mapping[str, str] | None, int, int], RefreshProgress]


@dataclass
class _Job:
    cwd: str | None
    environ: Mapping[str, str] | None
    progress: RefreshProgress


class IndexRefresher:
    THREAD_NAME = "tqmemory-index-refresher"

    def __init__(
        self,
        scope_locks: ScopeLocks,
        run_batch: BatchRunner,
        *,
        batch_files: int = 50,
        batch_items: int = 1000,
    ) -> None:
        self._locks = scope_locks
        self._run_batch = run_batch
        self._batch_files = batch_files
        self._batch_items = batch_items
        self._cond = threading.Condition()
        self._jobs: dict[str, _Job] = {}
        self._thread: threading.Thread | None = None
        self._stopping = False

    def schedule(
        self,
        project_id: str,
        *,
        cwd: str | None,
        environ: Mapping[str, str] | None,
        progress: RefreshProgress | None = None,
    ) -> None:
        """Queue a project once; a project already queued keeps its job."""
        with self._cond:
            if self._stopping or project_id in self._jobs:
                return
            self._jobs[project_id] = _Job(cwd, dict(environ) if environ is not None else None, progress or RefreshProgress())
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name=self.THREAD_NAME, daemon=True)
                self._thread.start()
            self._cond.notify_all()

    def is_active(self, project_id: str) -> bool:
        with self._cond:
            return project_id in self._jobs

    def status(self, project_id: str) -> dict[str, Any] | None:
        with self._cond:
            job = self._jobs.get(project_id)
            if job is None:
                return None
            return {
                "state": "refreshing",
                "pending_files": job.progress.remaining_files,
                "pending_items": job.progress.remaining_items,
            }

    def wait_idle(self, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: not self._jobs, timeout=timeout)

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop after the current batch; never waits longer than ``timeout``.

        Queued jobs are dropped (the next search reschedules what is still
        stale), and a later ``schedule`` starts a fresh worker.
        """
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _work(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._stopping or bool(self._jobs))
                if self._stopping:
                    self._jobs.clear()
                    self._stopping = False
                    self._thread = None
                    self._cond.notify_all()
                    return
                project_id, job = next(iter(self._jobs.items()))
            try:
                self._run_job(project_id, job)
            except Exception as exc:
                print(
                    f"[tqmemory] background index refresh for {project_id} failed "
                    f"({type(exc).__name__}: {exc}); the next search retries",
                    file=sys.stderr,
                )
            finally:
                with self._cond:
                    self._jobs.pop(project_id, None)
                    self._cond.notify_all()

    def _run_job(self, project_id: str, job: _Job) -> None:
        previous: RefreshProgress | None = None
        while not self._stopping:
            with self._locks.hold([project_lock_key(project_id)], timeout=0, tool="background_refresh"):
                progress = self._run_batch(job.cwd, job.environ, self._batch_files, self._batch_items)
            with self._cond:
                job.progress = progress
            if not progress.pending:
                return
            if previous is not None and progress.total >= previous.total:
                # Nothing got smaller: what is left cannot be mirrored (e.g.
                # unreadable blocks) or keeps growing. Stop; a search reschedules.
                print(
                    f"[tqmemory] background index refresh for {project_id} made no progress "
                    f"({progress.remaining_files} files, {progress.remaining_items} rows left); stopping",
                    file=sys.stderr,
                )
                return
            previous = progress


__all__ = ["BatchRunner", "IndexRefresher", "RefreshProgress"]
