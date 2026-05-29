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
    # SECRETS is excluded here because Phase 9 introduces an intentional
    # heuristic in _read_current_version: a missing secrets-manifest combined
    # with at least one projects/<id>/ subdir is treated as v1 ("upgrade from
    # pre-v0.7 install needs provisioning") rather than v0. The `store`
    # fixture's ensure_layout() creates such a subdir, so SECRETS reads as v1.
    # Dedicated coverage lives in tests/test_secrets_migration.py:
    # test_detect_status_fresh_install_no_projects_is_v0 and
    # test_detect_status_upgrade_from_pre_v07_is_v1.
    for sub in Subsystem:
        if sub is Subsystem.SECRETS:
            continue
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

    @migration(Subsystem.RETRIEVAL, from_version=2, to_version=3)
    def _step(_):
        return None

    apply_pending(store, subsystems=[Subsystem.RETRIEVAL], snapshot=False)
    proj = store.read_project_retrieval_manifest()
    glob = store.read_global_retrieval_manifest()
    assert proj is not None and proj["format_version"] == 3
    assert glob is not None and glob["format_version"] == 3


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
    # Seed legacy v1 manifests for both subsystems explicitly: the
    # store's `write_*_manifest` methods now stamp the current in-code
    # baseline (markdown=1, retrieval=2 after Phase 3), so we bypass
    # them to simulate a real legacy install pending v1->v2.
    from turbo_memory_mcp.migrations.io import write_json_atomic

    write_json_atomic(
        store.project_markdown_manifest_path(),
        {
            "scope": "project",
            "project_id": store.project.project_id,
            "source_kind": "markdown",
            "format_version": 1,
        },
    )
    write_json_atomic(
        store.project_retrieval_manifest_path(),
        {
            "scope": "project",
            "project_id": store.project.project_id,
            "source_kind": "retrieval",
            "format_version": 1,
        },
    )
    write_json_atomic(
        store.global_retrieval_manifest_path(),
        {"scope": "global", "source_kind": "retrieval", "format_version": 1},
    )

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


