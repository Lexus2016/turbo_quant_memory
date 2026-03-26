# Phase 5: Hydration and Write-Back - Research

**Researched:** 2026-03-26
**Domain:** hydration contract design, bounded local context recovery, typed write-back memory
**Confidence:** MEDIUM

<user_constraints>
## User Constraints

These constraints come from `05-CONTEXT.md`, `AGENTS.md`, `TECHNICAL_SPEC.md`, `MEMORY_STRATEGY.md`, and completed Phase 1-4 artifacts.

- local-first
- easy deployment
- Python 3.11+
- one stdio MCP server, not a second retrieval or hydration service
- current storage root remains `~/.turbo-quant-memory/`
- `semantic_search(...)` remains the only canonical retrieval entry point
- hydration must be an explicit follow-up, not a default payload expansion
- one universal `hydrate(...)` tool, not separate block/note/related MCP tools
- canonical hydration input shape is `hydrate(item_id, scope, mode=...)`
- default hydration must return the winning item plus a bounded local neighborhood
- neighborhood must be symmetric around the hit
- no separate top-level `related_blocks(...)` tool in v1
- write-back stays explicit and curated
- note kinds must use a small fixed enum: `decision`, `lesson`, `handoff`, `pattern`
- quality of context matters more than raw recall volume
- provenance must survive every retrieval and write-back step
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RET-03 | Agent can hydrate a fuller block or source excerpt on demand when the compressed result is insufficient. | Add one bounded `hydrate(...)` tool that resolves an `item_id` from `semantic_search(...)`, returns the full target content, and expands only a small file-local neighborhood for Markdown hits. |
| RET-04 | Agent can request related blocks around a result to recover local neighbourhood context. | Implement related-context recovery as a `hydrate(..., mode=\"related\")` expansion rather than a new public tool. Keep it source-local and deterministic. |
| MEM-01 | Agent can write back an important decision, lesson, or reusable note into persistent memory. | Extend `remember_note(...)` with a fixed `kind` enum and persist it in note JSON, retrieval rows, and hydration/search payloads. |
| MEM-02 | Stored memory notes are indexed and searchable together with Markdown source content. | Runtime support already exists after Phase 4; Phase 5 should close this requirement by making typed notes part of the published contract, retrieval rows, tests, and smoke path. |
</phase_requirements>

## Summary

Phase 5 should stay **additive** to the current Phase 4 design:

1. keep `semantic_search(...)` as the compact entry point,
2. add a single `hydrate(...)` MCP tool for explicit escalation,
3. keep hydration **bounded and file-local**,
4. extend note persistence with a fixed `kind` enum,
5. reflect note kinds in retrieval/search/hydration payloads so write-back stays useful across sessions.

The narrowest design that satisfies the phase is:

- `hydrate(item_id, scope, mode="default")`
- `mode="default"` returns the full target item plus `before=1` / `after=1` neighbors for Markdown hits
- `mode="related"` returns the full target item plus `before=2` / `after=2` neighbors for Markdown hits
- note hydration ignores neighborhood and returns the full note body plus `kind`, `tags`, `source_refs`, and `promoted_from` when relevant
- `remember_note(...)` becomes typed with `kind in {"decision", "lesson", "handoff", "pattern"}`

This design closes `RET-03` and `RET-04` without widening the MCP surface and closes `MEM-01` / `MEM-02` by making note writes more structured while preserving the existing Phase 4 retrieval loop.

**Primary recommendation:** implement a dedicated `hydration.py` orchestration layer plus minimal `MemoryStore` helpers for item lookup and neighborhood recovery; extend note records and retrieval rows with `kind`; then update docs/tests/smoke flows to publish the new contract.

## External Signals

The Phase 5 direction remains consistent with current MCP practice:

- the official MCP Python SDK supports typed tool registration and structured JSON responses over stdio servers, which fits a single-tool hydration escalation rather than many fragmented tools;
- current MCP best-practice material emphasizes predictable schemas, small public tool surfaces, and explicit tool invocation for larger operations instead of overloading default responses;
- local `stdio` remains the correct transport for this single-user repo-attached memory server.

These signals reinforce the repo's existing contract-first approach rather than forcing a new transport or service boundary.

## Standard Stack

### Core

