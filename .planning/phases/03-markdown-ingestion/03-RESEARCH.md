# Phase 3: Markdown Ingestion - Research

**Researched:** 2026-03-25
**Domain:** Markdown parsing, deterministic chunking, filesystem-backed incremental indexing
**Confidence:** MEDIUM

<user_constraints>
## User Constraints

No `CONTEXT.md` exists for Phase 3. These constraints are derived from the user request, `AGENTS.md`, `TECHNICAL_SPEC.md`, `MEMORY_STRATEGY.md`, `README.md`, and Phase 2 summaries.

- local-first
- easy deployment
- Python 3.11+
- no mandatory DB service
- existing storage root `~/.turbo-quant-memory/`
- project/global namespaces already implemented in Phase 2
- avoid claims of direct hosted-model KV control
- research focus: pragmatic Markdown parsing/chunking for v1, deterministic block identity, incremental changed-files-only reindex, fit with the current filesystem-backed store, and validation strategy
- preserve provenance on every stored/retrieved item
- keep `global` curated; do not weaken the Phase 2 promotion model just to ingest Markdown
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ING-01 | User can register one or more Markdown directories for indexing. | Add one `index_paths(paths, mode)` MCP tool that both registers roots and performs indexing into the current `project` namespace. Persist registered roots under the existing filesystem store. |
| ING-02 | Server splits Markdown into stable retrievable blocks using heading-aware chunking with fallback size limits. | Use `markdown-it-py` token parsing, section-by-heading chunking, and deterministic location-based block IDs with per-block checksums. |
| ING-03 | User can reindex only changed content instead of rebuilding the entire memory store every time. | Maintain per-root and per-file manifests; skip files whose stat fingerprint is unchanged, confirm changed files via content checksum, and rewrite only changed/deleted files. |
</phase_requirements>

## Summary

Phase 3 should stay deliberately narrow: add a **project-scoped Markdown ingestion pipeline** on top of the existing JSON/filesystem store, and do **not** introduce LanceDB or embeddings yet. The existing Phase 2 foundation already gives this repo deterministic project identity, a central storage root, atomic JSON writes, and a stable MCP contract. The next step is to turn Markdown files into stable block artifacts and file manifests that later phases can search and hydrate.

The most pragmatic parser choice for v1 is `markdown-it-py`, not regex splitting and not a heavy document-ingestion framework. Official docs show it can parse Markdown to tokens and a syntax tree, which is enough to implement heading-aware chunking safely across CommonMark constructs. This keeps Phase 3 simple, testable, local-first, and compatible with Python 3.11+.

The core architectural decision is: **block identity should be location-based, not content-hash-based**. Content hashes belong in `checksum` fields for freshness detection. Stable retrieval IDs should survive normal content edits when the logical section location is unchanged. Combined with per-file manifests, that enables changed-files-only incremental refresh without reprocessing the whole corpus.

**Primary recommendation:** Implement Phase 3 as `markdown-it-py` + filesystem manifests/blocks under the existing `MemoryStore`; defer LanceDB and `sentence-transformers` until Phase 4 semantic retrieval.

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `markdown-it-py` | `4.0.0` | Parse Markdown into tokens / syntax tree for heading-aware chunking | Official CommonMark-oriented parser with token stream and syntax-tree helpers; avoids brittle regex parsing. |
| `mcp` | `1.26.0` current on PyPI; project currently pins `>=1.12.4,<2.0` | Extend the existing MCP tool surface with `index_paths` | Already the project foundation; Phase 3 should reuse the same stdio server contract. |
| Python stdlib (`pathlib`, `hashlib`, `json`, `os`, `tempfile`) | Python `3.11+` | File walking, checksums, manifest persistence, atomic writes | Already fits the repo’s Phase 2 storage approach and avoids unnecessary new services. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `mdit-py-plugins` | `0.5.0` | Optional front matter / Markdown extension support | Only add if the actual Markdown corpus relies on YAML front matter or a specific markdown-it plugin. |
| `lancedb` | `0.30.1` | Embedded vector store for semantic retrieval | Defer to Phase 4. Not needed to satisfy Phase 3 ingestion correctness. |
| `sentence-transformers` | `5.3.0` | Local embeddings for semantic search | Defer to Phase 4. Phase 3 should store clean blocks first, then embed them later. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `markdown-it-py` token parsing | Regex heading splitting | Simpler at first glance, but breaks on setext headings, fenced code blocks, tables, and nested Markdown structure. |
| Filesystem block registry first | Immediate LanceDB ingestion | Earlier vector plumbing, but Phase 3 becomes coupled to retrieval concerns before block correctness is proven. |
| Location-based block IDs + separate checksums | Content-hash block IDs | Easier invalidation, but block identity churns on every edit and weakens stable provenance/hydration references. |

