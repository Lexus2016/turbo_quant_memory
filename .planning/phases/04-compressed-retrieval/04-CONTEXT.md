# Phase 4: Compressed Retrieval - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver the first real retrieval layer over stored Markdown blocks and memory notes so agents receive compact, provenance-rich results instead of raw file dumps by default.

Потрібно реалізувати перший повноцінний retrieval-layer поверх збережених Markdown-блоків і memory-notes, щоб агенти за замовчуванням отримували компактні, provenance-rich результати замість сирих дампів файлів.

This phase establishes the canonical retrieval API, default result-card shape, mixed ranking behavior across Markdown blocks and notes, and low-confidence signaling. It does **not** yet implement full hydration, neighborhood recovery, or write-back indexing for notes beyond what already exists.

Ця фаза фіксує канонічний retrieval API, дефолтну форму result-card, правила змішаного ранжування між Markdown-блоками і notes, а також сигналізацію low-confidence. Вона **ще не** реалізує повний hydration, neighborhood recovery або write-back indexing для notes понад уже наявний функціонал.

</domain>

<decisions>
## Implementation Decisions

### Retrieval API Shape
- **D-01:** Phase 4 introduces `semantic_search(...)` as the only canonical public retrieval tool.
- **D-02:** The existing `search_memory(...)` tool should be removed from the public API during Phase 4 rather than kept as a compatibility alias.
- **D-03:** `semantic_search(...)` must search across both indexed Markdown blocks and existing persistent memory notes from the start.

### Default Result Card
- **D-04:** Default retrieval results use a balanced card rather than a minimal pointer-only envelope or a rich excerpt-heavy envelope.
- **D-05:** Each default result card must include a short `compressed_summary`.
- **D-06:** Each default result card may include at most `2-3` `key_points` or equivalent high-signal bullets.
- **D-07:** Default retrieval results must **not** include raw source excerpts by default; fuller source text belongs to later hydration behavior.

### Cross-Source Ranking
- **D-08:** Within each scope, retrieval ranking should prefer Markdown source blocks ahead of memory notes when relevance is close.
- **D-09:** Memory notes should behave as compressed shortcuts or hints, but not as replacements for the original source block when source evidence exists.
- **D-10:** Existing namespace ordering remains in force: `project/global/hybrid` behavior and strong `project` bias are carried forward from Phase 2.

### Low-Confidence and Ambiguity Behavior
- **D-11:** `semantic_search(...)` should return cautious best-effort results rather than an empty payload when confidence is low or ambiguity is high.
- **D-12:** Low-confidence or ambiguous retrievals must carry an explicit warning signal in the response contract.
- **D-13:** The retrieval contract should make uncertainty visible enough that a later hydration step can be triggered intentionally rather than silently assumed.

### the agent's Discretion
- Exact field names for warning-state metadata such as `confidence_state`, `warning`, or equivalent
- Exact scoring formula and how lexical/embedding signals are combined
- Exact formatting of `key_points` versus a short card summary body
- Exact limit defaults for result counts and compression lengths

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Product and Scope
- `.planning/PROJECT.md` — product thesis, compressed-first retrieval principle, and local-first constraints
- `.planning/REQUIREMENTS.md` — Phase 4 requirements `RET-01`, `RET-02`, `SAFE-01`, and `SAFE-02`
- `.planning/ROADMAP.md` — Phase 4 boundary and success criteria
- `.planning/STATE.md` — carried-forward decisions from completed Phases 1-3

### Prior Locked Decisions
- `.planning/phases/01-client-integration-foundation/01-CONTEXT.md` — canonical MCP server/package/runtime contract
- `.planning/phases/02-namespace-model/02-CONTEXT.md` — locked namespace rules, project/global/hybrid semantics, compact provenance expectations
- `.planning/phases/03-markdown-ingestion/03-02-SUMMARY.md` — stable chunking, `__preamble__`, and location-based `block_id` contract
- `.planning/phases/03-markdown-ingestion/03-03-SUMMARY.md` — live `index_paths(...)` contract and persisted Markdown indexing behavior

### Retrieval Design
- `TECHNICAL_SPEC.md` — retrieval tool surface, compressed-first principle, hydration separation, and quality guardrails
- `MEMORY_STRATEGY.md` — compact result envelope, provenance expectations, and project-biased retrieval rules

### Current Runtime Surfaces
- `src/turbo_memory_mcp/contracts.py` — existing compact payload builders and current public contract surface
- `src/turbo_memory_mcp/server.py` — current public MCP tools and runtime integration points
- `src/turbo_memory_mcp/store.py` — persisted Markdown block and note storage primitives available to retrieval
- `src/turbo_memory_mcp/ingestion.py` — current source of indexed Markdown blocks and file-manifest semantics

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/turbo_memory_mcp/contracts.py` already centralizes public payload building, so Phase 4 should extend the shared contract layer rather than assembling ad hoc tool responses.
- `src/turbo_memory_mcp/store.py` already persists both `memory_note` records and Markdown block records with `source_path`, `heading_path`, `block_id`, `source_checksum`, and `updated_at`.
- `src/turbo_memory_mcp/ingestion.py` already guarantees deterministic `block_id` values and project-scoped block storage suitable for retrieval indexing.
- `tests/test_tools.py` and `tests/test_smoke_contract.py` already protect the public MCP contract and should be extended when the retrieval API changes.

### Established Patterns
- The repo is contract-first: payload shapes are expected to live in `contracts.py` and then be consumed by the runtime, tests, and docs.
- Compact provenance is already a hard project preference: default responses should carry trust-bearing metadata without debug-heavy sprawl.
- One MCP tool should own one clear public behavior. The repo currently favors stable top-level tools over nested multi-mode subcommands.

### Integration Points
- Phase 4 should plug retrieval into the existing stdio server instead of creating a separate process or service.
- Retrieval must consume the persisted Markdown block model from Phase 3 without changing the ingestion contract.
- The old note-only `search_memory(...)` path is the immediate public surface that Phase 4 will replace with the canonical `semantic_search(...)` flow.

</code_context>

<specifics>
## Specific Ideas

- The user explicitly wants **minimum context without losing essential context**.
- The default retrieval card should be compact but still sufficient for the agent to choose a correct next action.
- `compressed_summary` plus at most `2-3` `key_points` is preferred over either a pointer-only card or a raw excerpt preview.
- Markdown source blocks should win over notes when both are similarly relevant, because notes are helpers, not substitutes for source evidence.
- Ambiguous or low-confidence retrieval should stay usable, but it must clearly say that certainty is low.

</specifics>

<deferred>
## Deferred Ideas

- Full hydration behavior and neighborhood recovery stay in Phase 5.
- Returning raw excerpt previews by default was considered and rejected for Phase 4 to protect token discipline.
- Keeping `search_memory(...)` as a compatibility alias was considered and rejected in favor of a clean public retrieval contract.
- A separate dedicated note-search tool was considered and rejected for Phase 4 because the canonical direction is one retrieval entry point across both blocks and notes.

</deferred>

---
*Phase: 04-compressed-retrieval*
*Context gathered: 2026-03-26*
