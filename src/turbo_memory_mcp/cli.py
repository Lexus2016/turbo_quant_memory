"""Command line interface for the local Turbo Quant Memory MCP server."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="turbo-memory-mcp",
        description="Run the local Turbo Quant Memory MCP server.",
        epilog="Blessed runtime: turbo-memory-mcp serve",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="command")
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the local stdio MCP server.",
        description="Start the local stdio MCP server.",
    )
    serve_parser.set_defaults(handler=_handle_serve)

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Inspect or apply pending schema migrations.",
        description=(
            "Inspect or apply pending schema migrations. Default is "
            "--status (no mutations). Use --apply to upgrade after taking "
            "a rolling snapshot."
        ),
    )
    action_group = migrate_parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--status",
        dest="migrate_action",
        action="store_const",
        const="status",
        help="Show current vs latest version per subsystem (default).",
    )
    action_group.add_argument(
        "--dry-run",
        dest="migrate_action",
        action="store_const",
        const="dry_run",
        help="List pending upgrades without touching storage.",
    )
    action_group.add_argument(
        "--apply",
        dest="migrate_action",
        action="store_const",
        const="apply",
        help="Take snapshot, then apply all pending upgrades.",
    )
    action_group.add_argument(
        "--snapshot-only",
        dest="migrate_action",
        action="store_const",
        const="snapshot_only",
        help="Create a rolling snapshot without applying anything.",
    )
    action_group.add_argument(
        "--list-snapshots",
        dest="migrate_action",
        action="store_const",
        const="list_snapshots",
        help="Show available snapshots (oldest -> newest).",
    )
    action_group.add_argument(
        "--restore-from",
        dest="restore_path",
        metavar="SNAPSHOT_PATH",
        help="Restore live storage from the given snapshot directory.",
    )
    migrate_parser.add_argument(
        "--no-snapshot",
        action="store_true",
        help="Skip the snapshot step before --apply (use only in tests).",
    )
    migrate_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass safety checks (e.g. running daemon detection).",
    )
    migrate_parser.set_defaults(
        migrate_action="status",
        restore_path=None,
        handler=_handle_migrate,
    )
    return parser


def _handle_serve(_: argparse.Namespace) -> int:
    from .server import run_stdio_server

    run_stdio_server()
    return 0


def _handle_migrate(args: argparse.Namespace) -> int:
    from .migrations import (
        Subsystem,
        apply_pending,
        create_snapshot,
        detect_status,
        list_snapshots,
        restore_snapshot,
    )
    from .server import build_runtime_context

    try:
        _, store = build_runtime_context()
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot resolve storage context: {exc}", file=sys.stderr)
        return 1

    # Mutual exclusion between --restore-from and the action flags is
    # enforced by argparse (all of them share the same action_group).
    if args.restore_path is not None:
        return _migrate_restore_from(
            store, restore_snapshot, args.restore_path, force=args.force
        )

    action = args.migrate_action
    if action == "status":
        return _migrate_print_status(store, detect_status, Subsystem)
    if action == "dry_run":
        return _migrate_dry_run(store, apply_pending)
    if action == "apply":
        return _migrate_apply(
            store,
            apply_pending,
            snapshot=not args.no_snapshot,
            force=args.force,
        )
    if action == "snapshot_only":
        return _migrate_snapshot_only(store, create_snapshot)
    if action == "list_snapshots":
        return _migrate_list_snapshots(store, list_snapshots)
    print(f"error: unknown migrate action: {action!r}", file=sys.stderr)
    return 1


def _daemon_lockfile_present(store) -> "Path | None":
    """Return the lockfile path if a primary daemon may be running."""
    from pathlib import Path

    lock = Path(store.storage_root) / ".daemon.lock"
    return lock if lock.exists() else None


def _migrate_print_status(store, detect_status, Subsystem) -> int:  # noqa: N803
    statuses = detect_status(store)
    pending_total = 0
    print(f"storage_root: {store.storage_root}")
    for sub in Subsystem:
        status = statuses[sub]
        marker = "OK" if not status.needs_upgrade else "UPGRADE"
        line = (
            f"  [{marker}] {sub.value:>12s}  "
            f"current=v{status.current_version}  "
            f"latest=v{status.latest_version}"
        )
        if status.needs_upgrade:
            pending_total += len(status.pending)
            line += f"  pending={len(status.pending)}"
        if status.error:
            line += f"  error={status.error}"
        print(line)
    if pending_total == 0:
        print("\nAll subsystems are up to date.")
    else:
        print(
            f"\n{pending_total} migration step(s) pending. "
            "Run with --dry-run to inspect, --apply to upgrade."
        )
    return 0


def _migrate_dry_run(store, apply_pending) -> int:  # noqa: N803
    outcomes = apply_pending(store, dry_run=True)
    if not outcomes:
        print("Nothing to migrate.")
        return 0
    print(f"Would apply {len(outcomes)} migration step(s):")
    for outcome in outcomes:
        m = outcome.migration
        desc = f": {m.description}" if m.description else ""
        print(
            f"  - {m.subsystem.value} v{m.from_version} -> v{m.to_version}{desc}"
        )
    print("\nRe-run with --apply to perform the upgrade.")
    return 0


def _migrate_apply(store, apply_pending, *, snapshot: bool, force: bool) -> int:  # noqa: N803
    from .migrations import create_snapshot as _create_snapshot
    from .migrations import detect_status as _detect_status

    if not force:
        lock = _daemon_lockfile_present(store)
        if lock is not None:
            print(
                f"error: daemon lockfile present at {lock}. Stop the running "
                "primary daemon (close all MCP clients) before --apply, or "
                "pass --force if you are sure no daemon is writing.",
                file=sys.stderr,
            )
            return 1

    # Skip the (potentially expensive) snapshot when there is nothing
    # to migrate — otherwise users get a backup every time they call
    # --apply by reflex.
    statuses = _detect_status(store)
    if not any(s.needs_upgrade for s in statuses.values()):
        print("Nothing to migrate.")
        return 0

    # Take the snapshot here (not inside apply_pending) so the CLI can
    # reference the exact path in both the success message and the
    # roll-back hint on failure. Pass snapshot=False downstream to avoid
    # a second backup.
    snap_path = None
    if snapshot:
        try:
            snap_path = _create_snapshot(store.storage_root)
        except OSError as exc:
            print(
                f"error: failed to create snapshot before --apply: {exc}",
                file=sys.stderr,
            )
            return 1
        print(f"Snapshot taken at: {snap_path}")
    else:
        print(
            "warning: --no-snapshot set, no rolling backup will be taken.",
            file=sys.stderr,
        )

    outcomes = apply_pending(store, dry_run=False, snapshot=False)
    if not outcomes:
        # Pending was non-empty during detect but apply produced no work
        # — extremely unlikely (registry mutation under our feet). Treat
        # it as a no-op but the user already paid for the snapshot.
        print("Nothing to migrate.")
        return 0
    failed = [o for o in outcomes if not o.success]
    for outcome in outcomes:
        m = outcome.migration
        flag = "OK" if outcome.success else "FAIL"
        line = (
            f"  [{flag}] {m.subsystem.value} v{m.from_version} -> v{m.to_version}  "
            f"{outcome.duration_ms:.1f} ms"
        )
        if outcome.error:
            line += f"  error={outcome.error}"
        print(line)
    if failed:
        print(
            f"\n{len(failed)} step(s) failed. Storage left at last "
            "successfully-bumped version. "
            "Inspect ~/.turbo-quant-memory/migration.log for details.",
            file=sys.stderr,
        )
        if snap_path is not None:
            print(
                f"To roll back, run: turbo-memory-mcp migrate --restore-from {snap_path}",
                file=sys.stderr,
            )
        return 1
    print(f"\nApplied {len(outcomes)} migration step(s).")
    return 0


def _migrate_snapshot_only(store, create_snapshot) -> int:  # noqa: N803
    path = create_snapshot(store.storage_root)
    print(f"Snapshot created at: {path}")
    return 0


def _migrate_list_snapshots(store, list_snapshots) -> int:  # noqa: N803
    snaps = list_snapshots(store.storage_root)
    if not snaps:
        print("No snapshots present.")
        return 0
    print(f"Snapshots under {store.storage_root}/.snapshots/ (oldest -> newest):")
    for snap in snaps:
        print(f"  {snap.name}  ({snap})")
    return 0


def _migrate_restore_from(store, restore_snapshot, path_arg: str, *, force: bool) -> int:  # noqa: N803
    from pathlib import Path

    if not force:
        lock = _daemon_lockfile_present(store)
        if lock is not None:
            print(
                f"error: daemon lockfile present at {lock}. Stop the running "
                "primary daemon before --restore-from, or pass --force.",
                file=sys.stderr,
            )
            return 1
    snap_path = Path(path_arg).expanduser().resolve()
    try:
        restore_snapshot(store.storage_root, snap_path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Restored storage from snapshot: {snap_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    return int(handler(args))
