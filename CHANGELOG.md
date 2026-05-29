# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0] - 2026-05-29

### Added
- **Opt-in ONNX embedding backend.** `TQMEMORY_EMBEDDING_BACKEND=fastembed` (with
  `pip install turbo-memory-mcp[onnx]`) runs the same multilingual model via ONNX
  Runtime instead of PyTorch — a much smaller RAM footprint for ~2 GB machines,
  vector-compatible with the default backend so no reindex is needed. The default
  backend (`sentence-transformers`) is unchanged.
- **Write-time duplicate/conflict hints.** `remember_note` now surfaces highly
  similar existing project notes as `similar_notes` (supersede or
  review-for-conflict candidates) so the agent can reconcile. The server only
  surfaces candidates — it never auto-deprecates.

### Changed
- **Retrieval fusion is now vector-first and gated.** When the dense lane's top
  hit is confident the BM25 lane is skipped; otherwise BM25 is fused via RRF as a
  down-weighted rescue (previously an equal-weight RRF). Measured to cut the
  hybrid-vs-vector MRR deficit from -0.049 to -0.006 across 28 real corpora while
  preserving the cases where the keyword lane genuinely helps.
- The retrieval vector dimension is now derived from the active embedding model
  (no longer hardcoded to 384), so switching to a different-dimension model needs
  no schema change.

## [0.8.1] - 2026-05-29

Patch release. Fixes the reported server version, which was hardcoded and had
drifted from the actual release.

### Fixed
- `__version__` (surfaced by `server_info`, `health`, the CLI `--version`, and
  the daemon handshake) was a hardcoded literal still reading `0.7.1` even on
  the 0.7.2 and 0.8.0 installs. It now derives from the installed package
  metadata (single source of truth, driven by `pyproject.toml`), so it can no
  longer drift from the release. Falls back to a literal only when running from
  un-installed source. Note: the 0.7.2 and 0.8.0 tags still report `0.7.1` via
  `server_info`; upgrade to 0.8.1 to get accurate version reporting.

## [0.8.0] - 2026-05-29

Minor release. Switches the default embedding model to a multilingual one,
dramatically improving retrieval for non-English content (Cyrillic, Polish,
Spanish, Chinese). Requires a one-time re-embed migration.

### Changed
- Default embedding model is now
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (was
  `all-MiniLM-L6-v2`). On a controlled 6-language probe (EN/UK/RU/PL/ES/ZH),
  avg recall@1 rose from 0.55 to 0.98; Cyrillic in particular went from ~0.25
  to 1.0. The model is still 384-dimensional, so the LanceDB schema is
  unchanged.
- The embedding model is now overridable per install via the
  `TQMEMORY_EMBEDDING_MODEL` environment variable, with no code change.

### Migration
- New retrieval migration v3 -> v4 re-embeds every block and note with the new
  model. Source data (note JSONs, markdown) is untouched; the derived vector
  index is rebuilt from the canonical store, so nothing is lost. After
  upgrading, stop all MCP clients and run `turbo-memory-mcp migrate --apply`.
  Because it re-encodes the whole corpus it can take a while on large stores —
  the `health` / `server_info` migration hint now says so explicitly.

### Tests
- Added `test_upgrade_retrieval_v3_to_v4_reembeds_both_scopes`; made two
  retrieval-manifest version assertions version-agnostic so future format
  bumps no longer require touching them.

## [0.7.2] - 2026-05-29

Patch release. Improves hybrid-retrieval ranking so exact keyword (BM25)
matches are no longer suppressed, and hardens secrets test isolation.

### Fixed
- Hybrid retrieval scored every BM25/FTS-only hit with a flat synthetic
  distance (0.5) regardless of its BM25 rank, capping even a perfect
  exact-term match at "medium" confidence and discarding the BM25 lane's
  ranking signal in the additive scorer. An FTS-only hit's synthetic distance
  is now derived from its BM25 rank (rank 1 → 0.15, +0.08 per rank, capped at
  0.6), so a strong exact match can reach high confidence. The
  `_rank_candidates` sort-key rounding was also tightened (2 → 3 decimals) so
  fine relevance differences are no longer flattened into recency-ordered
  ties. Runtime-only: no stored data, schema, or format-version change;
  existing indexes work unchanged (no migration required).

