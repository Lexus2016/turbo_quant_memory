# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
