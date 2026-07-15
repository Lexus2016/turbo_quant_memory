"""Deterministic project identity resolution for namespace-aware memory."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

ENV_PROJECT_ROOT = "TQMEMORY_PROJECT_ROOT"
ENV_PROJECT_ID = "TQMEMORY_PROJECT_ID"
ENV_PROJECT_NAME = "TQMEMORY_PROJECT_NAME"
GIT_SHOW_TOPLEVEL_COMMAND = "git rev-parse --show-toplevel"
GIT_ORIGIN_URL_COMMAND = "git remote get-url origin"
_GIT_COMMAND_TIMEOUT_SECONDS = 3.0

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


# Identity resolution runs on every tool call and, without an override, forks
# git twice (rev-parse + remote get-url) under the daemon's single-writer lock,
# blocking all clients. Cache the result keyed on (cwd, TQMEMORY_PROJECT_* env,
# git-config fingerprint). Keying on the FULL identity inputs — not just cwd — is
# what preserves the issue-#1 fix: a shared daemon serving proxies from different
# repos never crosses namespaces. The git-config mtime fingerprint invalidates
# the entry (cheaply, no fork) the instant a repo gains/loses a remote; the TTL
# is only a backstop for a filesystem with coarse mtime resolution.
_IDENTITY_CACHE_TTL_SECONDS = 30.0
_IDENTITY_CACHE_MAXSIZE = 512
_IDENTITY_CACHE: dict[tuple, tuple["ProjectIdentity", float]] = {}
_IDENTITY_CACHE_LOCK = threading.Lock()


def _clear_identity_cache() -> None:
    """Drop all cached identities (test hygiene / explicit invalidation)."""
    with _IDENTITY_CACHE_LOCK:
        _IDENTITY_CACHE.clear()


def _git_config_fingerprint(start: Path) -> int | None:
    """Cheap fingerprint of the nearest repo's git config: its ``mtime_ns``, or None.

    Walks up from ``start`` for a ``.git`` entry without a subprocess. When the
    config changes (``git remote add`` rewrites ``.git/config``) the mtime
    advances so the identity cache misses immediately instead of masking the
    change for the whole TTL.
    """
    try:
        current = start.expanduser().resolve()
    except OSError:
        return None
    for _ in range(64):
        git_entry = current / ".git"
        try:
            if git_entry.is_dir():
                try:
                    return (git_entry / "config").stat().st_mtime_ns
                except OSError:
                    return git_entry.stat().st_mtime_ns
            if git_entry.is_file():
                return git_entry.stat().st_mtime_ns
        except OSError:
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _identity_cache_key(cwd: Path | str | None, env: Mapping[str, str]) -> tuple:
    explicit_root = _clean_value(env.get(ENV_PROJECT_ROOT))
    if explicit_root:
        start: Path = Path(explicit_root).expanduser()
    elif cwd is not None:
        start = Path(cwd)
    else:
        start = Path.cwd()
    return (
        None if cwd is None else str(cwd),
        explicit_root,
        _clean_value(env.get(ENV_PROJECT_ID)),
        _clean_value(env.get(ENV_PROJECT_NAME)),
        _git_config_fingerprint(start),
    )


def resolve_project_identity(
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProjectIdentity:
    """Resolve the current project identity using overrides, git, then path fallback.

    Cached per (cwd, identity env, git-config fingerprint) so the two git forks
    below do not run on every tool call; see the cache notes for the isolation
    guarantee that preserves the issue-#1 namespace fix.
    """

    env = os.environ if environ is None else environ
    key = _identity_cache_key(cwd, env)
    now = time.monotonic()
    with _IDENTITY_CACHE_LOCK:
        cached = _IDENTITY_CACHE.get(key)
        if cached is not None and now - cached[1] < _IDENTITY_CACHE_TTL_SECONDS:
            return cached[0]
    identity = _resolve_project_identity_uncached(cwd, env)
    with _IDENTITY_CACHE_LOCK:
        if len(_IDENTITY_CACHE) >= _IDENTITY_CACHE_MAXSIZE:
            _IDENTITY_CACHE.clear()
        _IDENTITY_CACHE[key] = (identity, now)
    return identity


def _resolve_project_identity_uncached(
    cwd: Path | str | None,
    env: Mapping[str, str],
) -> ProjectIdentity:
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
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        # A hung or missing git must not stall identity resolution: fall back
        # to path identity exactly as a non-zero exit would.
        return None
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
