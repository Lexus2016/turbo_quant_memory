---
created: 2026-04-03T14:34:39.023Z
title: Make retrieval sync incremental
area: general
files:
  - src/turbo_memory_mcp/server.py:159
  - src/turbo_memory_mcp/server.py:195
  - src/turbo_memory_mcp/retrieval_index.py:56
---

## Problem

Code inspection confirmed that note writes, promotions, and deprecations trigger full retrieval-table rebuilds and full re-embedding for the whole scope. That makes write latency grow with corpus size and turns simple note operations into expensive global sync operations.

## Solution

Replace full-table overwrite sync with incremental upsert/delete by `item_id`. Project-note changes should update only affected note rows, and Markdown indexing should update only changed block rows. Keep a full rebuild path for repair or migration, but not as the default write path.
