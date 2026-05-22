"""Pre-migration snapshot helper.

A single rolling backup of the storage root is taken into
`<storage_root>/.snapshots/<utc_iso>/`. The N most recent snapshots are
kept (default 1, override via TQMEMORY_SNAPSHOTS_KEEP). The .snapshots/
directory and the runtime .daemon.lock are excluded from the copy.

Restore is a full recursive copy back. The caller is responsible for
making sure no daemon is using the storage when restoring.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

_SNAPSHOT_DIR = ".snapshots"
_KEEP_ENV = "TQMEMORY_SNAPSHOTS_KEEP"
_DEFAULT_KEEP = 1
_EXCLUDE_NAMES = {_SNAPSHOT_DIR, ".daemon.lock"}


def _keep_count() -> int:
    raw = os.environ.get(_KEEP_ENV)
    if raw is None:
        return _DEFAULT_KEEP
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_KEEP
    return max(1, n)


def snapshots_root(storage_root: Path) -> Path:
    return storage_root / _SNAPSHOT_DIR


def list_snapshots(storage_root: Path) -> list[Path]:
    """Existing snapshots, sorted oldest -> newest."""
    root = snapshots_root(storage_root)
    if not root.exists():
        return []
    entries = [p for p in root.iterdir() if p.is_dir()]
    entries.sort(key=lambda p: p.name)
    return entries


def create_snapshot(storage_root: Path) -> Path:
    """Copy storage_root contents into a new timestamped snapshot dir.

    Returns the path to the new snapshot. Older snapshots beyond the keep
    window are deleted after the new one is in place.
    """
    storage_root = storage_root.resolve()
    if not storage_root.exists():
        raise FileNotFoundError(f"storage_root does not exist: {storage_root}")

    snap_root = snapshots_root(storage_root)
    snap_root.mkdir(parents=True, exist_ok=True)

    # Microseconds in the stamp keep names monotonic and unique even when
    # several snapshots are taken within the same second (e.g. test loops).
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    target = snap_root / stamp
    suffix = 1
    while target.exists():
        target = snap_root / f"{stamp}-{suffix}"
        suffix += 1
    target.mkdir(parents=True)

    for child in storage_root.iterdir():
        if child.name in _EXCLUDE_NAMES:
            continue
        dest = target / child.name
        if child.is_dir():
            shutil.copytree(child, dest, symlinks=False)
        else:
            shutil.copy2(child, dest)

    _prune_old(storage_root)
    return target


def restore_snapshot(storage_root: Path, snapshot_path: Path) -> None:
    """Replace live state under storage_root with the snapshot contents.

    Existing entries (except .snapshots/ and .daemon.lock) are removed
    first, then the snapshot tree is copied back in place.
    """
    storage_root = storage_root.resolve()
    snapshot_path = snapshot_path.resolve()
    if not snapshot_path.exists() or not snapshot_path.is_dir():
        raise FileNotFoundError(f"snapshot not found: {snapshot_path}")
    try:
        snapshot_path.relative_to(snapshots_root(storage_root))
    except ValueError as exc:
        raise ValueError(
            f"snapshot {snapshot_path} is not inside {snapshots_root(storage_root)}"
        ) from exc

    for child in list(storage_root.iterdir()):
        if child.name in _EXCLUDE_NAMES:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    for child in snapshot_path.iterdir():
        dest = storage_root / child.name
        if child.is_dir():
            shutil.copytree(child, dest, symlinks=False)
        else:
            shutil.copy2(child, dest)


def _prune_old(storage_root: Path) -> None:
    keep = _keep_count()
    snaps = list_snapshots(storage_root)
    excess = len(snaps) - keep
    if excess <= 0:
        return
    for old in snaps[:excess]:
        shutil.rmtree(old, ignore_errors=True)