**Installation:**
```bash
uv add markdown-it-py
# Optional only if the corpus actually uses front matter or plugins:
uv add mdit-py-plugins
```

**Do not add in Phase 3:**
```bash
# Defer these to Phase 4 retrieval work
# uv add lancedb sentence-transformers
```

**Version verification:** verified against the official PyPI JSON API on 2026-03-25.

| Package | Verified Version | Upload Date |
|---------|------------------|-------------|
| `markdown-it-py` | `4.0.0` | `2025-08-11T12:57:51Z` |
| `mdit-py-plugins` | `0.5.0` | `2025-08-11T07:25:47Z` |
| `lancedb` | `0.30.1` | `2026-03-20T00:51:31Z` |
| `sentence-transformers` | `5.3.0` | `2026-03-12T14:53:39Z` |
| `mcp` | `1.26.0` | `2026-01-24T19:40:30Z` |

## Architecture Patterns

### Recommended Project Structure

```text
src/turbo_memory_mcp/
├── contracts.py          # extend MCP payload builders for indexing results
├── server.py             # add index_paths tool
├── store.py              # extend filesystem storage helpers for markdown roots/files/blocks
├── markdown_parser.py    # pure markdown parsing + heading-aware chunking
└── ingestion.py          # orchestration: register roots, detect changes, persist blocks

tests/
├── test_markdown_chunking.py
├── test_ingestion_store.py
└── test_ingestion_tools.py
```

### Recommended Storage Layout

```text
~/.turbo-quant-memory/
  projects/
    <project_id>/
      manifest.json
      notes/
        <note_id>.json
      markdown/
        manifest.json              # aggregate counts + last index time
        roots/
          <root_id>.json           # one record per registered directory
        files/
          <file_key>.json          # per-file fingerprint + block_ids
        blocks/
          <block_id>.json          # stable retrievable source blocks
  global/
    manifest.json
    notes/
      <note_id>.json
```

This keeps Phase 3 aligned with the Phase 2 storage philosophy:

- keep `notes/` untouched
- add Markdown ingestion as a new project-local subtree
- preserve atomic JSON writes
- avoid any mandatory DB service

### Pattern 1: Project-Scoped Root Registry

**What:** Register Markdown directories into the current `project` namespace and persist them under `projects/<project_id>/markdown/roots/`.

**When to use:** Always in Phase 3. Do not add raw Markdown ingestion into `global` yet.

**Why:** Phase 2 explicitly made `global` curated and promotion-only. Letting raw source trees write directly into `global` would weaken that contract.

**Recommended tool contract:**

```python
def index_paths(
    paths: list[str] | None = None,
    mode: str = "incremental",
) -> dict[str, object]:
    ...
```

**Recommendation:** If `paths` is provided, normalize and register them, then index them. If `paths` is omitted, rerun the previously registered roots for the current project.

### Pattern 2: Heading-Aware Section Extraction First, Fallback Splitting Second

**What:** Parse the file once, split at heading boundaries first, then only apply size-based fallback inside oversized sections.

**When to use:** Every Markdown file.

**Recommended v1 algorithm:**

1. Parse Markdown with `MarkdownIt("commonmark")`.
2. Build ordered sections from heading tokens.
3. Treat pre-heading content as a synthetic `__preamble__` section.
4. If a section is below the soft size limit, keep it whole.
5. If a section is too large, split it on block boundaries already present in the token stream.
6. If a single block element is still oversized, allow one oversized block in v1 instead of inventing fragile code-fence splitting.

