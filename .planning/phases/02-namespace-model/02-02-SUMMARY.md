---
phase: 02-namespace-model
plan: "02"
subsystem: api
tags: [python, mcp, namespace, provenance, pytest]
requires:
  - "02-01 deterministic identity and central namespace store"
provides:
  - "Namespace-aware MCP tools for remember, promote, and search flows"
  - "Compact provenance envelopes with current project and storage metadata"
  - "Deterministic hybrid ordering with project bias"
affects: ["02-03", "smoke validation", "client docs", "later ingestion phases"]
tech-stack:
  added: []
  patterns:
    - "Public MCP payloads are built through shared contract helpers rather than ad hoc tool responses"
    - "Namespace search remains note-centric in Phase 2, while retrieval quality stays deferred"
key-files:
  created:
    - tests/test_namespace_tools.py
  modified:
    - src/turbo_memory_mcp/contracts.py
    - src/turbo_memory_mcp/server.py
    - tests/test_tools.py
key-decisions:
  - "Kept `project`, `global`, and `hybrid` on the existing stdio server instead of branching into a second transport or package path."
  - "Rejected direct public writes into `global`; explicit promotion remains the only cross-project write path."
  - "Exposed compact item envelopes with provenance fields and lightweight previews instead of verbose lineage payloads."
patterns-established:
  - "Hybrid results are ranked deterministically with one isolated project-bias constant for future tuning."
  - "Server metadata now always reports both storage root and current project identity."
requirements-completed: [SCP-01, SCP-02, SCP-03, SCP-04]
duration: 1min
completed: 2026-03-25
---

# Phase 02 Plan 02: Namespace Model Summary

**Namespace-aware MCP tools for project writes, explicit promotion into global memory, and deterministic hybrid recall**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-25T21:22:54Z
- **Completed:** 2026-03-25T21:23:33Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments

- Extended the public contract so `server_info` and `self_test` now expose `storage_root`, `current_project`, supported query modes, and namespace defaults.
- Added `remember_note`, `promote_note`, and `search_memory` to the live stdio MCP server without changing the packaged runtime contract.
- Added automated coverage for direct-global-write rejection, promotion provenance, scope isolation, and hybrid project-first ordering.

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend the public contract for namespace-aware metadata** - `0403fa0` (`feat`)
2. **Task 2: Wire namespace-aware write, promote, and search tools into the MCP server** - `41195f4` (`feat`)
3. **Task 3: Add namespace contract and precedence tests** - `e5b83e5` (`test`)

## Files Created/Modified

- `src/turbo_memory_mcp/contracts.py` - Shared Phase 2 payload builders for server info, self-test, write results, and search envelopes.
- `src/turbo_memory_mcp/server.py` - Runtime implementations for `remember_note`, `promote_note`, `search_memory`, and hybrid ordering.
- `tests/test_tools.py` - Real stdio MCP assertions for the expanded tool catalog and namespace metadata.
- `tests/test_namespace_tools.py` - Hermetic coverage for project/global/hybrid behaviour and promotion lineage.

## Decisions Made

- Kept lexical note scoring intentionally simple in Phase 2 so the namespace contract lands before heavier retrieval machinery.
- Stored compact previews directly in tool responses to keep them useful for agents without bloating provenance metadata.
- Used environment overrides in tests to keep namespace behaviour hermetic and independent of the developer machine state.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Task 1's planned `tests/test_tools.py -k contract` verification depended on the runtime and test updates completed in Tasks 2 and 3. The intended contract check was closed by the final `uv run pytest -q tests/test_tools.py tests/test_namespace_tools.py` run after the full plan landed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `02-03` can now document real namespace behaviour instead of the Phase 1 placeholder contract.
- The smoke script can exercise a full write → promote → hybrid-query loop against the live stdio server.
- No blockers remain for docs and smoke alignment.

## Self-Check: PASSED

- Verified `uv run pytest -q tests/test_tools.py tests/test_namespace_tools.py` exits 0.
- Verified the live tool catalog now includes `remember_note`, `promote_note`, and `search_memory`.
- Verified `search_memory` supports `project`, `global`, and `hybrid`.
