"""Leased file watcher: a git baseline, then debounced journaling of every changed file in one working tree.

Only the session that holds the workspace lease runs a watcher, so a tree has at most one writer. ``start``
snapshots the dirty and untracked files, then starts a recursive observer on the root. Events are queued as
paths and flushed in debounced batches. A batch drops ``.git`` paths, paths that ``git check-ignore`` reports
and ``.tqmemoryignore`` matches. Every survivor is journaled: an existing file as content, a missing one as a
deletion.

A flush takes the coordination database lock inside ``journal.record``, so the runtime must never start or
stop a watcher while it holds that lock.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from typing import Final, Literal

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from turbo_memory_mcp import ingestion
from turbo_memory_mcp.workspace.journal import Journal
from turbo_memory_mcp.workspace.models import Workspace
from turbo_memory_mcp.workspace.paths import to_relative

logger = logging.getLogger(__name__)

WATCHER_THREAD_NAME: Final = "tqmemory-workspace-watcher"
# Quiet period after the last event before a batch is flushed.
_DEFAULT_DEBOUNCE_S: Final = 0.3
# A steady stream of events still flushes at least this often.
_MAX_DELAY_S: Final = 5.0
# How long the flush thread idles between checks when no event arrives.
_IDLE_WAIT_S: Final = 1.0
_JOIN_TIMEOUT_S: Final = 2.0
_GIT_TIMEOUT_S: Final = 10.0
_OBSERVED_EVENTS: Final = frozenset({"created", "modified", "closed", "deleted", "moved"})
_GIT_DIR_NAME: Final = ".git"


class WorkspaceWatcher:
    """Journals the changed files of one workspace while it runs; see the module docstring.

    ``attribute`` maps a workspace-relative path to ``(attribution, session_id)`` and runs on the flush
    thread without the database lock held. ``start`` and ``stop`` are idempotent, and ``stop`` returns
    within ``_JOIN_TIMEOUT_S`` even when a flush is stuck; the threads are daemons.
    """

    def __init__(
        self,
        workspace: Workspace,
        journal: Journal,
        attribute: Callable[[str], tuple[str, str | None]],
        *,
        debounce_s: float = _DEFAULT_DEBOUNCE_S,
        observer_factory: Callable[[], BaseObserver] = Observer,
    ) -> None:
        self._workspace = workspace
        self._journal = journal
        self._attribute = attribute
        self._debounce_s = debounce_s
        self._observer_factory = observer_factory
        self._lifecycle_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._pending: set[str] = set()
        self._wake = threading.Event()
        self._running = False
        self._observer: BaseObserver | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Record the git baseline, then watch the tree. Raises if the baseline or the observer cannot start."""

        with self._lifecycle_lock:
            if self._running:
                return
            self._baseline()
            stop_event = threading.Event()
            observer = self._observer_factory()
            try:
                observer.schedule(_Handler(self), str(self._workspace.root), recursive=True)
                observer.start()
            except Exception:
                _stop_quietly(observer)
                raise
            thread = threading.Thread(
                target=self._run,
                args=(stop_event,),
                name=WATCHER_THREAD_NAME,
                daemon=True,
            )
            thread.start()
            self._stop_event = stop_event
            self._observer = observer
            self._thread = thread
            self._running = True

    def stop(self) -> None:
        """Stop the observer and the flush thread, waiting at most ``_JOIN_TIMEOUT_S`` for each."""

        with self._lifecycle_lock:
            if not self._running:
                return
            self._running = False
            observer, thread = self._observer, self._thread
            self._observer = None
            self._thread = None
            self._stop_event.set()
        self._wake.set()
        if observer is not None:
            _stop_quietly(observer)
            observer.join(_JOIN_TIMEOUT_S)
        if thread is not None and thread is not threading.current_thread():
            thread.join(_JOIN_TIMEOUT_S)

    def flush_now(self) -> None:
        """Process the queued paths synchronously. Never raises; a failed batch is logged and dropped."""

        with self._flush_lock:
            with self._pending_lock:
                batch, self._pending = self._pending, set()
            if not batch:
                return
            try:
                rels = _relative_paths(self._workspace, batch)
                self._journal_batch(rels, baseline=False)
            except Exception:
                logger.warning("workspace watcher dropped a batch of %d paths", len(batch), exc_info=True)

    def _enqueue(self, paths: Iterable[str]) -> None:
        with self._pending_lock:
            self._pending.update(paths)
        self._wake.set()

    def _run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            if not self._wake.wait(_IDLE_WAIT_S):
                continue
            started = time.monotonic()
            # Quiet-period debounce: flush once no event arrived for the debounce interval, or after the max delay.
            while True:
                self._wake.clear()
                if stop_event.wait(self._debounce_s):
                    return
                if not self._wake.is_set() or time.monotonic() - started >= _MAX_DELAY_S:
                    break
            self.flush_now()

    def _baseline(self) -> None:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=self._workspace.root,
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            raise OSError(f"git status failed with exit code {result.returncode}")
        self._journal_batch(_porcelain_paths(result.stdout), baseline=True)

    def _journal_batch(self, rels: Iterable[str], *, baseline: bool) -> None:
        """Filter a batch and journal the survivors. Ignore-file or git-ignore failures raise and drop the batch."""

        candidates = sorted({rel for rel in rels if not _inside_git_dir(rel)})
        if not candidates:
            return
        patterns = ingestion._load_ignore_patterns(self._workspace.root)
        ignored = self._git_ignored(candidates)
        for rel in candidates:
            if rel in ignored or ingestion._matches_ignore(rel, patterns):
                continue
            try:
                if baseline:
                    self._journal.record(rel)
                    continue
                attribution, session_id = self._attribute(rel)
                kind: Literal["inferred", "unknown"] = "inferred" if attribution == "inferred" else "unknown"
                if (self._workspace.root / rel).is_file():
                    self._journal.record(rel, attribution=kind, session_id=session_id)
                else:
                    self._journal.record_deletion(rel, attribution=kind, session_id=session_id)
            except Exception:
                # One bad path must never stop the flush thread or the observer.
                logger.warning("workspace watcher could not journal %s", rel, exc_info=True)

    def _git_ignored(self, rels: list[str]) -> set[str]:
        payload = "".join(f"{rel}\0" for rel in rels).encode("utf-8", "surrogateescape")
        result = subprocess.run(
            ["git", "check-ignore", "-z", "--stdin"],
            cwd=self._workspace.root,
            input=payload,
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        # Exit 0: some paths are ignored; exit 1: none are; anything else is an error and raises.
        if result.returncode == 1:
            return set()
        if result.returncode != 0:
            raise OSError(f"git check-ignore failed with exit code {result.returncode}")
        return {item for item in result.stdout.decode("utf-8", "surrogateescape").split("\0") if item}


class _Handler(FileSystemEventHandler):
    """Turns observer events into queued paths; directory events are skipped because their children report too."""

    def __init__(self, watcher: WorkspaceWatcher) -> None:
        super().__init__()
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type not in _OBSERVED_EVENTS:
            return
        paths = [os.fsdecode(event.src_path)]
        dest = getattr(event, "dest_path", "")
        if event.event_type == "moved" and dest:
            paths.append(os.fsdecode(dest))
        self._watcher._enqueue(paths)


def _relative_paths(workspace: Workspace, absolute: Iterable[str]) -> list[str]:
    rels: list[str] = []
    for path in absolute:
        try:
            rels.append(to_relative(workspace, path))
        except ValueError:
            # Outside the tree (for example through a symlink): nothing to journal.
            logger.debug("workspace watcher ignores a path outside the tree: %s", path)
    return rels


def _porcelain_paths(output: bytes) -> list[str]:
    """Paths from ``git status --porcelain=v1 -z``; a rename or copy is followed by its original path, skipped here."""

    items = output.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(items):
        item = items[index]
        index += 1
        if len(item) < 4:
            continue
        status, path = item[:2], item[3:]
        if b"R" in status or b"C" in status:
            index += 1
        paths.append(os.fsdecode(path))
    return paths


def _inside_git_dir(rel: str) -> bool:
    # casefold: on case-insensitive filesystems ".GIT" names the git directory too.
    return any(part.casefold() == _GIT_DIR_NAME for part in rel.split("/"))


def _stop_quietly(observer: BaseObserver) -> None:
    try:
        observer.stop()
    except Exception:
        logger.debug("observer stop failed", exc_info=True)
