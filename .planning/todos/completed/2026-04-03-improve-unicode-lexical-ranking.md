completed: 2026-04-03
---
created: 2026-04-03T14:34:39.023Z
title: Improve Unicode lexical ranking
area: general
files:
  - src/turbo_memory_mcp/retrieval.py:17
  - src/turbo_memory_mcp/retrieval.py:215
  - tests/test_semantic_search.py
---

## Problem

Code inspection confirmed that lexical scoring tokenizes only ASCII-style terms via `[A-Za-z0-9_]+`. That degrades ranking quality for Ukrainian and Russian queries, which matters because the product and docs are explicitly multilingual and users will search in those languages.

## Solution

Replace ASCII-only tokenization with Unicode-aware tokenization and use `casefold()` for comparisons. Add retrieval tests for Ukrainian and Russian queries so multilingual ranking remains stable and does not regress.
