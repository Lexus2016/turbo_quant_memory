---
phase: 01-client-integration-foundation
plan: "02"
subsystem: docs
tags: [mcp, docs, claude-code, codex, cursor, opencode, antigravity]
requires:
  - "01-01 packaged stdio MCP server contract"
provides:
  - "Bilingual README onboarding for the Phase 1 runtime contract"
  - "Canonical config fixtures for Claude Code, Codex, Cursor, OpenCode, and Antigravity"
  - "Per-client smoke checklist with explicit Tier 1 and Tier 2 validation paths"
affects: ["01-03", "phase-2-onboarding", "operator adoption"]
tech-stack:
  added: []
  patterns:
    - "One server id and one uv-based launch contract are repeated across every client fixture"
    - "Tier labels stay explicit so documented compatibility is not confused with production proof"
key-files:
  created:
    - README.md
    - examples/clients/claude.project.mcp.json
    - examples/clients/codex.config.toml
    - examples/clients/cursor.project.mcp.json
    - examples/clients/opencode.config.json
    - examples/clients/antigravity.mcp.json
    - examples/clients/SMOKE_CHECKLIST.md
  modified: []
key-decisions:
  - "Used the same server id `tqmemory` and the same `uv run turbo-memory-mcp serve` contract in every checked-in fixture."
  - "Documented Antigravity as Tier 2 with honest manual-import wording instead of claiming equal proof to scriptable clients."
patterns-established:
  - "README and smoke docs must mirror the runtime metadata returned by `server_info` and `self_test`."
  - "Client onboarding lives under examples/clients so operators can inspect config fixtures beside the checklist."
requirements-completed: [INT-03, INT-04]
duration: 1min
completed: 2026-03-25
---

# Phase 01 Plan 02: Client Integration Foundation Summary

**Bilingual README onboarding plus canonical Claude Code, Codex, Cursor, OpenCode, and Antigravity MCP fixtures with a per-client smoke checklist**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-25T20:31:40Z
- **Completed:** 2026-03-25T20:32:16Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- Added a bilingual `README.md` that documents the canonical `uv` install path, `pip` fallback, server id, runtime contract, tool surface, and support tiers.
- Added checked-in config fixtures for Claude Code, Codex, Cursor, OpenCode, and Antigravity that all point to the same local stdio server.
- Added a bilingual manual smoke checklist with explicit success signals and honest Tier 1 versus Tier 2 labeling.

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the README quickstart and client support contract** - `aba00bf` (`docs`)
2. **Task 2: Add canonical config fixtures for every target client** - `14f90b3` (`docs`)
3. **Task 3: Publish the manual smoke checklist for all clients** - `080a990` (`docs`)

## Files Created/Modified

- `README.md` - Operator quickstart, runtime contract, supported clients, and tier definitions.
- `examples/clients/claude.project.mcp.json` - Claude Code project-scoped `mcpServers` fixture.
- `examples/clients/codex.config.toml` - Codex `config.toml` fixture for the shared `tqmemory` server.
- `examples/clients/cursor.project.mcp.json` - Cursor project-scoped MCP fixture.
- `examples/clients/opencode.config.json` - OpenCode local MCP fixture with `$schema` and command array.
- `examples/clients/antigravity.mcp.json` - Raw JSON fixture for Antigravity custom MCP import.
- `examples/clients/SMOKE_CHECKLIST.md` - Concrete connect-and-validate path for all five clients.

## Decisions Made

- Kept the README explicitly limited to Phase 1 integration and introspection so the docs do not imply a real memory loop before later phases.
- Reused the same server name and runtime string everywhere to keep client setup reproducible and easy to audit.
- Treated Antigravity as documented compatibility only, because the current setup path is manual UI import rather than equally scriptable CLI support.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plan `01-03` can validate the same documented runtime contract over a real stdio MCP session.
- Phase 2 can build on the documented `project`, `global`, and `hybrid` scope vocabulary already surfaced in the README and checklist.

## Self-Check: PASSED

- Verified `README.md` contains the required sections and exact runtime strings.
- Verified all JSON examples parse with `python -m json.tool`.
- Verified the Codex TOML example parses with `tomllib`.
- Verified the smoke checklist covers all five target clients and references `self_test`.

