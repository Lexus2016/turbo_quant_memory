"""Startup-time behavior of the stdio server: opt-in auto-migration and the
daemon-state fields that ``health()`` exposes.

These cover the silent-startup-failure issue: an MCP client must be able to
tell a lock/migration problem apart from a network timeout, and an operator
must be able to opt into having pending migrations applied on boot.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import turbo_memory_mcp.migrations as migrations
import turbo_memory_mcp.server as server
from turbo_memory_mcp.contracts import build_health_payload
from turbo_memory_mcp.daemon import ENV_MIGRATE_ON_STARTUP, BootstrapResult


# --- stubs -----------------------------------------------------------------


class _Sub:
    def __init__(self, value: str) -> None:
        self.value = value


class _Status:
    def __init__(self, needs_upgrade: bool) -> None:
        self.needs_upgrade = needs_upgrade


class _Mig:
    def __init__(self, sub: str) -> None:
        self.subsystem = _Sub(sub)


class _Outcome:
    def __init__(self, success: bool, *, error: str | None = None, sub: str = "notes") -> None:
        self.success = success
        self.error = error
        self.migration = _Mig(sub)


class _FakeStore:
    def __init__(self, root: Path) -> None:
        self.storage_root = root


def _bootstrap(role: str) -> BootstrapResult:
    return BootstrapResult(role=role, endpoint=None, client=None)


@pytest.fixture(autouse=True)
def _reset_startup_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the write-once module globals for each test."""
    monkeypatch.setattr(server, "_auto_migration_result", None)
    monkeypatch.setattr(server, "_cached_bootstrap", None)


# --- auto-migration gating -------------------------------------------------


def test_auto_migrate_skips_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_MIGRATE_ON_STARTUP, raising=False)
    monkeypatch.setattr(server, "_cached_bootstrap", _bootstrap("standalone"))
    assert server._startup_auto_migrate() is None


def test_auto_migrate_skips_for_proxy_role(monkeypatch: pytest.MonkeyPatch) -> None:
    # A proxy does not own storage; only primary/standalone may migrate.
    monkeypatch.setenv(ENV_MIGRATE_ON_STARTUP, "1")
    monkeypatch.setattr(server, "_cached_bootstrap", _bootstrap("proxy"))
    assert server._startup_auto_migrate() is None


def test_auto_migrate_no_pending_returns_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_MIGRATE_ON_STARTUP, "1")
    monkeypatch.setattr(server, "_cached_bootstrap", _bootstrap("standalone"))
    monkeypatch.setattr(server, "build_runtime_context", lambda **_: (None, _FakeStore(tmp_path)))
    monkeypatch.setattr(migrations, "detect_status", lambda store: {})

    result = server._startup_auto_migrate()

    assert result == "skipped: nothing pending"
    assert server._auto_migration_result == "skipped: nothing pending"


def test_auto_migrate_applies_pending_after_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_MIGRATE_ON_STARTUP, "1")
    monkeypatch.setattr(server, "_cached_bootstrap", _bootstrap("primary"))
    monkeypatch.setattr(server, "build_runtime_context", lambda **_: (None, _FakeStore(tmp_path)))
    monkeypatch.setattr(migrations, "detect_status", lambda store: {"notes": _Status(True)})

    snapped: dict[str, Path] = {}

    def _snap(root: Path) -> Path:
        snapped["root"] = root
        return tmp_path / "snap"

    monkeypatch.setattr(migrations, "create_snapshot", _snap)
    monkeypatch.setattr(migrations, "apply_pending", lambda store, **_: [_Outcome(True), _Outcome(True)])

    result = server._startup_auto_migrate()

    assert result == "applied: 2 step(s)"
    assert snapped["root"] == tmp_path  # snapshot is taken before applying


def test_auto_migrate_reports_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_MIGRATE_ON_STARTUP, "1")
    monkeypatch.setattr(server, "_cached_bootstrap", _bootstrap("standalone"))
    monkeypatch.setattr(server, "build_runtime_context", lambda **_: (None, _FakeStore(tmp_path)))
    monkeypatch.setattr(migrations, "detect_status", lambda store: {"notes": _Status(True)})
    monkeypatch.setattr(migrations, "create_snapshot", lambda root: tmp_path / "snap")
    monkeypatch.setattr(
        migrations, "apply_pending", lambda store, **_: [_Outcome(False, error="boom", sub="notes")]
    )

    result = server._startup_auto_migrate()

    assert result is not None and result.startswith("failed:")


def test_auto_migrate_swallows_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    # A broken migration check must never crash startup — the server should
    # still come up and report the error via health() instead of dying.
    monkeypatch.setenv(ENV_MIGRATE_ON_STARTUP, "1")
    monkeypatch.setattr(server, "_cached_bootstrap", _bootstrap("standalone"))

    def _boom(**_: object) -> tuple[object, object]:
        raise RuntimeError("ctx exploded")

    monkeypatch.setattr(server, "build_runtime_context", _boom)

    result = server._startup_auto_migrate()

    assert result is not None and result.startswith("error:")
    assert "ctx exploded" in result


# --- health daemon-state fields --------------------------------------------


def test_health_payload_includes_daemon_fields_when_set() -> None:
    payload = build_health_payload(
        daemon_role="primary",
        migration_auto_result="applied: 1 step(s)",
    )
    assert payload["daemon_role"] == "primary"
    assert payload["migration_auto_result"] == "applied: 1 step(s)"


def test_health_payload_omits_daemon_fields_when_none() -> None:
    payload = build_health_payload()
    assert "daemon_role" not in payload
    assert "migration_auto_result" not in payload


def test_tool_health_reflects_cached_daemon_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_migration_pending_signal", lambda **_: (False, None))
    monkeypatch.setattr(server, "_cached_bootstrap", _bootstrap("primary"))
    monkeypatch.setattr(server, "_auto_migration_result", "applied: 1 step(s)")

    payload = server._tool_health({}, cwd=None, environ={})

    assert payload["daemon_role"] == "primary"
    assert payload["migration_auto_result"] == "applied: 1 step(s)"
