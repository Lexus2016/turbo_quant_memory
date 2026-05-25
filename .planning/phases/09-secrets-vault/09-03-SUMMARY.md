# Phase 9 Wave 3 — Summary

**Completed:** 2026-05-25
**Status:** Release-ready. Phase 9 closes with `v0.7.0`.

## Deliverables

| Commit | Subject | What it adds |
|---|---|---|
| `smoke(secrets)` | Extend `scripts/smoke_test.py` with secrets round-trip | `EXPECTED_TOOL_NAMES` brought up to date with all 18 tools (added link/unlink/get_related from v0.6.1 plus the four new Phase 9 tools); `tool_count` assertion bumped `11 -> 18`; `TQMEMORY_SECRETS_PASSPHRASE='tqmemory-smoke-passphrase-only'` added to smoke env; `set_secret -> list_secrets -> get_secret -> delete_secret -> get_secret(missing)` round-trip with assertions on response shape (no `secret_value` echo from set/list, value present only in get_secret's dedicated field, status='missing' after delete). New `PASS secrets vault round-trip` line. |
| `docs(readme)` | README EN/UK/RU reassurance section | Top-level `🔐 Secrets Vault (NEW in v0.7.0)` section structured as WHY / WHAT CHANGES / WHAT DOES NOT CHANGE / WHERE secrets live and DON'T / HOW to use / THREAT MODEL / FAQ. Explicit verifiable claim: `src/` contains zero outbound HTTP code. |
| `docs(spec)` | TECHNICAL_SPEC EN/UK/RU | MCP Tool Surface table extended; new Secrets vault subsection under Data Model; Security and Trust extended with the threat model. |
| `docs(strategy)` | MEMORY_STRATEGY EN/UK/RU | Secrets vs Notes subsection plus guardrail extensions. |
| `docs(agents)` | AGENTS.md + project CLAUDE.md | Accessing Project Secrets recipe — never `set_secret` with a chat-transcript value, surface `setup_hint` verbatim, pass `secret_value` programmatically. |
| `chore(release)` | CHANGELOG + version bump + SMOKE_CHECKLIST | `CHANGELOG [0.7.0]` entry with Added / Changed / Security / Migration / Documentation sections; `__version__` and `pyproject.toml` bumped to `0.7.0`; uv.lock regenerated; `examples/clients/SMOKE_CHECKLIST.md` step 9 (secrets round-trip) added with assertions; `tool_count = 18`. |

**Final test suite count: 271 passing (unchanged from end of Wave 2; Wave 3 is doc-only plus smoke fixture which already runs green).**

## Verification

```
uv run ruff check src tests scripts             — Phase 9 files clean
uv run pytest -q                                — 271 passed
uv run python scripts/smoke_test.py             — green end-to-end (now includes
                                                  PASS secrets vault round-trip)
python -c "from turbo_memory_mcp import __version__; print(__version__)"  — 0.7.0
```

## Behavioral contract surface after Phase 9

| Surface | State |
|---|---|
| Tool count | 18 (was 14 on v0.6.1) |
| New tools | `set_secret`, `get_secret`, `list_secrets`, `delete_secret` |
| Migration | `Subsystem.SECRETS` v1 -> v2; idempotent; unconditionally green |
| Storage | Per-project AES-256-GCM blob at `~/.turbo-quant-memory/projects/<id>/secrets/vault.tqv` |
| Isolation | Ingester / lint / retrieval refuse `secrets/` traversal |
| Audit | Per-project `audit.jsonl` with `{ts, action, name}` only |
| Master key | Env → existing keyring → keyring auto-bootstrap → hard fail |
| Transmission | Zero outbound HTTP code in `src/` — verified |

## Wave 3 deviations from 09-03-PLAN.md

1. **SMOKE_CHECKLIST.uk.md / SMOKE_CHECKLIST.ru.md were not updated** in this commit chain. The English variant is the live operational checklist; the translations can be brought in a follow-up. Skipped here for scope discipline (Phase 9 is already a 16-commit chain).
2. **Client fixture files (examples/clients/*.mcp.json / .toml / .json)** were NOT touched. They are pure MCP-server registration metadata (no tool listing inside). The new tools are auto-discovered by every MCP client at handshake time via `list_tools`; no fixture change is needed.
3. **v0.7.0 git tag NOT created in this commit chain.** Tagging happens out-of-band by a release workflow / human after CI is green on the release branch.

## What is left for a follow-up (post v0.7.0)

- `turbo-memory-mcp secret-set` CLI subcommand so users can set their initial secrets without ever pasting a value into a chat transcript.
- Master-key rotation tool (`rotate_master_key(project_id)`).
- SMOKE_CHECKLIST.uk.md / SMOKE_CHECKLIST.ru.md translation pass to mirror the EN update.
- Secret export / cross-machine sync — currently out of scope per locked decisions; will revisit if user demand surfaces.

## Phase 9 complete

The full chain (16 commits):

```
chore(release)        — version bump + CHANGELOG + smoke checklist + Wave 3 summary
docs(agents)          — AGENTS + project CLAUDE
docs(strategy)        — MEMORY_STRATEGY EN/UK/RU
docs(spec)            — TECHNICAL_SPEC EN/UK/RU
docs(readme)          — README EN/UK/RU reassurance section
smoke(secrets)        — smoke_test.py round-trip + tool_count 18
docs(phase-9)         — Wave 2 summary
secrets(isolation)    — ingester + lint guards + 6 tests
secrets(mcp)          — 4 MCP tools + 12 tests
secrets(migration)    — Subsystem.SECRETS + 10 tests
secrets(api)          — public surface + Wave 1 summary
secrets(audit)        — per-project audit log + 11 tests
secrets(store)        — on-disk vault CRUD + 24 tests
secrets(keyresolver)  — env-first key resolution + 10 tests
planning(phase-9)     — README reassurance refinement
secrets(crypto)       — AES-256-GCM + Argon2id + 28 tests
secrets(deps)         — keyring + cryptography + argon2-cffi
planning              — Phase 9 introduction
```

Net: 73 new unit/integration tests, full suite passes 271/271, smoke runs end-to-end with the new secrets round-trip, all bilingual user-facing docs updated, version bumped to `0.7.0`, no behavior change for users who never call `set_secret`.
