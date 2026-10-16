"""Leased file watcher: a git baseline, then debounced journaling of changed files in one working tree.

Only the session that holds the workspace lease runs a watcher, so a tree has at most one writer. ``start``
snapshots the dirty and untracked files, then starts a recursive observer on the root. Events are queued as
paths and flushed in debounced batches. A batch drops ``.git`` paths, paths that ``git check-ignore`` reports
and ``.tqmemoryignore`` matches. Every survivor is journaled: an existing file as content; a missing file as a
deletion, but only when the journal already holds content for it or HEAD tracks it, so a temp file created and
removed inside one burst leaves nothing behind. A removed directory records a deletion for each journaled file
under it.

Git runs with ``--no-optional-locks`` and ``GIT_OPTIONAL_LOCKS=0``: the watcher only reads the repository and
never writes an index lock into it.

A flush takes the coordination database lock inside ``journal.record``, so the runtime must never start or
stop a watcher while it holds that lock. ``stop`` bounds every blocking step: the observer stop, the flush
thread join and a final drain of the queued paths each run on a helper thread with a timeout.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from functools import partial
from pathlib import Path
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
_HELPER_THREAD_NAME: Final = "tqmemory-workspace-watcher-helper"
# Quiet period after the last event before a batch is flushed.
_DEFAULT_DEBOUNCE_S: Final = 0.3
# A steady stream of events still flushes at least this often.
_MAX_DELAY_S: Final = 5.0
# How long the flush thread idles between checks when no event arrives.
_IDLE_WAIT_S: Final = 1.0
# Upper bound for every blocking step of stop, and for each helper thread.
_JOIN_TIMEOUT_S: Final = 2.0
_GIT_TIMEOUT_S: Final = 10.0
# Paths per git command, so one batch never exceeds argument-list limits.
_GIT_CHUNK: Final = 200
_OBSERVED_EVENTS: Final = frozenset({"created", "modified", "closed", "deleted", "moved"})
_GONE_DIRECTORY_EVENTS: Final = frozenset({"deleted", "moved"})
_GIT_DIR_NAME: Final = ".git"

ContentUnder = Callable[[str], list[str]]
Attribute = Callable[[str], tuple[str, str | None]]


class WorkspaceWatcher:
    """Journals the changed files of one workspace while it runs; see the module docstring.

    ``attribute`` maps a workspace-relative path to ``(attribution, session_id)`` and runs on the flush
    thread without the database lock held. ``content_under`` lists the journaled content paths under a
    directory; without it, a removed directory records nothing. ``start`` and ``stop`` are idempotent.
    ``unhealthy_reason`` reports a dead observer or flush thread so the owner can release the lease.
    """

    def __init__(
        self,
        workspace: Workspace,
        journal: Journal,
        attribute: Attribute,
        *,
        debounce_s: float = _DEFAULT_DEBOUNCE_S,
        observer_factory: Callable[[], BaseObserver] = Observer,
        content_under: ContentUnder | None = None,
    ) -> None:
        self._workspace = workspace
        self._journal = journal
        self._attribute = attribute
        self._debounce_s = debounce_s
        self._observer_factory = observer_factory
        self._content_under = content_under
        self._lifecycle_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._pending: set[str] = set()
        self._pending_dirs: set[str] = set()
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
            observer = self._observer_factory()
            try:
                observer.schedule(_Handler(self), str(self._workspace.root), recursive=True)
                observer.start()
            except Exception:
                _run_bounded(lambda: _stop_and_join(observer), "observer stop after a failed start")
                raise
            stop_event = threading.Event()
            thread = threading.Thread(target=self._run, args=(stop_event,), name=WATCHER_THREAD_NAME, daemon=True)
            thread.start()
            self._stop_event = stop_event
            self._observer = observer
            self._thread = thread
            self._running = True

    def stop(self) -> None:
        """Stop the observer, join the flush thread and drain the queued paths; each step is bounded."""

        with self._lifecycle_lock:
            if not self._running:
                return
            self._running = False
            observer, thread = self._observer, self._thread
            self._observer = None
            self._thread = None
            self._stop_event.set()
        if observer is not None:
            _run_bounded(lambda: _stop_and_join(observer), "observer stop")
        self._wake.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(_JOIN_TIMEOUT_S)
        # The observer is stopped first, so nothing new arrives; the drain journals what it queued before that.
        _run_bounded(self.flush_now, "final flush")

    def unhealthy_reason(self) -> str | None:
        """Why the running watcher can no longer journal, or None while it is healthy."""

        with self._lifecycle_lock:
            if not self._running:
                return "watcher is not running"
            observer, thread = self._observer, self._thread
        if observer is None or not observer.is_alive():
            return "file observer thread stopped"
        # The observer thread can outlive its emitters; without a live emitter no event will arrive.
        emitters = observer.emitters
        if not emitters or any(not emitter.is_alive() for emitter in emitters):
            return "file observer emitter stopped"
        if thread is None or not thread.is_alive():
            return "watcher flush thread stopped"
        if not self._workspace.root.is_dir():
            return "workspace root is missing"
        return None

    def flush_now(self) -> None:
        """Process the queued paths and directories synchronously. Never raises; failures are logged."""

        with self._flush_lock:
            with self._pending_lock:
                batch, self._pending = self._pending, set()
                gone, self._pending_dirs = self._pending_dirs, set()
            if batch:
                try:
                    self._journal_batch(_relative_paths(self._workspace, batch), baseline=False)
                except Exception:
                    logger.warning("workspace watcher dropped a batch of %d paths", len(batch), exc_info=True)
            for directory in sorted(gone):
                try:
                    self._journal_gone_directory(directory)
                except Exception:
                    logger.warning("workspace watcher could not handle removed directory %s", directory, exc_info=True)

    def _enqueue(self, paths: Iterable[str]) -> None:
        with self._pending_lock:
            self._pending.update(paths)
        self._wake.set()

    def _enqueue_gone_directory(self, path: str) -> None:
        with self._pending_lock:
            self._pending_dirs.add(path)
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
        result = _git(self._workspace.root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
        if result.returncode != 0:
            raise OSError(f"git status failed with exit code {result.returncode}")
        self._journal_batch(_porcelain_paths(result.stdout), baseline=True)

    def _journal_batch(self, rels: Iterable[str], *, baseline: bool) -> None:
        """Filter a batch and journal the survivors. Git or ignore-file failures raise and drop the batch."""

        candidates = sorted({rel for rel in rels if not _inside_git_dir(rel)})
        if not candidates:
            return
        patterns = ingestion._load_ignore_patterns(self._workspace.root)
        ignored = self._git_ignored(candidates)
        kept = [rel for rel in candidates if rel not in ignored and not ingestion._matches_ignore(rel, patterns)]
        if baseline:
            for rel in kept:
                _guard(rel, partial(self._journal.record, rel))
            return
        missing = [rel for rel in kept if not (self._workspace.root / rel).is_file()]
        head_tracked = self._tracked_in_head(missing)
        for rel in kept:
            _guard(rel, partial(self._journal_one, rel, head_tracked))

    def _journal_one(self, rel: str, head_tracked: set[str]) -> None:
        attribution, session_id = self._attribute(rel)
        kind: Literal["inferred", "unknown"] = "inferred" if attribution == "inferred" else "unknown"
        if (self._workspace.root / rel).is_file():
            self._journal.record(rel, attribution=kind, session_id=session_id)
            return
        # A deletion is news only for a path the journal has content for, or one that a commit tracks.
        if not self._latest_is_content(rel) and rel not in head_tracked:
            return
        self._journal.record_deletion(rel, attribution=kind, session_id=session_id)

    def _journal_gone_directory(self, absolute: str) -> None:
        if self._content_under is None:
            return
        rel = to_relative(self._workspace, absolute)
        # Always enumerate: a directory that was moved away and recreated still lost its journaled files.
        if rel == "." or _inside_git_dir(rel):
            return
        for child in self._content_under(rel):
            if (self._workspace.root / child).exists():
                continue
            _guard(child, partial(self._journal_gone_child, child))

    def _journal_gone_child(self, child: str) -> None:
        attribution, session_id = self._attribute(child)
        kind: Literal["inferred", "unknown"] = "inferred" if attribution == "inferred" else "unknown"
        self._journal.record_deletion(child, attribution=kind, session_id=session_id)

    def _latest_is_content(self, rel: str) -> bool:
        versions = self._journal.history(rel, limit=1)
        return bool(versions) and versions[0].kind == "content"

    def _tracked_in_head(self, rels: list[str]) -> set[str]:
        """The subset of ``rels`` that HEAD tracks. Nothing is tracked before the first commit."""

        if not rels:
            return set()
        head = _git(self._workspace.root, ["rev-parse", "--verify", "--quiet", "HEAD"])
        if head.returncode == 1:
            return set()
        if head.returncode != 0:
            raise OSError(f"git rev-parse failed with exit code {head.returncode}")
        tracked: set[str] = set()
        for start in range(0, len(rels), _GIT_CHUNK):
            chunk = rels[start : start + _GIT_CHUNK]
            result = _git(
                self._workspace.root,
                ["ls-tree", "-z", "--name-only", "HEAD", "--", *chunk],
                literal_pathspecs=True,
            )
            if result.returncode != 0:
                raise OSError(f"git ls-tree failed with exit code {result.returncode}")
            tracked.update(_split_nul(result.stdout))
        return tracked

    def _git_ignored(self, rels: list[str]) -> set[str]:
        payload = b"".join(os.fsencode(rel) + b"\0" for rel in rels)
        result = _git(self._workspace.root, ["check-ignore", "-z", "--stdin"], stdin=payload)
        # Exit 0: some paths are ignored; exit 1: none are; anything else is an error and raises.
        if result.returncode == 1:
            return set()
        if result.returncode != 0:
            raise OSError(f"git check-ignore failed with exit code {result.returncode}")
        return set(_split_nul(result.stdout))


class _Handler(FileSystemEventHandler):
    """Turns observer events into queued work. Removed or moved-away directories queue a directory check."""

    def __init__(self, watcher: WorkspaceWatcher) -> None:
        super().__init__()
        self._watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        event_type = event.event_type
        if event.is_directory:
            if event_type in _GONE_DIRECTORY_EVENTS:
                self._watcher._enqueue_gone_directory(os.fsdecode(event.src_path))
            return
        if event_type not in _OBSERVED_EVENTS:
            return
        paths = [os.fsdecode(event.src_path)]
        dest = getattr(event, "dest_path", "")
        if event_type == "moved" and dest:
            paths.append(os.fsdecode(dest))
        self._watcher._enqueue(paths)


def _git(
    root: Path | str,
    args: list[str],
    *,
    stdin: bytes | None = None,
    literal_pathspecs: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    """One git command that never takes optional locks, so the repository is only read.

    ``literal_pathspecs`` is set only for commands that take pathspecs; ``check-ignore`` rejects the magic.
    """

    prefix = ["--literal-pathspecs"] if literal_pathspecs else []
    return subprocess.run(
        ["git", "--no-optional-locks", *prefix, *args],
        cwd=root,
        input=stdin,
        capture_output=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )


def _run_bounded(action: Callable[[], None], label: str) -> bool:
    """Run ``action`` on a daemon helper thread for at most ``_JOIN_TIMEOUT_S``; abandon it on timeout."""

    def target() -> None:
        try:
            action()
        except Exception:
            logger.warning("workspace watcher %s failed", label, exc_info=True)

    helper = threading.Thread(target=target, name=_HELPER_THREAD_NAME, daemon=True)
    helper.start()
    helper.join(_JOIN_TIMEOUT_S)
    if helper.is_alive():
        logger.warning("workspace watcher abandoned %s after %.1f s", label, _JOIN_TIMEOUT_S)
        return False
    return True


def _stop_and_join(observer: BaseObserver) -> None:
    observer.stop()
    if observer.is_alive():
        observer.join(_JOIN_TIMEOUT_S)


def _guard(rel: str, action: Callable[[], object]) -> None:
    """Run one journal write; a failure is logged and never stops the batch or the flush thread."""

    try:
        action()
    except Exception:
        logger.warning("workspace watcher could not journal %s", rel, exc_info=True)


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


def _split_nul(output: bytes) -> list[str]:
    return [os.fsdecode(item) for item in output.split(b"\0") if item]


def _inside_git_dir(rel: str) -> bool:
    # casefold: on case-insensitive filesystems ".GIT" names the git directory too.
    return any(part.casefold() == _GIT_DIR_NAME for part in rel.split("/"))
