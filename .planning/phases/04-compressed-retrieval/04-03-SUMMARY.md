---
phase: 04-compressed-retrieval
plan: "03"
subsystem: docs-and-smoke
tags: [documentation, smoke-test, mcp, pytest, semantic-search]
requires:
  - "04-01 retrieval index foundation"
  - "04-02 semantic retrieval contract"
provides:
  - "Published Phase 4 docs aligned to semantic_search"
  - "Contract tests for the live Phase 4 MCP surface"
  - "Real stdio smoke validation for semantic retrieval over Markdown and notes"
affects: ["phase-4 completion", "phase-5 hydration planning", "operator onboarding"]
tech-stack:
  added: []
  patterns:
    - "Operator docs describe only the live MCP surface and defer hydration to the next phase"
    - "Smoke validation proves both Markdown retrieval and project/global note ordering through the real stdio server"
key-files:
  created: []
  modified:
    - README.md
    - TECHNICAL_SPEC.md
    - MEMORY_STRATEGY.md
    - examples/clients/SMOKE_CHECKLIST.md
    - tests/test_tools.py
    - tests/test_smoke_contract.py
    - scripts/smoke_test.py
key-decisions:
  - "Published semantic_search as the only retrieval term in docs, smoke guidance, and tool-catalog tests."
  - "Kept smoke assertions compact and pass/fail oriented while still proving balanced-card fields and no raw-excerpt leaks."
patterns-established:
  - "Every public contract update must land in docs, contract tests, and the real stdio smoke path together."
  - "Phase boundaries are explicit: balanced-card retrieval now, hydration in Phase 5."
requirements-completed: [RET-01, RET-02, SAFE-01, SAFE-02]
duration: 10m
completed: 2026-03-26
---

# Phase 04 Plan 03: Compressed Retrieval Summary

**Published Phase 4 semantic retrieval guidance, locked the public contract, and proved it through the real stdio smoke path**

## Performance

- **Duration:** 10 min
- **Started:** 2026-03-26T10:23:00+01:00
- **Completed:** 2026-03-26T10:32:48+01:00
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- Updated `README.md`, `TECHNICAL_SPEC.md`, `MEMORY_STRATEGY.md`, and the client smoke checklist so all operator-facing material now describes `semantic_search(...)`, balanced cards, warning states, and Phase 5 hydration boundaries.
- Refreshed the published contract tests so the live tool catalog expects `semantic_search` and semantic retrieval payloads assert `block_id`, `compressed_summary`, `key_points`, `confidence_state`, and no raw excerpt fields.
- Extended `scripts/smoke_test.py` to exercise the real stdio server end-to-end: index Markdown, write and promote notes, prove Markdown retrieval, and prove project-before-global ordering for note results.

## Task Commits

Each task was committed atomically:

1. **Task 1: Update docs and operator guidance for semantic retrieval** - `012993d` (`docs`)
2. **Task 2: Update the published tool and smoke-contract tests for Phase 4** - `2fc447d` (`test`)
3. **Task 3: Extend the real stdio smoke path for semantic retrieval** - `2fc447d` (`test`)

## Files Created/Modified

- `README.md` - Updated runtime contract, result envelope, and smoke path to the Phase 4 `semantic_search(...)` contract.
- `TECHNICAL_SPEC.md` - Aligned retrieval scope, balanced-card payload, and Phase 5 hydration boundary with the implemented server behavior.
- `MEMORY_STRATEGY.md` - Documented markdown-first ranking, warning states, and compact retrieval envelopes.
- `examples/clients/SMOKE_CHECKLIST.md` - Refreshed shared client validation flow to use `semantic_search` and the 8-tool catalog.
- `tests/test_tools.py` - Locked the live tool catalog to `PHASE_4_TOOL_NAMES`.
- `tests/test_smoke_contract.py` - Added semantic balanced-card contract assertions.
- `scripts/smoke_test.py` - Proved end-to-end semantic retrieval over indexed Markdown and persistent notes through the real stdio MCP server.

## Decisions Made

- Chose to publish only the live `semantic_search(...)` contract everywhere instead of documenting legacy names or temporary aliases.
- Kept the smoke path compact and CLI-friendly while still asserting the core Phase 4 guarantees: provenance, balanced-card fields, markdown hits, project/global ordering, and no raw excerpt leaks.

## Deviations from Plan

None.

## Issues Encountered

- The first real smoke run had to download `sentence-transformers/all-MiniLM-L6-v2` and emitted an unauthenticated Hugging Face warning, but the stdio smoke flow still completed successfully without code changes.

## User Setup Required

None beyond the existing `uv sync` or `pip install -e .` setup.

## Next Phase Readiness

- Phase 4 is now complete end-to-end: the retrieval layer is implemented, documented, contract-tested, and smoke-validated.
- Phase 5 can focus purely on explicit hydration and richer write-back flows instead of fixing Phase 4 surface inconsistencies.

## Self-Check: PASSED

- Verified `uv run pytest -q tests/test_tools.py tests/test_smoke_contract.py` exits 0.
- Verified `uv run python scripts/smoke_test.py` exits 0.
- Verified `uv run pytest -q` exits 0.
