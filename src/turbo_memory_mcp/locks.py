"""Per-project dispatch locks.

One process-wide dispatch lock made every project wait for the slowest call
of any other project: a reindex of one large project made every other
agent's tqmemory call time out. A call now takes the lock of its own
project, and the global-scope lock only when it touches global memory.

Deadlock freedom comes from a fixed order, enforced by ``hold``: at most one
project key, always before ``GLOBAL_LOCK_KEY``.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

GLOBAL_LOCK_KEY = "global"


class DispatchBusy(RuntimeError):
    """A lock stayed held past the dispatch bound.

    A ``RuntimeError`` so callers and the daemon's error reconstruction keep
    treating it like the busy error they already know.
    """


class ScopeLocks:
    """Lazily created reentrant locks keyed by project id, plus the global key.

    Records which tool holds each key, so a busy error can name the blocker.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._holders: dict[str, str | None] = {}

    def lock_for(self, key: str) -> threading.RLock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.RLock()
            return lock

    def holder(self, key: str) -> str | None:
        with self._guard:
            return self._holders.get(key)

    @contextmanager
    def hold(self, keys: Sequence[str], *, timeout: float, tool: str) -> Iterator[None]:
        """Acquire ``keys`` in order within ``timeout`` seconds overall.

        ``timeout <= 0`` waits without a bound. On a timeout the keys already
        taken are released before ``DispatchBusy`` is raised.
        """

        _check_order(keys)
        deadline = time.monotonic() + timeout if timeout > 0 else None
        taken: list[tuple[str, threading.RLock, str | None]] = []
        try:
            for key in keys:
                lock = self.lock_for(key)
                if deadline is None:
                    acquired = lock.acquire()
                else:
                    acquired = lock.acquire(timeout=max(deadline - time.monotonic(), 0.0))
                if not acquired:
                    holder = self.holder(key)
                    blocker = f"{holder!r}" if holder else "another operation"
                    # Log too: the caller only sees its own failed tool, while
                    # the holder's name is what makes the stall diagnosable.
                    print(
                        f"[tqmemory] dispatch lock contention: {tool!r} gave up, held by {blocker}",
                        file=sys.stderr,
                        flush=True,
                    )
                    raise DispatchBusy(
                        f"memory server busy: {blocker} holds the dispatch lock "
                        f"(waited {timeout:g}s); retry this call later"
                    )
                with self._guard:
                    previous = self._holders.get(key)
                    self._holders[key] = tool
                taken.append((key, lock, previous))
            yield
        finally:
            # Restore rather than clear: the locks are reentrant, so a nested
            # hold hands the slot back to its caller.
            for key, lock, previous in reversed(taken):
                with self._guard:
                    self._holders[key] = previous
                lock.release()


def _check_order(keys: Sequence[str]) -> None:
    if not keys:
        raise ValueError("hold() needs at least one lock key")
    if GLOBAL_LOCK_KEY in keys[:-1]:
        raise ValueError(f"{GLOBAL_LOCK_KEY!r} must be the last lock key")
    if sum(1 for key in keys if key != GLOBAL_LOCK_KEY) > 1:
        raise ValueError("a call may hold at most one project lock")


DEFAULT_SCOPE_LOCKS = ScopeLocks()

__all__ = [
    "DEFAULT_SCOPE_LOCKS",
    "GLOBAL_LOCK_KEY",
    "DispatchBusy",
    "ScopeLocks",
]
