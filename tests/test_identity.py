from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from turbo_memory_mcp.identity import (
    hash_identity_source,
    normalize_remote_url,
    resolve_project_identity,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init")
    return path


def test_remote_first_identity_uses_origin_url(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "remote-first")
    _git(repo, "remote", "add", "origin", "git@GitHub.com:ExampleOrg/ExampleRepo.git")

    identity = resolve_project_identity(cwd=repo)

    assert identity.identity_kind == "git_remote"
    assert identity.identity_source == "github.com/ExampleOrg/ExampleRepo"
    assert identity.project_id == hash_identity_source("github.com/ExampleOrg/ExampleRepo")
    assert len(identity.project_id) == 16
    assert identity.project_name == "remote-first"


def test_no_remote_fallback_uses_repo_root_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "local-only")

    identity = resolve_project_identity(cwd=repo)
    repeated_identity = resolve_project_identity(cwd=repo)

    assert identity.identity_kind == "repo_path"
    assert identity.identity_source == str(repo.resolve())
    assert identity.project_id == hash_identity_source(str(repo.resolve()))
    assert identity.project_id
    assert repeated_identity.project_id == identity.project_id


def test_explicit_override_precedence_bypasses_git_discovery(tmp_path: Path) -> None:
    repo = tmp_path / "not-even-a-repo"
    repo.mkdir()

    identity = resolve_project_identity(
        cwd=repo,
        environ={
            "TQMEMORY_PROJECT_ROOT": str(repo),
            "TQMEMORY_PROJECT_ID": "manual-project-id",
            "TQMEMORY_PROJECT_NAME": "Manual Name",
        },
    )

    assert identity.project_root == repo.resolve()
    assert identity.project_id == "manual-project-id"
    assert identity.project_name == "Manual Name"
    assert identity.identity_kind == "override"
    assert identity.identity_source == "manual-project-id"
    assert identity.remote_url is None


@pytest.mark.parametrize(
    "git_failure",
    [
        subprocess.TimeoutExpired(cmd=["git"], timeout=3.0),
        FileNotFoundError("git"),
    ],
    ids=["hung_git", "missing_git"],
)
def test_hung_or_missing_git_falls_back_to_path_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_failure: Exception,
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise git_failure

    monkeypatch.setattr("turbo_memory_mcp.identity.subprocess.run", _raise)

    identity = resolve_project_identity(cwd=tmp_path)

    assert identity.identity_kind == "repo_path"
    assert identity.project_root == tmp_path.resolve()
    assert identity.remote_url is None


def test_canonical_remote_variants_hash_to_the_same_project_id() -> None:
    ssh_identity = normalize_remote_url("git@GitHub.com:ExampleOrg/ExampleRepo.git")
    https_identity = normalize_remote_url("https://github.com/ExampleOrg/ExampleRepo.git")

    assert ssh_identity == https_identity
    assert hash_identity_source(ssh_identity) == hash_identity_source(https_identity)
