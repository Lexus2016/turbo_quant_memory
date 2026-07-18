"""Regression tests for M#1: a fresh install must report zero pending migrations,
and a manifest write must never skip the legacy v1->v2 tier reclass.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.migrations import (
    Subsystem,
    clear_registry,
    detect_status,
    migration,
)
from turbo_memory_mcp.store import MemoryStore


def _identity(root: Path) -> ProjectIdentity:
    return ProjectIdentity(
        project_id="freshtest000001",
        project_name="Fresh Install",
        project_root=root,
        identity_source="local/fresh",
        identity_kind="local_path",
        remote_url=None,
    )


@pytest.fixture(autouse=True)
def _real_notes_secrets_chain():
    # Register exactly the NOTES/SECRETS v1->v2 chains so latest_version == 2.
    # Save and RESTORE the real registry (rather than clear on teardown) so this
    # module does not pollute registry-dependent tests that run after it.
    from turbo_memory_mcp.migrations import registry as _reg

    saved = list(_reg.REGISTRY)
    clear_registry()
    migration(Subsystem.NOTES, from_version=1, to_version=2)(lambda s: None)
    migration(Subsystem.SECRETS, from_version=1, to_version=2)(lambda s: None)
    yield
    _reg.REGISTRY[:] = saved


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(_identity(tmp_path / "repo"), storage_root=tmp_path / "store")


def test_fresh_install_reports_zero_pending(tmp_path: Path) -> None:
    """The core M#1 bug: a brand-new install must NOT report pending NOTES or
    SECRETS migrations. Reproduced without clearing the real registry's chains.
    """
    store = _store(tmp_path)
    store.write_project_manifest()
    store.write_global_manifest()

    status = detect_status(store)
    assert status[Subsystem.NOTES].needs_upgrade is False, status[Subsystem.NOTES]
    assert status[Subsystem.SECRETS].needs_upgrade is False, status[Subsystem.SECRETS]


def test_fresh_manifest_stamps_latest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = store.write_project_manifest()
    assert manifest["format_version"] == 2  # nothing to migrate on a fresh layout


def test_legacy_notes_without_version_still_pending(tmp_path: Path) -> None:
    """A pre-Phase-2 layout (notes on disk, manifest lacking format_version) must
    still report NOTES pending so the tier reclass runs.
    """
    store = _store(tmp_path)
    store.project_notes_dir().mkdir(parents=True, exist_ok=True)
    (store.project_notes_dir() / "legacynote01.json").write_text(
        '{"note_id": "legacynote01", "scope": "project"}', encoding="utf-8"
    )
    store.project_manifest_path().write_text(
        json.dumps({"scope": "project", "project_id": store.project.project_id}),
        encoding="utf-8",
    )

    status = detect_status(store)
    assert status[Subsystem.NOTES].needs_upgrade is True, status[Subsystem.NOTES]


def test_legacy_manifest_write_does_not_skip_reclass(tmp_path: Path) -> None:
    """The subtle M#1 footgun: a legacy install that writes a manifest (e.g. via
    remember_note) BEFORE `migrate --apply` must NOT get its version advanced to
    2 — that would silently skip the reclass. write_project_manifest preserves
    v1 (notes exist) so the migration still runs.
    """
    store = _store(tmp_path)
    store.project_notes_dir().mkdir(parents=True, exist_ok=True)
    (store.project_notes_dir() / "n1.json").write_text(
        '{"note_id": "n1", "scope": "project"}', encoding="utf-8"
    )
    store.project_manifest_path().write_text(
        json.dumps({"scope": "project", "project_id": store.project.project_id}),
        encoding="utf-8",
    )

    written = store.write_project_manifest()
    assert written["format_version"] == 1  # preserved: migration owns the bump

    status = detect_status(store)
    assert status[Subsystem.NOTES].needs_upgrade is True, status[Subsystem.NOTES]


def test_invalid_format_version_with_notes_is_not_advanced(tmp_path: Path) -> None:
    """A garbage format_version + notes on disk must be treated like a missing
    version (-> v1), never force-advanced to latest — otherwise the reclass is
    silently skipped (grok review of M#1).
    """
    store = _store(tmp_path)
    store.project_notes_dir().mkdir(parents=True, exist_ok=True)
    (store.project_notes_dir() / "n1.json").write_text(
        '{"note_id": "n1", "scope": "project"}', encoding="utf-8"
    )
    store.project_manifest_path().write_text(
        json.dumps(
            {
                "scope": "project",
                "project_id": store.project.project_id,
                "format_version": "garbage",
            }
        ),
        encoding="utf-8",
    )

    written = store.write_project_manifest()
    assert written["format_version"] == 1  # invalid + records -> v1, not latest
