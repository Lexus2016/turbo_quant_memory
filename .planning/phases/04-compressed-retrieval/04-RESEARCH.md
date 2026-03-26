# Phase 4: Compressed Retrieval - Research

**Researched:** 2026-03-26
**Domain:** semantic retrieval, compressed result cards, provenance-aware ranking, low-confidence signaling
**Confidence:** MEDIUM

<user_constraints>
## User Constraints

These constraints are taken from `04-CONTEXT.md`, `AGENTS.md`, `TECHNICAL_SPEC.md`, `MEMORY_STRATEGY.md`, and completed Phase 1-3 artifacts.

- local-first
- easy deployment
- Python 3.11+
- one stdio MCP server, not a separate retrieval service
- current storage root remains `~/.turbo-quant-memory/`
- project/global/hybrid namespace rules from Phase 2 stay intact
- retrieval must prefer minimal token volume **without losing essential context**
- Phase 4 canonical retrieval API is `semantic_search(...)`
- `search_memory(...)` should not remain a public compatibility alias
- default result payload is a balanced card: `compressed_summary` + at most `2-3` `key_points`
- no raw excerpt by default
- search must cover both indexed Markdown blocks and existing memory notes
- ranking stays project-biased and markdown-first inside each scope
- low-confidence or ambiguous retrieval should return cautious results with explicit warnings
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RET-01 | Agent can run semantic search and receive the top relevant blocks with score, source path, and block ID. | Add `semantic_search(...)` backed by local embeddings and a file-backed retrieval index that returns block- or note-backed result cards with explicit scores. |
| RET-02 | Agent can request a compressed context card for a block that removes low-signal noise while preserving the key meaning. | Build a deterministic card-compression layer over stored Markdown blocks and notes; default to a balanced card and keep raw excerpts out of the default payload. |
| SAFE-01 | Every returned result includes source provenance that lets the agent or user trace it back to the original file and block. | Carry forward the compact provenance envelope and extend it with block-specific fields such as `block_id`, `heading_path`, and source-kind aware identifiers. |
| SAFE-02 | The server avoids returning full raw files by default and instead requires explicit hydration calls for larger context. | Keep `semantic_search(...)` and compressed-card responses excerpt-free by default; defer fuller recovery paths to Phase 5. |
</phase_requirements>

## Summary

Phase 4 should introduce a **derived retrieval index**, not replace the Phase 3 filesystem store as the source of truth. The cleanest fit for the current repo is:

1. keep JSON records in `MemoryStore` as canonical persisted content,
2. add an embedded `LanceDB` index for fast semantic lookup,
3. use `sentence-transformers` with `all-MiniLM-L6-v2` as the CPU-friendly default embedding model,
4. expose one public `semantic_search(...)` tool that queries the index, merges project/global results using the existing scope policy, and returns balanced compressed cards.

This is the narrowest path that satisfies `RET-01`, `RET-02`, `SAFE-01`, and `SAFE-02` without prematurely implementing hydration. The key architectural move is to treat the retrieval index as a **mirror of stored notes and blocks**, not as the primary data store. That keeps Phase 4 compatible with the current Phase 3 ingestion contract and preserves the repo’s local-first, inspectable-storage philosophy.

**Primary recommendation:** add `lancedb` plus `sentence-transformers`, mirror project/global records into scope-specific embedded tables, and implement `semantic_search(...)` as the new public retrieval API. Keep `remember_note(...)` and `promote_note(...)` intact, but make retrieval index synchronization part of the internal runtime path so notes are searchable without inventing a second note-search API.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `lancedb` | `0.30.1` | Embedded file-backed vector search and optional FTS/hybrid search | Matches the project’s “no separate DB service” requirement while giving a real local retrieval engine. |
| `sentence-transformers` | `5.3.0` | Local CPU-friendly embeddings | Supports `all-MiniLM-L6-v2`, which the project already treats as the pragmatic default. |
| `torch` | transitively via `sentence-transformers` | Runtime backend for embeddings | Required by the chosen embedding stack; acceptable because this is still CPU-capable on laptops. |
| `mcp` | already pinned in project | Extend the current stdio MCP tool surface | Phase 4 should remain a contract-first extension of the existing server. |

### Supporting

