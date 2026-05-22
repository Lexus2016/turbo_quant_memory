"""Schema migration framework for Turbo Quant Memory.

Activates the existing format-version constants (MARKDOWN_FORMAT_VERSION,
RETRIEVAL_FORMAT_VERSION, USAGE_STATS_FORMAT_VERSION) as a real upgrade
system. Each subsystem keeps its own linear chain of upgrades. The runner
detects gaps and applies them in order; a snapshot is taken before any
mutation. Daemon startup only detects-and-warns — opt-in apply is via
`turbo-memory-mcp migrate --apply`.

Phase A (this file) only ships the framework. Real upgrade functions are
registered by later phases (Phase 1 offsets, Phase 2 tiers, etc.).
"""
from __future__ import annotations

from .registry import (
    REGISTRY,
    Migration,
    Subsystem,
    clear_registry,
    get_chain,
    latest_version,
    migration,
)
from .runner import (
    MigrationOutcome,
    SubsystemStatus,
    apply_pending,
    detect_status,
    format_pending_warning,
)
from .snapshot import create_snapshot, list_snapshots, restore_snapshot
from .log import log_event, log_path

__all__ = [
    "REGISTRY",
    "Migration",
    "MigrationOutcome",
    "Subsystem",
    "SubsystemStatus",
    "apply_pending",
    "clear_registry",
    "create_snapshot",
    "detect_status",
    "format_pending_warning",
    "get_chain",
    "latest_version",
    "list_snapshots",
    "log_event",
    "log_path",
    "migration",
    "restore_snapshot",
]
