"""Tests for the Phase A migration framework."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.migrations import (
    Migration,
    MigrationOutcome,
    Subsystem,
    apply_pending,
    clear_registry,
    create_snapshot,
    detect_status,
    format_pending_warning,
    get_chain,
    latest_version,
    list_snapshots,
    migration,
    restore_snapshot,
)
from turbo_memory_mcp.store import MemoryStore


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _project_identity(project_root: Path) -> ProjectIdentity:
    return ProjectIdentity(
        project_id="migtest1234abcd",
        project_name="Migration Test",
        project_root=project_root,
        identity_source="local/migtest",
        identity_kind="local_path",
        remote_url=None,
    )


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    storage_root = tmp_path / "store"
    store = MemoryStore(
        _project_identity(tmp_path / "repo"), storage_root=storage_root
    )
    store.ensure_layout()
    store.ensure_markdown_layout()
    store.ensure_retrieval_layout()
    store.ensure_telemetry_layout()
    return store


@pytest.fixture(autouse=True)
def _isolate_registry_and_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clear_registry()
    monkeypatch.setenv("TQMEMORY_MIGRATION_LOG_PATH", str(tmp_path / "migration.log"))
    yield
    clear_registry()


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def test_decorator_registers_migration_with_correct_fields() -> None:
    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2, description="add offsets")
    def _upgrade(layout) -> None:
        return None

    chain = get_chain(Subsystem.MARKDOWN, 1)
    assert len(chain) == 1
    step = chain[0]
    assert step.subsystem is Subsystem.MARKDOWN
    assert step.from_version == 1
    assert step.to_version == 2
    assert step.description == "add offsets"


def test_invalid_version_jump_raises() -> None:
    with pytest.raises(ValueError):
        Migration(
            subsystem=Subsystem.MARKDOWN,
            from_version=1,
            to_version=3,
            func=lambda _: None,
        )


def test_from_version_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Migration(
            subsystem=Subsystem.MARKDOWN,
            from_version=0,
            to_version=1,
            func=lambda _: None,
        )


def test_get_chain_returns_steps_in_order() -> None:
    @migration(Subsystem.MARKDOWN, from_version=2, to_version=3)
    def _two_three(_):
        return None

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _one_two(_):
        return None

    chain = get_chain(Subsystem.MARKDOWN, 1)
    assert [(m.from_version, m.to_version) for m in chain] == [(1, 2), (2, 3)]


def test_get_chain_with_gap_raises() -> None:
    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _one_two(_):
        return None

    @migration(Subsystem.MARKDOWN, from_version=3, to_version=4)
    def _three_four(_):
        return None

    with pytest.raises(ValueError, match="gap"):
        get_chain(Subsystem.MARKDOWN, 1)


def test_duplicate_step_raises() -> None:
    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _a(_):
        return None

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _b(_):
        return None

    with pytest.raises(ValueError, match="Duplicate"):
        get_chain(Subsystem.MARKDOWN, 1)


def test_latest_version_falls_back_to_store_constant() -> None:
    # Registry empty -> uses constant from store.py
    # MARKDOWN_FORMAT_VERSION=1, USAGE_STATS_FORMAT_VERSION=2 currently
    assert latest_version(Subsystem.MARKDOWN) == 1
    assert latest_version(Subsystem.USAGE_STATS) == 2


def test_latest_version_takes_max_of_store_and_registry() -> None:
    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _step(_):
        return None

    assert latest_version(Subsystem.MARKDOWN) == 2


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def test_detect_status_treats_missing_manifests_as_version_zero(store: MemoryStore) -> None:
    statuses = detect_status(store)
    for sub in Subsystem:
        assert statuses[sub].current_version == 0
        assert statuses[sub].pending == []


def test_detect_status_reads_current_format_version(store: MemoryStore) -> None:
    store.write_markdown_manifest()
    statuses = detect_status(store)
    assert statuses[Subsystem.MARKDOWN].current_version == 1


def test_apply_pending_dry_run_does_not_mutate(store: MemoryStore) -> None:
    store.write_markdown_manifest()

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _step(_):
        raise AssertionError("must not be called in dry-run")

    outcomes = apply_pending(store, dry_run=True)
    assert len(outcomes) == 1
    assert outcomes[0].migration.from_version == 1
    # Manifest still at v1
    manifest = store.read_markdown_manifest()
    assert manifest is not None
    assert manifest["format_version"] == 1


def test_apply_pending_executes_in_order_and_bumps_manifest(
    store: MemoryStore,
) -> None:
    store.write_markdown_manifest()
    calls: list[int] = []

    @migration(Subsystem.MARKDOWN, from_version=2, to_version=3)
    def _two_three(_):
        calls.append(2)

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _one_two(_):
        calls.append(1)

    outcomes = apply_pending(store, subsystems=[Subsystem.MARKDOWN], snapshot=False)
    assert calls == [1, 2]
    assert all(o.success for o in outcomes)
    manifest = store.read_markdown_manifest()
    assert manifest["format_version"] == 3


def test_apply_pending_failure_stops_chain_and_leaves_prior_version(
    store: MemoryStore,
) -> None:
    store.write_markdown_manifest()
    calls: list[int] = []

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _one_two(_):
        calls.append(1)

    @migration(Subsystem.MARKDOWN, from_version=2, to_version=3)
    def _two_three(_):
        calls.append(2)
        raise RuntimeError("boom")

    @migration(Subsystem.MARKDOWN, from_version=3, to_version=4)
    def _three_four(_):
        calls.append(3)

    outcomes = apply_pending(
        store, subsystems=[Subsystem.MARKDOWN], snapshot=False
    )
    # Stopped on failure of v2->v3, never tried v3->v4
    assert calls == [1, 2]
    assert len(outcomes) == 2
    assert outcomes[-1].success is False
    assert "boom" in (outcomes[-1].error or "")
    # Manifest reflects last successfully-bumped version
    manifest = store.read_markdown_manifest()
    assert manifest["format_version"] == 2


def test_apply_pending_is_idempotent_after_success(store: MemoryStore) -> None:
    store.write_markdown_manifest()
    invocations = {"n": 0}

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _step(_):
        invocations["n"] += 1

    first = apply_pending(store, subsystems=[Subsystem.MARKDOWN], snapshot=False)
    second = apply_pending(store, subsystems=[Subsystem.MARKDOWN], snapshot=False)
    assert invocations["n"] == 1
    assert len(first) == 1
    assert second == []


def test_apply_pending_retrieval_bumps_both_project_and_global(
    store: MemoryStore,
) -> None:
    store.write_project_retrieval_manifest()
    store.write_global_retrieval_manifest()

    @migration(Subsystem.RETRIEVAL, from_version=1, to_version=2)
    def _step(_):
        return None

    apply_pending(store, subsystems=[Subsystem.RETRIEVAL], snapshot=False)
    proj = store.read_project_retrieval_manifest()
    glob = store.read_global_retrieval_manifest()
    assert proj is not None and proj["format_version"] == 2
    assert glob is not None and glob["format_version"] == 2


def test_format_pending_warning_is_none_when_up_to_date(store: MemoryStore) -> None:
    store.write_markdown_manifest()
    assert format_pending_warning(store) is None


def test_format_pending_warning_lists_pending_chains(store: MemoryStore) -> None:
    store.write_markdown_manifest()

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2, description="add offsets")
    def _step(_):
        return None

    warning = format_pending_warning(store)
    assert warning is not None
    assert "markdown" in warning
    assert "v1 -> v2" in warning


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #


def test_create_snapshot_copies_files(store: MemoryStore) -> None:
    store.write_project_manifest()
    snap = create_snapshot(store.storage_root)
    assert snap.exists()
    # Project manifest must be visible inside the snapshot
    proj_id = store.project.project_id
    copied = snap / "projects" / proj_id / "manifest.json"
    assert copied.exists()


def test_create_snapshot_excludes_snapshots_dir_and_lockfile(store: MemoryStore) -> None:
    store.write_project_manifest()
    (store.storage_root / ".daemon.lock").write_text("noise", encoding="utf-8")
    snap1 = create_snapshot(store.storage_root)
    # Now there's a .snapshots dir and a .daemon.lock at the root
    snap2 = create_snapshot(store.storage_root)
    assert not (snap2 / ".snapshots").exists()
    assert not (snap2 / ".daemon.lock").exists()
    # And the original snapshot is still there
    assert snap1.exists() or len(list_snapshots(store.storage_root)) == 1


def test_restore_snapshot_round_trip(store: MemoryStore) -> None:
    store.write_project_manifest()
    snap = create_snapshot(store.storage_root)

    # Mutate live state after snapshot.
    extra = store.storage_root / "projects" / store.project.project_id / "extra.json"
    extra.write_text(json.dumps({"k": "v"}), encoding="utf-8")
    assert extra.exists()

    restore_snapshot(store.storage_root, snap)
    assert not extra.exists()
    # Original manifest is still readable
    assert store.read_project_manifest() is not None


def test_restore_rejects_path_outside_snapshots(
    store: MemoryStore, tmp_path: Path
) -> None:
    rogue = tmp_path / "rogue_snapshot"
    rogue.mkdir()
    with pytest.raises(ValueError):
        restore_snapshot(store.storage_root, rogue)


def test_keep_count_prunes_old_snapshots(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TQMEMORY_SNAPSHOTS_KEEP", "2")
    store.write_project_manifest()

    snaps = []
    for _ in range(4):
        snaps.append(create_snapshot(store.storage_root))

    surviving = list_snapshots(store.storage_root)
    assert len(surviving) == 2
    # Newest two should be the survivors
    survivors_set = {p.name for p in surviving}
    expected = {snaps[-1].name, snaps[-2].name}
    assert survivors_set == expected