**Recommended limits:** start with a soft limit around `1000` characters and a hard limit around `1500` characters. This is an implementation heuristic, not an official library constraint; tune it later once Phase 4 locks the embedding model.

**Example:**
```python
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

md = MarkdownIt("commonmark")
tokens = md.parse(text)
tree = SyntaxTreeNode(tokens)
```
Source: `markdown-it-py` official docs (`docs/using.md`)

### Pattern 3: Stable Location-Based Block IDs, Separate Content Checksums

**What:** Build block IDs from logical location, not from the raw content hash.

**When to use:** Every stored Markdown block.

**Recommended ID components:**

- `project_id`
- `root_id`
- `relative_source_path`
- `heading_path_key`
- `chunk_index_within_section`

**Recommended checksum fields:**

- `source_checksum` for the full file
- `block_checksum` for the block content

**Recommended block payload:**

```json
{
  "block_id": "md_...",
  "scope": "project",
  "project_id": "...",
  "source_kind": "markdown",
  "root_id": "...",
  "source_path": "docs/architecture/adr-001.md",
  "heading_path": ["Architecture", "Storage"],
  "chunk_index": 0,
  "content_raw": "...",
  "block_checksum": "...",
  "source_checksum": "...",
  "updated_at": "2026-03-25T..."
}
```

**Reasoning:** this preserves stable IDs for the same logical section while still allowing refresh detection from checksums.

### Pattern 4: Two-Stage Incremental Refresh

**What:** Use filesystem stats as a cheap skip path, then confirm true content changes with a checksum.

**When to use:** Every `mode="incremental"` run.

**Recommended per-file manifest fields:**

- `root_id`
- `source_path`
- `size`
- `mtime_ns`
- `source_checksum`
- `block_ids`
- `indexed_at`

**Incremental algorithm:**

1. Walk registered roots for `*.md`.
2. Compare discovered files with stored file manifests.
3. If `size` and `mtime_ns` are unchanged, skip the file without opening it.
4. If stats changed, read the file and compute `source_checksum`.
5. If checksum did not change, update the manifest stats only.
6. If checksum changed, rebuild blocks for that file, replace old block records for that file only, and update the manifest.
7. If a previously indexed file no longer exists, delete its block records and file manifest.

This satisfies the requested "changed files only" behavior without prematurely building per-block diff machinery.

### Anti-Patterns to Avoid

- **Do not parse Markdown with regexes alone:** you will mis-handle CommonMark edge cases and fenced code blocks.
- **Do not make block IDs equal to content hashes:** that makes every edit look like a brand-new block.
- **Do not rebuild the whole corpus on each run:** it wastes time now and will waste embedding time later.
- **Do not weaken the project/global contract:** Phase 3 should not add direct global source ingestion just because directory registration exists.
- **Do not couple ingestion to vector search yet:** correctness of blocks and manifests must land before semantic retrieval.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Markdown grammar parsing | A custom regex/line-scan parser | `markdown-it-py` | CommonMark edge cases, nested structures, and plugin support are already solved. |
| Vector storage / ANN retrieval | Custom JSON cosine search or bespoke vector files | `lancedb` in Phase 4 | Embedded local persistence, table lifecycle, delete/update/search already exist. |
| Embedding model pipeline | Ad hoc transformer wrappers | `sentence-transformers` in Phase 4 | Mature local CPU-capable embedding API with a large model ecosystem. |

**Key insight:** Hand-roll only the repo-specific parts: root registration, deterministic block identity, manifest bookkeeping, and payload contracts. Do not hand-roll Markdown grammar or future vector DB features.

## Common Pitfalls

### Pitfall 1: Regex Heading Splits Corrupt Real Markdown

**What goes wrong:** Blocks split on `#` lines but ignore setext headings, code fences, nested lists, and tables.

**Why it happens:** A line-oriented parser treats Markdown as plain text instead of a structured syntax.

**How to avoid:** Parse with `markdown-it-py` and extract heading-bounded sections from the token stream.

