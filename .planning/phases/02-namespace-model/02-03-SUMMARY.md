---
phase: 02-namespace-model
plan: "03"
subsystem: docs
tags: [python, mcp, smoke, docs, namespace]
requires:
  - "02-02 namespace-aware MCP contract"
provides:
  - "Operator docs aligned with the implemented namespace model"
  - "Real stdio smoke validation for remember, promote, and hybrid query flow"
  - "Regression tests for current_project and compact provenance envelopes"
affects: ["phase-3-onboarding", "client validation", "operator adoption"]
tech-stack:
  added: []
  patterns:
    - "Docs and smoke paths must describe the same live namespace contract that the server exposes"
    - "Smoke validation uses a temporary isolated storage root to avoid polluting the operator environment"
key-files:
  created: []
  modified:
    - README.md
    - MEMORY_STRATEGY.md
    - examples/clients/SMOKE_CHECKLIST.md
    - scripts/smoke_test.py
    - tests/test_smoke_contract.py
key-decisions:
  - "Documented Phase 2 as the current runtime contract instead of keeping Phase 1 placeholder language around."
  - "Kept the smoke path compact and pass/fail-oriented while still exercising write, promote, and hybrid search."
patterns-established:
  - "Operator-facing examples should validate a real end-to-end note flow, not only introspection tools."
  - "Regression tests for docs-facing metadata should use direct builder assertions in addition to live smoke runs."
requirements-completed: [SCP-01, SCP-02, SCP-03, SCP-04]
duration: 1min
completed: 2026-03-25
---

# Phase 02 Plan 03: Namespace Model Summary

**Published Phase 2 namespace docs plus a real stdio smoke path for project write, global promotion, and hybrid recall**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-25T21:28:18Z
- **Completed:** 2026-03-25T21:28:33Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Updated the README, memory strategy, and client smoke checklist so they now describe the implemented namespace contract instead of the old Phase 1 placeholder model.
- Upgraded the stdio smoke script to validate the real Phase 2 flow: `remember_note`, `promote_note`, and `search_memory(scope="hybrid")`.
- Added regression tests for `current_project`, `storage_root`, compact item envelopes, and conditional `promoted_from`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Update operator docs for the implemented namespace contract** - `ffcdd9d` (`docs`)
2. **Task 2: Upgrade the stdio smoke path and contract regression coverage** - `c338ef0` (`test`)

## Files Created/Modified

- `README.md` - Current runtime contract, namespace model, storage layout, and smoke flow.
- `MEMORY_STRATEGY.md` - Implementation-aligned Phase 2 strategy reference with exact namespace rules.
- `examples/clients/SMOKE_CHECKLIST.md` - Shared namespace validation flow for Claude Code, Codex, Cursor, OpenCode, and Antigravity.
- `scripts/smoke_test.py` - Real stdio namespace smoke path using an isolated temporary storage root.
- `tests/test_smoke_contract.py` - Regression coverage for `current_project`, `storage_root`, and compact item envelopes.

## Decisions Made

- Treated README and smoke docs as part of the runtime contract, not as loose marketing copy.
- Used an isolated temp storage root inside the smoke script so the repo validation never mutates the operator’s real memory store.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Normalized the smoke expectation to the resolved temporary storage path**
- **Found during:** Task 2 (Upgrade the stdio smoke path and contract regression coverage)
- **Issue:** On macOS, `TemporaryDirectory` resolves to `/private/var/...`, while the initial expectation used the unresolved symlinked path.
- **Fix:** Compared the smoke result against the resolved temp storage root instead of the raw temporary path string.
- **Files modified:** `scripts/smoke_test.py`
- **Verification:** `uv run python scripts/smoke_test.py`
- **Committed in:** `c338ef0`

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** No scope expansion. The fix only made the smoke contract robust across the real local filesystem path that the server already reports.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 2 now has code, docs, smoke validation, and regression coverage aligned on the same namespace vocabulary.
- Phase 3 can start from a stable base where project/global ownership and provenance are already locked.

## Self-Check: PASSED

- Verified `uv run python scripts/smoke_test.py` exits 0.
- Verified `uv run pytest -q tests/test_smoke_contract.py` exits 0.
- Verified published docs reference the same namespace flow exercised by the smoke script.