| Library / Asset | Purpose | Why Standard |
|-----------------|---------|--------------|
| existing MCP Python SDK | add `hydrate(...)` and evolve `remember_note(...)` | Keeps the same stdio runtime and tool registration model. |
| existing `MemoryStore` JSON records | source of truth for notes and Markdown blocks | Hydration should read from existing persisted records rather than invent a second content store. |
| existing `RetrievalIndex` + `semantic_search(...)` | compact retrieval and item identity hand-off | Phase 5 should build on `item_id`, `block_id`, and `can_hydrate` already emitted by Phase 4. |
| existing LanceDB mirror | typed-note searchability | No new index engine is needed; only row shape and sync behavior need extension for `kind`. |

### Supporting

| Asset | Purpose | When to Use |
|-------|---------|-------------|
| `src/turbo_memory_mcp/retrieval.py` | preserve result-level provenance and `can_hydrate` semantics | Always |
| `src/turbo_memory_mcp/contracts.py` | define public payload shapes for hydration and typed note writes | Always |
| Markdown file manifests from Phase 3 | derive bounded file-local neighborhoods | Always for Markdown hydration |
| `tests/test_semantic_search.py` and `tests/test_namespace_tools.py` | extend current contract coverage | Always |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| one universal `hydrate(...)` | separate `hydrate_block(...)`, `hydrate_note(...)`, `related_blocks(...)` | More explicit, but harms cross-client simplicity and increases MCP-surface noise. |
| symmetric file-local neighborhood | heading-bounded or semantic cross-file expansion | Potentially richer, but less predictable in token volume and harder to validate in v1. |
| fixed required note kinds | free-form or inferred note types | More flexible, but quickly degrades memory quality and retrieval trust. |
| no new dependencies | add a summarizer or reranker now | Too broad for the phase; hydration/write-back does not require new models. |

## Architecture Patterns

### Pattern 1: Hydration Resolves Search Hits, Not Arbitrary Sources

**What:** `hydrate(...)` should operate on existing `item_id` values that are already emitted by `semantic_search(...)`.

**Why:** This keeps the public flow simple:

1. run `semantic_search(...)`,
2. inspect the compact card,
3. call `hydrate(item_id, scope, mode=...)` only when needed.

It also avoids forcing agents to reason about separate `note_id` / `block_id` APIs.

### Pattern 2: Neighborhood Recovery Must Stay Source-Local and Deterministic

**What:** For Markdown hits, hydration should read the target block plus symmetric nearby blocks from the same file.

**Recommended defaults:**

- `default` mode: `before=1`, `after=1`
- `related` mode: `before=2`, `after=2`

**Why:** This satisfies both fuller-context recovery and related-context recovery while preserving token discipline. File-local deterministic expansion is easier to test than heading-wide or semantic neighbor expansion.

### Pattern 3: Notes Hydrate Differently from Markdown Blocks

**What:** Note hydration should return the full stored note plus its operational metadata, not artificial neighbor expansion.

**Recommended note payload:**

- `item_id`
- `scope`
- `project_id`
- `project_name`
- `source_kind`
- `note_kind`
- `title`
- `content`
- `tags`
- `source_refs`
- `updated_at`
- `promoted_from` when relevant

**Why:** Notes are already curated units. Expanding them via fake neighborhoods adds tokens without improving context quality.

### Pattern 4: Typed Write-Back Should Be First-Class

**What:** `remember_note(...)` should accept a fixed `kind` field and persist it end to end.

**Recommended enum:**

- `decision`
- `lesson`
- `handoff`
- `pattern`

**Why:** The user explicitly rejected one undifferentiated note type. Fixed kinds improve later recall quality at almost zero payload cost.

### Pattern 5: MEM-02 Is a Contract-Hardening Task, Not a New Subsystem

**What:** Phase 4 already made notes searchable beside Markdown content. Phase 5 should not rebuild that. It should:

- preserve typed note data through sync,
- expose typed note metadata in search/hydration results,
- prove the behavior through tests and smoke flows.

**Why:** Re-implementing note indexing would be redundant and risks scope drift.

### Pattern 6: Hydration Payloads Should Stay Structured

**What:** Hydration results should use explicit sections rather than one concatenated blob.

**Recommended Markdown hydration shape:**

- `status`
- `item`
- `mode`
- `neighbors_before`
- `neighbors_after`
- `neighbor_window`

Where:

