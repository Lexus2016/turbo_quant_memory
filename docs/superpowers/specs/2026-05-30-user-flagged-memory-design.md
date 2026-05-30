# Design: User-Flagged Memory (provenance) — "Фіча А"

- **Date:** 2026-05-30
- **Status:** Proposed (awaiting user review before implementation)
- **Scope decision:** Variant **B** (provenance field + retrieval boost). Variant A rejected (cannot fulfil priority requirement), Variant C (proactive surfacing) deferred (junk-drawer risk without cleanup).
- **Related memory:** decision `793bba6750a94e6b` (LightMem analysis), lesson `e1b9b1df42094746` (P1 write-time hints), decision `b6503d848e77469c` (Фіча Б rejected).

## Problem

`tqmemory` cannot distinguish a note the **agent wrote on its own** from one the **human explicitly ordered remembered**. The user wants: "save this important info to my knowledge base" to persist knowledge that is more trusted and surfaces ahead of agent-inferred guesses. Today every note ranks by the same score regardless of origin.

## Goal

When the user explicitly flags information as important, persist it with a `human-explicit` provenance marker and rank it above agent-originated notes of equal relevance — without heavy machinery, at the current corpus scale (this project = 108 notes; system max = 296).

## Non-goals (explicit scope boundary)

- No proactive surfacing / context injection (Variant C) — deferred until B proves insufficient; carries junk-drawer risk without a cleanup mechanism.
- No slash-command `/remember` — natural-language detection covers it; a slash command is a Claude Code artifact, not tqmemory code.
- No auto-decay, no auto-deprecation, no auto-mutation. Honors the established principle: **the server never auto-deprecates — the agent judges** (lesson `e1b9b1df42094746`).

## The provenance boundary (correctness-critical)

`provenance = "human-explicit"` is set **only** when the user explicitly instructs the agent to remember something ("запам'ятай", "закинь у базу знань", "save this", "this is important for later"). Anything the agent writes on its own initiative (a `lesson`/`decision` during work) stays `"agent"`. If everything became `human-explicit`, the marker would be meaningless.

## Design

### Data model (`store.py`)
- New constants: `NOTE_PROVENANCE = ("human-explicit", "agent")`, `DEFAULT_PROVENANCE = "agent"`.
- New normalizer `normalize_provenance(value: str | None) -> str` (unknown → `"agent"`), mirroring `normalize_note_kind`.
- `provenance` added to the on-disk note serialization.

### Write path (`store.py` + `server.py`)
- `MemoryStore.write_project_note(...)` gains `provenance: str | None = None` (resolved to `DEFAULT_PROVENANCE`). Backward compatible: existing callers and other MCP clients get `"agent"`.
- `remember_note` MCP tool accepts and forwards `provenance`.

### Migration (`migrations/`)
- Bump `NOTES_FORMAT_VERSION` 1 → 2.
- Backfill: every existing note gets `provenance = "agent"` (they were agent-written). Run via the existing `migrations/runner` with snapshot for atomicity.

### Retrieval boost (`retrieval_index.py`)
- New constant `PROVENANCE_HUMAN_BONUS` applied in `_rrf_merge`, following the existing `MARKDOWN_KIND_BONUS` pattern.
- **Calibration caveat** (learned from P1's uncalibrated 0.88/0.78 thresholds): start at `~0.05`, then calibrate against a real note corpus. The boost must lift a `human-explicit` note when relevance is close — it must NOT override relevance and drag an unrelated human note to the top.
- Best-effort: a boost failure must never break search (same defensive pattern as P1 similarity hints).

### Result payload (`contracts.py`)
- `build_note_item_payload` / write payload expose `provenance` so the agent sees the marker in `semantic_search` / `hydrate` results.

### Agent behavior (no tqmemory code)
- Recognize explicit "remember this" intent in natural language → call `remember_note(provenance="human-explicit")`, choosing `kind` (`decision` for a directive/rule, `lesson`/`pattern` for a fact) and `scope` (`project` vs `global`) from context.

## Edge cases / error handling

- Existing 108 notes backfilled to `"agent"` (conservative — no retroactive human marks).
- Unknown/invalid `provenance` → normalized to `"agent"`.
- Boost is best-effort; failure degrades gracefully to unbiased ranking.
- Half-applied migration guarded by the existing runner's snapshot/atomicity.

## Testing

- **Migration:** post-upgrade, all pre-existing notes = `"agent"`; `format_version` bumped; idempotent re-run.
- **Write:** `provenance` round-trips; omitted → default `"agent"`.
- **Retrieval:** deterministic test via a fake embedder (like `tests/test_write_time_hints.py`) — at equal relevance, `human-explicit` ranks above `agent`.
- **Payload:** `provenance` present in search/hydrate results.
- **Backward compat:** `remember_note` without `provenance` stores `"agent"`.

## Necessity self-check

Each of the 5 code points + the boost is necessary and minimal: remove provenance → no origin distinction; remove the boost → the field is decorative; remove the migration → old notes break tier/field assumptions. Nothing here is "nice to have". This is the scope the agent would want for its own work, and no more.
