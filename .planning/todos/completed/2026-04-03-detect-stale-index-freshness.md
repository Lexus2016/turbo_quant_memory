completed: 2026-04-03
---
created: 2026-04-03T14:34:39.023Z
title: Detect stale index freshness
area: general
files:
  - src/turbo_memory_mcp/server.py:364
  - src/turbo_memory_mcp/retrieval.py:116
  - src/turbo_memory_mcp/ingestion.py:34
---

## Problem

Audit confirmed that freshness can remain `fresh` even after a Markdown source file changes on disk. In a temp reproduction, a file was indexed, then edited without re-running `index_paths(...)`; `server_info()` still reported `fresh`, and `semantic_search(...)` returned the old summary. This breaks the main product promise: agents can miss the latest context and keep acting on stale knowledge.

## Solution

Add real staleness detection against filesystem metadata or checksums instead of relying only on row counts. Mark project freshness as `stale` when any indexed file no longer matches its manifest. After that, either auto-run incremental reindex before `semantic_search(...)` and `hydrate(...)`, or return an explicit stale warning that blocks callers from trusting old context.