### Tests
- Added `test_semantic_search_bm25_only_hit_ranks_first_with_high_confidence`
  as a regression guard for the BM25 scoring fix, and updated `test_rrf_merge`
  to assert the rank-aware synthesized distance.
- Isolated the two `*_unavailable_key_returns_structured_error` secrets tests
  from a `TQMEMORY_SECRETS_PASSPHRASE` inherited from the developer's shell
  (the key resolver reads the real process env via priority 1), which produced
  false failures on machines that have the passphrase configured.

## [0.7.1] - 2026-05-28

Patch release. Fixes a stale-lockfile deadlock that could block
`turbo-memory-mcp migrate --apply` (and `--restore-from`) indefinitely after a
daemon exited uncleanly.

### Fixed
- The migration guard treated the mere *existence* of `<storage_root>/.daemon.lock`
  as proof that a primary daemon was running. A daemon that exits uncleanly
  (SIGKILL, crash, host sleep) never runs its release hook, leaving behind a
  lockfile that names a now-dead PID — so `migrate --apply` / `--restore-from`
  would refuse forever even though nothing was writing, forcing operators to
  reach for `--force` or delete the file by hand. The guard now reads the PID
  from the lockfile and checks liveness via the same `_is_pid_alive` helper the
  daemon uses at startup: only a lock whose PID is still alive blocks. A
  lockfile naming a dead PID is correctly treated as stale, and a malformed or
  unreadable lock stays conservative (reported as present) so `--force` remains
  the deliberate escape hatch. This makes the migrate guard and daemon-startup
  staleness detection agree on what counts as a live owner.

### Tests
- Added regression coverage in `tests/test_migrations.py`:
  `test_cli_apply_ignores_stale_lockfile_with_dead_pid` (dead PID → migration
  proceeds) and `test_cli_apply_refuses_when_daemon_pid_alive` (live PID via
  `os.getpid()` → migration blocked).

## [0.7.0] - 2026-05-25

Project-scoped encrypted secrets vault. Agents can now persist and retrieve
connection credentials (SSH keys, DB DSNs, API tokens) across sessions and
machine reboots — encrypted at rest, hard-isolated from `semantic_search`,
`hydrate`, and `lint_knowledge_base`, and never transmitted anywhere
(`src/` continues to contain zero outbound HTTP code).

### Added
- Four new MCP tools: `set_secret(name, value)`, `get_secret(name)`,
  `list_secrets()`, `delete_secret(name)`. Each is implicitly scoped to the
  active project; there is no `scope` parameter.
- `Subsystem.SECRETS` migration chain (`v1 -> v2`) that walks
  `<storage_root>/projects/*` on first `turbo-memory-mcp migrate --apply`
  after upgrade and provisions an empty per-project vault under
  `projects/<project_id>/secrets/`. Idempotent and unconditionally green —
  if the master key cannot be resolved during migration, a stub `meta.json`
  is written and the vault initializes on first successful `set_secret`.
- Per-project encrypted store: `vault.tqv` (AES-256-GCM, 12-byte nonce,
  16-byte GCM tag), `meta.json` (KDF params + key_mode for diagnostics,
  no key material), `audit.jsonl` (append-only access log;
  `(timestamp, action, name)` only, values never logged).
- Subsystem-level marker `<storage_root>/secrets-manifest.json` tracking
  the SECRETS migration `format_version`.
- Master-key resolution priority: env `TQMEMORY_SECRETS_PASSPHRASE`
  (Argon2id, per-project salt) → existing OS keyring entry
  (`turbo-quant-memory` / `secrets-master-<project_id>`) → keyring
  auto-bootstrap (generate + store) on a writable backend → hard fail with
  an actionable setup hint. No interactive prompt fallback — it would
  silently die on reboot.
