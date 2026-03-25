"""Deterministic project identity resolution for namespace-aware memory."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

ENV_PROJECT_ROOT = "TQMEMORY_PROJECT_ROOT"
ENV_PROJECT_ID = "TQMEMORY_PROJECT_ID"
ENV_PROJECT_NAME = "TQMEMORY_PROJECT_NAME"
GIT_SHOW_TOPLEVEL_COMMAND = "git rev-parse --show-toplevel"
GIT_ORIGIN_URL_COMMAND = "git remote get-url origin"

_SCP_REMOTE_RE = re.compile(r"^(?:(?P<user>[^@]+)@)?(?P<host>[^:/]+):(?P<path>.+)$")


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    """Stable identity metadata for the current repository."""

    project_id: str
    project_name: str
    project_root: Path
    identity_source: str
    identity_kind: str
    remote_url: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload = {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_root": str(self.project_root),
            "identity_source": self.identity_source,
            "identity_kind": self.identity_kind,
        }
        if self.remote_url:
            payload["remote_url"] = self.remote_url
        return payload


def resolve_project_identity(
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProjectIdentity:
    """Resolve the current project identity using overrides, git, then path fallback."""

    env = os.environ if environ is None else environ
    project_root = resolve_project_root(cwd=cwd, environ=env)
    project_name = _clean_value(env.get(ENV_PROJECT_NAME)) or project_root.name
    explicit_project_id = _clean_value(env.get(ENV_PROJECT_ID))

    if explicit_project_id:
        return ProjectIdentity(
            project_id=explicit_project_id,
            project_name=project_name,
            project_root=project_root,
            identity_source=explicit_project_id,
            identity_kind="override",
        )

    remote_url = _run_git_command(project_root, *GIT_ORIGIN_URL_COMMAND.split()[1:])
    if remote_url:
        identity_source = normalize_remote_url(remote_url)
        identity_kind = "git_remote"
    else:
        identity_source = str(project_root)
        identity_kind = "repo_path"

    return ProjectIdentity(
        project_id=hash_identity_source(identity_source),
        project_name=project_name,
        project_root=project_root,
        identity_source=identity_source,
        identity_kind=identity_kind,
        remote_url=remote_url,
    )


def resolve_project_root(
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the repository root, honoring an explicit override when present."""

    env = os.environ if environ is None else environ
    explicit_root = _clean_value(env.get(ENV_PROJECT_ROOT))
    if explicit_root:
        return Path(explicit_root).expanduser().resolve()

    start_dir = Path(cwd).expanduser().resolve() if cwd is not None else Path.cwd().resolve()
    repo_root = _run_git_command(start_dir, *GIT_SHOW_TOPLEVEL_COMMAND.split()[1:])
    if repo_root:
        return Path(repo_root).expanduser().resolve()
    return start_dir


def hash_identity_source(identity_source: str) -> str:
    """Derive the default 16-character project id from a canonical identity source."""

    return hashlib.sha256(identity_source.encode("utf-8")).hexdigest()[:16]


def normalize_remote_url(remote_url: str) -> str:
    """Normalize git remote variants into a stable host/path identity string."""

    raw = remote_url.strip()
    if not raw:
        raise ValueError("Remote URL cannot be empty.")

    if "://" not in raw:
        scp_match = _SCP_REMOTE_RE.match(raw)
        if scp_match:
            host = scp_match.group("host").lower()
            path = _normalize_remote_path(scp_match.group("path"))
            return f"{host}/{path}" if path else host

    parsed = urlparse(raw)
    if parsed.scheme == "file" or (not parsed.scheme and not parsed.netloc):
        return _normalize_path_identity(parsed.path or raw)

    if parsed.hostname:
        host = parsed.hostname.lower()
        path = _normalize_remote_path(parsed.path)
        return f"{host}/{path}" if path else host

    return _normalize_path_identity(raw)


def _normalize_remote_path(value: str) -> str:
    trimmed = value.strip().strip("/")
    trimmed = trimmed.removeprefix("/")
    return _strip_dot_git(trimmed)


def _normalize_path_identity(value: str) -> str:
    path = Path(value).expanduser().resolve()
    return _strip_dot_git(str(path))


def _strip_dot_git(value: str) -> str:
    stripped = value.rstrip("/")
    if stripped.endswith(".git"):
        stripped = stripped[:-4]
    return stripped


def _run_git_command(cwd: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


__all__ = [
    "ENV_PROJECT_ID",
    "ENV_PROJECT_NAME",
    "ENV_PROJECT_ROOT",
    "GIT_ORIGIN_URL_COMMAND",
    "GIT_SHOW_TOPLEVEL_COMMAND",
    "ProjectIdentity",
    "hash_identity_source",
    "normalize_remote_url",
    "resolve_project_identity",
    "resolve_project_root",
]
