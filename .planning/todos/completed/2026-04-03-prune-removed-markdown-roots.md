completed: 2026-04-03
---
created: 2026-04-03T14:34:39.023Z
title: Prune removed markdown roots
area: general
files:
  - src/turbo_memory_mcp/ingestion.py:34
  - src/turbo_memory_mcp/store.py:320
  - src/turbo_memory_mcp/retrieval_index.py:56
---

## Problem

Audit confirmed that switching indexed roots leaves stale data behind. In a temp reproduction, indexing `docs_a` and then indexing only `docs_b` produced two Markdown blocks in storage, meaning content from the removed root was still present. That keeps dead context searchable and can pollute retrieval results.

## Solution

Add root-pruning logic when the caller provides an explicit root set, especially for `mode="full"`. If a previously registered root is omitted, remove its file manifests, blocks, and retrieval rows. Add tests that prove removed roots disappear completely from storage stats and search results.