- Three new runtime dependencies (all pure-Python wheels with native
  CPython extensions, no system-level deps): `keyring>=25.0.0,<26.0`,
  `cryptography>=43.0.0,<47.0`, `argon2-cffi>=23.1.0,<24.0`.
- New CLI subcommand `turbo-memory-mcp secret-set NAME` for first-time
  secret provisioning without exposing the value to a chat transcript.
  On a TTY it reads via `getpass` (hidden input, never enters shell
  history or scrollback); on a pipe it consumes stdin verbatim. Exit
  codes: `0` stored, `2` invalid input (empty / bad name), `3` master
  key unavailable (the setup hint is printed to stderr verbatim).
- 73 new unit / integration tests (`tests/test_secrets_*.py`) covering
  crypto round-trips, KDF determinism, key-resolver branches,
  store CRUD + permissions + tampering, migration provisioning,
  MCP response shapes, ingest/lint/retrieval isolation, and a
  sentinel-grep regression that proves planted secret values never
  surface via `semantic_search`.

### Changed
- `self_test.tool_count` grows `14 -> 18`. `EXPECTED_TOOL_NAMES` extended
  in `tests/test_tools.py` and `scripts/smoke_test.py`. Smoke
  `tool_count` assertion also bumped from `11` (stale) to `18` in the
  same pass.
- `scripts/smoke_test.py` now performs a full `set_secret -> list_secrets
  -> get_secret -> delete_secret -> get_secret(==missing)` round-trip
  against a temp `TQMEMORY_HOME` with a smoke-only
  `TQMEMORY_SECRETS_PASSPHRASE`. New `PASS secrets vault round-trip` line
  in the success summary.

### Security
- New `Security and Trust > Secrets vault threat model` section in
  `TECHNICAL_SPEC` (EN/UK/RU). In scope: accidental backup leaks
  (Time Machine, rsync, iCloud), share-screen / screenshot leaks,
  accidental `git add`. Explicitly out of scope: root compromise, live
  daemon takeover, hardware attacks, multi-tenant isolation. Users with
  bigger threat models should use a dedicated secret manager.
- Ingestion (`ingestion._resolve_roots`) and lint (`knowledge_lint._resolve_roots`)
  refuse to register any path inside the vault subtree with a clear
  `ValueError`. Both `_iter_markdown_files` walkers skip files under
  `projects/<id>/secrets/` as defense in depth.
- `get_secret` response keeps the value in a dedicated `secret_value`
  field only; `set` / `list` / `delete` responses never echo the value.
- Agent recipe documented in `AGENTS.md`, project `CLAUDE.md`, README
  EN/UK/RU section 5, and MEMORY_STRATEGY EN/UK/RU. Split by whether
  the value is already in the chat:
    * NOT yet in chat -> recommend `turbo-memory-mcp secret-set NAME`
      from a terminal (getpass hidden input keeps it out of any
      transcript). This is the prophylactic path.
    * Already in chat (user pasted or agent generated) -> call
      `set_secret(name, value)` directly. The agent's deterministic
      `project_id` resolution is the safer write path once exposure
      has happened; pushing the user back to the CLI just to redo a
      value already in the transcript is friction without protection
      and risks landing the secret in the wrong project if the user's
      terminal cwd does not match the intended one.

### Migration
- Stop all MCP clients, then run `turbo-memory-mcp migrate --apply`.
  A rolling snapshot is taken automatically; on failure the CLI prints
  the exact `--restore-from` command. The v1->v2 step creates the
  vault directory structure for every existing project, then bumps
  `secrets-manifest.json` to `format_version=2`. The migration is
  idempotent — re-running on a fully-provisioned tree is a no-op.

### Documentation
- New "Secrets Vault (NEW in v0.7.0)" sections in all three README
  variants (EN/UK/RU), written in user-reassurance tone: WHY built,
  WHAT CHANGES, WHAT DOES NOT CHANGE, WHERE secrets live and where
  they don't, HOW to use, THREAT MODEL, FAQ.