**Warning signs:** Code blocks or quoted text become separate chunks unexpectedly; section boundaries disagree with rendered Markdown.

### Pitfall 2: Block IDs Change on Every Edit

**What goes wrong:** Hydration references and later retrieval links break because edited blocks get new IDs.

**Why it happens:** The implementation uses content hashes as public block identity.

**How to avoid:** Keep public `block_id` location-based and store `block_checksum` separately.

**Warning signs:** A one-word edit changes every downstream reference for the same logical section.

### Pitfall 3: Incremental Reindex Trusts `mtime` Too Much

**What goes wrong:** Files get reprocessed unnecessarily after touch/copy operations, or actual changes are missed if the timestamp story is noisy.

**Why it happens:** The pipeline uses only modification time and never confirms with a content hash.

**How to avoid:** Use stat fingerprint for quick skip, but checksum as the source of truth when stats changed.

**Warning signs:** Reindex counts are noisy even when content is unchanged, or changed files remain stale.

### Pitfall 4: Phase 3 Accidentally Becomes Phase 4

**What goes wrong:** The plan pulls in embeddings, vector tables, reranking, and retrieval heuristics before stable block artifacts exist.

**Why it happens:** Semantic search is nearby in the roadmap, so the implementation overreaches early.

**How to avoid:** Treat Phase 3 as a source-of-truth preparation phase: roots, files, blocks, manifests, and deterministic provenance only.

**Warning signs:** New dependencies include `torch`, `sentence-transformers`, or `lancedb` before a single stable block test exists.

## Code Examples

Verified patterns from official sources:

### Parse Markdown to Tokens

```python
from markdown_it import MarkdownIt

md = MarkdownIt("commonmark")
tokens = md.parse(
    "# Header\n\n"
    "Some *text*\n\n"
    "## Child\n\n"
    "- item\n"
)
```
Source: https://github.com/executablebooks/markdown-it-py/blob/master/docs/using.md

### Convert Tokens to a Syntax Tree

```python
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

md = MarkdownIt("commonmark")
tokens = md.parse("# Header\n\nBody text")
tree = SyntaxTreeNode(tokens)
```
Source: https://github.com/executablebooks/markdown-it-py/blob/master/docs/using.md

### Deferred Phase 4 Example: Local LanceDB Connection

```python
import lancedb

db = lancedb.connect("~/.lancedb")
table = db.open_table("documents")
results = table.search([0.1, 0.3]).limit(20).to_list()
```
Source: https://github.com/lancedb/lancedb/blob/main/python/README.md

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Regex-only Markdown chunking | Token/AST-based Markdown parsing | Mature current practice; `markdown-it-py` `4.0.0` released 2025-08-11 | Safer section boundaries and fewer broken blocks. |
| Immediate vectorization during ingestion | Clean block registry first, embeddings later | Recommended for this repo’s roadmap and constraints | Lower Phase 3 complexity and cleaner validation. |
| One-shot full rebuilds | Incremental manifest-driven refresh | Current embedded/local ingestion pattern | Lower latency and less future re-embedding churn. |

**Deprecated/outdated:**

- Regex-only Markdown ingestion for a real CommonMark corpus: brittle and expensive to debug.
- Content-hash-as-public-ID design: poor fit for stable provenance and hydration.

## Open Questions

1. **Should `index_paths` allow `paths=None` to reindex already registered roots?**
   - What we know: incremental reindex is a hard requirement, and root registration is also a hard requirement.
   - What's unclear: whether the public tool contract should force callers to resubmit the same paths every time.
   - Recommendation: yes, allow omitted paths to mean "use the registered roots for this project".

2. **Do we need structured front matter extraction in Phase 3?**
   - What we know: the requirements mention metadata and tags, but no current project doc requires YAML front matter specifically.
   - What's unclear: whether the actual target corpus depends on front matter tags for retrieval quality.
   - Recommendation: default to no extra dependency; add `mdit-py-plugins` only if the real corpus needs it.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | Runtime + tests | ✓ | `3.11.13` | — |