| Library / Asset | Purpose | When to Use |
|-----------------|---------|-------------|
| existing `MemoryStore` JSON records | canonical source content and provenance | Always — keep JSON storage as source of truth |
| existing `markdown_parser.py` + Phase 3 block layout | deterministic block ids and section metadata | Always — retrieval should not redefine block identity |
| LanceDB FTS / hybrid search primitives | lexical boost or ambiguity handling | Optional inside Phase 4 if ranking quality needs more than embeddings alone |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| LanceDB mirror index | manual cosine search over all stored embeddings | Simpler at first, but weaker file-backed query ergonomics and no built-in path toward hybrid search. |
| Sentence Transformers + `all-MiniLM-L6-v2` | heavier cross-encoder or larger embedding model | Better quality in some cases, but too much friction for the project’s easy-deploy CPU-first goal. |
| One canonical `semantic_search(...)` tool | keep `search_memory(...)` as alias | Lower migration cost, but directly conflicts with the locked Phase 4 user decision. |
| Default balanced cards | raw excerpt preview in search results | Higher recall confidence, but breaks token-discipline and Phase 4 safety intent. |

**Library usage confirmed from primary docs:**

- LanceDB official docs show embedded local Python usage via `lancedb.connect(\"<path>\")`, table creation/opening, vector search, and optional FTS/hybrid flows.
- Sentence Transformers official docs show direct local model loading with `SentenceTransformer(\"all-MiniLM-L6-v2\")` and `encode(...)`, plus manual semantic-search guidance for small-to-medium corpora.

## Architecture Patterns

### Recommended Project Structure

```text
src/turbo_memory_mcp/
├── retrieval_index.py      # embedded index sync and table access
├── retrieval_contracts.py  # balanced card builders, warnings, source-kind aware payloads
├── retrieval.py            # semantic query orchestration and ranking
├── contracts.py            # export new tool names / shared public payloads
├── server.py               # replace search_memory with semantic_search
└── store.py                # optional helpers for retrieving blocks/notes by id for card building

tests/
├── test_retrieval_index.py
├── test_semantic_search.py
├── test_tools.py
├── test_smoke_contract.py
└── test_namespace_tools.py  # migrated away from public search_memory expectations
```

### Pattern 1: Retrieval Index Mirrors the JSON Store

**What:** Treat `MemoryStore` records as source truth and mirror them into embedded retrieval tables.

**Why:** Phase 3 already created deterministic block storage. Replacing that with a new canonical store would create unnecessary drift and complicate provenance.

**Recommended scope-specific shape:**

- project retrieval table under `projects/<project_id>/retrieval/`
- global retrieval table under `global/retrieval/`

Each row should carry enough metadata to build the public result card without rereading whole files:

- `scope`
- `project_id`
- `project_name`
- `source_kind`
- `item_id`
- `block_id` or `note_id`
- `source_path`
- `heading_path`
- `title`
- `tags`
- `content_search`
- `content_summary_seed`
- `updated_at`
- `vector`

### Pattern 2: Query Both Scopes, Then Merge with Existing Bias Rules

**What:** Query relevant scope indexes separately, then merge results in Python using the already-locked namespace policy.

**Recommended behavior:**

- `project` mode → query only the current project retrieval index
- `global` mode → query only the global retrieval index
- `hybrid` mode → query both, then merge with:
  - strong project bias
  - markdown-first tie-break inside each scope
  - newer `updated_at`
  - stable identity

**Why:** This preserves the Phase 2 semantics instead of pushing project/global precedence into an opaque vector index query.

### Pattern 3: Balanced Result Cards as the Default Retrieval Contract

**What:** `semantic_search(...)` should return balanced cards, not raw content.

**Recommended public result fields:**

- `scope`
- `project_id`
- `project_name`
- `source_kind`
- `item_id`
- `block_id` when source kind is block-backed
- `source_path`
- `heading_path` or `title`
- `score`
- `confidence`
- `confidence_state` (or equivalent warning enum)
- `compressed_summary`
- `key_points` (max `2-3`)
- `can_hydrate`
- `promoted_from` when relevant

**Why:** This satisfies the user’s “minimum context without losing context” rule better than either pointer-only hits or excerpt-heavy hits.

### Pattern 4: Low-Confidence Signaling Must Be Explicit but Lightweight

**What:** Do not fail silently on weak matches. Return best-effort hits plus an explicit warning signal.

**Recommended states:**

- `high`
- `medium`
- `low`
- `ambiguous`

**Recommended warning contract:**

- one compact state field
- optional short `warning` string
- no verbose debug trace in default responses

**Why:** Phase 4 needs to preserve trust per token, not dump scoring diagnostics into every search result.

### Pattern 5: Notes Participate in Search, but Do Not Outrank Source Blocks by Default

**What:** Memory notes are searchable in Phase 4 because the user locked that decision, but ranking should still favor actual source blocks.

**Recommended source-kind precedence inside each scope:**

1. `markdown`
2. `memory_note`

**Why:** Notes are curated shortcuts and can accelerate agent behavior, but they should not hide the underlying source evidence unless they are clearly more relevant.