- `TECHNICAL_SPEC` (EN/UK/RU): MCP Tool Surface table extended; new
  "Secrets vault" subsection under Data Model; "Security and Trust"
  extended with the explicit "src/ contains zero outbound HTTP code"
  claim plus the threat-model breakdown.
- `MEMORY_STRATEGY` (EN/UK/RU): new "Secrets vs Notes" subsection plus
  guardrails extension.
- Per-phase planning artifacts under `.planning/phases/09-secrets-vault/`
  with 3 wave plans + 2 wave summaries + 1 context document.

## [0.6.1] - 2026-05-22

Knowledge Graph Relations implementation. Allows linking memory notes, source files, issues, or tasks in a structured graph at both project and global scopes.

### Added
- Three new MCP tools: `link_entities(...)`, `unlink_entities(...)`, and `get_related_entities(...)`.
- Automatic graph relation enrichment: `semantic_search` and `hydrate` now automatically query the graph and return matching relations inside the payload (`relations` field).
- Cross-scope capability: relations can be created and queried at both `project` and `global` scopes.
- Fully automated Pytest suite for graph relations verifying store logic, search enrichment, and contract compliance.

### Changed
- Refactored `CURRENT_TOOL_NAMES` in `contracts.py` to support 14 tools (up from 11).
- Enhanced retrieval decoration pipeline to look up relations on candidate blocks during retrieval.

## [0.6.0] - 2026-05-22

Phase 3 of the Memory Quality v1 milestone: hybrid retrieval combining
dense-vector search with BM25 (full-text-search) via Reciprocal Rank
Fusion. Particularly addresses the structural overhead-on-short-notes
problem documented in v0.5.x — exact-term hits (function names, file
paths, IDs) now bubble up reliably even when their vector signal is
weak.

### Added
- BM25 lane in `RetrievalIndex.search`: every query now hits the
  dense-vector index AND the FTS index in parallel, results combined
  via RRF (`k=60`). Synthetic `_distance=0.5` for FTS-only hits keeps
  downstream scoring stable.
- `_ensure_fts_index(table)` idempotent helper that creates the BM25
  index on `content_search` on first use.
- `_safe_vector_search` / `_safe_fts_search` defensive wrappers — one
  broken lane no longer takes down the whole query.
- `_rrf_merge` utility producing reproducible rankings (preserves
  vector-row `_distance` when an item appears in both lanes).

### Migration
- New `Subsystem.RETRIEVAL` upgrade v2 -> v3 in `migrations/upgrades.py`:
  no data movement, just creates the FTS index on existing tables.
  Fast on any size of corpus.
- `RETRIEVAL_FORMAT_VERSION` bumped to 2 in `store.py` so fresh
  installs land at v2 and only the v2 -> v3 step is pending.
- Pre-existing v1 installs run both `v1 -> v2` (tier column reset) and
  `v2 -> v3` (FTS index) in a single `migrate --apply` invocation.

### Backwards compatibility
- Legacy installs that have not yet run `migrate --apply` get an empty
  FTS lane and the same vector-only behavior as v0.5.x. No regression.
- The agent-visible `migrations_pending` / `migrations.pending` signals
  introduced in v0.5.1 surface the new pending step exactly the same
  way: agents say "run `turbo-memory-mcp migrate --apply`" to the user.

### Tests
- `_rrf_merge` correctness (combining lanes, synthetic distance,
  skipping rows without `item_id`).
- `_ensure_fts_index` idempotency on a real LanceDB instance.
- `_safe_fts_search` graceful degradation contract.
- Live LanceDB hybrid probe: an item that tops both lanes ranks #1.
- `upgrade_retrieval_v2_to_v3` exercise via stubbed `RetrievalIndex` —
  both scopes open, both get `_ensure_fts_index`. No reset.
- Full suite: 168/168 green.

## [0.5.1] - 2026-05-22

