# Phase 9 Wave 2 — Summary

**Completed:** 2026-05-25
**Status:** Migration registered, 4 MCP tools exposed, ingestion / lint / retrieval hardened. The Phase 9 vault is now reachable from live agents and isolated from every retrieval path.

## Deliverables

| Commit | Subject | What it adds |
|---|---|---|
| `secrets(migration)` | Subsystem.SECRETS with v1->v2 provisioning upgrade | `Subsystem.SECRETS` enum + `SECRETS_FORMAT_VERSION = 2` constant; `secrets_manifest_path()` / `read_secrets_manifest()` on `MemoryStore`; `upgrade_secrets_v1_to_v2` registered in `migrations/upgrades.py`; smart `_read_current_version` for SECRETS (no false "pending" warning on fresh installs); `_bump_manifest` for SECRETS. Existing `test_detect_status_treats_missing_manifests_as_version_zero` updated to skip SECRETS with rationale. 10 new tests in `tests/test_secrets_migration.py`. |
| `secrets(mcp)` | Expose 4 MCP tools | `set_secret` / `get_secret` / `list_secrets` / `delete_secret`. 6 new response builders in `contracts.py`; 4 `*_impl` functions + handler + dispatch wiring in `server.py`; tool count grows 14 -> 18 (`tests/test_tools.py` bumped + EXPECTED_TOOL_NAMES extended). 12 integration tests in `tests/test_secrets_mcp_tools.py`. |
| `secrets(isolation)` | Harden ingestion + lint walkers against secrets/ subtree | New `secrets/paths.py` with `is_inside_secrets_storage(path, storage_root)`; refusal guards in `ingestion._resolve_roots` and `knowledge_lint._resolve_roots`; defense-in-depth filters in both `_iter_markdown_files` functions. 6 isolation tests in `tests/test_secrets_isolation.py`. |

**Cumulative test count after Wave 2: 271 (was 243 after Wave 1).**

## Verification

```
uv run ruff check src tests scripts             — Phase 9 files clean
uv run pytest -q                                — 271 passed
python -c "from turbo_memory_mcp.server import TOOL_HANDLERS; print(len(TOOL_HANDLERS))"  — 18
```

## Locked design decisions taken during Wave 2

1. **SECRETS migration is v1 -> v2, not v0 -> v1.** The existing `Migration` validator rejects `from_version<1` and the runner ignores `current_version<1`. Treating "missing secrets-manifest with existing project dirs" as v1 ("upgrade from pre-v0.7 install") matches the NOTES legacy-v1 convention. Fresh installs with zero project dirs stay at v0 so they don't see a noisy "pending migration" warning.
2. **`get_secret` on a fresh install returns `"missing"`, not `"error"`.** Honest UX: when there is literally no vault file, "no such secret yet" is correct. The `master_key_unavailable` error surfaces on `set_secret` (writes need a key) and on any operation against a vault that already exists with a lost key.
3. **`secret_value` lives in a dedicated field on `get_secret` responses only.** `set`, `list`, and `delete` never echo the value. The audit log records the access (`set` / `get` / `list` / `delete` × `name`) but never the value. Sentinel-grep test asserts the value never appears anywhere in the audit log.
4. **Isolation guard targets the canonical layout, not the dir name.** `is_inside_secrets_storage(path, storage_root)` matches `<storage_root>/projects/<id>/secrets/...` specifically. A user's own `~/Documents/my-secrets-notes/` is not affected — only the vault storage is protected.
5. **Migration unconditionally green.** If a project's master key cannot be resolved during migration (headless install without env passphrase yet), the upgrade function creates `secrets/` + stub `meta.json` with `vault_initialized: false` and skips `vault.tqv`. First successful `set_secret` lazily completes provisioning. Migration never raises on missing key.

## Behavioral contract for existing v0.6.1 installs

When a user upgrades from v0.6.1 to v0.7.0 and runs `turbo-memory-mcp migrate --apply`:

1. The runner detects SECRETS at `current_version=1` (storage has project dirs, no secrets-manifest yet).
2. The v1->v2 upgrade walks `<storage_root>/projects/*` and provisions each project's `secrets/` directory.
3. If `TQMEMORY_SECRETS_PASSPHRASE` is set or the OS keyring is writable, each `vault.tqv` is created as an empty AES-256-GCM blob. Otherwise just a stub `meta.json` is written; the vault initializes on first `set_secret` per project.
4. Subsystem manifest at `<storage_root>/secrets-manifest.json` is bumped to `format_version: 2`.

Existing notes, markdown indexing, semantic search, hydration, and link queries continue working without any change. The vault is invisible to all of them by design.

## Outstanding for Wave 3 (docs + smoke + release)

- Extend `scripts/smoke_test.py` with a `set_secret -> get_secret -> list_secrets -> delete_secret` round-trip; bump `tool_count` assertion to 18 (it is currently still at 11 in the smoke script — a pre-existing inconsistency separate from this phase but worth fixing as part of the secrets release rollout).
- Bilingual README sections (EN / UK / RU) framed as USER REASSURANCE: nothing breaks, your secrets stay on your machine, we never transmit them anywhere, you keep full control. Backed by the verified fact that `src/` contains zero outbound HTTP code (no `requests` / `httpx` / `aiohttp` / `urllib.request` / `urlopen` / raw sockets).
- TECHNICAL_SPEC / MEMORY_STRATEGY / AGENTS / project CLAUDE updates.
- `CHANGELOG.md` entry for `v0.7.0`.
- `examples/clients/` snippets demonstrating the four new tools.