### Pattern 6: Keep Compression Structural, Not Generative

**What:** For Phase 4, build compressed cards deterministically from stored text and metadata rather than introducing a separate summarizer model.

**Recommended compression steps:**

1. normalize whitespace
2. preserve title / heading context
3. prefer bullets, assertions, signatures, and short declarative lines
4. strip long boilerplate links or repeated markup noise
5. produce at most `2-3` `key_points`

**Why:** This meets Phase 4 scope with low risk. Generative summarization belongs to future compression-intelligence work.

## Testing and Verification Guidance

### Recommended Test Surfaces

- retrieval-index sync tests for blocks and notes
- semantic ranking tests across `project`, `global`, and `hybrid`
- markdown-first tie-break tests when note and block scores are close
- balanced-card payload tests ensuring no raw excerpt leaks by default
- low-confidence/ambiguity signaling tests
- smoke flow update for:
  - `index_paths(...)`
  - `semantic_search(...)`
  - cautious result signaling

### High-Risk Areas

1. **API migration risk:** removing `search_memory(...)` will break current tests/docs unless the migration is explicit and complete.
2. **Index freshness risk:** notes and blocks can drift from the retrieval index if sync boundaries are vague.
3. **Payload creep risk:** it is easy to let “balanced” cards bloat into excerpt-heavy mini-documents.
4. **Ranking trust risk:** a naive score-only merge can let notes displace source blocks and violate the user’s source-first intent.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest 8.x` |
| Config file | `pyproject.toml` |
| Quick run command | `uv run pytest -q tests/test_retrieval_index.py tests/test_semantic_search.py tests/test_tools.py tests/test_smoke_contract.py` |
| Full suite command | `uv run pytest -q && uv run python scripts/smoke_test.py` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| RET-01 | semantic search returns relevant hits with score, source path, and block ID | unit + contract | `uv run pytest -q tests/test_semantic_search.py -k semantic_search` | ❌ Wave 0 |
| RET-02 | balanced compressed cards preserve key meaning without raw dump | unit | `uv run pytest -q tests/test_semantic_search.py -k balanced_card` | ❌ Wave 0 |
| SAFE-01 | every result carries usable provenance | contract | `uv run pytest -q tests/test_smoke_contract.py -k semantic_search` | ❌ Wave 0 |
| SAFE-02 | raw files are not returned by default | unit + smoke | `uv run pytest -q tests/test_semantic_search.py -k no_raw_excerpt` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest -q tests/test_retrieval_index.py tests/test_semantic_search.py tests/test_tools.py tests/test_smoke_contract.py`
- **Per wave merge:** `uv run pytest -q`
- **Phase gate:** `uv run pytest -q` and `uv run python scripts/smoke_test.py`

### Wave 0 Gaps

- [ ] `tests/test_retrieval_index.py` — index sync for markdown blocks and notes
- [ ] `tests/test_semantic_search.py` — ranking, balanced cards, and low-confidence signaling
- [ ] `tests/test_tools.py` — tool catalog updated from `search_memory` to `semantic_search`
- [ ] `tests/test_smoke_contract.py` — semantic retrieval payload contract
- [ ] `scripts/smoke_test.py` — extend smoke to exercise `semantic_search(...)`

## Sources

### Primary (HIGH confidence)

- Context7 `/lancedb/lancedb` — embedded local connect/open/search plus hybrid/FTS patterns from official docs: https://github.com/lancedb/lancedb/blob/main/python/README.md
- Context7 `/huggingface/sentence-transformers` — official local `SentenceTransformer(...)`, `encode(...)`, and manual semantic-search guidance: https://github.com/huggingface/sentence-transformers/blob/main/README.md
- Project ground truth:
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/.planning/phases/04-compressed-retrieval/04-CONTEXT.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/TECHNICAL_SPEC.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/MEMORY_STRATEGY.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/.planning/ROADMAP.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/.planning/REQUIREMENTS.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/contracts.py`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/server.py`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/store.py`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/ingestion.py`

### Secondary (MEDIUM confidence)

- `.planning/phases/03-markdown-ingestion/03-RESEARCH.md` — prior verified version signals for `lancedb` and `sentence-transformers`, still within the stated validity window

### Tertiary (LOW confidence)

- None

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH - current official docs support the embedded LanceDB path and CPU-friendly SentenceTransformer usage directly.
- Architecture: MEDIUM - the mirror-index design is a project-fit recommendation derived from current code structure and user decisions.
- Validation: MEDIUM - the test map is concrete, but exact file names and wave boundaries still belong to planning.

**Research date:** 2026-03-26
**Valid until:** 2026-04-25
