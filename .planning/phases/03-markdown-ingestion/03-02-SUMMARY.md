---
phase: 03-markdown-ingestion
plan: "02"
subsystem: parser
tags: [python, markdown-it-py, chunking, commonmark, pytest]
requires:
  - "03-01 markdown storage foundation"
provides:
  - "Pure markdown-it-py parser module for heading-aware chunking"
  - "Synthetic __preamble__ support for content before the first heading"
  - "Location-based block id helper independent from content hashes"
affects: ["03-03", "phase-4 retrieval", "smoke validation"]
tech-stack:
  added:
    - "markdown-it-py>=4.0.0,<5.0"
  patterns:
    - "Markdown parsing stays pure and filesystem-independent"
    - "Block identity is derived from root_id + source path + heading path + chunk index"
key-files:
  created:
    - src/turbo_memory_mcp/markdown_parser.py
    - tests/test_markdown_chunking.py
  modified:
    - pyproject.toml
    - uv.lock
key-decisions:
  - "Used markdown-it-py rather than regex parsing to keep heading extraction deterministic and CommonMark-aware."
  - "Accepted oversized single chunks when needed instead of inventing fragile code-fence splitting heuristics."
patterns-established:
  - "Chunking should prefer semantic section boundaries first and only then fall back to size-based paragraph splits."
  - "Stable ids must come from logical location, while `block_checksum` tracks content changes separately."
requirements-completed: [ING-02]
duration: 24s
completed: 2026-03-26
---

# Phase 03 Plan 02: Markdown Ingestion Summary

**Added deterministic Markdown parsing, heading-aware chunking, and stable location-based block identity**

## Performance

- **Duration:** 24 sec
- **Started:** 2026-03-26T08:07:00+01:00
- **Completed:** 2026-03-26T08:07:24+01:00
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments

- Added `markdown-it-py` as the only new Phase 3 parsing dependency.
- Created `src/turbo_memory_mcp/markdown_parser.py` with `MarkdownIt("commonmark")`, `SyntaxTreeNode`, `__preamble__`, and deterministic fallback chunking.
- Locked the parser contract with focused tests for preamble handling, nested heading paths, fallback splitting, and stable `block_id` values.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add the Markdown parser dependency and stable parser module scaffold** - `704953e` (`feat`)
2. **Task 2: Implement heading-aware chunking with size-based fallback** - `704953e` (`feat`)
3. **Task 3: Lock chunking behaviour with focused unit tests** - `9f54d0e` (`test`)

## Files Created/Modified

- `pyproject.toml` - Added `markdown-it-py` as the Phase 3 parser dependency.
- `uv.lock` - Captured the resolved parser dependency set for repeatable installs.
- `src/turbo_memory_mcp/markdown_parser.py` - Pure CommonMark parsing, section extraction, fallback chunking, and `build_block_id(...)`.
- `tests/test_markdown_chunking.py` - Coverage for `__preamble__`, nested headings, fenced code blocks, fallback splitting, and stable ids.

## Decisions Made

- Kept the parser module isolated from MCP and filesystem concerns so ingestion orchestration can stay small.
- Used location-based ids instead of content-based ids so normal edits do not churn block identity.

## Deviations from Plan

None.

## Issues Encountered

None.

## User Setup Required

None.

## Next Phase Readiness

- `03-03` can now parse any registered Markdown file into stable block candidates before persistence.
- Phase 4 retrieval can reuse the parser’s `heading_path`, `chunk_index`, and `block_checksum` contract directly.

## Self-Check: PASSED

- Verified `uv run pytest -q tests/test_markdown_chunking.py` exits 0.
- Verified `markdown-it-py` is the only new Phase 3 parsing dependency.
- Verified block ids are location-based rather than content-hash-based.