| `uv` | Canonical install/run/test path | ✓ | `0.10.11` | `pip` / `python -m turbo_memory_mcp` |
| `pytest` | Automated validation | ✓ | `9.0.2` | `uv run pytest -q` from project env |
| `git` | Existing project identity resolution | ✓ | `2.50.1` | env overrides already exist for tests |
| Separate DB service | Phase 3 storage | Not required | — | Filesystem store is the primary design |

**Missing dependencies with no fallback:**
- None for Phase 3 research and planning.

**Missing dependencies with fallback:**
- None at research time.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest 9.0.2` |
| Config file | `pyproject.toml` |
| Quick run command | `uv run pytest -q tests/test_markdown_chunking.py tests/test_ingestion_store.py tests/test_ingestion_tools.py` |
| Full suite command | `uv run pytest -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ING-01 | Register one or more Markdown directories and persist project-scoped roots | unit + smoke | `uv run pytest -q tests/test_ingestion_tools.py -x` | ❌ Wave 0 |
| ING-02 | Produce heading-aware stable blocks with deterministic IDs and provenance | unit | `uv run pytest -q tests/test_markdown_chunking.py -x` | ❌ Wave 0 |
| ING-03 | Reindex changed files only; skip untouched files; remove deleted files | unit + smoke | `uv run pytest -q tests/test_ingestion_store.py -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest -q tests/test_markdown_chunking.py tests/test_ingestion_store.py tests/test_ingestion_tools.py`
- **Per wave merge:** `uv run pytest -q`
- **Phase gate:** `uv run pytest -q` and `uv run python scripts/smoke_test.py`

### Wave 0 Gaps

- [ ] `tests/test_markdown_chunking.py` — stable heading-aware block generation for `ING-02`
- [ ] `tests/test_ingestion_store.py` — root/file manifest persistence, delete handling, incremental skip/rewrite coverage for `ING-01` and `ING-03`
- [ ] `tests/test_ingestion_tools.py` — `index_paths` MCP contract and result counts for `ING-01` and `ING-03`
- [ ] `scripts/smoke_test.py` — extend smoke to create a temp Markdown directory and validate one incremental reindex cycle

## Sources

### Primary (HIGH confidence)

- Context7 `/executablebooks/markdown-it-py` - parsing tokens and `SyntaxTreeNode` usage from official docs: https://github.com/executablebooks/markdown-it-py/blob/master/docs/using.md
- Context7 `/lancedb/lancedb` - local connect/create/open/search patterns from the official Python README: https://github.com/lancedb/lancedb/blob/main/python/README.md
- Context7 `/huggingface/sentence-transformers` - current official usage/examples and release note signal via docs: https://sbert.net/
- PyPI official package pages / JSON API for version and release-date verification:
  - https://pypi.org/project/markdown-it-py/
  - https://pypi.org/project/mdit-py-plugins/
  - https://pypi.org/project/lancedb/
  - https://pypi.org/project/sentence-transformers/
  - https://pypi.org/project/mcp/
- Project ground truth:
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/TECHNICAL_SPEC.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/MEMORY_STRATEGY.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/README.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/.planning/ROADMAP.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/.planning/REQUIREMENTS.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/.planning/phases/02-namespace-model/02-01-SUMMARY.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/.planning/phases/02-namespace-model/02-02-SUMMARY.md`
  - `/Users/admin/_Projects/turbo_quant_mcp_memory/.planning/phases/02-namespace-model/02-03-SUMMARY.md`

### Secondary (MEDIUM confidence)

- `Sentence Transformers` home page quickstart and package-reference pages on `sbert.net` for current usage examples and release-note banner: https://sbert.net/

### Tertiary (LOW confidence)

- None.

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH - current package versions were verified on official PyPI and parsing APIs were verified via official docs.
- Architecture: MEDIUM - the storage and ID recommendations are constrained by the existing codebase and roadmap, but some implementation details are still repo-specific design choices.
- Pitfalls: MEDIUM - mostly grounded in parser/tool behavior and existing project constraints, but some are expert inference rather than direct vendor documentation.

**Research date:** 2026-03-25
**Valid until:** 2026-04-24
