"""Schema migration framework for Turbo Quant Memory.

Activates the existing format-version constants (MARKDOWN_FORMAT_VERSION,
RETRIEVAL_FORMAT_VERSION, USAGE_STATS_FORMAT_VERSION, NOTES_FORMAT_VERSION)
as a real upgrade system. Each subsystem keeps its own linear chain of
upgrades. The runner detects gaps and applies them in order; a snapshot
is taken before any mutation. Daemon startup only detects-and-warns —
opt-in apply is via `turbo-memory-mcp migrate --apply`.

Real upgrade functions live in `upgrades.py` and are registered on
package import.
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

# Import upgrades last so @migration decorators run against a fully
# loaded registry module. This is the canonical place to register
# real upgrade steps for every phase.
from . import upgrades  # noqa: F401 — import for side effects

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
    "upgrades",
]