Agent-visible pending-migration signal. Closes the UX gap from v0.5.0:
the stderr warning at daemon startup is only visible to humans reading
client logs. After upgrading the binary, agents still ran against the
legacy v1 schema silently. v0.5.1 surfaces the same detection through
two MCP probes every agent calls at session start.

### Added
- `mcp__tqmemory__health` payload now includes:
  - `migrations_pending: bool` — single flag so agents can branch cheaply.
  - `migrations_hint: str` (only when pending) — one-line operator
    instruction safe to surface in a tool response.
- `mcp__tqmemory__server_info` payload now includes a `migrations`
  block with per-subsystem detail:
  ```json
  "migrations": {
    "pending": true,
    "subsystems": [
      {"subsystem": "notes", "current_version": 1, "latest_version": 2,
       "pending": true, "step_count": 1},
      ...
    ],
    "hint": "Stop all MCP clients, then run `turbo-memory-mcp migrate --apply` ..."
  }
  ```
- Tests cover both the payload shape and the live store -> probe path
  (legacy manifest yields `pending=true`; clean store yields
  `pending=false`).

### Agent integration recommendation
Agents (Claude Code / Codex / Gemini CLI / Cursor / OpenCode / etc.)
should query `mcp__tqmemory__server_info` (or the cheaper
`mcp__tqmemory__health`) on session start. If `migrations.pending`
(or `migrations_pending`) is true, surface the included `hint`
verbatim to the user — it already names the exact command and
notes the lockfile requirement.

## [0.5.0] - 2026-05-22

Phase A (migration framework) and Phase 2 (tier separation) of the
Memory Quality v1 milestone. Behavior change: episodic notes (handoffs)
no longer pollute default semantic_search.

Backward-compatible upgrade: legacy LanceDB tables keep working until
`migrate --apply` runs. The daemon-lock guard blocks `--apply` while a
primary MCP daemon is running so users do not race with a live writer.

### Phase 2 — Tier separation (this release)

- Notes now carry a `tier` field: `durable` (decisions / patterns /
  lessons), `episodic` (handoffs), or `reference` (markdown blocks).
- `semantic_search` accepts a new `tier_filter` argument. Default is
  `("durable", "reference")` so session handoffs no longer drown durable
  knowledge in the default search. Opt episodic in via
  `tier_filter=("episodic",)` or list every tier to query everything.
- `remember_note` auto-assigns tier from `kind`: `handoff` -> episodic,
  everything else -> durable. Explicit `tier=` overrides for tests.
- New `Subsystem.NOTES` migration chain in the framework. Pre-Phase-2
  manifests (without a `format_version` field) are detected as v1 and
  the `notes v1->v2` migration tags every existing note with the right
  tier — idempotent on re-run.
- New `Subsystem.RETRIEVAL` v1->v2 migration resets the LanceDB tables
  on first run so the new `tier` column appears in the vector index.
- Migration framework: project + global manifests now record
  `format_version` (was absent before Phase 2).
- MCP wrapper `semantic_search_impl` exposes `tier_filter` so MCP
  clients can opt into episodic search (e.g. `tier_filter=("episodic",)`)
  when needed; default still excludes episodic.
- `promote_note` preserves an explicit `tier` set on the project note
  when copying it to global scope (previously the tier was silently
  re-derived from `kind` on the global side).
- Graceful upgrade path: until `migrate --apply` runs, existing LanceDB
  tables stay on the v1 schema (no `tier` column). `RetrievalIndex.search`
  introspects the live table and skips the WHERE clause when the column
  is missing, so search keeps working between upgrade and migrate.
- `semantic_search` and `hydrate` payloads now expose the `tier` field
  on every item (both note and markdown). Clients can filter or render
  by tier without re-fetching the source record. `tier` is omitted on
  legacy hits that pre-date Phase 2; markdown blocks always carry
  `tier="reference"`.
