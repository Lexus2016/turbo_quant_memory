---
phase: 01-client-integration-foundation
plan: "03"
subsystem: testing
tags: [python, mcp, stdio, smoke, pytest]
requires:
  - "01-01 packaged stdio MCP server contract"
  - "01-02 published runtime and client onboarding docs"
provides:
  - "Real stdio smoke validation against uv run turbo-memory-mcp serve"
  - "Regression tests for published server metadata used by docs and clients"
affects: ["phase-2-regressions", "operator validation", "client onboarding"]
tech-stack:
  added: []
  patterns:
    - "Smoke validation exercises the packaged server as a child process rather than mocking MCP transport"
    - "Metadata tests lock the public contract that external clients rely on"
key-files:
  created:
    - scripts/smoke_test.py
    - tests/test_smoke_contract.py
  modified: []
key-decisions:
  - "Launched the smoke path through `uv run turbo-memory-mcp serve` so the scripted validation matches the documented operator path."
  - "Kept regression coverage focused on server metadata that docs and client configs consume directly."
patterns-established:
  - "Operational smoke tooling should validate the external MCP contract, not just internal helper functions."
  - "Contract metadata that appears in docs should also be asserted in a dedicated regression layer."
requirements-completed: [INT-01, INT-02, INT-03, INT-04]
duration: 1min
completed: 2026-03-25
---

# Phase 01 Plan 03: Client Integration Foundation Summary

**Real stdio smoke validation through a uv-launched MCP child process plus regression tests for the published server metadata**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-25T20:32:18Z
- **Completed:** 2026-03-25T20:32:25Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Added a real end-to-end smoke script that launches `uv run turbo-memory-mcp serve`, initializes an MCP client session, and validates all four Phase 1 tools.
- Added dedicated regression coverage for the runtime metadata that the README and client fixtures depend on.
- Verified the full repo contract through `uv run python scripts/smoke_test.py` and `uv run pytest -q`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Build the end-to-end stdio smoke script** - `e675c30` (`feat`)
2. **Task 2: Add contract regression tests for the smoke metadata** - `b6dadec` (`test`)

## Files Created/Modified

- `scripts/smoke_test.py` - Offline MCP smoke script that launches the packaged server through `uv` and validates tool calls.
- `tests/test_smoke_contract.py` - Regression tests for `runtime_command`, `server_id`, client tiers, and exported tool names.

## Decisions Made

- Used the official MCP stdio client for the smoke path so validation happens over the same transport the real hosts will use.
- Kept the smoke output compact and fail-fast so operators get a clear pass/fail result without reading large logs.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 1 now has both human-facing smoke instructions and a scriptable repo-local smoke path.
- Phase 2 can extend the same testing pattern when real `project`, `global`, and `hybrid` memory behaviour lands.

## Self-Check: PASSED

- Verified `uv run python scripts/smoke_test.py` exits 0 and exercises all four tools.
- Verified `uv run pytest -q tests/test_smoke_contract.py` exits 0.
- Verified `uv run pytest -q` stays green with the new smoke layer included.

