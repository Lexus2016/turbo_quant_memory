from __future__ import annotations

from turbo_memory_mcp import __version__
from turbo_memory_mcp.skill_install import (
    SKILL_NAME,
    load_skill,
    parse_skill_version,
)


def test_skill_version_matches_package_version() -> None:
    text, version = load_skill()

    assert version == __version__
    assert parse_skill_version(text) == version


def test_skill_frontmatter_and_body_are_complete() -> None:
    text, _ = load_skill()

    assert f"name: {SKILL_NAME}" in text
    assert "description:" in text
    # The four contract sections from the spec.
    assert "## 1. Detect" in text
    assert "## 2. Install & Deploy" in text
    assert "## 3. Operate" in text
    assert "## 4. Troubleshoot" in text


def test_parse_skill_version_handles_missing_marker() -> None:
    assert parse_skill_version("no frontmatter here\n") is None
