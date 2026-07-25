"""Deploy the canonical agent skill (SKILL.md) into agent skill directories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

SKILL_NAME = "turbo-quant-memory"
SKILL_RESOURCE = "skills/turbo-quant-memory/SKILL.md"

_VERSION_RE = re.compile(r"^version:\s*([0-9][0-9A-Za-z.\-]*)\s*$", re.MULTILINE)


def load_skill() -> tuple[str, str]:
    """Return (text, version) of the packaged canonical SKILL.md."""
    text = (
        resources.files("turbo_memory_mcp")
        .joinpath(SKILL_RESOURCE)
        .read_text(encoding="utf-8")
    )
    version = parse_skill_version(text)
    if version is None:
        raise ValueError(f"packaged {SKILL_RESOURCE} has no version marker")
    return text, version


def parse_skill_version(text: str) -> str | None:
    """Extract the first `version:` line marker, or None when absent."""
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


# Client key -> (skills dir relative to $HOME, always-install flag).
# Non-flagged clients receive the skill only when their config dir (the first
# path segment) exists in $HOME.
CLIENT_SKILL_DIRS: dict[str, tuple[str, bool]] = {
    "agents": (".agents/skills", True),  # universal skills dir — always
    "claude": (".claude/skills", False),
    "gemini": (".gemini/skills", False),
    "kimi": (".kimi-code/skills", False),
    "codex": (".codex/skills", False),
}


@dataclass
class InstallResult:
    client: str
    target: Path
    status: str  # "installed" | "upgraded" | "reinstalled" | "current" | "failed"
    old_version: str | None
    new_version: str
    error: str | None = None


def resolve_targets(home: Path, client: str | None = None) -> list[tuple[str, Path]]:
    """Skill-file target paths for every client that should receive the skill."""
    targets: list[tuple[str, Path]] = []
    for name, (rel, always) in CLIENT_SKILL_DIRS.items():
        if client is not None and name != client:
            continue
        config_dir = home / rel.split("/")[0]
        if always or client is not None or config_dir.is_dir():
            targets.append((name, home / rel / SKILL_NAME / "SKILL.md"))
    return targets


def install_skill(
    home: Path | None = None,
    *,
    client: str | None = None,
    dry_run: bool = False,
) -> list[InstallResult]:
    """Install or upgrade the skill into every resolved target. Idempotent."""
    home = home or Path.home()
    text, new_version = load_skill()
    results: list[InstallResult] = []
    for name, target in resolve_targets(home, client):
        existed = target.is_file()
        old_version: str | None = None
        if existed:
            try:
                old_version = parse_skill_version(target.read_text(encoding="utf-8"))
            except OSError:
                old_version = None  # unreadable copy -> treat as reinstalled
        if old_version == new_version:
            results.append(
                InstallResult(name, target, "current", old_version, new_version)
            )
            continue
        if not existed:
            status = "installed"
        elif old_version is not None:
            status = "upgraded"
        else:
            status = "reinstalled"
        if not dry_run:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            except OSError as exc:
                results.append(
                    InstallResult(name, target, "failed", old_version, new_version, str(exc))
                )
                continue
        results.append(InstallResult(name, target, status, old_version, new_version))
    return results