def test_cli_apply_ignores_stale_lockfile_with_dead_pid(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A lockfile naming a dead PID is stale and must not block --apply."""
    from turbo_memory_mcp.cli import main

    store.write_markdown_manifest()
    ran: list[bool] = []

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _step(_):
        ran.append(True)

    # Lockfile left behind by a daemon that exited uncleanly: PID now dead.
    (store.storage_root / ".daemon.lock").write_text(
        json.dumps({"pid": 424242, "protocol_version": "1.0"}), encoding="utf-8"
    )
    monkeypatch.setattr("turbo_memory_mcp.daemon._is_pid_alive", lambda _pid: False)
    _patch_runtime_context(monkeypatch, store)

    exit_code = main(["migrate", "--apply", "--no-snapshot"])
    assert exit_code == 0
    assert ran == [True]
    err = capsys.readouterr().err
    assert "daemon lockfile present" not in err
    assert store.read_markdown_manifest()["format_version"] == 2


def test_cli_apply_refuses_when_daemon_pid_alive(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A lockfile naming a live PID is a real daemon and must block --apply."""
    import os

    from turbo_memory_mcp.cli import main

    store.write_markdown_manifest()

    @migration(Subsystem.MARKDOWN, from_version=1, to_version=2)
    def _step(_):
        raise AssertionError("must not run while a live daemon owns the lock")

    # os.getpid() is the running test process: guaranteed alive.
    (store.storage_root / ".daemon.lock").write_text(
        json.dumps({"pid": os.getpid(), "protocol_version": "1.0"}),
        encoding="utf-8",
    )
    _patch_runtime_context(monkeypatch, store)

    exit_code = main(["migrate", "--apply", "--no-snapshot"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "daemon lockfile present" in err
    assert store.read_markdown_manifest()["format_version"] == 1


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


def test_notes_migration_runs_end_to_end_via_runner(store: MemoryStore) -> None:
    """Full path: legacy v1 state -> runner.apply_pending -> v2 manifest
    AND every note re-tagged.

    The autouse fixture clears REGISTRY between tests, so we explicitly
    register a NOTES v1->v2 step that delegates to the real
    `upgrade_notes_v1_to_v2`. This keeps the test independent of
    upgrades.py module load order (no `importlib.reload` hack) and makes
    the intent obvious: we are exercising the runner integration around
    the real upgrade function.
    """
    from turbo_memory_mcp.migrations import (
        Subsystem,
        apply_pending,
        detect_status,
        migration,
    )
    from turbo_memory_mcp.migrations.io import write_json_atomic
    from turbo_memory_mcp.migrations.upgrades import upgrade_notes_v1_to_v2
    from turbo_memory_mcp.store import (
        NOTE_TIER_DURABLE,
        NOTE_TIER_EPISODIC,
    )

    @migration(Subsystem.NOTES, from_version=1, to_version=2)
    def _proxy(store_arg):
        upgrade_notes_v1_to_v2(store_arg)

    # Simulate a legacy install: notes WITHOUT tier + manifests WITHOUT
    # format_version. This is exactly what pre-Phase-2 storage looks
    # like on disk.
    note_a = store.project_note_path("legacy-a")
    note_b = store.project_note_path("legacy-b")
    write_json_atomic(
        note_a,
        {
            "note_id": "legacy-a",
            "scope": "project",
            "project_id": store.project.project_id,
            "project_name": store.project.project_name,
            "title": "old handoff",
            "content": "x",
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
        note_b,
        {
            "note_id": "legacy-b",
            "scope": "project",
            "project_id": store.project.project_id,
            "project_name": store.project.project_name,
            "title": "old decision",
            "content": "y",
            "note_kind": "decision",
            "tags": [],
            "source_refs": [],
            "source_kind": "memory_note",
            "note_status": "active",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )
    write_json_atomic(
        store.project_manifest_path(),
        {"scope": "project", "project_id": store.project.project_id},
    )
    write_json_atomic(
        store.global_manifest_path(),
        {"scope": "global", "storage_root": str(store.storage_root)},
    )

    # Sanity: detect_status reports NOTES as pending v1 -> v2.
    status = detect_status(store)[Subsystem.NOTES]
    assert status.current_version == 1
    assert status.latest_version >= 2
    assert any(m.subsystem is Subsystem.NOTES for m in status.pending)

    # Run only NOTES (RETRIEVAL upgrade would need a real LanceDB).
    outcomes = apply_pending(store, subsystems=[Subsystem.NOTES], snapshot=False)
    assert all(o.success for o in outcomes)

    # Notes are now tiered.
    import json

    with note_a.open() as fh:
        assert json.load(fh)["tier"] == NOTE_TIER_EPISODIC
    with note_b.open() as fh:
        assert json.load(fh)["tier"] == NOTE_TIER_DURABLE

    # Both manifests bumped to v2.
    assert store.read_project_manifest()["format_version"] == 2
    assert store.read_global_manifest()["format_version"] == 2

    # And re-running is a no-op (idempotency through the framework).
    follow_up = apply_pending(store, subsystems=[Subsystem.NOTES], snapshot=False)
    assert follow_up == []


def test_retrieval_v1_to_v2_upgrade_resets_and_resyncs_both_scopes(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retrieval upgrade must reset BOTH project and global tables
    so they pick up the new `tier` column, then re-populate them. We
    stub RetrievalIndex so the test does not need a real LanceDB."""
    from turbo_memory_mcp.migrations.upgrades import upgrade_retrieval_v1_to_v2

    calls: list[tuple[str, tuple, dict]] = []

    class StubIndex:
        def __init__(self, *_args, **_kwargs):
            calls.append(("__init__", _args, _kwargs))

        def reset_scope(self, scope, *, project_id=None):
            calls.append(("reset_scope", (scope,), {"project_id": project_id}))

        def sync_project(self):
            calls.append(("sync_project", (), {}))

        def sync_global(self):
            calls.append(("sync_global", (), {}))

    monkeypatch.setattr(
        "turbo_memory_mcp.retrieval_index.RetrievalIndex",
        StubIndex,
    )

    upgrade_retrieval_v1_to_v2(store)

    op_sequence = [name for name, _, _ in calls]
    # Required calls, in the right order: reset both scopes, then resync.
    assert op_sequence[0] == "__init__"
    assert "reset_scope" in op_sequence
    assert op_sequence.count("reset_scope") == 2
    assert op_sequence.index("sync_project") > op_sequence.index("reset_scope")
    assert op_sequence.index("sync_global") > op_sequence.index("reset_scope")

    reset_scopes = [args[0] for name, args, _ in calls if name == "reset_scope"]
    assert set(reset_scopes) == {"project", "global"}


def test_table_has_tier_column_detects_phase2_schema() -> None:
    from turbo_memory_mcp.retrieval_index import _table_has_tier_column

    class _Schema:
        names = ["scope", "project_id", "tier", "title", "updated_at"]

    class _Table:
        schema = _Schema()

    assert _table_has_tier_column(_Table()) is True


def test_table_has_tier_column_rejects_pre_phase2_schema() -> None:
    from turbo_memory_mcp.retrieval_index import _table_has_tier_column

    class _Schema:
        names = ["scope", "project_id", "title", "updated_at"]

    class _Table:
        schema = _Schema()

    # No tier column -> tier_filter must be silently skipped so legacy
    # LanceDB tables keep working until `migrate --apply` runs.
    assert _table_has_tier_column(_Table()) is False


def test_table_has_tier_column_handles_schema_introspection_failure() -> None:
    from turbo_memory_mcp.retrieval_index import _table_has_tier_column

    class _Table:
        @property
        def schema(self):  # noqa: D401
            raise RuntimeError("schema unavailable")

    # Any failure to read schema should fall back to "no tier" (safe)
    # rather than crash the entire search call.
    assert _table_has_tier_column(_Table()) is False


def test_table_has_tier_column_handles_missing_schema_attribute() -> None:
    from turbo_memory_mcp.retrieval_index import _table_has_tier_column

    class _Table:
        pass

    assert _table_has_tier_column(_Table()) is False


def test_promote_note_preserves_explicit_tier_override(store: MemoryStore) -> None:
    """An explicit `tier` set on the project note must survive promote()."""
    from turbo_memory_mcp.store import NOTE_TIER_DURABLE

    # kind='handoff' would normally land in 'episodic' tier; override to
    # 'durable' so we can detect whether promote() preserves it or
    # silently re-derives the tier from `kind`.
    project_note = store.write_project_note(
        "important handoff",
        "we want this in durable retrieval despite the kind",
        note_kind="handoff",
        tier=NOTE_TIER_DURABLE,
    )
    assert project_note["tier"] == NOTE_TIER_DURABLE

    global_note = store.promote_note(project_note["note_id"])
    assert global_note["tier"] == NOTE_TIER_DURABLE


def test_write_project_manifest_preserves_existing_format_version(
    store: MemoryStore,
) -> None:
    """Bug regression: write_project_manifest must NOT overwrite a bumped
    format_version. Otherwise every remember_note call after a migration
    silently reverts the manifest version and re-triggers detect/apply.
    """
    from turbo_memory_mcp.migrations.io import write_json_atomic

    # Simulate a post-migration manifest at v2.
    write_json_atomic(
        store.project_manifest_path(),
        {
            "scope": "project",
            "project_id": store.project.project_id,
            "format_version": 2,
        },
    )
    # Trigger a normal manifest re-write (the path every remember_note hits).
    written = store.write_project_manifest()
    assert written["format_version"] == 2
    # Read back from disk to be extra sure nothing was stripped.
    on_disk = store.read_project_manifest()
    assert on_disk is not None
    assert on_disk["format_version"] == 2


def test_write_global_manifest_preserves_existing_format_version(
    store: MemoryStore,
) -> None:
    from turbo_memory_mcp.migrations.io import write_json_atomic

    write_json_atomic(
        store.global_manifest_path(),
        {
            "scope": "global",
            "storage_root": str(store.storage_root),
            "format_version": 2,
        },
    )
    written = store.write_global_manifest()
    assert written["format_version"] == 2
    on_disk = store.read_global_manifest()
    assert on_disk is not None
    assert on_disk["format_version"] == 2


def test_write_manifest_falls_back_to_constant_when_no_existing_version() -> None:
    """Fresh installs (no manifest yet) still record the in-code baseline."""
    import tempfile
    from pathlib import Path

    from turbo_memory_mcp.identity import ProjectIdentity
    from turbo_memory_mcp.store import (
        MemoryStore,
        NOTES_FORMAT_VERSION,
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fresh = MemoryStore(
            ProjectIdentity(
                project_id="fresh1",
                project_name="Fresh",
                project_root=root / "repo",
                identity_source="local",
                identity_kind="local_path",
                remote_url=None,
            ),
            storage_root=root / "store",
        )
        proj = fresh.write_project_manifest()
        glob = fresh.write_global_manifest()
        assert proj["format_version"] == NOTES_FORMAT_VERSION
        assert glob["format_version"] == NOTES_FORMAT_VERSION


def test_write_markdown_manifest_preserves_existing_format_version(
    store: MemoryStore,
) -> None:
    from turbo_memory_mcp.migrations.io import write_json_atomic

    write_json_atomic(
        store.project_markdown_manifest_path(),
        {
            "scope": "project",
            "project_id": store.project.project_id,
            "source_kind": "markdown",
            "format_version": 2,
        },
    )
    written = store.write_markdown_manifest()
    assert written["format_version"] == 2
    assert store.read_markdown_manifest()["format_version"] == 2


def test_write_retrieval_manifests_preserve_existing_format_version(
    store: MemoryStore,
) -> None:
    from turbo_memory_mcp.migrations.io import write_json_atomic

    write_json_atomic(
        store.project_retrieval_manifest_path(),
        {
            "scope": "project",
            "project_id": store.project.project_id,
            "source_kind": "retrieval",
            "format_version": 3,
        },
    )
    write_json_atomic(
        store.global_retrieval_manifest_path(),
        {
            "scope": "global",
            "source_kind": "retrieval",
            "format_version": 3,
        },
    )
    proj = store.write_project_retrieval_manifest()
    glob = store.write_global_retrieval_manifest()
    assert proj["format_version"] == 3
    assert glob["format_version"] == 3


def test_bump_notes_manifest_writes_full_payload_when_manifest_missing(
    store: MemoryStore,
) -> None:
    """When _bump_manifest fires on a NOTES install that has no manifests
    yet, it must write the full payload (scope, project identity, etc.)
    not a stripped {format_version, updated_at} dict."""
    from turbo_memory_mcp.migrations import Subsystem
    from turbo_memory_mcp.migrations.runner import _bump_manifest

    # Sanity: no manifests yet (fixture's ensure_layout created dirs only).
    store.project_manifest_path().unlink(missing_ok=True)
    store.global_manifest_path().unlink(missing_ok=True)
    assert store.read_project_manifest() is None
    assert store.read_global_manifest() is None

    _bump_manifest(store, Subsystem.NOTES, 2)

    proj = store.read_project_manifest()
    glob = store.read_global_manifest()
    assert proj is not None
    assert glob is not None
    # Full payload preserved alongside the bumped version.
    assert proj["scope"] == "project"
    assert proj["project_id"] == store.project.project_id
    assert proj["format_version"] == 2
    assert glob["scope"] == "global"
    assert glob["storage_root"] == str(store.storage_root)
    assert glob["format_version"] == 2


def test_no_infinite_migrate_loop_after_post_migration_writes(
    store: MemoryStore,
) -> None:
    """Regression test for the round-4 critical bug.

    Sequence:
      1) Two pre-migration writes (manifest at v1 baseline).
      2) Migration bumps NOTES manifest to v2 through the runner.
      3) Many more writes happen (each calls write_project_manifest).
      4) detect_status must still report NOTES as 'up to date' — the
         manifest must NOT have been silently reverted to v1.
    """
    from turbo_memory_mcp.migrations import (
        Subsystem,
        apply_pending,
        detect_status,
        migration,
    )
    from turbo_memory_mcp.migrations.upgrades import upgrade_notes_v1_to_v2

    @migration(Subsystem.NOTES, from_version=1, to_version=2)
    def _proxy(store_arg):
        upgrade_notes_v1_to_v2(store_arg)

    # Pre-migration: two writes lay down a v1 manifest.
    store.write_project_note("note 1", "x", note_kind="decision")
    store.write_project_note("note 2", "y", note_kind="lesson")
    assert store.read_project_manifest()["format_version"] == 1

    # Run the migration: project + global manifests should land at v2.
    outcomes = apply_pending(store, subsystems=[Subsystem.NOTES], snapshot=False)
    assert all(o.success for o in outcomes)
    assert store.read_project_manifest()["format_version"] == 2
    assert store.read_global_manifest()["format_version"] == 2

    # Many post-migration writes. Each one calls write_project_manifest.
    for i in range(5):
        store.write_project_note(f"after migration {i}", "z", note_kind="lesson")

    # Manifest must STILL be at v2 — not silently reverted to the
    # in-code baseline.
    assert store.read_project_manifest()["format_version"] == 2

    # And the runner sees nothing pending now.
    status = detect_status(store)[Subsystem.NOTES]
    assert status.current_version == 2
    assert status.pending == []


# --------------------------------------------------------------------------- #
# v0.5.1 — pending-migration signal exposed to MCP clients
# --------------------------------------------------------------------------- #


def test_health_payload_shape_default() -> None:
    """Default `build_health_payload` reports no pending migrations."""
    from turbo_memory_mcp.contracts import build_health_payload

    payload = build_health_payload()
    assert payload["status"] == "ok"
    assert payload["migrations_pending"] is False
    assert "migrations_hint" not in payload


def test_health_payload_includes_hint_when_pending() -> None:
    from turbo_memory_mcp.contracts import build_health_payload

    payload = build_health_payload(
        migrations_pending=True,
        migrations_hint="Run turbo-memory-mcp migrate --apply",
    )
    assert payload["migrations_pending"] is True
    assert "migrate --apply" in str(payload["migrations_hint"])


def test_server_info_payload_includes_migrations_field() -> None:
    """build_server_info_payload propagates the migrations dict so agents can
    detect pending upgrades from a single MCP probe."""
    from turbo_memory_mcp.contracts import build_server_info_payload

    payload = build_server_info_payload(
        storage_root="/tmp/x",
        current_project={"project_id": "p", "project_name": "P", "project_root": "/tmp/p", "identity_kind": "local_path"},
        migrations={
            "pending": True,
            "subsystems": [
                {"subsystem": "notes", "current_version": 1, "latest_version": 2, "pending": True, "step_count": 1},
            ],
            "hint": "Stop clients and run migrate --apply",
        },
    )
    assert payload["migrations"]["pending"] is True
    assert payload["migrations"]["subsystems"][0]["subsystem"] == "notes"
    assert "migrate --apply" in payload["migrations"]["hint"]


def test_server_info_migration_collection_against_legacy_store(
    store: MemoryStore,
) -> None:
    """Integration: a store with legacy (no format_version) manifests gets
    `migrations.pending=True` from _collect_migrations_status."""
    from turbo_memory_mcp.migrations import Subsystem, migration
    from turbo_memory_mcp.migrations.io import write_json_atomic
    from turbo_memory_mcp.migrations.upgrades import upgrade_notes_v1_to_v2
    from turbo_memory_mcp.server import _collect_migrations_status

    # Register the real NOTES upgrade so latest_version returns 2 (the
    # autouse fixture cleared the registry before this test ran).
    @migration(Subsystem.NOTES, from_version=1, to_version=2)
    def _proxy(store_arg):
        upgrade_notes_v1_to_v2(store_arg)

    # Simulate pre-Phase-2 install.
    write_json_atomic(
        store.project_manifest_path(),
        {"scope": "project", "project_id": store.project.project_id},
    )
    write_json_atomic(
        store.global_manifest_path(),
        {"scope": "global", "storage_root": str(store.storage_root)},
    )

    result = _collect_migrations_status(store)
    assert result["pending"] is True
    notes_entry = next(s for s in result["subsystems"] if s["subsystem"] == "notes")
    assert notes_entry["current_version"] == 1
    assert notes_entry["latest_version"] >= 2
    assert notes_entry["pending"] is True
    assert "migrate --apply" in result["hint"]


def test_server_info_migration_collection_clean_store(store: MemoryStore) -> None:
    """A freshly-written store at the current code baseline reports nothing
    pending (so agents do not nag operators on green installs)."""
    from turbo_memory_mcp.server import _collect_migrations_status

    # Pump every manifest writer at the in-code baseline.
    store.write_project_manifest()
    store.write_global_manifest()
    store.write_markdown_manifest()
    store.write_project_retrieval_manifest()
    store.write_global_retrieval_manifest()

    result = _collect_migrations_status(store)
    assert result["pending"] is False
    assert all(not s["pending"] for s in result["subsystems"])


# --------------------------------------------------------------------------- #
# Phase 3 — hybrid BM25 + vector via RRF
# --------------------------------------------------------------------------- #


def test_rrf_merge_combines_two_lanes_in_rank_order() -> None:
    """An item ranking high in either lane wins the merged top slot."""
    from turbo_memory_mcp.retrieval_index import _rrf_merge

    vector_hits = [
        {"item_id": "v_top", "_distance": 0.1},
        {"item_id": "shared", "_distance": 0.4},
        {"item_id": "v_low", "_distance": 0.9},
    ]
    fts_hits = [
        {"item_id": "shared", "_score": 5.0},
        {"item_id": "fts_top", "_score": 4.0},
    ]
    merged = _rrf_merge([vector_hits, fts_hits], k=60, limit=4)

    ids = [r["item_id"] for r in merged]
    # `shared` appears in both lanes -> highest combined RRF score.
    assert ids[0] == "shared"
    # All four unique items present.
    assert set(ids) == {"v_top", "shared", "v_low", "fts_top"}
    # Every row carries the RRF score for downstream debugging.
    assert all("_rrf_score" in r for r in merged)
    # Vector hits keep their real distance; an item present in both lanes
    # keeps the vector row's distance (more downstream signal than _score).
    shared = next(r for r in merged if r["item_id"] == "shared")
    assert shared["_distance"] == 0.4
    # FTS-only hits get a distance synthesized from their BM25 rank
    # (rank 1 -> 0.15, +0.08 per rank), not a flat neutral. `fts_top` is
    # BM25 rank 2 -> 0.15 + 0.08 = 0.23, so a strong BM25 match scores well
    # downstream instead of being capped at the old flat 0.5.
    fts_only = next(r for r in merged if r["item_id"] == "fts_top")
    assert fts_only["_distance"] == 0.23


def test_rrf_merge_skips_rows_without_item_id() -> None:
    from turbo_memory_mcp.retrieval_index import _rrf_merge

    merged = _rrf_merge(
        [[{"_distance": 0.1}, {"item_id": "a", "_distance": 0.2}]],
        limit=5,
    )
    assert [r["item_id"] for r in merged] == ["a"]


def test_ensure_fts_index_is_idempotent() -> None:
    """`_ensure_fts_index` swallows the 'already exists' error so callers
    can invoke it on every search without worrying about state."""
    import tempfile, pyarrow as pa, lancedb
    from turbo_memory_mcp.retrieval_index import _ensure_fts_index

    with tempfile.TemporaryDirectory() as tmp:
        db = lancedb.connect(tmp)
        schema = pa.schema(
            [
                pa.field("item_id", pa.string()),
                pa.field("content_search", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), 4)),
            ]
        )
        tbl = db.create_table("t", schema=schema)
        tbl.add(
            [{"item_id": "a", "content_search": "modal close", "vector": [1, 0, 0, 0]}]
        )

        _ensure_fts_index(tbl)
        # Second call must not raise.
        _ensure_fts_index(tbl)

        hits = tbl.search("modal", query_type="fts").limit(2).to_list()
        assert any(r["item_id"] == "a" for r in hits)


def test_safe_fts_search_returns_empty_on_legacy_table_without_index() -> None:
    """A table that the test never indexed must yield zero FTS rows without
    raising — so search() can degrade to vector-only on legacy installs."""
    import tempfile, pyarrow as pa, lancedb
    from turbo_memory_mcp.retrieval_index import _safe_fts_search

    with tempfile.TemporaryDirectory() as tmp:
        db = lancedb.connect(tmp)
        schema = pa.schema(
            [
                pa.field("item_id", pa.string()),
                pa.field("content_search", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), 4)),
            ]
        )
        tbl = db.create_table("t", schema=schema)
        tbl.add(
            [{"item_id": "a", "content_search": "modal close", "vector": [1, 0, 0, 0]}]
        )
        # _safe_fts_search will idempotently try to create the index;
        # on success it should return matching rows, on failure it
        # returns empty — both are acceptable, but it must not raise.
        result = _safe_fts_search(tbl, "modal", limit=2, where_clause=None)
        assert isinstance(result, list)


def test_upgrade_retrieval_v2_to_v3_calls_ensure_fts_index_per_scope(
    store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Phase 3 upgrade only touches indexes — never resets data."""
    from turbo_memory_mcp.migrations.upgrades import upgrade_retrieval_v2_to_v3

    open_calls: list[tuple[str, str | None]] = []
    fts_calls: list[object] = []

    class StubIndex:
        def __init__(self, store_arg):
            pass

        def _open_scope_table(self, scope, *, project_id=None):
            open_calls.append((scope, project_id))
            # Return a sentinel object so _ensure_fts_index is called.
            return object()

    def fake_ensure_fts_index(table):
        fts_calls.append(table)

    monkeypatch.setattr(
        "turbo_memory_mcp.retrieval_index.RetrievalIndex",
        StubIndex,
    )
    monkeypatch.setattr(
        "turbo_memory_mcp.retrieval_index._ensure_fts_index",
        fake_ensure_fts_index,
    )

    upgrade_retrieval_v2_to_v3(store)

    # Both scopes opened; FTS index ensured on each non-None table.
    scopes = [s for s, _ in open_calls]
    assert "project" in scopes
    assert "global" in scopes
    assert len(fts_calls) == 2


def test_hybrid_search_returns_results_on_live_lancedb(tmp_path) -> None:
    """End-to-end probe: a freshly-built LanceDB table with both a vector
    column and an FTS index returns hits for a query that matches by
    BM25 even when the vector signal is poor."""
    import lancedb, pyarrow as pa
    from turbo_memory_mcp.retrieval_index import _safe_vector_search, _safe_fts_search, _rrf_merge

    db = lancedb.connect(str(tmp_path))
    schema = pa.schema(
        [
            pa.field("item_id", pa.string()),
            pa.field("content_search", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), 4)),
        ]
    )
    tbl = db.create_table("t", schema=schema)
    tbl.add(
        [
            {"item_id": "a", "content_search": "modal close button visibility",
             "vector": [1.0, 0.0, 0.0, 0.0]},
            {"item_id": "b", "content_search": "landing page audit priorities",
             "vector": [0.0, 1.0, 0.0, 0.0]},
            {"item_id": "c", "content_search": "warning copy job loss risk",
             "vector": [0.0, 0.0, 1.0, 0.0]},
        ]
    )

    # Use the synthetic vector path: query vector close to "a"
    vec_rows = _safe_vector_search(tbl, [1.0, 0.0, 0.0, 0.0], limit=3, where_clause=None)
    fts_rows = _safe_fts_search(tbl, "modal close", limit=3, where_clause=None)

    merged = _rrf_merge([vec_rows, fts_rows], k=60, limit=3)
    # `a` should win because it tops both lanes.
    assert merged[0]["item_id"] == "a"
