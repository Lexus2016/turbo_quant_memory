"""Command line interface for the local Turbo Quant Memory MCP server."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
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

    secret_set_parser = subparsers.add_parser(
        "secret-set",
        help=(
            "Set a project secret without exposing the value to any chat "
            "transcript (input is read via getpass on a TTY)."
        ),
        description=(
            "Store a secret in the active project's encrypted vault. The "
            "value is read from stdin: when invoked on a TTY, getpass "
            "prompts with hidden input so the value never enters shell "
            "history or scrollback; when invoked from a pipe, stdin is "
            "consumed verbatim (trailing newline stripped). Active project "
            "is resolved from the current working directory the same way "
            "as the MCP server."
        ),
    )
    secret_set_parser.add_argument(
        "name",
        help="Secret name. Must match [A-Za-z0-9_.-]{1,128}.",
    )
    secret_set_parser.set_defaults(handler=_handle_secret_set)

    prune_parser = subparsers.add_parser(
        "prune-orphans",
        help="List or move orphaned project buckets (recorded root no longer exists).",
        description=(
            "Surface project buckets whose recorded project_root no longer "
            "exists on disk and, with --apply, MOVE them to staging/ "
            "(reversible) instead of deleting. Default is a dry run that only "
            "lists them. A missing root is not proof a project is dead (an "
            "unmounted volume, or storage shared across machines), so removal "
            "is never automatic and never a hard delete."
        ),
    )
    prune_parser.add_argument(
        "--apply",
        action="store_true",
        help="Move orphaned buckets to staging/ (reversible). Default lists only.",
    )
    prune_parser.set_defaults(handler=_handle_prune_orphans)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Quick diagnostics for daemon lock, migrations, and storage health.",
        description=(
            "Run a suite of quick checks: lockfile state, migration status, "
            "storage directory health, and socket reachability. Prints "
            "PASS/FAIL for each check with a short summary."
        ),
    )
    doctor_parser.set_defaults(handler=_handle_doctor)

    return parser


def _handle_serve(_: argparse.Namespace) -> int:
    from .server import run_stdio_server

    run_stdio_server()
    return 0


def _handle_prune_orphans(args: argparse.Namespace) -> int:
    """List orphaned project buckets, or (with --apply) move them to staging/.

    Never hard-deletes and never runs automatically: a missing project_root is
    not proof a project is dead (an unmounted external/network volume, or a
    storage root shared across machines), so the operator decides, and the move
    is reversible. Orphan buckets are by definition not the active project, so
    moving them is safe even while a daemon is running.
    """
    import shutil
    import time

    from .store import detect_orphaned_buckets, resolve_storage_root

    storage_root = resolve_storage_root()
    orphans = detect_orphaned_buckets(storage_root)
    if not orphans:
        print("No orphaned buckets — every project bucket maps to an existing root.")
        return 0

    print(f"Orphaned buckets ({len(orphans)} — recorded project_root missing on disk):")
    for orphan in orphans:
        print(
            f"  {orphan['project_id']}  {orphan['project_name']}  "
            f"notes={orphan['note_count']}  root={orphan['project_root']}"
        )

    if not args.apply:
        print(
            "\nDry run. Re-run with --apply to MOVE these to staging/ "
            "(reversible; no hard delete)."
        )
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest_root = storage_root / "staging" / f"orphan-prune-{stamp}"
    dest_root.mkdir(parents=True, exist_ok=True)
    moved = 0
    for orphan in orphans:
        src = storage_root / "projects" / orphan["project_id"]
        if not src.is_dir():
            continue
        shutil.move(str(src), str(dest_root / orphan["project_id"]))
        moved += 1
    print(f"\nMoved {moved} bucket(s) to {dest_root}")
    print(
        "Reversible: move a bucket back under projects/ to restore it, or delete "
        "the staging directory to purge."
    )
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
    """Return the lockfile path only if a *live* primary daemon owns it.

    Bare file existence is not enough. A daemon that exits uncleanly (SIGKILL,
    crash, host sleep) never runs its release hook, so it leaves behind a
    lockfile naming a now-dead PID. Treating that stale file as a live owner
    wedges ``--apply`` / ``--restore-from`` forever even though nothing is
    writing. Reuse the daemon's own PID-liveness check so this guard and daemon
    startup agree on what counts as a live owner: only a lock whose PID is
    still alive blocks. A malformed lock (unreadable, or missing its PID) stays
    conservative and reports as present so the operator decides via ``--force``.
    """
    from pathlib import Path

    from .daemon import _is_pid_alive, _read_lockfile

    lock = Path(store.storage_root) / ".daemon.lock"
    payload = _read_lockfile(lock)
    if payload is None:
        return None
    pid = payload.get("pid")
    if not isinstance(pid, int):
        return lock
    return lock if _is_pid_alive(pid) else None


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


def _handle_secret_set(args: argparse.Namespace) -> int:
    """Set a project secret without exposing the value to a chat transcript.

    Records the write into the per-project audit log on success — the CLI
    is the canonical first-time setup path, so audit coverage parity with
    the MCP ``set_secret`` impl is essential for accurate access history.

    Exit codes:
        0 - secret stored
        2 - invalid input (empty value, invalid name)
        3 - master key unavailable; stderr carries the setup hint verbatim
        4 - master key does not match the existing vault (mismatch); stderr
            carries the actionable hint verbatim
        130 - interrupted at the hidden-input prompt
    """
    import getpass

    from .secrets import (
        AuditLog,
        MasterKeyUnavailable,
        SecretsStore,
        VaultDecryptError,
    )
    from .server import build_runtime_context

    name = args.name

    if sys.stdin.isatty():
        try:
            value = getpass.getpass(f"Value for '{name}' (input hidden): ")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.", file=sys.stderr)
            return 130
    else:
        value = sys.stdin.read().rstrip("\n")

    if not value:
        print("error: empty value rejected.", file=sys.stderr)
        return 2

    project, store = build_runtime_context()
    vault = SecretsStore(store.storage_root, project.project_id)
    try:
        vault.set(name, value)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except MasterKeyUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except VaultDecryptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4

    AuditLog(vault.secrets_dir).record("set", name)
    print(f"Stored secret '{name}' for project '{project.project_id}'.")
    return 0


def _handle_doctor(_: argparse.Namespace) -> int:
    """Run quick diagnostics: lock, migrations, storage, socket."""
    import socket

    from .daemon import (
        HEALTH_CONNECT_TIMEOUT_SECONDS,
        _is_pid_alive,
        _read_lockfile,
    )
    from .server import build_runtime_context
    from .store import resolve_storage_root

    issues = 0

    # 1. Storage root
    try:
        storage_root = resolve_storage_root()
        if storage_root.is_dir():
            print(f"[PASS] storage_root: {storage_root}")
        else:
            print(f"[FAIL] storage_root missing: {storage_root}")
            issues += 1
    except Exception as exc:
        print(f"[FAIL] storage_root resolution: {exc}")
        issues += 1
        return issues

    # 2. Lockfile
    lock = storage_root / ".daemon.lock"
    payload = _read_lockfile(lock)
    if payload is None:
        print("[PASS] lockfile: no lock present (standalone or no daemon)")
    else:
        pid = payload.get("pid")
        proto = payload.get("protocol_version", "?")
        ver = payload.get("server_version", "?")
        addr = payload.get("address", "?")
        if not isinstance(pid, int):
            print(f"[WARN] lockfile present but malformed (no pid): {lock}")
            issues += 1
        elif _is_pid_alive(pid):
            print(f"[PASS] lockfile: live primary PID={pid} proto={proto} ver={ver}")
        else:
            print(f"[WARN] lockfile stale: PID {pid} is dead — {lock}")
            print(f"       Remove with: rm {lock}")
            issues += 1

        # 3. Socket reachability (only if lock claims a live owner).
        # The daemon listens on an AF_UNIX socket (Unix) or a named pipe
        # (Windows AF_PIPE). Only AF_UNIX is a connectable socket at a
        # filesystem address; a named pipe is not probeable this way, so skip
        # it cleanly rather than forcing a false failure.
        if isinstance(pid, int) and _is_pid_alive(pid) and addr and addr != "?":
            family = payload.get("family", "AF_UNIX")
            if family != "AF_UNIX":
                print(f"[INFO] socket: probe skipped for family={family}")
            elif not Path(addr).exists():
                print(f"[FAIL] socket missing: {addr} (PID {pid} alive but no socket)")
                issues += 1
            else:
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(HEALTH_CONNECT_TIMEOUT_SECONDS)
                    sock.connect(addr)
                    sock.close()
                    print(f"[PASS] socket: reachable at {addr}")
                except OSError as exc:
                    print(f"[FAIL] socket: cannot connect to {addr} — {exc}")
                    issues += 1

    # 4 & 5. Migrations + project identity share one runtime context so a
    # single resolution failure is reported once, not double-counted.
    try:
        project, store = build_runtime_context()
    except Exception as exc:
        print(f"[WARN] runtime context: cannot resolve project/store — {exc}")
        issues += 1
    else:
        try:
            from .migrations import detect_status

            statuses = detect_status(store)
            pending = [s for s in statuses.values() if s.needs_upgrade]
            if pending:
                subs = [s.subsystem.value for s in pending]
                print(f"[WARN] migrations: {len(pending)} pending — {', '.join(subs)}")
                print("       Run: turbo-memory-mcp migrate --status")
                issues += 1
            else:
                print("[PASS] migrations: all subsystems up to date")
        except Exception as exc:
            print(f"[WARN] migrations: cannot detect status — {exc}")
            issues += 1

        print(f"[PASS] project: {project.project_name} (id={project.project_id})")

    # Summary
    if issues == 0:
        print("\nAll checks passed.")
    else:
        print(f"\n{issues} issue(s) found. Review WARN/FAIL items above.")
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    return int(handler(args))
