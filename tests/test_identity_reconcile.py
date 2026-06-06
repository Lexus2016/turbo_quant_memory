"""Sticky identity reconciliation: a repo must keep the same memory bucket
when its identity *source* changes (a git remote added to a previously
path-keyed repo, or removed again), while a different project reusing the
same filesystem path must NOT inherit the previous project's memory.

These tests drive `reconcile_project_identity`, the only storage-aware seam.
`resolve_project_identity` itself stays a pure git/path resolver (see
`test_identity.py`).
"""

from __future__ import annotations

import json
from pathlib import Path

from turbo_memory_mcp.identity import ProjectIdentity, hash_identity_source
from turbo_memory_mcp.store import (
    MemoryStore,
    reconcile_project_identity,
)


def _seed_bucket(
    storage_root: Path,
    project_id: str,
    *,
    project_root: Path,
    identity_source: str,
    identity_kind: str,
    remote_url: str | None = None,
    identity_sources: list[str] | None = None,
) -> None:
    bucket = storage_root / "projects" / project_id
    bucket.mkdir(parents=True)
    manifest: dict[str, object] = {
        "scope": "project",
        "project_id": project_id,
        "project_name": project_root.name,
        "project_root": str(project_root),
        "identity_source": identity_source,
        "identity_kind": identity_kind,
        "format_version": 2,
        "updated_at": "2026-06-06T00:00:00Z",
    }
    if remote_url is not None:
        manifest["remote_url"] = remote_url
    if identity_sources is not None:
        manifest["identity_sources"] = identity_sources
    (bucket / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _path_candidate(project_root: Path) -> ProjectIdentity:
    source = str(project_root)
    return ProjectIdentity(
        project_id=hash_identity_source(source),
        project_name=project_root.name,
        project_root=project_root,
        identity_source=source,
        identity_kind="repo_path",
        remote_url=None,
    )


def _remote_candidate(project_root: Path, remote_source: str) -> ProjectIdentity:
    return ProjectIdentity(
        project_id=hash_identity_source(remote_source),
        project_name=project_root.name,
        project_root=project_root,
        identity_source=remote_source,
        identity_kind="git_remote",
        remote_url=f"git@github.com:{remote_source.split('/', 1)[-1]}.git",
    )


# --------------------------------------------------------------------------- #
# Core fix: remote added later must adopt the existing path-keyed bucket
# --------------------------------------------------------------------------- #


def test_remote_added_adopts_existing_path_bucket(tmp_path: Path) -> None:
    storage_root = tmp_path / "mem"
    repo = tmp_path / "repo"
    path_id = hash_identity_source(str(repo))
    _seed_bucket(
        storage_root,
        path_id,
        project_root=repo,
        identity_source=str(repo),
        identity_kind="repo_path",
        identity_sources=[str(repo)],
    )

    candidate = _remote_candidate(repo, "github.com/Org/Repo")
    assert candidate.project_id != path_id  # would have split

    resolved = reconcile_project_identity(candidate, storage_root)

    assert resolved.project_id == path_id  # adopted, not minted
    assert resolved.identity_kind == "git_remote"  # current resolution kind preserved
    assert resolved.project_root == repo


# --------------------------------------------------------------------------- #
# Safety boundary: same path, different remote → a genuinely new project
# --------------------------------------------------------------------------- #


def test_same_root_different_remote_mints_new_bucket(tmp_path: Path) -> None:
    storage_root = tmp_path / "mem"
    repo = tmp_path / "repo"
    old_remote = "github.com/Org/Old"
    _seed_bucket(
        storage_root,
        hash_identity_source(old_remote),
        project_root=repo,
        identity_source=old_remote,
        identity_kind="git_remote",
        remote_url="git@github.com:Org/Old.git",
        identity_sources=[old_remote],
    )

    candidate = _remote_candidate(repo, "github.com/Org/New")
    resolved = reconcile_project_identity(candidate, storage_root)

    # No adoption — must keep the freshly-hashed id for the new remote.
    assert resolved.project_id == hash_identity_source("github.com/Org/New")
    assert resolved.project_id != hash_identity_source(old_remote)


# --------------------------------------------------------------------------- #
# Stability the other direction: remote removed → adopt back by root
# --------------------------------------------------------------------------- #


def test_remote_removed_adopts_back_by_root(tmp_path: Path) -> None:
    storage_root = tmp_path / "mem"
    repo = tmp_path / "repo"
    remote = "github.com/Org/Repo"
    remote_id = hash_identity_source(remote)
    _seed_bucket(
        storage_root,
        remote_id,
        project_root=repo,
        identity_source=remote,
        identity_kind="git_remote",
        remote_url="git@github.com:Org/Repo.git",
        identity_sources=[remote],
    )

    candidate = _path_candidate(repo)  # remote gone, falls back to path
    resolved = reconcile_project_identity(candidate, storage_root)

    assert resolved.project_id == remote_id  # stable, no second split


# --------------------------------------------------------------------------- #
# A previously-seen source pins directly (fast path, == today's behavior)
# --------------------------------------------------------------------------- #


def test_known_remote_source_pins_same_bucket(tmp_path: Path) -> None:
    storage_root = tmp_path / "mem"
    repo = tmp_path / "repo"
    remote = "github.com/Org/Repo"
    remote_id = hash_identity_source(remote)
    _seed_bucket(
        storage_root,
        remote_id,
        project_root=repo,
        identity_source=remote,
        identity_kind="git_remote",
        remote_url="git@github.com:Org/Repo.git",
        identity_sources=[remote],
    )

    resolved = reconcile_project_identity(_remote_candidate(repo, remote), storage_root)
    assert resolved.project_id == remote_id


def test_accumulated_alias_pins_even_from_different_root(tmp_path: Path) -> None:
    # A bucket that already accumulated both its path and remote sources must
    # be found by either, even if the checkout later moves to a new path.
    storage_root = tmp_path / "mem"
    original_repo = tmp_path / "repo"
    moved_repo = tmp_path / "moved"
    remote = "github.com/Org/Repo"
    bucket_id = hash_identity_source(remote)
    _seed_bucket(
        storage_root,
        bucket_id,
        project_root=original_repo,
        identity_source=remote,
        identity_kind="git_remote",
        remote_url="git@github.com:Org/Repo.git",
        identity_sources=[str(original_repo), remote],
    )

    # Same remote, different checkout path → still the same bucket (remote source match).
    resolved = reconcile_project_identity(_remote_candidate(moved_repo, remote), storage_root)
    assert resolved.project_id == bucket_id


# --------------------------------------------------------------------------- #
# First use and override
# --------------------------------------------------------------------------- #


def test_path_only_established_project_is_unchanged(tmp_path: Path) -> None:
    # The no-regression guarantee: a repo that has always been path-keyed (no
    # remote) must resolve to its own existing bucket, identical to the old
    # pure resolver. reconcile only ever changes the answer at a source change.
    storage_root = tmp_path / "mem"
    repo = tmp_path / "repo"
    path_id = hash_identity_source(str(repo))
    _seed_bucket(
        storage_root,
        path_id,
        project_root=repo,
        identity_source=str(repo),
        identity_kind="repo_path",
        identity_sources=[str(repo)],
    )

    candidate = _path_candidate(repo)
    resolved = reconcile_project_identity(candidate, storage_root)

    assert resolved.project_id == candidate.project_id == path_id  # unchanged, no adoption
    assert resolved == candidate


def test_first_time_mints_candidate_unchanged(tmp_path: Path) -> None:
    storage_root = tmp_path / "mem"  # no projects/ yet
    candidate = _remote_candidate(tmp_path / "repo", "github.com/Org/Fresh")
    resolved = reconcile_project_identity(candidate, storage_root)
    assert resolved == candidate


def test_override_bypasses_bucket_scan(tmp_path: Path) -> None:
    storage_root = tmp_path / "mem"
    repo = tmp_path / "repo"
    # Seed a same-root bucket that WOULD be adopted if scanning happened.
    _seed_bucket(
        storage_root,
        "some-other-id",
        project_root=repo,
        identity_source=str(repo),
        identity_kind="repo_path",
        identity_sources=[str(repo)],
    )
    override = ProjectIdentity(
        project_id="manual-pin",
        project_name="Manual",
        project_root=repo,
        identity_source="manual-pin",
        identity_kind="override",
        remote_url=None,
    )
    resolved = reconcile_project_identity(override, storage_root)
    assert resolved.project_id == "manual-pin"  # untouched


# --------------------------------------------------------------------------- #
# write_project_manifest accumulates identity_sources (transparency + pinning)
# --------------------------------------------------------------------------- #


def test_write_manifest_accumulates_identity_sources(tmp_path: Path) -> None:
    storage_root = tmp_path / "mem"
    repo = tmp_path / "repo"
    bucket_id = "bucket123456789a"

    # Legacy manifest with only a single identity_source and no list.
    _seed_bucket(
        storage_root,
        bucket_id,
        project_root=repo,
        identity_source=str(repo),
        identity_kind="repo_path",
    )

    # Now resolve with a remote that adopted this same bucket id.
    adopted = ProjectIdentity(
        project_id=bucket_id,
        project_name=repo.name,
        project_root=repo,
        identity_source="github.com/Org/Repo",
        identity_kind="git_remote",
        remote_url="git@github.com:Org/Repo.git",
    )
    store = MemoryStore(adopted, storage_root=storage_root)
    manifest = store.write_project_manifest()

    assert manifest["identity_sources"] == sorted({str(repo), "github.com/Org/Repo"})
    on_disk = json.loads(store.project_manifest_path().read_text(encoding="utf-8"))
    assert on_disk["identity_sources"] == sorted({str(repo), "github.com/Org/Repo"})


# --------------------------------------------------------------------------- #
# End-to-end through build_runtime_context with real git: the original bug.
# --------------------------------------------------------------------------- #


def _git(cwd: Path, *args: str) -> None:
    import subprocess

    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_end_to_end_adding_remote_keeps_same_bucket(tmp_path: Path) -> None:
    from turbo_memory_mcp.server import build_runtime_context

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    env = {"TQMEMORY_HOME": str(tmp_path / "home")}

    # First use, no remote: path-keyed bucket + a note written into it.
    project1, store1 = build_runtime_context(cwd=repo, environ=env)
    assert project1.identity_kind == "repo_path"
    store1.write_project_note("Before", "body", note_kind="decision", note_id="n1")

    # A git remote is added later — the exact trigger that split "Тяни-Толкай".
    _git(repo, "remote", "add", "origin", "git@github.com:Org/Repo.git")

    project2, store2 = build_runtime_context(cwd=repo, environ=env)
    assert project2.identity_kind == "git_remote"  # resolution flipped...
    assert project2.project_id == project1.project_id  # ...but the bucket did NOT split
    assert store2.project_note_path("n1").exists()  # the pre-remote note is still here

    store2.write_project_manifest()
    on_disk = json.loads(store2.project_manifest_path().read_text(encoding="utf-8"))
    assert str(repo.resolve()) in on_disk["identity_sources"]
    assert "github.com/Org/Repo" in on_disk["identity_sources"]
