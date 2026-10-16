"""Workspace resolution and coordination storage paths.

Every path here is derived from the git working tree, so several agent sessions
opened anywhere inside one checkout resolve to the same workspace, while a
linked worktree (same project, different directory) stays its own workspace.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from turbo_memory_mcp.identity import resolve_project_identity, resolve_project_root
from turbo_memory_mcp.workspace.models import Workspace

ENV_WORKSPACE_DISABLE = "TQMEMORY_WORKSPACE_DISABLE"
_TRUTHY_VALUES = frozenset({"1", "true", "yes"})
_GIT_TIMEOUT_SECONDS = 5
_COORD_DB_NAME = "coord.sqlite"
_BLOBS_DIR_NAME = "blobs"


def resolve_workspace(
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Workspace:
    """Resolve the working tree that ``cwd`` belongs to and whether the journal may run in it."""

    env = os.environ if environ is None else environ
    base = resolve_project_root(cwd, env)
    toplevel = _git_toplevel(base)
    root = toplevel if toplevel is not None else base.resolve()
    is_git = toplevel is not None
    return Workspace(
        root=root,
        root_key=root.as_posix(),
        project_id=resolve_project_identity(cwd, env).project_id,
        is_git=is_git,
        journal_refusal=_journal_refusal(root, is_git),
    )


def workspace_storage_dir(storage_root: Path, project_id: str) -> Path:
    return storage_root / "projects" / project_id / "workspace"


def coord_db_path(storage_root: Path, project_id: str) -> Path:
    return workspace_storage_dir(storage_root, project_id) / _COORD_DB_NAME


def blobs_dir(storage_root: Path, project_id: str) -> Path:
    return workspace_storage_dir(storage_root, project_id) / _BLOBS_DIR_NAME


def is_disabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when the ``TQMEMORY_WORKSPACE_DISABLE`` kill switch is on."""

    env = os.environ if environ is None else environ
    return env.get(ENV_WORKSPACE_DISABLE, "").strip().lower() in _TRUTHY_VALUES


def to_relative(workspace: Workspace, path: str | Path) -> str:
    """Return ``path`` as a workspace-relative POSIX string.

    Relative inputs are read against the workspace root. Anything resolving
    outside the root raises ``ValueError``.
    """

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace.root / candidate
    try:
        relative = candidate.resolve().relative_to(workspace.root)
    except ValueError:
        raise ValueError("path outside workspace") from None
    return relative.as_posix()


def _git_toplevel(directory: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=directory,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    top = result.stdout.strip()
    if result.returncode != 0 or not top:
        return None
    return Path(top).resolve()


def _journal_refusal(root: Path, is_git: bool) -> str | None:
    if root.parent == root:
        return "workspace root is a filesystem root"
    if root == Path.home().resolve():
        return "workspace root is the home directory"
    if not is_git:
        return "not a git repository"
    return None
