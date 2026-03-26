---
phase: 03-markdown-ingestion
plan: "03"
subsystem: api
tags: [python, mcp, markdown, incremental-indexing, smoke, pytest]
requires:
  - "03-01 markdown storage foundation"
  - "03-02 markdown parser and chunking contract"
provides:
  - "Live index_paths MCP tool for root registration and Markdown indexing"
  - "Incremental refresh that skips untouched files and cleans deleted-file artifacts"
  - "Contract tests and stdio smoke validation for one full + incremental indexing cycle"
affects: ["phase-4 retrieval", "operator smoke validation", "future hydration"]
tech-stack:
  added: []
  patterns:
    - "Ingestion is project-scoped and reuses the existing stdio server rather than introducing a separate worker"
    - "Incremental refresh checks stat first, then confirms real content changes with source checksums"
key-files:
  created:
    - src/turbo_memory_mcp/ingestion.py
    - tests/test_ingestion_tools.py
  modified:
    - src/turbo_memory_mcp/contracts.py
    - src/turbo_memory_mcp/server.py
    - tests/test_tools.py
    - tests/test_smoke_contract.py
    - scripts/smoke_test.py
key-decisions:
  - "Exposed one `index_paths` tool that both registers roots and runs indexing instead of splitting registration and refresh into separate APIs."
  - "Default incremental runs reuse previously registered roots when `paths` is omitted."
  - "Kept retrieval out of Phase 3; indexing only persists blocks and metadata."
patterns-established:
  - "New live MCP tools must land together with direct contract tests and a real stdio smoke path."
  - "Indexing result payloads should stay compact and count-oriented so agents can react without reading verbose logs."
requirements-completed: [ING-01, ING-02, ING-03]
duration: 18s
completed: 2026-03-26
---

# Phase 03 Plan 03: Markdown Ingestion Summary

**Wired project-scoped Markdown indexing into the live MCP server with real incremental refresh behaviour**

## Performance

- **Duration:** 18 sec
- **Started:** 2026-03-26T08:07:24+01:00
- **Completed:** 2026-03-26T08:07:42+01:00
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- Added `src/turbo_memory_mcp/ingestion.py` to register roots, walk `*.md`, parse blocks, reuse registered roots, and clean deleted-file artifacts.
- Extended the public contract and live stdio server with `index_paths(paths=None, mode="incremental")`.
- Added ingestion contract tests plus a real smoke path that proves one full run, one idle incremental run, and one changed-file/deleted-file incremental run.

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement project-scoped Markdown ingestion orchestration** - `91a8f92` (`feat`)
2. **Task 2: Add ingestion contract tests for registration and incremental refresh** - `8ee475b` (`test`)
3. **Task 3: Extend the stdio smoke path to validate one incremental Markdown cycle** - `8ee475b` (`test`)

## Files Created/Modified

- `src/turbo_memory_mcp/ingestion.py` - Root registration, deterministic ids, file walk, checksum-aware refresh, and deleted-file cleanup.
- `src/turbo_memory_mcp/contracts.py` - Phase 3 tool catalog and indexing result payload builder.
- `src/turbo_memory_mcp/server.py` - Live `index_paths` MCP tool and runtime integration.
- `tests/test_tools.py` - Live tool-catalog and self-test coverage for the new Phase 3 surface.
- `tests/test_ingestion_tools.py` - Hermetic registration, idle incremental, change, and delete coverage.
- `tests/test_smoke_contract.py` - Builder-level regression coverage for the indexing payload contract.
- `scripts/smoke_test.py` - Real stdio Markdown indexing cycle over an isolated temporary docs tree.

## Decisions Made

- Counted `indexed_files` as the files currently present in registered roots after each run, while `changed_files`, `skipped_files`, and `deleted_files` describe what happened in that run.
- Treated unchanged-but-restatted files as `skipped` after checksum confirmation so the server avoids unnecessary block rewrites.

## Deviations from Plan

None.

## Issues Encountered

None.

## User Setup Required

None.

## Next Phase Readiness

- Phase 4 can build retrieval directly on persisted Markdown block records without changing ingestion semantics.
- The operator smoke path now proves indexing behaviour, so retrieval and hydration work can be layered on a validated foundation.

## Self-Check: PASSED

- Verified `uv run pytest -q tests/test_tools.py tests/test_ingestion_tools.py tests/test_smoke_contract.py` exits 0.
- Verified `uv run python scripts/smoke_test.py` exits 0.
- Verified `index_paths` supports root registration plus incremental reruns without rebuilding untouched files.
