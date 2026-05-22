"""Structured JSONL log for migration events.

One event per line. Append-only. Default location is
~/.turbo-quant-memory/migration.log, overridable via the
TQMEMORY_MIGRATION_LOG_PATH environment variable (used by tests).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__

_LOG_PATH_ENV = "TQMEMORY_MIGRATION_LOG_PATH"


def log_path() -> Path:
    """Return the path migration events are appended to."""
    override = os.environ.get(_LOG_PATH_ENV)
    if override:
        return Path(override)
    return Path.home() / ".turbo-quant-memory" / "migration.log"


def log_event(event: str, **fields: Any) -> None:
    """Append one JSON line describing a migration event.

    Always includes timestamp_utc and package_version.
    """
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "package_version": __version__,
        "event": event,
        **fields,
    }
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        fh.write("\n")
