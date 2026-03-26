---
phase: 04-compressed-retrieval
plan: "02"
subsystem: retrieval-contract
tags: [python, mcp, retrieval, ranking, pytest]
requires:
  - "04-01 retrieval index foundation"
provides:
  - "Canonical semantic_search MCP contract"
  - "Balanced-card retrieval payloads across markdown blocks and notes"
  - "Project-biased hybrid ranking with markdown-first tie-breaks and warning states"
affects: ["04-03 smoke validation", "phase-5 hydration", "client retrieval UX"]
tech-stack:
  added: []
  patterns:
    - "Retrieval results are balanced cards with provenance-first metadata and no raw excerpts by default"
    - "Hybrid search ranks by effective score, then project scope, then markdown-first ordering, then recency and stable identity"
key-files:
  created:
    - src/turbo_memory_mcp/retrieval.py
    - tests/test_semantic_search.py
  modified:
    - src/turbo_memory_mcp/contracts.py
    - src/turbo_memory_mcp/retrieval_index.py
    - src/turbo_memory_mcp/server.py
    - tests/test_namespace_tools.py
key-decisions:
  - "Removed search_memory from the public MCP surface immediately and made semantic_search the only retrieval entry point for Phase 4."
  - "Kept retrieval results compact by returning compressed_summary and up to three key_points instead of raw excerpts, while preserving promoted_from provenance for global notes."
patterns-established:
  - "Sync retrieval mirrors immediately after remember_note, promote_note, and index_paths so semantic search stays live without manual reindex steps."
  - "Low-confidence and ambiguous queries return cautious results with explicit warnings instead of silently failing."
requirements-completed: []
duration: 16m
completed: 2026-03-26
---

# Phase 04 Plan 02: Compressed Retrieval Summary

**Canonical `semantic_search(...)` now returns compact, provenance-rich balanced cards over Markdown blocks and notes**

## Performance

- **Duration:** 16 min
- **Started:** 2026-03-26T10:06:00+01:00
- **Completed:** 2026-03-26T10:22:35+01:00
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments

- Added `src/turbo_memory_mcp/retrieval.py` with semantic retrieval orchestration, scope-aware querying, hybrid ranking, warning states, and balanced-card shaping.
- Replaced the public retrieval API in `src/turbo_memory_mcp/server.py` so `semantic_search(...)` is the only exposed retrieval tool and live writes/indexing keep the retrieval mirror in sync.
- Extended contract and regression coverage so tests lock markdown-first ordering, project bias, promoted provenance, and no-raw-excerpt payload guarantees.

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement semantic retrieval orchestration and balanced-card compression** - `7f1e589` (`feat`)
2. **Task 2: Replace the public retrieval tool contract and wire retrieval sync boundaries** - `7f1e589` (`feat`)
3. **Task 3: Lock ranking, warning, and provenance behavior with focused tests** - `e51f982` (`test`)

## Files Created/Modified

- `src/turbo_memory_mcp/retrieval.py` - Added ranking, confidence evaluation, balanced-card shaping, and project/global/hybrid retrieval orchestration.
- `src/turbo_memory_mcp/contracts.py` - Added Phase 4 tool catalog and semantic item payload shaping with warning support.
- `src/turbo_memory_mcp/retrieval_index.py` - Added query support and cached embedder loading for runtime semantic search.
- `src/turbo_memory_mcp/server.py` - Replaced `search_memory` with `semantic_search` and synced retrieval mirrors after note writes, promotions, and Markdown indexing.
- `tests/test_semantic_search.py` - Added hermetic coverage for ranking, balanced cards, project bias, provenance, and ambiguity warnings.
- `tests/test_namespace_tools.py` - Switched namespace contract tests to `semantic_search_impl` and protected promoted provenance in global results.

## Decisions Made

- Dropped the public `search_memory(...)` alias immediately so Phase 4 has one unambiguous retrieval contract for all MCP clients.
- Preferred compact balanced cards over raw excerpts to minimise token volume while still preserving enough provenance and summary signal for correct hydration decisions.

## Deviations from Plan

None.

## Issues Encountered

- A server-layer regression appeared after removing legacy note-ranking helpers: `remember_note_impl` still called `_normalize_scope`. Reintroduced a tiny scope normaliser and re-ran the targeted suite before committing.

## User Setup Required

None.

## Next Phase Readiness

- `04-03` can now update smoke paths, docs, and public examples against the real `semantic_search(...)` contract instead of the removed `search_memory(...)` tool.
- Semantic retrieval already stays in sync with writes and indexing, so the last execution wave can focus on operator/documentation proof rather than core behavior.

## Self-Check: PASSED

- Verified `uv run pytest -q tests/test_semantic_search.py tests/test_namespace_tools.py` exits 0.
- Verified `uv run pytest -q tests/test_retrieval_index.py tests/test_semantic_search.py tests/test_namespace_tools.py` exits 0.
- Verified the live MCP server exports `semantic_search(...)` and no longer publicly exposes `search_memory(...)`.
