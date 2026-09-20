"""Guards against the version string drifting away from pyproject.toml.

The 0.28.0 distribution rename (`turbo-memory-mcp` -> `turbo-quant-memory`)
left `__init__.py` reading metadata under the old name, so every installed
copy silently fell back to the hardcoded literal. It stayed invisible until
0.29.0, when the release stopped bumping that literal and `--version` reported
0.28.3. These tests fail on either half of that.
"""

from __future__ import annotations

import json
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pytest

from turbo_memory_mcp import _FALLBACK_VERSION, __version__
from turbo_memory_mcp.contracts import PACKAGE_NAME

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
SERVER_JSON = PROJECT_ROOT / "server.json"
SKILL_MD = PROJECT_ROOT / "src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md"


def _pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def test_distribution_metadata_resolves_under_the_pyproject_name() -> None:
    dist_name = _pyproject()["name"]

    try:
        resolved = version(dist_name)
    except PackageNotFoundError:  # bare source checkout, nothing to compare
        pytest.skip(f"{dist_name} is not installed in this environment")

    assert resolved == _pyproject()["version"]
    assert __version__ == resolved


def test_uninstalled_source_fallback_matches_pyproject() -> None:
    assert _pyproject()["version"] == _FALLBACK_VERSION


def test_contract_package_name_is_the_real_distribution() -> None:
    assert _pyproject()["name"] == PACKAGE_NAME


def test_server_json_agrees_with_pyproject() -> None:
    """publish-mcp.yml asserts this at upload time; fail in pytest instead."""
    manifest = json.loads(SERVER_JSON.read_text(encoding="utf-8"))
    expected = _pyproject()["version"]

    assert manifest["version"] == expected
    assert [pkg["version"] for pkg in manifest["packages"]] == [expected]


def test_skill_pinned_install_example_is_current() -> None:
    """The frontmatter is covered by test_skill_install; the example was not."""
    expected = _pyproject()["version"]
    name = _pyproject()["name"]

    assert f"{name}=={expected}" in SKILL_MD.read_text(encoding="utf-8")
