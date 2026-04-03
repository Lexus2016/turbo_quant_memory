completed: 2026-04-03
---
created: 2026-04-03T14:34:39.023Z
title: Harden hybrid scope isolation
area: general
files:
  - src/turbo_memory_mcp/contracts.py:16
  - src/turbo_memory_mcp/server.py:95
  - src/turbo_memory_mcp/retrieval.py:22
---

## Problem

Audit confirmed that the default `hybrid` mode can return a promoted global note from a different `project_id`. A temp reproduction with `projA` and `projB` showed a `projB` global note ranking first for a query in `projA`. This is exactly the kind of cross-project memory mixing that can create false context and operational chaos.

## Solution

Tighten memory isolation rules. Change the default query mode to `project`, and require explicit opt-in for `hybrid`. For global retrieval, add policy gates such as `allowed_project_ids`, provenance filters, or an explicit "include global" flag so cross-project memory never enters the context by accident.
