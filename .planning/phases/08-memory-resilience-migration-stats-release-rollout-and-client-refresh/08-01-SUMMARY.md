---
phase: 08-memory-resilience-migration-stats-release-rollout-and-client-refresh
plan: "01"
subsystem: memory-resilience-and-release-rollout
tags: [migration, retrieval, telemetry, release, clients]
provides:
  - "Version-aware markdown and retrieval manifests with auto-rebuild on format mismatch"
  - "Persistent usage telemetry outside memory scopes with savings headlines and milestone surfacing"
  - "Refreshed smoke/docs/client rollout contract for v0.3.0 and local agent refresh"
affects: [OPS-01, OPS-02]
key-files:
  modified:
    - src/turbo_memory_mcp/store.py
    - src/turbo_memory_mcp/retrieval_index.py
    - src/turbo_memory_mcp/retrieval.py
    - src/turbo_memory_mcp/server.py
    - src/turbo_memory_mcp/contracts.py
    - src/turbo_memory_mcp/telemetry.py
    - scripts/smoke_test.py
    - README.md
    - README.uk.md
    - README.ru.md
    - examples/clients/SMOKE_CHECKLIST.md
    - examples/clients/codex.config.toml
completed: 2026-04-03
---

# Phase 08 Plan 01 Summary

Completed the post-v1 hardening and rollout slice for `v0.3.0`.

- Project and global derived indexes now carry explicit format manifests, and the server auto-rebuilds stale retrieval/markdown mirrors after version mismatches instead of serving outdated context.
- Retrieval and hydration now record persistent usage telemetry outside project/global memory, including estimated byte/token savings, bounded milestone headlines, and optional USD estimates when `TQMEMORY_INPUT_COST_PER_1M_TOKENS_USD` is configured.
- Audit hardening was folded into the release: stale freshness detection, root pruning, incremental retrieval sync with safe fallback, safer `project` default query mode, Unicode-aware lexical ranking, and cached storage snapshots.
- Docs, smoke coverage, and local client rollout files now target `v0.3.0`, and the local installed MCP binary can be refreshed in place for Claude Code, Codex, Gemini, and OpenCode because they all point at the shared `turbo-memory-mcp` executable path.

Verification:

- `uv run ruff check src tests scripts`
- `uv run pytest -q`
- `uv run python scripts/smoke_test.py`
