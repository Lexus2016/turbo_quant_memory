# Phase 5: Hydration and Write-Back - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Recover fuller local context when `semantic_search(...)` is insufficient and let agents persist curated learnings for later sessions without breaking the compressed-first discipline established in Phase 4.

Повернути повніший локальний контекст, коли `semantic_search(...)` уже недостатньо, і дати агентам змогу зберігати curated-висновки для наступних сесій, не ламаючи compressed-first дисципліну, зафіксовану у Phase 4.

This phase adds explicit hydration behavior and richer write-back semantics. It does **not** widen the public MCP surface into many specialized retrieval tools, and it does **not** turn on automatic session dumping by default.

Ця фаза додає явну hydration-поведінку і багатші write-back semantics. Вона **не** розширює публічний MCP-surface на багато спеціалізованих retrieval-tools і **не** вмикає автоматичний session dump за замовчуванням.

</domain>

<decisions>
## Implementation Decisions

### Hydration API Shape
- **D-01:** Phase 5 uses one universal `hydrate(...)` tool as the canonical escalation path after `semantic_search(...)`.
- **D-02:** The canonical hydration input shape is `hydrate(item_id, scope, mode=...)` rather than separate block/note tools or passing a whole result object back into MCP.
- **D-03:** Phase 5 should not add a separate top-level `related_blocks(...)` tool in v1; related-context recovery belongs under hydration modes.

### Default Hydration Boundary
- **D-04:** The default first hydration step returns the winning item plus a bounded local neighborhood rather than only the winning item or a whole section/file dump.
- **D-05:** In v1, local neighborhood should use symmetric blocks around the hit, not heading-bounded or adaptive mixed expansion.
- **D-06:** Hydration should stay deterministic and token-bounded so agents can escalate context in predictable steps.

### Write-Back Capture Model
- **D-07:** Write-back remains explicit and curated; Phase 5 should not auto-generate session summaries by default.
- **D-08:** Write-back should support lightweight note kinds instead of one undifferentiated note type.
- **D-09:** v1 note kinds use a small fixed enum rather than an open-ended or inferred type system.
- **D-10:** The initial note-kind direction is `decision`, `lesson`, `handoff`, and `pattern`.

### the agent's Discretion
- Exact `hydrate(...)` mode names such as `default`, `related`, or `full`
- Exact default neighbor counts on each side of the hit
- Exact payload shape differences between hydrated source blocks and hydrated notes
- Exact note-kind field name and serialization contract
- Exact validation rules for note-kind writes, as long as the fixed small enum stays intact

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Product and Scope
- `.planning/PROJECT.md` — compressed-first product thesis, local-first constraints, and the requirement that agents can write back reusable learnings
- `.planning/REQUIREMENTS.md` — Phase 5 requirements `RET-03`, `RET-04`, `MEM-01`, and `MEM-02`
- `.planning/ROADMAP.md` — Phase 5 boundary and success criteria
- `.planning/STATE.md` — current project position after Phase 4 completion

### Prior Locked Decisions
- `.planning/phases/01-client-integration-foundation/01-CONTEXT.md` — one core `stdio` MCP server and stable package/runtime contract
- `.planning/phases/02-namespace-model/02-CONTEXT.md` — `project/global/hybrid` semantics, explicit promotion into `global`, and compact provenance rules
- `.planning/phases/04-compressed-retrieval/04-CONTEXT.md` — canonical `semantic_search(...)`, balanced-card retrieval, markdown-first ranking, and no raw excerpts by default
- `.planning/phases/04-compressed-retrieval/04-02-SUMMARY.md` — live semantic retrieval contract and synchronization behavior
- `.planning/phases/04-compressed-retrieval/04-03-SUMMARY.md` — published docs and smoke-validated Phase 4 runtime surface

### Runtime and Memory Design
- `README.md` — published Phase 4 contract, including `can_hydrate` and the statement that fuller hydration is deferred to Phase 5
- `TECHNICAL_SPEC.md` — hydration/write-back scope, future tool direction, and quality guardrails
- `MEMORY_STRATEGY.md` — namespace rules, compact retrieval envelope, and write policy that Phase 5 must preserve

### Current Code Surfaces
- `src/turbo_memory_mcp/server.py` — existing MCP tool registration and current note/retrieval integration points
- `src/turbo_memory_mcp/retrieval.py` — current semantic retrieval flow, warning states, and `can_hydrate` escalation signal
- `src/turbo_memory_mcp/store.py` — persisted Markdown block, file-manifest, and note storage primitives available to hydration/write-back
- `src/turbo_memory_mcp/contracts.py` — shared payload builders and current public tool catalog

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/turbo_memory_mcp/store.py` already exposes `read_markdown_block(...)`, note read/write/promotion primitives, and Markdown file manifests that hydration can reuse without redesigning storage.
- `src/turbo_memory_mcp/retrieval.py` already returns `item_id`, `block_id`, `heading_path`, `scope`, and `can_hydrate`, so Phase 5 can build escalation on top of the existing result card instead of inventing a second retrieval entry point.
- `src/turbo_memory_mcp/server.py` already keeps note writes, promotion, indexing, and retrieval in one MCP server surface, which favors adding one hydration tool instead of many specialized tools.
- `src/turbo_memory_mcp/contracts.py` already centralizes payload shaping, so hydrated responses and richer write-back envelopes should follow the same contract-first pattern.

### Established Patterns
- The repo is contract-first: runtime, tests, docs, and smoke flows are expected to share the same payload vocabulary.
- One top-level MCP tool should own one clear public behavior; the codebase currently avoids nested tool sprawl.
- Compressed-first default behavior is now a hard product rule; fuller context must be an explicit escalation step rather than a silent default.
- Namespace safety from Phase 2 remains in force: `global` stays promotion-only and provenance must survive every step.

### Integration Points
- Hydration should connect directly to `semantic_search(...)` result envelopes via `item_id`, `block_id`, and `scope`.
- Neighborhood recovery should consume persisted Markdown block/file manifests without changing Phase 3 indexing contracts.
- Write-back should reuse the existing note persistence and retrieval-sync flow so new curated entries become searchable alongside Markdown source content.

</code_context>

<specifics>
## Specific Ideas

- The user wants **good context quality with minimum content**, not maximal recall payloads.
- One universal hydration entry point is preferred over multiple MCP tools, because it keeps the client UX simpler across Claude Code, Codex, Cursor, OpenCode, and Antigravity.
- The first hydrate should feel like “just enough more context” rather than “dump the whole file”.
- Write-back should stay curated and reusable; default automatic session dumping was explicitly rejected.
- Fixed note kinds should stay small and operationally meaningful: `decision`, `lesson`, `handoff`, `pattern`.

</specifics>

<deferred>
## Deferred Ideas

- Separate top-level `related_blocks(...)` as a public MCP tool
- Whole-section or whole-file hydration as the default first escalation step
- Automatic session summaries or auto-handoff dumps by default
- Automatic note-kind inference from note content
- Cross-project semantic related-context expansion as a default hydration behavior

</deferred>

---
*Phase: 05-hydration-and-write-back*
*Context gathered: 2026-03-26*
