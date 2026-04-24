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
