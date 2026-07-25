"""Deploy the canonical agent skill (SKILL.md) into agent skill directories."""

from __future__ import annotations

import re
from importlib import resources

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
    """Extract the `version:` frontmatter marker, or None when absent."""
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None
