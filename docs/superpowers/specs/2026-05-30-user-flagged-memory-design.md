# Design: User-Flagged Memory (provenance) — "Фіча А"

- **Date:** 2026-05-30
- **Status:** Proposed (awaiting user review before implementation)
- **Scope decision:** Variant **B** (provenance field + retrieval boost), implemented **lazily — no migration, no format-version bump**. Variant A rejected (cannot fulfil priority requirement), Variant C (proactive surfacing) deferred (junk-drawer risk without cleanup).
- **Related memory:** decision `793bba6750a94e6b` (LightMem analysis), lesson `e1b9b1df42094746` (P1 write-time hints), decision `b6503d848e77469c` (Фіча Б rejected).

## Problem

`tqmemory` cannot distinguish a note the **agent wrote on its own** from one the **human explicitly ordered remembered**. The user wants: "save this important info to my knowledge base" to persist knowledge that is more trusted and surfaces ahead of agent-inferred guesses. Today every note ranks by the same score regardless of origin.

## Goal

When the user explicitly flags information as important, persist it with a `human-explicit` provenance marker and rank it above agent-originated notes of equal relevance — without heavy machinery, at the current corpus scale (this project = 108 notes; system max = 296).

## Non-goals (explicit scope boundary)

- No proactive surfacing / context injection (Variant C) — deferred until B proves insufficient; carries junk-drawer risk without a cleanup mechanism.
- No slash-command `/remember` — natural-language detection covers it; a slash command is a Claude Code artifact, not tqmemory code.
- No auto-decay, no auto-deprecation, no auto-mutation. Honors the established principle: **the server never auto-deprecates — the agent judges** (lesson `e1b9b1df42094746`).
- **No on-disk migration and no LanceDB schema change** (see "Why no migration" below).

## The provenance boundary (correctness-critical)

`provenance = "human-explicit"` is set **only** when the user explicitly instructs the agent to remember something ("запам'ятай", "закинь у базу знань", "save this", "this is important for later"). Anything the agent writes on its own initiative (a `lesson`/`decision` during work) stays `"agent"`. If everything became `human-explicit`, the marker would be meaningless.

## Design

### Data model (`store.py`)
- New constants: `NOTE_PROVENANCE_HUMAN = "human-explicit"`, `NOTE_PROVENANCE_AGENT = "agent"`, `NOTE_PROVENANCES = (...)`, `DEFAULT_PROVENANCE = "agent"`.
- New normalizer `normalize_provenance(value: str | None) -> str`. Unlike `normalize_note_kind` (which raises on unknown), this **never raises** — unknown/empty/missing degrades to `"agent"`. This is what makes legacy notes safe to read without a migration.
- `_build_note_record` writes `provenance` into the note dict; `_normalize_note_record` fills it on read for any note that lacks it.

### Write path (`store.py` + `server.py`)
- `MemoryStore.write_project_note(...)` / `write_global_note(...)` gain `provenance: str | None = None` (resolved to `DEFAULT_PROVENANCE`). `promote_note` carries the source note's provenance forward.
- `remember_note` MCP tool + `remember_note_impl` + `_tool_remember_note` accept and forward `provenance` (default `"agent"`). Backward compatible: callers omitting it get `"agent"`.

### Why no migration (lazy normalize-on-read)
The retrieval path (`_decorate_candidate`) and `list_notes`/`read_note` all pass note JSON through `_normalize_note_record`. Adding the default there means every legacy note **reads as `"agent"`** without rewriting it. The field is optional and additive, so:
- no `NOTES_FORMAT_VERSION` bump,
- no global backfill across the user's other projects (the very concern raised during review),
- no LanceDB column / no RETRIEVAL re-embed.

New notes get the field on write; old notes get it on read. They converge without a migration step.

### Retrieval boost (`retrieval.py`)
- New constant `PROVENANCE_HUMAN_BONUS = 0.06` next to the existing `MARKDOWN_KIND_BONUS`.
- Applied in `_query_scope` (where `effective_score` is composed from `base_score + lexical_bonus + project_bias + kind_bonus`), NOT in `_rrf_merge`. The LanceDB row has no provenance column, so the bonus is decided by reading the **canonical note JSON** (`store.read_note`) for note-kind rows only — cheap at our scale (hundreds of small JSONs, <5ms each). Best-effort: any read failure falls back to a zero bonus and never breaks search.
- **Calibration caveat** (learned from P1's uncalibrated 0.88/0.78 thresholds): `0.06` is a heuristic. The boost must lift a `human-explicit` note when relevance is close — it must NOT override relevance and drag an unrelated human note to the top. Tune on a real corpus.

### Result payload (`contracts.py` + `retrieval.py`)
- `build_note_item_payload` (write result), `build_semantic_item_payload` (search), and `build_hydrated_note_item_payload` (hydrate) surface `provenance`; `_decorate_candidate` adds it to search items. The agent sees the marker in results.

### Agent behavior (no tqmemory code)
- Recognize explicit "remember this" intent in natural language → call `remember_note(provenance="human-explicit")`, choosing `kind` (`decision` for a directive/rule, `lesson`/`pattern` for a fact) and `scope` from context. Documented in the README AI directive.

## Edge cases / error handling

- Legacy notes without the field read as `"agent"` (lazy normalize; no rewrite).
- Unknown/invalid `provenance` → normalized to `"agent"` (never raises).
- Retrieval bonus is best-effort; a per-note read failure degrades to an unbiased score.
- Extra disk reads in `_query_scope` are bounded by the candidate count (≤ ~limit×3) and the small corpus; acceptable now. If the corpus ever grows enough to make this hot, migrate `provenance` into the LanceDB mirror (RETRIEVAL bump) — explicitly deferred.

## Testing

- **Normalizer:** human-explicit / agent / unknown→agent / None→agent.
- **Write:** `provenance` round-trips; omitted → `"agent"`.
- **Backward compat:** a note JSON with the field stripped reads back as `"agent"`.
- **remember_note:** default → `"agent"`; explicit → `"human-explicit"` (asserted on the write payload).
- **Retrieval:** deterministic test via a fake embedder (like `tests/test_write_time_hints.py`) — two notes of equal relevance, the `human-explicit` one ranks first and carries `provenance` in the item.

## Necessity self-check

Each piece is necessary and minimal: remove the field → no origin distinction; remove the normalizer-on-read → legacy notes break payload assumptions and we'd be forced into a migration; remove the boost → the field is decorative. Dropping the migration (vs the first draft) removed the only heavy/irreversible piece without losing any capability. This is the scope the agent would want for its own work, and no more.
