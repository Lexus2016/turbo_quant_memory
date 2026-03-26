---
phase: 05-hydration-and-write-back
plan: "02"
subsystem: live-contract-and-typed-notes
tags: [mcp, retrieval, note-kind, contract]
provides:
  - "One public hydrate tool in the live MCP surface"
  - "Typed note write-back with fixed kinds"
  - "Search-time note_kind visibility across retrieval rows and payloads"
affects: [RET-03, RET-04, MEM-01, MEM-02]
key-files:
  modified:
    - src/turbo_memory_mcp/contracts.py
    - src/turbo_memory_mcp/retrieval_index.py
    - src/turbo_memory_mcp/retrieval.py
    - src/turbo_memory_mcp/server.py
    - tests/test_namespace_tools.py
    - tests/test_retrieval_index.py
    - tests/test_semantic_search.py
completed: 2026-03-26
---

# Phase 05 Plan 02 Summary

Wired hydration and typed write-back into the live server contract.

- Exported `hydrate(...)` as the canonical explicit escalation tool.
- Made `remember_note(...)` require one of `decision`, `lesson`, `handoff`, or `pattern`.
- Extended retrieval rows, note envelopes, and semantic-search payloads with `note_kind`.

Verification:

- `uv run pytest -q tests/test_namespace_tools.py tests/test_retrieval_index.py tests/test_semantic_search.py`

