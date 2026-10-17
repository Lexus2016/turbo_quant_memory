"""Per-project dispatch locks (2026-10-17).

One process-wide dispatch lock made every project wait for the slowest call
of any other project: a reindex of one large project made every other
agent's tqmemory call time out. Calls now take the lock of their own
project, plus a global-scope lock only when they touch global memory.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from turbo_memory_mcp import server
from turbo_memory_mcp.locks import GLOBAL_LOCK_KEY, DispatchBusy, ScopeLocks


def _environ(tmp_path: Path, project: str) -> dict[str, str]:
    root = tmp_path / project
    root.mkdir(exist_ok=True)
    return {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(root),
        "TQMEMORY_PROJECT_ID": f"project-{project}",
        "TQMEMORY_PROJECT_NAME": project,
    }


class _SlowTool:
    """A tool handler that holds its lock until released."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> dict[str, Any]:
        self.entered.set()
        self.release.wait(10)
        return {"ok": True}


def _fast(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> dict[str, Any]:
    return {"ok": True}


def _hold_in_background(dispatch: Any, tool: str, kwargs: dict[str, Any], slow: _SlowTool) -> threading.Thread:
    thread = threading.Thread(target=lambda: dispatch(tool, kwargs), daemon=True)
    thread.start()
    assert slow.entered.wait(5)
    return thread


def test_slow_call_in_one_project_does_not_block_another_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow = _SlowTool()
    monkeypatch.setitem(server.TOOL_HANDLERS, "index_paths", slow)
    monkeypatch.setitem(server.TOOL_HANDLERS, "health", _fast)
    dispatch = server.make_local_dispatcher(scope_locks=ScopeLocks(), dispatch_lock_timeout=0.2)

    holder = _hold_in_background(dispatch, "index_paths", {"_environ": _environ(tmp_path, "alpha")}, slow)
    try:
        start = time.monotonic()
        assert dispatch("health", {"_environ": _environ(tmp_path, "beta")}) == {"ok": True}
        assert time.monotonic() - start < 1.0
    finally:
        slow.release.set()
        holder.join(10)


def test_second_call_in_same_project_gets_busy_naming_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow = _SlowTool()
    monkeypatch.setitem(server.TOOL_HANDLERS, "index_paths", slow)
    monkeypatch.setitem(server.TOOL_HANDLERS, "health", _fast)
    dispatch = server.make_local_dispatcher(scope_locks=ScopeLocks(), dispatch_lock_timeout=0.2)

    holder = _hold_in_background(dispatch, "index_paths", {"_environ": _environ(tmp_path, "alpha")}, slow)
    try:
        with pytest.raises(DispatchBusy, match="'index_paths' holds the dispatch lock"):
            dispatch("health", {"_environ": _environ(tmp_path, "alpha")})
    finally:
        slow.release.set()
        holder.join(10)
    # The project lock is free again once the holder finished.
    assert dispatch("health", {"_environ": _environ(tmp_path, "alpha")}) == {"ok": True}


def test_global_scope_call_waits_only_for_global_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow = _SlowTool()
    monkeypatch.setitem(server.TOOL_HANDLERS, "semantic_search", slow)
    monkeypatch.setitem(server.TOOL_HANDLERS, "recent_context", _fast)
    dispatch = server.make_local_dispatcher(scope_locks=ScopeLocks(), dispatch_lock_timeout=0.2)

    holder = _hold_in_background(
        dispatch, "semantic_search", {"scope": "global", "_environ": _environ(tmp_path, "alpha")}, slow
    )
    try:
        beta = _environ(tmp_path, "beta")
        assert dispatch("recent_context", {"scope": "project", "_environ": beta}) == {"ok": True}
        with pytest.raises(DispatchBusy, match="'semantic_search' holds the dispatch lock"):
            dispatch("recent_context", {"scope": "hybrid", "_environ": beta})
    finally:
        slow.release.set()
        holder.join(10)


def test_hold_releases_project_lock_when_global_times_out() -> None:
    locks = ScopeLocks()
    global_held = threading.Event()
    release = threading.Event()

    def _hold_global() -> None:
        with locks.hold([GLOBAL_LOCK_KEY], timeout=0, tool="global-holder"):
            global_held.set()
            release.wait(10)

    thread = threading.Thread(target=_hold_global, daemon=True)
    thread.start()
    assert global_held.wait(5)
    try:
        with (
            pytest.raises(DispatchBusy, match="'global-holder' holds the dispatch lock"),
            locks.hold(["p1", GLOBAL_LOCK_KEY], timeout=0.1, tool="x"),
        ):
            pass
        acquired: list[bool] = []
        probe = threading.Thread(target=lambda: acquired.append(locks.lock_for("p1").acquire(timeout=0.5)))
        probe.start()
        probe.join(5)
        assert acquired == [True]
        assert locks.holder("p1") is None
    finally:
        release.set()
        thread.join(10)


def test_hold_rejects_global_before_project_and_two_projects() -> None:
    locks = ScopeLocks()
    with pytest.raises(ValueError), locks.hold([GLOBAL_LOCK_KEY, "p1"], timeout=0.1, tool="x"):
        pass
    with pytest.raises(ValueError), locks.hold(["p1", "p2"], timeout=0.1, tool="x"):
        pass


def test_lock_keys_put_project_first_and_global_only_when_needed() -> None:
    assert server.lock_keys_for("index_paths", {}, "p1") == ["p1"]
    assert server.lock_keys_for("semantic_search", {"scope": "project"}, "p1") == ["p1"]
    assert server.lock_keys_for("semantic_search", {"scope": " Hybrid "}, "p1") == ["p1", GLOBAL_LOCK_KEY]
    assert server.lock_keys_for("remember_note", {"scope": "global"}, "p1") == ["p1", GLOBAL_LOCK_KEY]
    assert server.lock_keys_for("promote_note", {}, "p1") == ["p1", GLOBAL_LOCK_KEY]


def test_unresolvable_project_still_reaches_the_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> dict[str, Any]:
        raise FileNotFoundError("handler's own error")

    def _identity_fails(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("identity lookup failed")

    monkeypatch.setitem(server.TOOL_HANDLERS, "health", _boom)
    monkeypatch.setattr(server, "resolve_project_identity", _identity_fails)
    dispatch = server.make_local_dispatcher(scope_locks=ScopeLocks(), dispatch_lock_timeout=0.2)

    with pytest.raises(FileNotFoundError, match="handler's own error"):
        dispatch("health", {"_cwd": str(tmp_path / "missing"), "_environ": {}})
