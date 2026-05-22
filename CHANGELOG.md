# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
