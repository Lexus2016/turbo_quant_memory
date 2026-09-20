"""Turbo Quant Memory MCP package."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

# Kept in sync with pyproject.toml by test_version_metadata.py. Only ever used
# when the package is not installed at all (a bare source checkout).
_FALLBACK_VERSION = "0.29.1"

try:
    # Single source of truth: installed package metadata (driven by
    # pyproject.toml). Prevents the version string from drifting away from the
    # actual release, as a hardcoded literal did through 0.7.2 and 0.8.0.
    __version__ = version("turbo-quant-memory")
except PackageNotFoundError:
    try:
        # Pre-0.28.0 environments still carry the old distribution metadata;
        # an editable install made before the rename is the realistic case.
        __version__ = version("turbo-memory-mcp")
    except PackageNotFoundError:  # running from un-installed source
        __version__ = _FALLBACK_VERSION
