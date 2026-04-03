---
created: 2026-04-03T14:34:39.023Z
title: Decouple writes from embedder
area: general
files:
  - src/turbo_memory_mcp/retrieval_index.py:31
  - src/turbo_memory_mcp/retrieval_index.py:91
  - src/turbo_memory_mcp/server.py:159
---

## Problem

Audit confirmed that the write path depends on loading the `SentenceTransformer` embedder. Cold start logs show model loading on first use, and in constrained or offline environments this can make note writes slow or fragile. Memory capture should not depend on heavyweight retrieval infrastructure being healthy.

## Solution

Split canonical persistence from embedding sync. A successful `remember_note(...)` must store the note first and remain reliable even if embedding sync is delayed, retried, or temporarily unavailable. Add a degraded mode or pending-sync marker so retrieval can recover later without losing the note.
