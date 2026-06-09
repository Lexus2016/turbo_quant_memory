"""Turbo Quant Memory MCP package."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    # Single source of truth: installed package metadata (driven by
    # pyproject.toml). Prevents the version string from drifting away from the
    # actual release, as a hardcoded literal did through 0.7.2 and 0.8.0.
    __version__ = version("turbo-memory-mcp")
except PackageNotFoundError:  # running from un-installed source
    __version__ = "0.15.0"
