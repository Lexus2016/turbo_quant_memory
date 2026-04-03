---
created: 2026-04-03T14:34:39.023Z
title: Cache storage stats and server info
area: general
files:
  - src/turbo_memory_mcp/server.py:331
  - src/turbo_memory_mcp/server.py:364
  - src/turbo_memory_mcp/store.py:96
---

## Problem

Code inspection confirmed that `server_info()` and freshness reporting repeatedly scan notes, Markdown manifests, and blocks from disk. That is acceptable for tiny corpora but will become an avoidable cost as repositories and note counts grow, especially if clients call `server_info()` frequently.

## Solution

Introduce cached counters or manifest-level aggregate stats updated on write, deprecate, promote, index, and prune operations. Keep a slow recompute path for repair, but make the common `server_info()` path use cheap precomputed metadata.
