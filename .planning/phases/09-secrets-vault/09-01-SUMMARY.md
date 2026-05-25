# Phase 9 Wave 1 — Summary

**Completed:** 2026-05-25
**Status:** Foundation modules + tests landed; package importable, not yet wired into the live MCP server (Wave 2 work).

## Deliverables

| File | Lines | Purpose |
|---|---|---|
| `pyproject.toml` | +3 deps | `argon2-cffi`, `cryptography`, `keyring` added with pinned ranges. |
| `uv.lock` | regen | Pinned: `argon2-cffi==23.1.0`, `cryptography==46.0.7`, `keyring==25.7.0`. |
| `src/turbo_memory_mcp/secrets/__init__.py` | 33 | Public surface: `SecretsStore`, `AuditLog`, `MasterKeyUnavailable`, `resolve_master_key`. |
| `src/turbo_memory_mcp/secrets/crypto.py` | 79 | AES-256-GCM `encrypt/decrypt` + Argon2id `derive_key_from_passphrase` over `cryptography` + `argon2-cffi`. No bespoke crypto. |
| `src/turbo_memory_mcp/secrets/keyresolver.py` | 117 | Env -> existing keyring entry -> keyring auto-bootstrap -> `MasterKeyUnavailable`. Internal `KeyResolutionMode` enum. |
| `src/turbo_memory_mcp/secrets/store.py` | 215 | `SecretsStore` with idempotent `provision`, atomic writes via tempfile + fsync + os.replace, 0o700 dir / 0o600 files. |
| `src/turbo_memory_mcp/secrets/audit.py` | 60 | `AuditLog` per-project append-only JSONL log; never logs values; mode 0o600. |
| `tests/test_secrets_crypto.py` | 28 tests | Round-trip, unique nonce, wrong/tampered key/ciphertext, KDF determinism, KDF project + passphrase separation, KDF input validation. |
| `tests/test_secrets_keyresolver.py` | 10 tests | All four resolution branches, project separation, idempotent bootstrap, corrupted keyring entry, invalid base64. |
| `tests/test_secrets_store.py` | 24 tests | Provision idempotency, stub-meta-then-set lazy init, CRUD round-trip, persistence across instances, name + value validation, file permissions, vault tampering. |
| `tests/test_secrets_audit.py` | 11 tests | Schema (only ts/action/name; no `value` ever), append semantics, list-action convention, mode 0o600, count. |

**Total new tests: 73, all green.**

## Verification

```
uv run ruff check src/turbo_memory_mcp/secrets/ tests/test_secrets_*.py
  All checks passed!

uv run pytest -q
  243 passed
```

## Deviations from 09-01-PLAN.md

1. **Tests live in flat `tests/`, not `tests/unit/`.** The project's existing convention is flat. Test paths were adjusted; functionality unchanged.
2. **Probe-based keyring writability check dropped.** Plan called for a sentinel `__tqv_probe__` set/delete round-trip before bootstrap. Simplified to a direct `set_password` of the real key wrapped in `try/except KeyringError`. Same coverage, less Keychain noise on real macOS use, one fewer round-trip.
3. **`cryptography` dependency upper bound widened.** Plan said `>=42`. Implemented as `>=43.0.0,<47.0` after observing that the project already had `cryptography==46.0.x` as a transitive — the `<46.0` plan bound forced an accidental downgrade. Bound widened so 46.x stays available.
4. **`__init__.py` public surface excludes `KeyResolutionMode`.** Internal-only per plan; confirmed not exported.

## Pre-existing ruff lints (NOT introduced by Wave 1)

`uv run ruff check src tests scripts` surfaces 9 errors in files last touched by v0.6.0 / v0.6.1 commits (cli.py:152, tests/test_daemon.py:14,17, tests/test_migrations.py:6,13,1441,1469,1536, tests/test_relations.py:3). All Phase 9 files pass cleanly in isolation. These pre-existing lints are intentionally NOT fixed in Wave 1 per scope discipline — they belong to a separate cleanup task.

## Outstanding for Wave 2

- Register `Subsystem.SECRETS` in `migrations/registry.py` and add `SECRETS_FORMAT_VERSION = 1` to `store.py`.
- Implement `@migration(SECRETS, 0->1)` that walks existing project directories and calls `SecretsStore.provision()` on each.
- Add four MCP tools (`set_secret` / `get_secret` / `list_secrets` / `delete_secret`) to `server.py` with `secret_value` returned in a dedicated field (never in descriptive text).
- Harden `ingestion.py`, `knowledge_lint.py`, and the retrieval index against `secrets/` directory traversal.
- Bump `server_info()` payload with active-project secrets block.
- Bump `self_test.tool_count` 14 -> 18 and update smoke fixtures.

## What this Wave does NOT yet do (so it cannot break running installs)

- No migration is registered. Existing installs see zero behavioral change from this commit chain.
- No MCP tool is exposed. `set_secret` is not callable by agents until Wave 2.
- No ingestion / retrieval changes. `semantic_search` / `hydrate` / `lint_knowledge_base` behave identically to v0.6.1.

The Wave 1 chain is purely additive infrastructure. Safe to ship as `v0.7.0-alpha` if desired; otherwise wait for Wave 2 to land for a single coordinated release.
