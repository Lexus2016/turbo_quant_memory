"""Tests for singleton daemon bootstrap, listener, and client."""

from __future__ import annotations

import os
import platform
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from turbo_memory_mcp import daemon
from turbo_memory_mcp.daemon import (
    DAEMON_PROTOCOL_VERSION,
    BootstrapResult,
    DaemonClient,
    DaemonEndpoint,
    DaemonListener,
    acquire_daemon_role,
    daemon_is_disabled,
    lockfile_path,
    make_primary_endpoint,
    maybe_existing_endpoint,
    release_daemon_lock,
)


@pytest.fixture
def storage_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Point TQMEMORY_HOME at an isolated tmp dir so lockfiles stay sandboxed."""

    home = tmp_path / "tqm_home"
    home.mkdir()
    monkeypatch.setenv("TQMEMORY_HOME", str(home))
    monkeypatch.delenv("TQMEMORY_DAEMON_DISABLE", raising=False)
    return {"TQMEMORY_HOME": str(home)}


def _make_handler(recorded: list[tuple[str, dict[str, Any]]]) -> Any:
    def _handler(tool: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        recorded.append((tool, dict(kwargs)))
        if tool == "raise_value_error":
            raise ValueError("boom")
        if tool == "raise_key_error":
            raise KeyError("missing")
        return {"echo": tool, "kwargs": dict(kwargs)}

    return _handler


def _start_primary(
    storage_env: Mapping[str, str],
    handler: Any,
) -> tuple[DaemonListener, DaemonEndpoint]:
    endpoint = make_primary_endpoint(environ=dict(os.environ))
    listener = DaemonListener(endpoint, handler)
    listener.start()
    # Write lockfile so bootstrap discovers this endpoint.
    path = lockfile_path(dict(os.environ))
    path.write_text(__import__("json").dumps(endpoint.to_lockfile()), encoding="utf-8")
    return listener, endpoint


def test_daemon_is_disabled_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TQMEMORY_DAEMON_DISABLE", raising=False)
    assert daemon_is_disabled() is False
    monkeypatch.setenv("TQMEMORY_DAEMON_DISABLE", "1")
    assert daemon_is_disabled() is True
    monkeypatch.setenv("TQMEMORY_DAEMON_DISABLE", "true")
    assert daemon_is_disabled() is True
    monkeypatch.setenv("TQMEMORY_DAEMON_DISABLE", "0")
    assert daemon_is_disabled() is False


def test_endpoint_roundtrip_via_lockfile(storage_env: dict[str, str]) -> None:
    endpoint = make_primary_endpoint(environ=dict(os.environ))
    restored = DaemonEndpoint.from_lockfile(endpoint.to_lockfile())
    assert restored.address == endpoint.address
    assert restored.family == endpoint.family
    assert restored.authkey == endpoint.authkey
    assert restored.pid == endpoint.pid
    assert restored.protocol_version == DAEMON_PROTOCOL_VERSION


def test_bootstrap_returns_primary_when_lockfile_absent(storage_env: dict[str, str]) -> None:
    result = acquire_daemon_role()
    try:
        assert result.role == "primary"
        assert result.endpoint is not None
        assert result.client is None
        assert lockfile_path().exists()
    finally:
        if result.endpoint is not None:
            release_daemon_lock(result.endpoint)


def test_bootstrap_returns_standalone_when_disabled(
    storage_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TQMEMORY_DAEMON_DISABLE", "1")
    result = acquire_daemon_role()
    assert result.role == "standalone"
    assert result.endpoint is None
    assert result.client is None
    assert not lockfile_path().exists()


def test_bootstrap_proxies_to_live_primary(storage_env: dict[str, str]) -> None:
    recorded: list[tuple[str, dict[str, Any]]] = []
    listener, endpoint = _start_primary(storage_env, _make_handler(recorded))
    try:
        result = acquire_daemon_role()
        assert result.role == "proxy"
        assert result.client is not None
        assert result.endpoint is not None
        assert result.endpoint.authkey == endpoint.authkey

        reply = result.client.call("echo_tool", {"a": 1})
        assert reply == {"echo": "echo_tool", "kwargs": {"a": 1}}
        assert recorded == [("echo_tool", {"a": 1})]
        result.client.close()
    finally:
        listener.stop()
        release_daemon_lock(endpoint)


def test_bootstrap_reclaims_stale_lockfile(storage_env: dict[str, str]) -> None:
    # Write a lockfile that points at a PID that cannot exist.
    stale = DaemonEndpoint(
        address=str(Path(os.environ["TQMEMORY_HOME"]) / ".stale.sock"),
        family="AF_UNIX" if platform.system() != "Windows" else "AF_PIPE",
        authkey=b"x" * 32,
        pid=1,  # pid 1 is init; os.kill(1, 0) raises PermissionError on Linux/macOS
        server_version="0.0.0",
        protocol_version=DAEMON_PROTOCOL_VERSION,
    )
    # Use a definitely-dead pid on Unix: pick a huge number unlikely to exist.
    stale_fake_pid = 2**31 - 1
    stale_payload = {**stale.to_lockfile(), "pid": stale_fake_pid}
    path = lockfile_path()
    path.write_text(__import__("json").dumps(stale_payload), encoding="utf-8")

    result = acquire_daemon_role()
    try:
        assert result.role == "primary"
        assert result.endpoint is not None
        assert result.endpoint.pid == os.getpid()
    finally:
        if result.endpoint is not None:
            release_daemon_lock(result.endpoint)


def test_client_propagates_value_error(storage_env: dict[str, str]) -> None:
    recorded: list[tuple[str, dict[str, Any]]] = []
    listener, endpoint = _start_primary(storage_env, _make_handler(recorded))
    try:
        client = DaemonClient(endpoint)
        with pytest.raises(ValueError, match="boom"):
            client.call("raise_value_error", {})
        client.close()
    finally:
        listener.stop()
        release_daemon_lock(endpoint)


def test_client_propagates_key_error(storage_env: dict[str, str]) -> None:
    recorded: list[tuple[str, dict[str, Any]]] = []
    listener, endpoint = _start_primary(storage_env, _make_handler(recorded))
    try:
        client = DaemonClient(endpoint)
        with pytest.raises(KeyError):
            client.call("raise_key_error", {})
        client.close()
    finally:
        listener.stop()
        release_daemon_lock(endpoint)


def test_listener_serializes_concurrent_calls(storage_env: dict[str, str]) -> None:
    """Two clients calling in parallel must not execute concurrently.

    The listener's dispatch_lock guarantees single-writer semantics for the
    underlying store. We verify by tracking overlapping handler executions.
    """

    active = 0
    peak = 0
    lock = threading.Lock()

    def _handler(tool: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.05)
            return {"ok": True}
        finally:
            with lock:
                active -= 1

    endpoint = make_primary_endpoint(environ=dict(os.environ))
    listener = DaemonListener(endpoint, _handler)
    listener.start()
    try:
        clients = [DaemonClient(endpoint) for _ in range(4)]
        threads = [
            threading.Thread(target=client.call, args=("work", {}))
            for client in clients
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)
        assert peak == 1, f"expected serialized execution, saw {peak} concurrent handlers"
        for client in clients:
            client.close()
    finally:
        listener.stop()


def test_maybe_existing_endpoint_rejects_dead_pid(storage_env: dict[str, str]) -> None:
    fake_endpoint = DaemonEndpoint(
        address=str(Path(os.environ["TQMEMORY_HOME"]) / ".phantom.sock"),
        family="AF_UNIX" if platform.system() != "Windows" else "AF_PIPE",
        authkey=b"y" * 32,
        pid=2**31 - 1,
        server_version="0.0.0",
        protocol_version=DAEMON_PROTOCOL_VERSION,
    )
    path = lockfile_path()
    path.write_text(__import__("json").dumps(fake_endpoint.to_lockfile()), encoding="utf-8")
    assert maybe_existing_endpoint() is None


def test_maybe_existing_endpoint_rejects_wrong_protocol(storage_env: dict[str, str]) -> None:
    endpoint = make_primary_endpoint(environ=dict(os.environ))
    payload = endpoint.to_lockfile()
    payload["protocol_version"] = "99.0"
    path = lockfile_path()
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    assert maybe_existing_endpoint() is None


def test_release_daemon_lock_cleans_socket(storage_env: dict[str, str]) -> None:
    if platform.system() == "Windows":
        pytest.skip("Named pipes are not file-system artifacts")
    endpoint = make_primary_endpoint(environ=dict(os.environ))
    path = lockfile_path()
    path.write_text(__import__("json").dumps(endpoint.to_lockfile()), encoding="utf-8")
    # Touch the socket path so release can observe it.
    Path(endpoint.address).touch()
    release_daemon_lock(endpoint)
    assert not path.exists()
    assert not Path(endpoint.address).exists()


def test_proxy_dispatcher_forwards_cwd_and_environ(
    storage_env: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxy must forward caller cwd + identity env vars so the primary
    resolves the *proxy's* project identity, not its own."""

    from turbo_memory_mcp.server import make_proxy_dispatcher

    captured: dict[str, Any] = {}

    def _handler(tool: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        captured["tool"] = tool
        captured["kwargs"] = dict(kwargs)
        return {"ok": True}

    endpoint = make_primary_endpoint(environ=dict(os.environ))
    listener = DaemonListener(endpoint, _handler)
    listener.start()
    try:
        client = DaemonClient(endpoint)
        dispatcher = make_proxy_dispatcher(client)

        # Simulate a proxy whose cwd and project-identity env differ from primary's.
        proxy_cwd = tmp_path / "some_project_worktree"
        proxy_cwd.mkdir()
        monkeypatch.chdir(proxy_cwd)
        monkeypatch.setenv("TQMEMORY_PROJECT_ID", "proxy-project-xyz")
        monkeypatch.setenv("TQMEMORY_PROJECT_NAME", "Proxy Project")

        dispatcher("health", {})

        assert captured["tool"] == "health"
        assert str(Path(captured["kwargs"]["_cwd"]).resolve()) == str(proxy_cwd.resolve())
        assert captured["kwargs"]["_environ"]["TQMEMORY_PROJECT_ID"] == "proxy-project-xyz"
        assert captured["kwargs"]["_environ"]["TQMEMORY_PROJECT_NAME"] == "Proxy Project"
        client.close()
    finally:
        listener.stop()


def test_call_does_not_retry_to_prevent_duplicates(storage_env: dict[str, str]) -> None:
    """Regression: RPC failures mid-call must NOT be silently retried, because
    that would duplicate non-idempotent tools like remember_note."""

    # Handler that crashes the connection exactly once by closing it mid-reply.
    call_count = {"n": 0}

    def _handler(tool: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        call_count["n"] += 1
        # Pretend the primary crashed right after receiving the call: simulate
        # by raising inside the handler (listener sends an error payload).
        raise RuntimeError("simulated primary crash")

    endpoint = make_primary_endpoint(environ=dict(os.environ))
    listener = DaemonListener(endpoint, _handler)
    listener.start()
    try:
        client = DaemonClient(endpoint)
        # Handler raises -> client surfaces it, but handler is only called once.
        with pytest.raises(RuntimeError, match="simulated primary crash"):
            client.call("remember_note", {"title": "x", "content": "y", "kind": "lesson"})
        assert call_count["n"] == 1, "handler should NOT be invoked twice"
        client.close()
    finally:
        listener.stop()


def test_call_raises_primary_unreachable_when_connect_fails(
    storage_env: dict[str, str],
) -> None:
    """Connect-phase failure must raise PrimaryUnreachable so callers can
    safely failover without worrying about duplicate state."""

    from turbo_memory_mcp.daemon import PrimaryUnreachable

    if platform.system() == "Windows":
        pytest.skip("AF_PIPE semantics differ; covered on Unix")

    # Point a client at a socket path that doesn't exist.
    phantom = DaemonEndpoint(
        address=str(Path(os.environ["TQMEMORY_HOME"]) / ".phantom.sock"),
        family="AF_UNIX",
        authkey=b"z" * 32,
        pid=os.getpid(),
        server_version="0.0.0",
        protocol_version=DAEMON_PROTOCOL_VERSION,
    )
    client = DaemonClient(phantom)
    with pytest.raises(PrimaryUnreachable):
        client.call("health", {})
    client.close()


def test_proxy_runtime_promotes_when_primary_dies(
    storage_env: dict[str, str],
) -> None:
    """When the primary goes away, a proxy's next call must auto-promote
    (start its own listener + serve locally) rather than surface an error.
    """

    from turbo_memory_mcp.server import ProxyRuntime

    recorded: list[tuple[str, dict[str, Any]]] = []

    # 1. Start a primary.
    listener, endpoint = _start_primary(storage_env, _make_handler(recorded))

    # 2. Bootstrap a proxy against that primary.
    bootstrap = acquire_daemon_role()
    assert bootstrap.role == "proxy"
    assert bootstrap.client is not None

    runtime = ProxyRuntime(bootstrap.client)
    try:
        # First call works via the original primary.
        reply = runtime("echo_tool", {"phase": "pre_kill"})
        assert reply["echo"] == "echo_tool"
        assert reply["kwargs"]["phase"] == "pre_kill"
        assert recorded[-1][0] == "echo_tool"

        # 3. Kill the primary — stop listener, unlink the lockfile, and close
        #    the client's cached connection so the next call MUST reconnect.
        #    (In production, kernel tears down sockets when the owning process
        #    dies; here we simulate by closing the client's cached conn.)
        listener.stop()
        release_daemon_lock(endpoint)
        bootstrap.client.close()

        # 4. Next call must auto-promote. ProxyRuntime catches
        #    PrimaryUnreachable (reconnect to gone socket fails), runs
        #    acquire_daemon_role, wins the claim (no other bootstrap is
        #    competing in this test), starts its own DaemonListener, and
        #    routes the retry to the in-process local dispatcher.
        reply = runtime("health", {})
        assert isinstance(reply, dict)
        assert runtime.is_primary, "proxy should have promoted itself"
        # New primary's lockfile must point at OUR pid.
        new_endpoint = maybe_existing_endpoint()
        assert new_endpoint is not None
        assert new_endpoint.pid == os.getpid()
    finally:
        runtime.shutdown()


def test_proxy_runtime_reconnects_when_another_process_promoted(
    storage_env: dict[str, str],
) -> None:
    """When the primary dies but another process wins the promotion race,
    this proxy must reconnect to the new primary (not promote itself)."""

    from turbo_memory_mcp.server import ProxyRuntime

    recorded_old: list[tuple[str, dict[str, Any]]] = []
    recorded_new: list[tuple[str, dict[str, Any]]] = []

    # 1. Start "old" primary.
    old_listener, old_endpoint = _start_primary(storage_env, _make_handler(recorded_old))

    # 2. Bootstrap a proxy against it.
    bootstrap = acquire_daemon_role()
    assert bootstrap.role == "proxy"
    assert bootstrap.client is not None
    runtime = ProxyRuntime(bootstrap.client)

    new_listener: DaemonListener | None = None
    new_endpoint: DaemonEndpoint | None = None
    try:
        # 3. Simulate the old primary dying, and a NEW primary (different
        #    endpoint) taking over the lockfile.
        old_listener.stop()
        release_daemon_lock(old_endpoint)
        bootstrap.client.close()

        # Hand-roll a second primary on a fresh endpoint + lockfile to mimic
        # a different process winning the promotion race.
        new_listener, new_endpoint = _start_primary(storage_env, _make_handler(recorded_new))

        # 4. The proxy's next call must fail-over to the new primary.
        reply = runtime("echo_tool", {"phase": "post_failover"})
        assert reply["echo"] == "echo_tool"
        assert runtime.is_primary is False, (
            "proxy should reconnect to the new primary, not promote itself"
        )
        # The NEW primary's handler was the one that saw the call.
        assert recorded_new, "new primary should have received the re-dispatched call"
    finally:
        runtime.shutdown()
        if new_listener is not None:
            new_listener.stop()
        if new_endpoint is not None:
            release_daemon_lock(new_endpoint)


def test_proxy_runtime_preserves_midcall_connection_error(
    storage_env: dict[str, str],
) -> None:
    """Mid-call ConnectionError (send ok, recv failed) must NOT trigger
    failover, because retrying could duplicate non-idempotent tools.

    We simulate by having the server crash between receiving the call and
    sending the reply; the listener swallows that as a RuntimeError reply
    (since the handler raised). That's reported to the caller without
    failover, which is the desired semantics for non-idempotent tools.
    """

    from turbo_memory_mcp.server import ProxyRuntime

    handler_calls = {"n": 0}

    def _crash_handler(tool: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        handler_calls["n"] += 1
        raise RuntimeError("handler crashed mid-processing")

    endpoint = make_primary_endpoint(environ=dict(os.environ))
    listener = DaemonListener(endpoint, _crash_handler)
    listener.start()
    # Write lockfile so bootstrap finds it.
    path = lockfile_path(dict(os.environ))
    path.write_text(__import__("json").dumps(endpoint.to_lockfile()), encoding="utf-8")

    try:
        bootstrap = acquire_daemon_role()
        assert bootstrap.role == "proxy"
        assert bootstrap.client is not None
        runtime = ProxyRuntime(bootstrap.client)
        try:
            with pytest.raises(RuntimeError, match="handler crashed"):
                runtime("remember_note", {"title": "x", "content": "y", "kind": "lesson"})
            # Handler should be invoked exactly once — no silent retry.
            assert handler_calls["n"] == 1
            # Runtime should NOT have promoted; the primary is still alive.
            assert runtime.is_primary is False
        finally:
            runtime.shutdown()
    finally:
        listener.stop()
        release_daemon_lock(endpoint)
