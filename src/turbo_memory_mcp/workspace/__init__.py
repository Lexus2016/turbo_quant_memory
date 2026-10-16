"""Workspace coordination: shared git working-tree identity and storage layout."""

from __future__ import annotations

from turbo_memory_mcp.workspace.models import Workspace
from turbo_memory_mcp.workspace.paths import resolve_workspace

__all__ = ["Workspace", "resolve_workspace"]