- All manifest writers (`write_project_manifest`, `write_global_manifest`,
  `write_markdown_manifest`, `write_*_retrieval_manifest`) now preserve
  any bumped `format_version` on disk instead of overwriting it with
  the in-code constant. Without this every `remember_note` /
  `index_paths` after a `migrate --apply` would silently revert the
  manifest and re-trigger the detect/apply loop forever. Auto-repair
  of stale `format_version=0` manifests still works because the
  effective value is `max(existing, in_code_baseline)`.
- `_bump_manifest` for `NOTES` now creates the proper full project /
  global manifest payloads (scope, identity, storage_root) when they
  do not exist on disk, instead of writing a stripped
  `{format_version, updated_at}` dict.

### Phase A — Migration framework foundation

Foundation activated earlier this cycle. Internal — surfaces only when
the daemon detects a pending upgrade or when the operator runs
`turbo-memory-mcp migrate`.

### Added
- `migrations/` package: `@migration` decorator, `Subsystem` enum
  (`markdown`, `retrieval`, `usage_stats`), linear-chain registry,
  per-subsystem `format_version` detection.
- Runner with detect / dry-run / atomic apply (manifest is bumped last
  per step so a crash leaves storage at the previous version and the
  upgrade can be safely retried).
- Rolling-backup snapshot helper under `<storage_root>/.snapshots/` with
  microsecond-precision timestamps and configurable retention
  (`TQMEMORY_SNAPSHOTS_KEEP`, default 1). `restore_snapshot` uses a
  staging-then-copy pattern so a failed copy rolls back to the original
  state instead of leaving storage half-written.
- Structured JSONL log at `~/.turbo-quant-memory/migration.log`
  (overridable via `TQMEMORY_MIGRATION_LOG_PATH`).
- Daemon startup (primary / standalone roles only) detects pending
  upgrades and writes a single-line warning to stderr. Detection never
  blocks startup; proxies skip the check.
- `turbo-memory-mcp migrate` CLI subcommand:
  - `--status` (default): per-subsystem current vs latest.
  - `--dry-run`: list pending upgrades without touching storage.
  - `--apply`: snapshot + atomic apply. Refuses to run if a daemon
    lockfile is present unless `--force` is set. On failure, prints the
    exact `--restore-from` command to roll back.
  - `--snapshot-only`: take a backup without applying.
  - `--list-snapshots`: list available snapshots.
  - `--restore-from <path>`: restore live storage from a snapshot.
  - `--no-snapshot`: escape hatch (tests only).
  - `--force`: bypass daemon-lockfile check.
- Test coverage for registry chain/gap/duplicate, runner atomicity /
  idempotency / failure path / multi-subsystem, snapshot
  create / restore / rollback / retention, log JSONL shape, and the
  full CLI surface (status, dry-run, apply, list, restore, daemon-lock
  guard, snapshot-failure handling).

## [0.4.3] - 2026-05-22

Maintenance baseline before the next architecture cycle. No behavioral changes.

### Added
- `CHANGELOG.md` with the full release history.
- `.claude/` directory listed in `.gitignore` so Claude Code local configuration is no longer tracked.

### Changed
- README and SMOKE checklist install commands now point at `v0.4.3`.

## [0.4.2] - 2026-04-28

### Added
- Gemini CLI fixture and the bundled `.gemini/settings.json` now ship `"context": {"fileName": ["AGENTS.md", "GEMINI.md"]}`, so Gemini CLI picks up the same `AGENTS.md` project prompts the rest of the agents already use — no duplicate `GEMINI.md` mirror required.
- New SMOKE checklist step warning operators that merging the Gemini fixture into an existing `settings.json` must preserve the `context` block.
- Tracking entry `.planning/todos/2026-04-28-lint-false-positives.md` for two `lint_knowledge_base` issues (ASCII-only title-key normalization, `broken_link` reports for `DEFAULT_IGNORED_DIR_NAMES`).

### Changed
- README and SMOKE checklist install commands updated to point at the actual current release, with a new `Upgrading` subsection covering `uv tool install --reinstall` and the one-time `~/.gemini/settings.json` migration.

