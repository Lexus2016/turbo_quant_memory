"""turbo_memory_mcp.secrets — project-scoped encrypted vault (Phase 9).

This package contains the cryptographic primitives, key resolution, on-disk
store, and audit log for the per-project secrets vault. The full public
surface (``SecretsStore``, ``AuditLog``, ``MasterKeyUnavailable``,
``resolve_master_key``) is wired up after all Wave 1 modules land.
"""