- `item` carries the fully hydrated target block
- neighbor arrays contain compact block objects with `block_id`, `heading_path`, `source_path`, and `content`
- `neighbor_window` states `before` and `after` counts used

**Why:** Structured payloads are easier for agents to reason about and easier to freeze in tests than ad-hoc text bundles.

## Testing and Verification Guidance

### Recommended Test Surfaces

- store tests for typed note persistence and source-local neighborhood lookup
- hydration unit tests for Markdown blocks and memory notes
- namespace/server tests for `remember_note(kind=...)` validation and `hydrate(...)` runtime behavior
- semantic-search tests for typed note visibility in retrieval results
- tool-catalog and smoke-contract tests for the new public tool surface
- real stdio smoke flow for `index_paths(...)`, `semantic_search(...)`, `hydrate(...)`, and typed note writes

### High-Risk Areas

1. **Surface sprawl risk:** hydration can easily split into too many tools if the contract is not kept narrow.
2. **Token creep risk:** neighborhood expansion can silently grow too large if bounds are not explicit.
3. **Traceability risk:** if hydrated neighbors drop provenance or ordering, agents lose trust in the source relationship.
4. **Write quality risk:** free-form notes without typed meaning will pollute memory even if retrieval works.
5. **Traceability mismatch risk:** `MEM-02` can be over-implemented unless Phase 5 treats it as typed-note contract hardening.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest 8.x` |
| Config file | `pyproject.toml` |
| Quick run command | `uv run pytest -q tests/test_storage.py tests/test_hydration.py tests/test_namespace_tools.py tests/test_semantic_search.py tests/test_tools.py tests/test_smoke_contract.py` |
| Full suite command | `uv run pytest -q && uv run python scripts/smoke_test.py` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| RET-03 | `hydrate(...)` returns fuller content for a selected hit | unit + contract | `uv run pytest -q tests/test_hydration.py -k default_mode` | ❌ Wave 0 |
| RET-04 | `hydrate(..., mode="related")` returns deterministic local neighbors | unit | `uv run pytest -q tests/test_hydration.py -k related_mode` | ❌ Wave 0 |
| MEM-01 | `remember_note(...)` validates and persists fixed note kinds | unit + server | `uv run pytest -q tests/test_namespace_tools.py -k note_kind` | ❌ Wave 0 |
| MEM-02 | typed notes remain searchable with Markdown content | unit + contract | `uv run pytest -q tests/test_semantic_search.py -k note_kind` | ❌ Wave 0 |

### Sampling Rate

- after each task commit: run the narrowest plan-scoped pytest target
- after each plan wave: run `uv run pytest -q`
- before phase completion: run `uv run pytest -q && uv run python scripts/smoke_test.py`
- max feedback latency: under 45 seconds

### Manual Verification

| Behavior | Requirement | Why Manual | Instructions |
|----------|-------------|------------|--------------|
| default hydration feels like “just enough more context” | RET-03, RET-04 | automated tests can verify shape, not usefulness | run `semantic_search(...)` on a real docs query, then `hydrate(...)` on the top hit and confirm one follow-up call is enough to act |
| note kinds stay meaningful and low-noise | MEM-01 | semantics are hard to score mechanically | create one note of each kind and confirm they are distinguishable in search/hydration output |
| typed notes do not overshadow source blocks unnecessarily | MEM-02 | ranking quality benefits from human inspection | run hybrid queries that match both docs and notes; confirm source blocks still lead when evidence is close |

## Recommended Implementation Strategy

1. Add a dedicated hydration module and minimal `MemoryStore` lookup helpers.
2. Extend note records, retrieval rows, and contracts with `note_kind`.
3. Wire `hydrate(...)` and the typed `remember_note(...)` contract into `server.py`.
4. Update retrieval shaping so note results expose `note_kind` without bloating Markdown result cards.
5. Refresh docs, smoke flows, and contract tests so the public API is frozen consistently.

## Planning Notes

- Split implementation into three execution plans:
  - foundation helpers and hydration storage/runtime
  - server contract plus typed write-back and retrieval integration
  - docs/tests/smoke publication and regression hardening
- Keep Phase 5 free of new external services or model dependencies.
- Keep cross-file semantic “related” expansion deferred to a later phase.

---
*Phase: 05-hydration-and-write-back*
*Research completed: 2026-03-26*
