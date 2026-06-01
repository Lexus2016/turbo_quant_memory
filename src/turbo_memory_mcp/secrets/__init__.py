"""turbo_memory_mcp.secrets — project-scoped encrypted vault (Phase 9).

Per-project AES-256-GCM secrets storage with hard isolation from notes,
search, and hydration paths. The master key resolves via, in order:
env var ``TQMEMORY_SECRETS_PASSPHRASE`` -> existing OS keyring entry ->
keyring auto-bootstrap (write paths only) -> hard fail with an actionable
setup hint.

Public surface:
    SecretsStore(storage_root, project_id)
        Bind to one project's vault. ``.provision()`` is idempotent and
        unconditionally green for migration use.
    AuditLog(secrets_dir)
        Append-only per-project access log next to ``vault.tqv``.
    resolve_master_key(project_id, *, allow_bootstrap=False)
        Internal resolver re-exported for the migration runner and
        server-info diagnostics.
    MasterKeyUnavailable
        Raised when no master-key path works. Message contains the
        setup hint verbatim and can be surfaced into MCP error responses.
    VaultDecryptError
        Raised when a vault exists but cannot be decrypted with the
        resolved key (shadowing env passphrase, or tampered ciphertext).
        The MCP layer maps it to a structured ``master_key_mismatch`` error
        instead of letting a bare ``InvalidTag`` escape (DEFECT A).
"""

from .audit import AuditLog
from .keyresolver import MasterKeyUnavailable, resolve_master_key
from .paths import is_inside_secrets_storage
from .store import SecretsStore, VaultDecryptError

__all__ = [
    "AuditLog",
    "MasterKeyUnavailable",
    "SecretsStore",
    "VaultDecryptError",
    "is_inside_secrets_storage",
    "resolve_master_key",
]
