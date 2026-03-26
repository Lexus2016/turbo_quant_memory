---
phase: 03-markdown-ingestion
plan: "01"
subsystem: storage
tags: [python, filesystem, markdown, ingestion, pytest]
requires:
  - "02-01 central namespace store"
provides:
  - "Project-scoped markdown/{roots,files,blocks} storage layout"
  - "Atomic root, file manifest, and block persistence helpers"
  - "Source-scoped cleanup for incremental rewrites and deletes"
affects: ["03-03", "incremental indexing", "phase-4 retrieval"]
tech-stack:
  added: []
  patterns:
    - "Markdown ingestion data persists as atomic JSON files under the existing project namespace"
    - "One source file can be rewritten or deleted without touching unrelated block records"
key-files:
  created:
    - tests/test_ingestion_store.py
  modified:
    - src/turbo_memory_mcp/store.py
key-decisions:
  - "Kept Markdown storage inside the existing project store instead of introducing a database before retrieval exists."
  - "Stored roots, file manifests, and blocks as separate records so incremental refresh can target one file at a time."
patterns-established:
  - "Markdown ingestion state should live under projects/<project_id>/markdown/{roots,files,blocks}."
  - "Store helpers should expose delete/replace operations keyed by root_id + source_path, not only by opaque ids."
requirements-completed: []
duration: 10s
completed: 2026-03-26
---

# Phase 03 Plan 01: Markdown Ingestion Summary

**Built the persistent Markdown storage foundation required for later indexing and retrieval**

## Performance

- **Duration:** 10 sec
- **Started:** 2026-03-26T08:06:50+01:00
- **Completed:** 2026-03-26T08:07:00+01:00
- **Tasks:** 3
- **Files modified:** 2

## Accomplishments

- Extended `MemoryStore` with project-scoped Markdown layout helpers for `manifest.json`, `roots/`, `files/`, and `blocks/`.
- Added root-record, file-manifest, and block-record persistence with exact Phase 3 checksum and provenance fields.
- Added targeted cleanup helpers so one source file can be rewritten or removed without forcing a full-corpus rebuild.

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend the central store with Markdown root, file, and block layouts** - `c4c0cbb` (`feat`)
2. **Task 2: Add storage tests for root registration and manifest persistence** - `31a3b69` (`test`)
3. **Task 3: Add targeted cleanup coverage for changed and deleted files** - `31a3b69` (`test`)

## Files Created/Modified

- `src/turbo_memory_mcp/store.py` - Markdown path helpers, record writers/readers, and source-scoped cleanup operations.
- `tests/test_ingestion_store.py` - Layout, root persistence, manifest/block payload, and targeted cleanup coverage.

## Decisions Made

- Kept Markdown storage in the existing filesystem store so deployment stays zero-DB and local-first.
- Used explicit `source_checksum` and `block_ids` fields on file manifests because later incremental orchestration depends on both.

## Deviations from Plan

None.

## Issues Encountered

None.

## User Setup Required

None.

## Next Phase Readiness

- `03-03` can now register roots and rewrite only affected file manifests and block records.
- Phase 4 retrieval can build on persisted `heading_path`, `source_path`, and `block_checksum` metadata without changing the storage model.

## Self-Check: PASSED

- Verified `uv run pytest -q tests/test_ingestion_store.py` exits 0.
- Verified Markdown storage lives under `projects/<project_id>/markdown/{roots,files,blocks}`.
- Verified one file can be cleaned up without deleting unrelated block records.