## [0.4.1] - 2026-04-24

### Fixed
- Automatic proxy failover when the primary process dies. Proxies detect the loss via `PrimaryUnreachable` on the next RPC call, one surviving proxy atomically promotes itself to primary, and every other orphaned proxy reconnects. No MCP client restart required.
- Race in concurrent promotion serialized via lockfile ordering.
- Phase-aware RPC error handling: connect/send-phase failures translate into `PrimaryUnreachable` (safe to replay); mid-call failures surface unchanged so non-idempotent tools like `remember_note` are never silently duplicated.
- Hardened singleton rollout from v0.4.0.

### Added
- Gemini CLI fixture ships `context.fileName: ["AGENTS.md", "GEMINI.md"]` for consistent project prompts across agents.

## [0.4.0] - 2026-04-24

### Added
- Singleton daemon transport for cross-process memory sharing. Only one `turbo-memory-mcp` process per machine holds the sentence-transformers model and LanceDB handles; every additional MCP-client launch becomes a thin stdio↔socket proxy.
- Cross-platform coordination: `AF_UNIX` socket on Unix/macOS (short path, 0600 perms), named pipe on Windows. Authenticated via a 32-byte random authkey stored in `~/.turbo-quant-memory/.daemon.lock`.
- Lazy imports in `retrieval_index` so proxy processes do not pay the ~470 MB cost of PyTorch / LanceDB / PyArrow when they only forward RPC.

### Changed
- Measured savings: ~1 GB RSS for four concurrent MCP clients (primary 530 MB, proxies ~50 MB each vs 437 MB each before).

### Notes
- Existing on-disk state (JSON notes, LanceDB tables, Markdown blocks, manifests) is unchanged and fully backward-compatible.
- Escape hatch: set `TQMEMORY_DAEMON_DISABLE=1` to fall back to per-process mode.

## [0.3.2] - 2026-04-12

### Added
- `.tqmemoryignore` support for excluding paths from Markdown indexing. One glob pattern per line, `#` for comments; works in project root or any indexed directory.

### Fixed
- `__version__` bump and telemetry payload corrections.

## [0.3.1] - 2026-04-04

### Added
- Shared-memory guidance for Codex and Gemini CLI handoffs in README and client integration docs.
- Ready Gemini CLI fixture plus smoke-check steps for validating the same `tqmemory` server across clients.

### Changed
- Clarified that shared memory is local same-machine continuity, not remote cloud sync.

## [0.3.0] - 2026-04-03

### Added
- Resilience and telemetry rollout.
- Audit hardening todos captured in `.planning/`.

### Changed
- Trilingual README copy (English, Ukrainian, Russian).

## [0.2.4] - 2026-04-03

### Added
- Knowledge-base linting (`lint_knowledge_base`) for broken links, orphans, and duplicate titles.

### Changed
- Release notes and documentation refresh.

## [0.2.3] - 2026-03-28

### Added
- Trilingual documentation set across README, TECHNICAL_SPEC, MEMORY_STRATEGY, and SMOKE checklists.

## [0.2.2] - 2026-03-26

### Fixed
- Package metadata corrections for PyPI/GitHub release artifacts.

## [0.2.1] - 2026-03-26

### Changed
- Memory hygiene improvements (note lifecycle housekeeping, telemetry payload cleanup).

## [0.2.0] - 2026-03-25

### Added
- Note lifecycle tools: `deprecate_note`, `promote_note`, and release-grade documentation around them.

## [0.1.0] - 2026-03-15

### Added
- Initial release.
- `remember_note`, `semantic_search`, `hydrate`, `index_paths`, `lint_knowledge_base` core tools.
- Project/global scope memory.
- Hydration paths and benchmark suite.
- Trilingual documentation (English, Ukrainian, Russian).

[0.6.1]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.4.3...v0.5.0
[0.4.3]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.2.4...v0.3.0
[0.2.4]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Lexus2016/turbo_quant_memory/releases/tag/v0.1.0
