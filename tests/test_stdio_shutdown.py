"""Client closing the stdio pipe exits cleanly instead of crashing the daemon.

Reported by Alisa / Hermes Agent (Nous Research): when the MCP client closes the
stdio pipe (restart, session reload, graceful shutdown), the anyio stdio
transport raises ``BrokenPipeError`` — usually wrapped in a
``BaseExceptionGroup`` by the transport TaskGroup — which previously propagated
out of the entry points and crashed the daemon with an unhandled
``ExceptionGroup`` traceback. The entry points now swallow only the benign
disconnect and re-raise anything else, so real failures are never masked.
"""

from __future__ import annotations

import errno

import pytest

from turbo_memory_mcp import server
from turbo_memory_mcp.daemon import BootstrapResult


class _FakeServer:
    """Stand-in for build_server(...) whose .run() raises a chosen error."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def run(self, *, transport: str) -> None:
        raise self._exc


def _leaves(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            yield from _leaves(sub)
    else:
        yield exc


# --- helper: _reraise_unless_stdio_disconnect ------------------------------


def test_lone_broken_pipe_is_swallowed() -> None:
    assert server._reraise_unless_stdio_disconnect(BrokenPipeError()) is None


def test_lone_connection_reset_is_swallowed() -> None:
    assert server._reraise_unless_stdio_disconnect(ConnectionResetError()) is None


def test_group_of_only_broken_pipe_is_swallowed() -> None:
    group = BaseExceptionGroup("transport", [BrokenPipeError()])
    assert server._reraise_unless_stdio_disconnect(group) is None


def test_nested_group_of_disconnects_is_swallowed() -> None:
    group = BaseExceptionGroup(
        "outer",
        [BaseExceptionGroup("inner", [BrokenPipeError(), ConnectionResetError()])],
    )
    assert server._reraise_unless_stdio_disconnect(group) is None


def test_lone_real_error_is_reraised() -> None:
    with pytest.raises(ValueError, match="boom"):
        server._reraise_unless_stdio_disconnect(ValueError("boom"))


def test_mixed_group_reraises_only_the_real_error() -> None:
    group = BaseExceptionGroup("mixed", [BrokenPipeError(), ValueError("boom")])
    with pytest.raises(BaseExceptionGroup) as excinfo:
        server._reraise_unless_stdio_disconnect(group)
    leaves = list(_leaves(excinfo.value))
    assert any(isinstance(e, ValueError) for e in leaves)
    assert not any(isinstance(e, BrokenPipeError) for e in leaves)


def test_keyboard_interrupt_is_reraised() -> None:
    with pytest.raises(KeyboardInterrupt):
        server._reraise_unless_stdio_disconnect(KeyboardInterrupt())


# --- helper: bare OSError errno coverage (non-Linux disconnect errnos) -----


@pytest.mark.parametrize("code", [errno.ESHUTDOWN, errno.ENOTCONN, errno.EPIPE])
def test_bare_oserror_disconnect_errno_is_swallowed(code: int) -> None:
    assert server._reraise_unless_stdio_disconnect(OSError(code, "peer gone")) is None


def test_bare_oserror_unrelated_errno_is_reraised() -> None:
    # A genuine IO error (e.g. disk full) must never be mistaken for a disconnect.
    with pytest.raises(OSError) as excinfo:
        server._reraise_unless_stdio_disconnect(OSError(errno.ENOSPC, "no space"))
    assert excinfo.value.errno == errno.ENOSPC


def test_group_of_bare_disconnect_errno_is_swallowed() -> None:
    group = BaseExceptionGroup("transport", [OSError(errno.ESHUTDOWN, "gone")])
    assert server._reraise_unless_stdio_disconnect(group) is None


def test_mixed_group_of_disconnect_errno_and_real_error_reraises_real() -> None:
    group = BaseExceptionGroup(
        "mixed", [OSError(errno.ENOTCONN, "gone"), ValueError("boom")]
    )
    with pytest.raises(BaseExceptionGroup) as excinfo:
        server._reraise_unless_stdio_disconnect(group)
    leaves = list(_leaves(excinfo.value))
    assert any(isinstance(e, ValueError) for e in leaves)
    assert not any(
        isinstance(e, OSError) and e.errno == errno.ENOTCONN for e in leaves
    )


# --- entry point: _run_standalone ------------------------------------------


def test_run_standalone_swallows_broken_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "make_local_dispatcher", lambda *a, **k: object())
    monkeypatch.setattr(
        server,
        "build_server",
        lambda dispatcher: _FakeServer(BaseExceptionGroup("t", [BrokenPipeError()])),
    )
    # Must return cleanly, not raise.
    server._run_standalone()


def test_run_standalone_reraises_real_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "make_local_dispatcher", lambda *a, **k: object())
    monkeypatch.setattr(
        server, "build_server", lambda dispatcher: _FakeServer(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError, match="boom"):
        server._run_standalone()


# --- entry point: _run_proxy -----------------------------------------------


def _patch_proxy(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> list[str]:
    calls: list[str] = []

    class _FakeRuntime:
        def __init__(self, client: object) -> None:
            calls.append("init")

        def shutdown(self) -> None:
            calls.append("shutdown")

    monkeypatch.setattr(server, "ProxyRuntime", _FakeRuntime)
    monkeypatch.setattr(server, "build_server", lambda runtime: _FakeServer(exc))
    return calls


def test_run_proxy_swallows_broken_pipe_and_shuts_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_proxy(monkeypatch, BaseExceptionGroup("t", [BrokenPipeError()]))
    server._run_proxy(BootstrapResult(role="proxy", endpoint=None, client=object()))
    assert calls == ["init", "shutdown"]


def test_run_proxy_reraises_real_error_but_still_shuts_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_proxy(monkeypatch, RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        server._run_proxy(BootstrapResult(role="proxy", endpoint=None, client=object()))
    assert calls == ["init", "shutdown"]


# --- entry point: _run_primary ---------------------------------------------


def _patch_primary(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> list[str]:
    events: list[str] = []

    class _FakeListener:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def start(self) -> None:
            events.append("start")

        def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(server, "DaemonListener", _FakeListener)
    monkeypatch.setattr(
        server, "make_local_dispatcher", lambda *a, **k: (lambda tool, kwargs: None)
    )
    monkeypatch.setattr(server, "_startup_auto_migrate", lambda: None)
    monkeypatch.setattr(server, "_warn_about_pending_migrations", lambda: None)
    monkeypatch.setattr(
        server, "release_daemon_lock", lambda endpoint: events.append("release")
    )
    monkeypatch.setattr(server, "build_server", lambda dispatcher: _FakeServer(exc))
    return events


def test_run_primary_swallows_broken_pipe_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _patch_primary(monkeypatch, BaseExceptionGroup("t", [BrokenPipeError()]))
    server._run_primary(BootstrapResult(role="primary", endpoint=object(), client=None))
    assert events == ["start", "stop", "release"]


def test_run_primary_reraises_real_error_but_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _patch_primary(monkeypatch, RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        server._run_primary(
            BootstrapResult(role="primary", endpoint=object(), client=None)
        )
    assert events == ["start", "stop", "release"]
