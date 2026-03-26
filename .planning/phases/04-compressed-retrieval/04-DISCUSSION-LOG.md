# Phase 4: Compressed Retrieval - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in `04-CONTEXT.md` — this log preserves the alternatives considered.

**Date:** 2026-03-26
**Phase:** 4-Compressed Retrieval
**Areas discussed:** Retrieval API shape, Result card format, Ranking policy across source kinds, Low-confidence behavior

---

## Retrieval API Shape

| Option | Description | Selected |
|--------|-------------|----------|
| `search_memory(...)` remains canonical and becomes semantic + block-aware | Lowest migration cost, but keeps the older naming contract | |
| `semantic_search(...)` becomes canonical and `search_memory(...)` stays as alias/deprecated alias | Cleaner naming with compatibility bridge | |
| `semantic_search(...)` becomes canonical and `search_memory(...)` is removed immediately | Clean public API reset for retrieval | ✓ |

**User's choice:** `semantic_search(...)` becomes canonical and `search_memory(...)` is removed immediately
**Follow-up clarification:** `semantic_search(...)` must search across both Markdown blocks and existing memory notes, not just blocks.
**Notes:** This intentionally prefers a clean public retrieval contract over compatibility preservation.

---

## Result Card Format

| Option | Description | Selected |
|--------|-------------|----------|
| Lean card | Minimal pointer-like payload with summary only | |
| Balanced card | Short `compressed_summary` plus `2-3` high-signal `key_points` | ✓ |
| Rich card | Adds an excerpt preview from raw source | |

**Selection method:** Builder decision based on the user's directive: "minimum context without loss of context"
**Notes:** The chosen shape is a strict balanced card: compact summary, at most `2-3` key points, and no raw excerpt by default.

---

## Ranking Policy Across Source Kinds

| Option | Description | Selected |
|--------|-------------|----------|
| Markdown-first inside each scope | Prefer source blocks over notes when relevance is close | ✓ |
| Equal ranking by score only | Treat blocks and notes identically | |
| Notes-first inside each scope | Prefer compact notes before original source | |

**User's choice:** Markdown-first inside each scope
**Notes:** Notes remain useful compressed hints, but they should not displace direct source evidence when both are similarly relevant.

---

## Low-Confidence and Ambiguity Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Return cautious results with explicit warning | Best-effort results stay usable while uncertainty is visible | ✓ |
| Return empty with warning | Safer, but often too blunt for agents | |
| Hard-threshold + ask to refine query | High precision, but adds too much friction | |

**User's choice:** Return cautious results with explicit warning
**Notes:** Retrieval should not silently pretend confidence is high. The response must surface ambiguity clearly enough that later hydration or refinement can be triggered intentionally.

---

## the agent's Discretion

- Exact response field names for low-confidence signaling
- Exact score composition and ranking formula
- Exact summary/key-points formatting inside the default retrieval card

## Deferred Ideas

- Full hydration and neighborhood recovery in Phase 5
- Raw excerpt preview as a default retrieval payload
- Compatibility alias for `search_memory(...)`
- Separate dedicated note-search tool in Phase 4
