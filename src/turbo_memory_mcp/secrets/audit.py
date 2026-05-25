"""Per-project append-only audit log for the secrets vault.

Lives inside the same ``secrets/`` directory as ``vault.tqv``. ``project_id``
is implicit from the path; deleting a project removes its audit history too,
matching the project-scope-only invariant.

Each record is a single JSON line:

    {"ts": "<iso utc>", "action": "set|get|list|delete", "name": "..."}

The secret VALUE is never logged. By convention the ``list`` action records
``name="*"`` because no single name is involved.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

AUDIT_FILENAME = "audit.jsonl"
_ALLOWED_ACTIONS = frozenset({"set", "get", "list", "delete"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class AuditLog:
    """Append-only per-project audit log living next to ``vault.tqv``."""

    def __init__(self, secrets_dir: Path | str) -> None:
        self.secrets_dir = Path(secrets_dir)
        self.path = self.secrets_dir / AUDIT_FILENAME

    def record(self, action: str, name: str) -> None:
        if action not in _ALLOWED_ACTIONS:
            raise ValueError(
                f"action {action!r} must be one of {sorted(_ALLOWED_ACTIONS)}"
            )
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        payload = {"ts": _utc_now_iso(), "action": action, "name": name}
        line = json.dumps(payload, ensure_ascii=False) + "\n"

        # O_APPEND on POSIX guarantees atomic appends up to PIPE_BUF (4096 on
        # Linux/macOS), which more than covers a single JSON line.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(self.path, flags, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        # Enforce 0o600 idempotently (umask may have weakened the create mode).
        os.chmod(self.path, 0o600)

    def count(self) -> int:
        """Approximate line count for server_info reporting."""
        if not self.path.exists():
            return 0
        with self.path.open("rb") as fh:
            return sum(1 for _ in fh)
