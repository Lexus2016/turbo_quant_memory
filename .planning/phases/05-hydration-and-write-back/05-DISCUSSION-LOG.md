# Phase 5: Hydration and Write-Back - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in `05-CONTEXT.md` — this log preserves the alternatives considered.

**Date:** 2026-03-26
**Phase:** 5-Hydration and Write-Back
**Areas discussed:** Hydration entrypoint, First hydrate boundary, Related-context recovery, Write-back capture model

---

## Hydration Entrypoint

| Option | Description | Selected |
|--------|-------------|----------|
| One universal hydrate tool | One canonical hydration path for both Markdown hits and notes | ✓ |
| Separate tools by source kind | Distinct block and note hydration tools | |
| Split flow with multiple tools | Separate read/related/note-expansion surfaces | |

**User's choice:** One universal hydrate tool
**Follow-up clarification:** The canonical shape should be `hydrate(item_id, scope, mode=...)`.
**Notes:** The goal is to keep the MCP surface simple across all supported clients while still allowing escalation from compact cards.

---

## First Hydrate Boundary

| Option | Description | Selected |
|--------|-------------|----------|
| Winning item only | Minimal expansion, but often too small for real work | |
| Winning item + local neighborhood | Balanced default escalation with bounded extra context | ✓ |
| Whole section by heading | Better continuity, but payload can grow unpredictably | |
| Whole file excerpt window | Simple mental model, but poor token discipline | |

**User's choice:** Winning item + local neighborhood
**Follow-up clarification:** The local neighborhood should use symmetric blocks around the hit.
**Notes:** This keeps the first hydration deterministic and bounded while still improving local comprehension.

---

## Related-Context Recovery

| Option | Description | Selected |
|--------|-------------|----------|
| Source-local related only | Separate related tool limited to the same source neighborhood | |
| Source-local + same-root semantic | Separate related tool with limited semantic expansion | |
| Cross-project semantic related | Broadest related graph, highest complexity and noise risk | |
| No separate related tool | Keep related recovery inside hydration modes instead of a new top-level API | ✓ |

**Selection method:** Builder decision based on the user's directive to optimize for context quality at minimum content
**Notes:** A separate top-level `related_blocks(...)` API was rejected for v1. Related recovery should ride through `hydrate(..., mode=...)` so the escalation path stays unified.

---

## Write-Back Capture Model

| Option | Description | Selected |
|--------|-------------|----------|
| Explicit curated notes only | Cleanest memory, but weaker for structured handoff/reuse | |
| Curated notes + lightweight note kinds | Explicit writes plus meaningful categories for later recall | ✓ |
| Auto session summaries by default | Higher coverage, but much noisier memory | |
| Hybrid with optional auto-handoff | Flexible, but larger Phase 5 surface | |

**User's choice:** Curated notes + lightweight note kinds
**Follow-up clarification:** Use a small fixed enum in v1 rather than open-ended or inferred types.
**Notes:** Initial note-kind direction: `decision`, `lesson`, `handoff`, `pattern`.

---

## the agent's Discretion

- Exact `hydrate(...)` mode names
- Exact default neighborhood counts
- Exact hydrated payload contracts
- Exact note-kind field naming and validation

## Deferred Ideas

- Separate top-level `related_blocks(...)` tool
- Whole-section or whole-file default hydration
- Automatic session summaries by default
- Automatic note-kind inference from note content
