"""Orphan-bucket detection (lifecycle hygiene, Feature 2).

A project bucket whose recorded ``project_root`` no longer exists on disk is
*surfaced* (in ``server_info``) but never auto-deleted: a missing root is not
proof a project is dead (an external/network volume may be unmounted, or the
storage root shared across machines). Removal stays a deliberate, assisted
action — this feature only makes the dead weight visible.
"""

from __future__ import annotations

import json
from pathlib import Path

from turbo_memory_mcp.server import server_info_impl
from turbo_memory_mcp.store import detect_orphaned_buckets


def _seed_bucket(
    storage_root: Path,
    project_id: str,
    *,
    project_root: Path,
    note_ids: list[str] | None = None,
) -> None:
    bucket = storage_root / "projects" / project_id
    (bucket / "notes").mkdir(parents=True)
    (bucket / "manifest.json").write_text(
        json.dumps(
            {
                "scope": "project",
                "project_id": project_id,
                "project_name": project_root.name,
                "project_root": str(project_root),
                "identity_source": str(project_root),
                "identity_kind": "repo_path",
                "format_version": 2,
                "updated_at": "2026-06-06T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    for note_id in note_ids or []:
        (bucket / "notes" / f"{note_id}.json").write_text("{}", encoding="utf-8")


def test_detect_flags_only_buckets_with_missing_root(tmp_path: Path) -> None:
    storage_root = tmp_path / "mem"
    live_root = tmp_path / "live"
    live_root.mkdir()
    gone_root = tmp_path / "gone"  # never created

    _seed_bucket(storage_root, "liveaaaaaaaaaaaa", project_root=live_root, note_ids=["a"])
    _seed_bucket(
        storage_root,
        "deadbbbbbbbbbbbb",
        project_root=gone_root,
        note_ids=["x", "y", "z"],
    )

    orphans = detect_orphaned_buckets(storage_root)

    assert [o["project_id"] for o in orphans] == ["deadbbbbbbbbbbbb"]
    only = orphans[0]
    assert only["project_root"] == str(gone_root)
    assert only["note_count"] == 3


def test_detect_empty_when_no_projects_dir(tmp_path: Path) -> None:
    assert detect_orphaned_buckets(tmp_path / "nonexistent") == []


def test_server_info_surfaces_orphaned_buckets(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    env = {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "project-alpha",
        "TQMEMORY_PROJECT_NAME": "Alpha Project",
    }
    storage_root = Path(env["TQMEMORY_HOME"])

    # An orphan bucket whose root was deleted.
    _seed_bucket(
        storage_root,
        "orphancccccccccc",
        project_root=tmp_path / "deleted-elsewhere",
        note_ids=["n1", "n2"],
    )

    payload = server_info_impl(environ=env)

    orphans = payload["orphaned_buckets"]
    ids = [o["project_id"] for o in orphans]
    assert "orphancccccccccc" in ids
    assert "project-alpha" not in ids  # active project's root exists → not an orphan
