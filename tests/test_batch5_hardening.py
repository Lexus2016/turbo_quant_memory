"""Tests for Batch 5 hardening: F6 (loud standalone), U2 (snapshot before restore)."""
from __future__ import annotations

from pathlib import Path

import pytest

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.store import MemoryStore


def _store(tmp_path: Path) -> MemoryStore:
    ident = ProjectIdentity(
        project_id="batch5test00001",
        project_name="Batch5",
        project_root=tmp_path / "repo",
        identity_source="local/b5",
        identity_kind="local_path",
        remote_url=None,
    )
    s = MemoryStore(ident, storage_root=tmp_path / "store")
    s.ensure_layout()
    return s


# --- F6: standalone fallback is loud in health() ---


def test_health_warns_on_standalone(monkeypatch: pytest.MonkeyPatch) -> None:
    from turbo_memory_mcp import server

    class _B:
        role = "standalone"

    monkeypatch.setattr(server, "_cached_bootstrap", _B())
    monkeypatch.setattr(server, "_migration_pending_signal", lambda **k: (False, None))
    monkeypatch.setattr(server, "_auto_migration_result", None, raising=False)

    payload = server._tool_health({}, cwd=None, environ=None)
    assert "daemon_warning" in payload
    assert "standalone" in payload["daemon_warning"].lower()


def test_health_no_warning_when_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    from turbo_memory_mcp import server

    class _B:
        role = "primary"

    monkeypatch.setattr(server, "_cached_bootstrap", _B())
    monkeypatch.setattr(server, "_migration_pending_signal", lambda **k: (False, None))
    monkeypatch.setattr(server, "_auto_migration_result", None, raising=False)

    payload = server._tool_health({}, cwd=None, environ=None)
    assert "daemon_warning" not in payload


# --- U2: --restore-from snapshots the current state first ---


def test_restore_from_snapshots_current_state_first(tmp_path: Path) -> None:
    from turbo_memory_mcp.cli import _migrate_restore_from
    from turbo_memory_mcp.migrations import (
        create_snapshot,
        list_snapshots,
        restore_snapshot,
    )

    store = _store(tmp_path)
    marker = store.storage_root / "marker.txt"
    marker.write_text("original", encoding="utf-8")

    snapshot = create_snapshot(store.storage_root)  # capture "original"
    marker.write_text("changed", encoding="utf-8")  # diverge live state

    before = len(list_snapshots(store.storage_root))
    rc = _migrate_restore_from(store, restore_snapshot, str(snapshot), force=True)

    assert rc == 0
    assert marker.read_text(encoding="utf-8") == "original"  # restored
    after = len(list_snapshots(store.storage_root))
    assert after > before  # a pre-restore snapshot (undo handle) was created
