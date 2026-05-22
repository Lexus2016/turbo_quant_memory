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


def test_apply_pending_handles_multiple_subsystems_in_one_call(
    store: MemoryStore,
) -> None:
    store.write_markdown_manifest()
    store.write_project_retrieval_manifest()
    store.write_global_retrieval_manifest()

    seen: list[str] = []

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _md(_):
        seen.append("md")

    @migration(Subsystem.RETRIEVAL, from_version=1, to_version=2)
    def _rt(_):
        seen.append("rt")

    outcomes = apply_pending(store, snapshot=False)
    assert len(outcomes) == 2
    assert set(seen) == {"md", "rt"}
    assert store.read_markdown_manifest()["format_version"] == 2
    assert store.read_project_retrieval_manifest()["format_version"] == 2
    assert store.read_global_retrieval_manifest()["format_version"] == 2


def test_apply_pending_restore_after_failed_copy_keeps_data(
    store: MemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed restore must move staged originals back so storage stays usable."""
    from turbo_memory_mcp.migrations import snapshot as snapshot_mod

    store.write_project_manifest()
    snap = create_snapshot(store.storage_root)

    # Write a sentinel that must survive a rolled-back restore.
    sentinel = store.storage_root / "projects" / store.project.project_id / "sentinel.txt"
    sentinel.write_text("alive", encoding="utf-8")

    # Force the copy phase to fail.
    real_copytree = snapshot_mod.shutil.copytree

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated copy failure")

    monkeypatch.setattr(snapshot_mod.shutil, "copytree", boom)

    with pytest.raises(RuntimeError, match="simulated copy failure"):
        restore_snapshot(store.storage_root, snap)

    # Restore re-raises but the sentinel must still be reachable via the
    # rolled-back staging move.
    monkeypatch.setattr(snapshot_mod.shutil, "copytree", real_copytree)
    assert sentinel.exists(), "rollback should keep the sentinel reachable"
    assert sentinel.read_text(encoding="utf-8") == "alive"


def test_log_event_writes_jsonl_with_required_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from turbo_memory_mcp.migrations import log_event, log_path

    log_file = tmp_path / "events.log"
    monkeypatch.setenv("TQMEMORY_MIGRATION_LOG_PATH", str(log_file))
    assert log_path() == log_file

    log_event("apply_success", subsystem="markdown", from_version=1, to_version=2)
    log_event("snapshot_created", path="/tmp/snap")

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "apply_success"
    assert first["subsystem"] == "markdown"
    assert first["from_version"] == 1
    assert first["to_version"] == 2
    assert "timestamp_utc" in first
    assert "package_version" in first
    second = json.loads(lines[1])
    assert second["event"] == "snapshot_created"
    assert second["path"] == "/tmp/snap"


# --------------------------------------------------------------------------- #
# CLI integration
# --------------------------------------------------------------------------- #


def _patch_runtime_context(monkeypatch: pytest.MonkeyPatch, store: MemoryStore) -> None:
    """Make CLI commands use the test store instead of resolving real CWD."""
    from turbo_memory_mcp import server as server_mod

    def _fake_ctx(*_args, **_kwargs):
        return store.project, store

    monkeypatch.setattr(server_mod, "build_runtime_context", _fake_ctx)


def test_cli_list_snapshots_reports_empty(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from turbo_memory_mcp.cli import main

    _patch_runtime_context(monkeypatch, store)
    exit_code = main(["migrate", "--list-snapshots"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "No snapshots present." in out


def test_cli_list_snapshots_shows_existing(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from turbo_memory_mcp.cli import main

    store.write_project_manifest()
    snap = create_snapshot(store.storage_root)
    _patch_runtime_context(monkeypatch, store)
    exit_code = main(["migrate", "--list-snapshots"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert snap.name in out


def test_cli_apply_refuses_when_daemon_lockfile_present(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from turbo_memory_mcp.cli import main

    store.write_markdown_manifest()

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _step(_):
        raise AssertionError("must not run while daemon is up")

    # Simulate live primary daemon.
    (store.storage_root / ".daemon.lock").write_text("{}", encoding="utf-8")
    _patch_runtime_context(monkeypatch, store)

    exit_code = main(["migrate", "--apply", "--no-snapshot"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "daemon lockfile present" in err
    # Manifest must not have been bumped.
    assert store.read_markdown_manifest()["format_version"] == 1


def test_cli_apply_force_bypasses_lockfile_check(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from turbo_memory_mcp.cli import main

    store.write_markdown_manifest()
    ran: list[bool] = []

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _step(_):
        ran.append(True)

    (store.storage_root / ".daemon.lock").write_text("{}", encoding="utf-8")
    _patch_runtime_context(monkeypatch, store)

    exit_code = main(["migrate", "--apply", "--no-snapshot", "--force"])
    assert exit_code == 0
    assert ran == [True]
    assert store.read_markdown_manifest()["format_version"] == 2


def test_cli_restore_from_rejects_invalid_path(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from turbo_memory_mcp.cli import main

    _patch_runtime_context(monkeypatch, store)
    bogus = tmp_path / "does_not_exist"
    exit_code = main(["migrate", "--restore-from", str(bogus)])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err.lower()


def test_cli_restore_from_succeeds_with_valid_snapshot(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from turbo_memory_mcp.cli import main

    store.write_project_manifest()
    snap = create_snapshot(store.storage_root)
    # Mutate state after snapshot.
    extra = store.storage_root / "projects" / store.project.project_id / "after.json"
    extra.write_text("{}", encoding="utf-8")

    _patch_runtime_context(monkeypatch, store)
    exit_code = main(["migrate", "--restore-from", str(snap)])
    assert exit_code == 0
    assert not extra.exists()


def test_cli_restore_from_conflicts_with_action_flag(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """argparse mutex enforces it: --restore-from and --apply cannot coexist."""
    from turbo_memory_mcp.cli import main

    _patch_runtime_context(monkeypatch, store)
    store.write_project_manifest()
    snap = create_snapshot(store.storage_root)

    with pytest.raises(SystemExit) as exc_info:
        main(["migrate", "--apply", "--no-snapshot", "--restore-from", str(snap)])
    # argparse uses exit code 2 for usage errors.
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "not allowed with argument" in err


def test_cli_apply_skips_snapshot_when_nothing_pending(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No pending upgrades -> no snapshot is created and we exit cleanly."""
    from turbo_memory_mcp.cli import main
    from turbo_memory_mcp.migrations import list_snapshots as _list_snapshots

    store.write_markdown_manifest()  # at latest version per store constant
    _patch_runtime_context(monkeypatch, store)

    before = _list_snapshots(store.storage_root)
    exit_code = main(["migrate", "--apply"])
    after = _list_snapshots(store.storage_root)

    assert exit_code == 0
    assert "Nothing to migrate." in capsys.readouterr().out
    assert len(after) == len(before)  # no new snapshot


def test_cli_apply_success_prints_snapshot_path(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from turbo_memory_mcp.cli import main

    store.write_markdown_manifest()

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _step(_):
        return None

    _patch_runtime_context(monkeypatch, store)
    exit_code = main(["migrate", "--apply"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Snapshot taken at:" in out
    assert "Applied 1 migration step" in out


def test_cli_apply_snapshot_failure_reports_error(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from turbo_memory_mcp.cli import main
    from turbo_memory_mcp import migrations as migrations_pkg

    store.write_markdown_manifest()

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _step(_):
        raise AssertionError("apply must not run when snapshot fails")

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(migrations_pkg, "create_snapshot", boom)
    _patch_runtime_context(monkeypatch, store)

    exit_code = main(["migrate", "--apply"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "failed to create snapshot" in err
    # Manifest must not have been touched.
    assert store.read_markdown_manifest()["format_version"] == 1


def test_cli_apply_failure_prints_restore_hint(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from turbo_memory_mcp.cli import main

    store.write_markdown_manifest()

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _step(_):
        raise RuntimeError("boom")

    _patch_runtime_context(monkeypatch, store)
    # snapshot enabled (default) so we have something to restore from.
    exit_code = main(["migrate", "--apply"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "--restore-from" in err


# --------------------------------------------------------------------------- #
# Phase 2 — tier separation
# --------------------------------------------------------------------------- #


def test_tier_for_kind_classifies_handoff_as_episodic() -> None:
    from turbo_memory_mcp.store import (
        NOTE_TIER_DURABLE,
        NOTE_TIER_EPISODIC,
        tier_for_kind,
    )

    assert tier_for_kind("handoff") == NOTE_TIER_EPISODIC
    assert tier_for_kind("HANDOFF") == NOTE_TIER_EPISODIC
    assert tier_for_kind("decision") == NOTE_TIER_DURABLE
    assert tier_for_kind("pattern") == NOTE_TIER_DURABLE
    assert tier_for_kind("lesson") == NOTE_TIER_DURABLE
    assert tier_for_kind(None) == NOTE_TIER_DURABLE
    assert tier_for_kind("") == NOTE_TIER_DURABLE


def test_write_project_note_auto_assigns_tier_from_kind(store: MemoryStore) -> None:
    from turbo_memory_mcp.store import (
        NOTE_TIER_DURABLE,
        NOTE_TIER_EPISODIC,
    )

    handoff = store.write_project_note(
        "Session handoff", "context here", note_kind="handoff"
    )
    decision = store.write_project_note(
        "Stack choice", "we picked X", note_kind="decision"
    )
    assert handoff["tier"] == NOTE_TIER_EPISODIC
    assert decision["tier"] == NOTE_TIER_DURABLE


def test_write_project_note_respects_explicit_tier_override(store: MemoryStore) -> None:
    from turbo_memory_mcp.store import NOTE_TIER_DURABLE

    # Even though kind is handoff (would normally -> episodic), an
    # explicit tier override should win when it is a known tier.
    note = store._build_note_record(
        scope="project",
        title="t",
        content="c",
        note_kind="handoff",
        tags=None,
        source_refs=None,
        note_id=None,
        created_at=None,
        tier=NOTE_TIER_DURABLE,
    )
    assert note["tier"] == NOTE_TIER_DURABLE


def test_upgrade_notes_v1_to_v2_adds_tier_to_existing_notes(
    store: MemoryStore,
) -> None:
    """Legacy notes without tier get the right tier from their kind."""
    from turbo_memory_mcp.migrations.io import write_json_atomic
    from turbo_memory_mcp.migrations.upgrades import upgrade_notes_v1_to_v2
    from turbo_memory_mcp.store import (
        NOTE_TIER_DURABLE,
        NOTE_TIER_EPISODIC,
    )

    # Hand-craft legacy notes WITHOUT tier (simulating pre-Phase-2 state).
    handoff_path = store.project_note_path("legacy-handoff")
    decision_path = store.project_note_path("legacy-decision")
    write_json_atomic(
        handoff_path,
        {
            "note_id": "legacy-handoff",
            "scope": "project",
            "project_id": store.project.project_id,
            "project_name": store.project.project_name,
            "title": "old handoff",
            "content": "...",
            "note_kind": "handoff",
            "tags": [],
            "source_refs": [],
            "source_kind": "memory_note",
            "note_status": "active",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )
    write_json_atomic(
        decision_path,
        {
            "note_id": "legacy-decision",
            "scope": "project",
            "project_id": store.project.project_id,
            "project_name": store.project.project_name,
            "title": "old decision",
            "content": "...",
            "note_kind": "decision",
            "tags": [],
            "source_refs": [],
            "source_kind": "memory_note",
            "note_status": "active",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )

    upgrade_notes_v1_to_v2(store)

    import json

    with handoff_path.open() as fh:
        assert json.load(fh)["tier"] == NOTE_TIER_EPISODIC
    with decision_path.open() as fh:
        assert json.load(fh)["tier"] == NOTE_TIER_DURABLE


def test_upgrade_notes_v1_to_v2_is_idempotent(store: MemoryStore) -> None:
    from turbo_memory_mcp.migrations.upgrades import upgrade_notes_v1_to_v2
    from turbo_memory_mcp.store import NOTE_TIER_DURABLE

    # Note already has a tier; second upgrade pass must not overwrite it.
    store.write_project_note(
        "Already tiered",
        "content",
        note_kind="decision",
    )
    upgrade_notes_v1_to_v2(store)
    upgrade_notes_v1_to_v2(store)
    notes = store.list_notes("project")
    assert len(notes) == 1
    assert notes[0]["tier"] == NOTE_TIER_DURABLE


def test_notes_subsystem_treats_pre_phase2_manifest_as_v1(store: MemoryStore) -> None:
    """Manifest written without `format_version` must read as v1, not v0,
    so the runner triggers the v1->v2 migration on existing installs."""
    from turbo_memory_mcp.migrations.io import write_json_atomic
    from turbo_memory_mcp.migrations.runner import _read_current_version
    from turbo_memory_mcp.migrations import Subsystem

    # Simulate a pre-Phase-2 manifest (no format_version field).
    write_json_atomic(
        store.project_manifest_path(),
        {"scope": "project", "project_id": store.project.project_id},
    )
    write_json_atomic(
        store.global_manifest_path(),
        {"scope": "global", "storage_root": str(store.storage_root)},
    )

    assert _read_current_version(store, Subsystem.NOTES) == 1


def test_notes_bump_writes_format_version_to_both_manifests(
    store: MemoryStore,
) -> None:
    from turbo_memory_mcp.migrations.runner import _bump_manifest
    from turbo_memory_mcp.migrations import Subsystem

    # Seed both manifests with the legacy shape (no format_version).
    store.write_project_manifest()
    store.write_global_manifest()
    _bump_manifest(store, Subsystem.NOTES, 2)

    assert store.read_project_manifest()["format_version"] == 2
    assert store.read_global_manifest()["format_version"] == 2
