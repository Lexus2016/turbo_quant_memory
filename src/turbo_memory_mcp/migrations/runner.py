"""Migration orchestration: detect pending upgrades and apply them.

The runner is opt-in. Daemon startup only calls `detect_status` and logs
a warning if anything is pending. Apply is triggered by the CLI
(`turbo-memory-mcp migrate --apply`) so the user sees and approves the
snapshot step.

Flow per subsystem:
    1. Read current format_version from manifest (0 if no manifest yet).
    2. Look up the registered upgrade chain from current to latest.
    3. For each step: run the upgrade function, then write the new
       format_version to the manifest. Manifest is the source of truth —
       it is updated LAST so a crash leaves the previous version in place
       and the upgrade can be safely retried.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..store import MemoryStore
from .io import write_json_atomic
from .log import log_event
from .registry import (
    Migration,
    Subsystem,
    get_chain,
    latest_version,
)
from .snapshot import create_snapshot


@dataclass
class SubsystemStatus:
    """Per-subsystem detection result."""

    subsystem: Subsystem
    current_version: int
    latest_version: int
    pending: list[Migration] = field(default_factory=list)
    error: str | None = None

    @property
    def needs_upgrade(self) -> bool:
        return bool(self.pending)


@dataclass
class MigrationOutcome:
    """Result of attempting one migration step."""

    migration: Migration
    success: bool
    error: str | None = None
    duration_ms: float = 0.0


def detect_status(store: MemoryStore) -> dict[Subsystem, SubsystemStatus]:
    """Detect per-subsystem version state without mutating anything."""
    return {sub: _status_for(store, sub) for sub in Subsystem}


def format_pending_warning(store: MemoryStore) -> str | None:
    """Return a one-paragraph warning if any subsystem has pending upgrades.

    None if everything is current. Caller decides where to write it
    (typically stderr on daemon startup).
    """
    statuses = detect_status(store)
    pending_lines: list[str] = []
    for status in statuses.values():
        if status.error:
            pending_lines.append(
                f"  {status.subsystem.value}: chain error - {status.error}"
            )
            continue
        if status.needs_upgrade:
            chain_desc = ", ".join(
                f"v{m.from_version}->v{m.to_version}" for m in status.pending
            )
            pending_lines.append(
                f"  {status.subsystem.value}: v{status.current_version} -> "
                f"v{status.latest_version} ({chain_desc})"
            )
    if not pending_lines:
        return None
    return (
        "[tqmemory] pending migrations detected. Run "
        "'turbo-memory-mcp migrate --status' for details, then "
        "'turbo-memory-mcp migrate --apply' to upgrade.\n"
        + "\n".join(pending_lines)
    )


def apply_pending(
    store: MemoryStore,
    *,
    subsystems: Iterable[Subsystem] | None = None,
    dry_run: bool = False,
    snapshot: bool = True,
) -> list[MigrationOutcome]:
    """Apply pending upgrades for the given subsystems.

    `dry_run=True` returns the outcomes that would be produced without
    touching disk. `snapshot=False` skips the rolling backup (only safe
    for tests).
    """
    targets = list(subsystems) if subsystems is not None else list(Subsystem)
    statuses = {s: _status_for(store, s) for s in targets}
    pending = [(s, status) for s, status in statuses.items() if status.needs_upgrade]

    if not pending:
        log_event(
            "detect_pending",
            subsystems=[s.value for s in targets],
            result="up_to_date",
        )
        return []

    if dry_run:
        outcomes: list[MigrationOutcome] = []
        for sub, status in pending:
            for mig in status.pending:
                outcomes.append(MigrationOutcome(mig, success=True))
                log_event(
                    "dry_run",
                    subsystem=sub.value,
                    from_version=mig.from_version,
                    to_version=mig.to_version,
                    description=mig.description,
                )
        return outcomes

    if snapshot:
        snap_path = create_snapshot(store.storage_root)
        log_event("snapshot_created", path=str(snap_path))

    outcomes = []
    for sub, status in pending:
        for mig in status.pending:
            outcome = _run_one(store, mig)
            outcomes.append(outcome)
            if not outcome.success:
                # Stop the whole run on first failure; surrounding manifests stay
                # at their pre-step versions (manifest is bumped last on success).
                return outcomes
    return outcomes


def _run_one(store: MemoryStore, mig: Migration) -> MigrationOutcome:
    t0 = time.monotonic()
    try:
        mig.func(store)
        _bump_manifest(store, mig.subsystem, mig.to_version)
    except Exception as exc:  # noqa: BLE001 — we capture all to log + return
        ms = (time.monotonic() - t0) * 1000
        log_event(
            "apply_failure",
            subsystem=mig.subsystem.value,
            from_version=mig.from_version,
            to_version=mig.to_version,
            duration_ms=ms,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return MigrationOutcome(mig, success=False, error=str(exc), duration_ms=ms)

    ms = (time.monotonic() - t0) * 1000
    log_event(
        "apply_success",
        subsystem=mig.subsystem.value,
        from_version=mig.from_version,
        to_version=mig.to_version,
        duration_ms=ms,
    )
    return MigrationOutcome(mig, success=True, duration_ms=ms)


def _status_for(store: MemoryStore, subsystem: Subsystem) -> SubsystemStatus:
    current = _read_current_version(store, subsystem)
    latest = latest_version(subsystem)
    error: str | None = None
    chain: list[Migration] = []
    if current >= 1:
        try:
            chain = get_chain(subsystem, current)
        except ValueError as exc:
            error = str(exc)
    return SubsystemStatus(
        subsystem=subsystem,
        current_version=current,
        latest_version=latest,
        pending=chain,
        error=error,
    )


def _read_current_version(store: MemoryStore, subsystem: Subsystem) -> int:
    if subsystem is Subsystem.MARKDOWN:
        manifest = store.read_markdown_manifest()
        return _version_from(manifest)
    if subsystem is Subsystem.RETRIEVAL:
        proj = store.read_project_retrieval_manifest()
        glob = store.read_global_retrieval_manifest()
        v_proj = _version_from(proj)
        v_glob = _version_from(glob)
        # Both should march together; if they drift, conservative MIN forces
        # the lagging one to catch up first.
        present = [v for v in (v_proj, v_glob) if v > 0]
        if not present:
            return 0
        return min(present)
    if subsystem is Subsystem.USAGE_STATS:
        usage = store.read_usage_stats()
        return _version_from(usage)
    if subsystem is Subsystem.NOTES:
        # NOTES uses project_manifest + global_manifest. Pre-Phase-2 those
        # manifests had no `format_version` at all, so a manifest that
        # exists without the field is treated as v1 (legacy, needs
        # upgrade). Absence of both manifests means a fresh install with
        # no notes yet -> no migration needed (v0).
        proj = store.read_project_manifest()
        glob = store.read_global_manifest()
        v_proj = _legacy_v1_or_format_version(proj)
        v_glob = _legacy_v1_or_format_version(glob)
        present = [v for v in (v_proj, v_glob) if v > 0]
        if not present:
            return 0
        return min(present)
    if subsystem is Subsystem.SECRETS:
        # SECRETS uses a single subsystem-level marker at storage_root.
        # Per-project secrets/meta.json files have their own internal version
        # but are not consulted here — they are managed by SecretsStore.
        #
        # When the marker is missing we distinguish two cases so fresh
        # installs do not see a noisy "pending migration" warning:
        #   - storage has at least one projects/<id>/ dir already -> v1
        #     ("upgrade from v0.6.1-era install; needs provisioning")
        #   - storage has no project dirs yet                     -> v0
        #     ("nothing to provision; no upgrade needed")
        manifest = store.read_secrets_manifest()
        if manifest:
            return _version_from(manifest)
        projects_root = store.storage_root / "projects"
        if not projects_root.exists():
            return 0
        try:
            has_projects = any(projects_root.iterdir())
        except OSError:
            return 0
        return 1 if has_projects else 0
    raise ValueError(f"Unknown subsystem: {subsystem!r}")


def _legacy_v1_or_format_version(manifest: dict[str, Any] | None) -> int:
    """Existing manifests without `format_version` -> 1 (legacy).

    Missing manifest -> 0 (fresh install). Manifest with `format_version`
    -> that integer.
    """
    if not manifest:
        return 0
    if "format_version" in manifest:
        return _version_from(manifest)
    return 1


def _version_from(manifest: dict[str, Any] | None) -> int:
    if not manifest:
        return 0
    raw = manifest.get("format_version", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _bump_manifest(store: MemoryStore, subsystem: Subsystem, new_version: int) -> None:
    """Set format_version in the relevant manifest(s) to new_version.

    Uses direct atomic JSON write so we can target a specific version
    independently of the in-code constants in store.py — that way a
    migration step from N to N+1 ends with format_version=N+1 even if
    the constant in store.py still reads N (Phase 1+ will bump the
    constant when introducing real upgrade logic).
    """
    timestamp = _utc_now()
    if subsystem is Subsystem.MARKDOWN:
        path = store.project_markdown_manifest_path()
        _bump_one(path, store.read_markdown_manifest(), new_version, timestamp)
        return
    if subsystem is Subsystem.RETRIEVAL:
        proj_path = store.project_retrieval_manifest_path()
        glob_path = store.global_retrieval_manifest_path()
        _bump_one(proj_path, store.read_project_retrieval_manifest(), new_version, timestamp)
        _bump_one(glob_path, store.read_global_retrieval_manifest(), new_version, timestamp)
        return
    if subsystem is Subsystem.USAGE_STATS:
        path = store.usage_stats_path()
        _bump_one(path, store.read_usage_stats(), new_version, timestamp)
        return
    if subsystem is Subsystem.NOTES:
        # If a manifest is missing, ask the store to create the proper
        # full payload (scope, project identity, storage_root, etc.)
        # so we never leave a stripped {format_version, updated_at}
        # behind. Then bump the version atomically.
        if store.read_project_manifest() is None:
            store.write_project_manifest()
        if store.read_global_manifest() is None:
            store.write_global_manifest()
        proj_path = store.project_manifest_path()
        glob_path = store.global_manifest_path()
        _bump_one(proj_path, store.read_project_manifest(), new_version, timestamp)
        _bump_one(glob_path, store.read_global_manifest(), new_version, timestamp)
        return
    if subsystem is Subsystem.SECRETS:
        path = store.secrets_manifest_path()
        _bump_one(path, store.read_secrets_manifest(), new_version, timestamp)
        return
    raise ValueError(f"Unknown subsystem: {subsystem!r}")


def _bump_one(path: Path, current: dict[str, Any] | None, new_version: int, when: str) -> None:
    payload = dict(current) if current else {}
    payload["format_version"] = new_version
    payload["updated_at"] = when
    write_json_atomic(path, payload)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
