"""Workspace resolution and coordination storage layout."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from turbo_memory_mcp.workspace import paths
from turbo_memory_mcp.workspace.models import Workspace
from turbo_memory_mcp.workspace.paths import (
    ENV_WORKSPACE_DISABLE,
    blobs_dir,
    coord_db_path,
    is_disabled,
    resolve_workspace,
    to_relative,
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _env(tmp_path: Path) -> dict[str, str]:
    return {"TQMEMORY_HOME": str(tmp_path / "memory-home")}


def test_two_cwds_in_one_tree_share_workspace(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)

    first = resolve_workspace(repo, _env(tmp_path))
    second = resolve_workspace(nested, _env(tmp_path))

    assert first.root_key == second.root_key
    assert first.root == repo.resolve()


def test_project_root_override_maps_to_git_toplevel(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    sub = repo / "sub"
    sub.mkdir()
    env = _env(tmp_path)
    env["TQMEMORY_PROJECT_ROOT"] = str(sub)

    workspace = resolve_workspace(tmp_path, env)

    assert workspace.root == repo.resolve()
    assert workspace.is_git is True


def test_worktrees_are_different_workspaces(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "remote", "add", "origin", "https://example.com/x.git")
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(worktree))

    main = resolve_workspace(repo, _env(tmp_path))
    linked = resolve_workspace(worktree, _env(tmp_path))

    assert main.root_key != linked.root_key
    assert main.project_id == linked.project_id


def test_project_id_same_for_root_and_subdir_override_without_remote(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    sub = repo / "sub"
    sub.mkdir()
    override_env = _env(tmp_path)
    override_env["TQMEMORY_PROJECT_ROOT"] = str(sub)

    from_root = resolve_workspace(repo, _env(tmp_path))
    from_subdir = resolve_workspace(tmp_path, override_env)

    assert from_subdir.root == from_root.root
    assert from_subdir.project_id == from_root.project_id


def _fake_git_stdout(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(paths.subprocess, "run", fake_run)


def test_trailing_space_in_toplevel_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Mocked git output: Windows cannot create a directory name ending in a space.
    _fake_git_stdout(monkeypatch, "/work/repo \n")

    toplevel = paths._git_toplevel(tmp_path)

    assert toplevel is not None
    assert toplevel.name == "repo "


def test_only_one_line_terminator_is_stripped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_git_stdout(monkeypatch, "/repo\n\n")
    toplevel = paths._git_toplevel(tmp_path)
    assert toplevel is not None
    assert toplevel.name == "repo\n"

    _fake_git_stdout(monkeypatch, "/work/repo \r\n")
    toplevel = paths._git_toplevel(tmp_path)
    assert toplevel is not None
    assert toplevel.name == "repo "


def test_non_git_root_refuses_journal(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    workspace = resolve_workspace(plain, _env(tmp_path))

    assert workspace.is_git is False
    assert workspace.journal_refusal == "not a git repository"


def test_git_root_allows_journal(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    assert resolve_workspace(repo, _env(tmp_path)).journal_refusal is None


def test_home_root_refuses_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: repo)

    workspace = resolve_workspace(repo, _env(tmp_path))

    assert workspace.journal_refusal == "workspace root is the home directory"


def test_filesystem_root_refuses_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(paths, "_git_toplevel", lambda directory: Path(directory.anchor))

    workspace = resolve_workspace(repo, _env(tmp_path))

    assert workspace.root == Path(repo.anchor)
    assert workspace.journal_refusal == "workspace root is a filesystem root"


def test_to_relative_rejects_outside_paths(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    workspace = resolve_workspace(repo, _env(tmp_path))

    with pytest.raises(ValueError, match="path outside workspace"):
        to_relative(workspace, "../outside")
    with pytest.raises(ValueError, match="path outside workspace"):
        to_relative(workspace, tmp_path / "elsewhere" / "x.py")
    assert to_relative(workspace, "src/a b/файл.py") == "src/a b/файл.py"


def test_to_relative_accepts_symlinked_root(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    link = tmp_path / "link"
    try:
        link.symlink_to(repo, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported")
    workspace = Workspace(root=link, root_key=link.as_posix(), project_id="p", is_git=True, journal_refusal=None)

    assert to_relative(workspace, repo / "src" / "a.py") == "src/a.py"
    assert to_relative(workspace, "src/a.py") == "src/a.py"


def test_to_relative_normalizes_resolution_errors(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    try:
        loop.symlink_to(loop, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not supported")
    workspace = Workspace(root=loop, root_key=loop.as_posix(), project_id="p", is_git=True, journal_refusal=None)

    with pytest.raises(ValueError, match="path outside workspace"):
        to_relative(workspace, "src/a.py")


def test_disable_switch() -> None:
    assert ENV_WORKSPACE_DISABLE == "TQMEMORY_WORKSPACE_DISABLE"
    for value in ("1", "TRUE", "yes"):
        assert is_disabled({ENV_WORKSPACE_DISABLE: value}) is True
    for value in ("0", ""):
        assert is_disabled({ENV_WORKSPACE_DISABLE: value}) is False
    assert is_disabled({}) is False


def test_storage_paths(tmp_path: Path) -> None:
    home = tmp_path / "home"

    assert coord_db_path(home, "p1") == home / "projects" / "p1" / "workspace" / "coord.sqlite"
    assert blobs_dir(home, "p1").parts[-2:] == ("workspace", "blobs")


@pytest.mark.parametrize("project_id", ["../x", "/abs", "a/b", "..", "C:escape", "a:b"])
def test_storage_paths_reject_unsafe_project_id(tmp_path: Path, project_id: str) -> None:
    with pytest.raises(ValueError):
        coord_db_path(tmp_path / "home", project_id)
