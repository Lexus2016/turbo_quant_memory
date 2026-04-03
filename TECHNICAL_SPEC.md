# Technical Specification

Other languages: [Ukrainian](TECHNICAL_SPEC.uk.md) | [Russian](TECHNICAL_SPEC.ru.md)

## Product Goal

Build a local-first MCP server that acts as practical long-term memory for AI coding agents.

The server should reduce repeated token usage by:

- moving cold context out of the active prompt
- returning compact, source-backed context first
- hydrating fuller detail only when the agent explicitly asks for it

## Product Boundary

| This project does | This project does not do |
|---|---|
| keeps project knowledge searchable on the MCP side | does not control hosted-model KV cache |
| stores notes and indexed Markdown locally | does not replace the model's internal memory |
| returns compact retrieval cards with provenance | does not promise universal savings for every repository |
| supports project and cross-project recall | does not try to solve every reasoning task with compression alone |

## Target Users

- Engineers working in real repositories with Claude Code, Codex, Cursor, OpenCode, and similar MCP clients.
- Teams that want local deployment with low operational overhead.
- Agent-heavy workflows that repeatedly revisit docs, notes, ADRs, and Markdown knowledge bases.

## Core Principles

| Principle | Meaning |
|---|---|
| Local-first | The core memory loop should work on the developer machine |
| Markdown-first | Human-readable Markdown stays a first-class source of truth |
| Compact-first retrieval | Return the smallest useful answer before opening more |
| Hydration on demand | Larger payloads must be explicitly requested |
| Traceability always | Every result must point back to a source |
| Easy setup | Installation and client wiring should take minutes, not hours |

## Technical Stack

| Area | Choice |
|---|---|
| Language | Python 3.11+ |
| MCP framework | official MCP Python SDK |
| Storage | local filesystem plus embedded LanceDB |
| Embeddings | Sentence Transformers with a lightweight local model |
| Config | environment variables plus typed settings |
| Packaging | `uv` first, `pip` fallback |

## Functional Scope

### 1. Source Ingestion

- Index one or more Markdown roots.
- Chunk content by headings, with deterministic fallbacks.
- Persist source metadata such as path, heading path, timestamps, tags, checksum, and block identity.
- Support incremental re-indexing for changed content.

### 2. Retrieval

- Accept free-text queries through `semantic_search(...)`.
- Return compact result cards instead of full raw file dumps.
- Keep provenance, relevance, confidence, and key points visible.
- Warn when retrieval confidence is low or ambiguous.

### 3. Hydration

- Open a fuller excerpt only when compact retrieval is not enough.
- Support a bounded local neighborhood around the selected hit.
- Keep hydration explicit so token budgets stay predictable.

### 4. Write-Back Memory

- Save decisions, lessons, handoffs, and reusable patterns.
- Store notes with type, tags, timestamps, and source references.
- Allow explicit promotion from `project` to `global`.
- Allow deprecation of outdated notes without deleting history.

### 5. Operations

- Expose health and runtime metadata.
- Show storage counts and index freshness.
- Provide a quick self-test contract.
- Keep smoke-test instructions for supported clients.

### 6. Knowledge-Base Hygiene

- Run structural lint checks on Markdown corpora.
- Detect broken internal Markdown links.
- Detect orphan candidates with no inbound or outbound internal links.
- Detect duplicate normalized titles that increase ambiguity during retrieval.

## MCP Tool Surface

| Tool | Purpose |
|---|---|
| `health()` | basic health check |
| `server_info()` | runtime, project, storage, and install contract |
| `list_scopes()` | available scopes and write/read defaults |
| `self_test()` | quick contract validation |
| `remember_note(...)` | store a typed note |
| `promote_note(note_id)` | copy a project note into reusable global memory |
| `deprecate_note(...)` | retire outdated knowledge |
| `semantic_search(...)` | retrieve compact context |
| `hydrate(...)` | open bounded fuller context |
| `index_paths(...)` | index or refresh Markdown roots |
| `lint_knowledge_base(...)` | run structural checks for link integrity and wiki consistency |

## Data Model

### Markdown block

| Field | Meaning |
|---|---|
| `block_id` | stable identity for the indexed block |
| `file_path` | source Markdown path |
| `heading_path` | heading hierarchy for the block |
| `content_raw` | full source content |
| `content_compressed` | compact retrieval representation |
| `embedding` | vector representation for search |
| `checksum` | change detection |
| `created_at` / `updated_at` | timing metadata |
| `tags` | optional labels |
| `source_kind` | `markdown` |

### Memory note

| Field | Meaning |
|---|---|
| `note_id` | note identity |
| `title` | note title |
| `content` | full note body |
| `note_kind` | `decision`, `lesson`, `handoff`, or `pattern` |
| `summary` | compact summary for retrieval |
| `tags` | optional labels |
| `session_id` | session linkage when relevant |
| `project_id` | owning project |
| `created_at` | creation timestamp |
| `source_refs` | provenance references |

## Performance Targets

- local startup should stay comfortable on a developer laptop
- first indexing pass should fit normal interactive setup expectations
- retrieval latency should feel interactive on small and medium corpora
- recall-heavy workflows should usually save substantial context versus naive full-file opening

## Security and Trust

- Restrict indexing to explicitly selected paths.
- Keep output sizes bounded by default.
- Preserve source boundaries and provenance.
- Treat notes and retrieved memory as tool data, not authority.
- Avoid hidden outbound-network requirements for the core local flow.

## Deployment Contract

| Step | Expected contract |
|---|---|
| Recommended install | `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.4` |
| Fallback install | `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.4` |
| Runtime command | `turbo-memory-mcp serve` |
| Claude Code example | `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve` |
| Equivalent examples | Codex, Cursor, OpenCode, and Antigravity configs ship in the repository |

## Testing Strategy

| Layer | Coverage |
|---|---|
| Unit tests | chunking, IDs, payload contracts, provenance mapping |
| Integration tests | index -> search -> hydrate, note write-back, and knowledge-base lint flows |
| Smoke tests | fresh install, client connection, indexing, retrieval, hydration |
| Benchmarking | repository-level context-savings report with real measurements |

## Acceptance Criteria

1. A new user can install the package and connect it to a supported client in minutes.
2. The server can index Markdown files and search them semantically.
3. Default retrieval returns compact, source-backed context instead of raw dumps.
4. Agents can explicitly hydrate fuller context when needed.
5. Notes can be saved, promoted, deprecated, and found later.
6. Operators can inspect health, freshness, and storage state quickly.
7. Operators can lint indexed Markdown knowledge bases for broken links, orphan candidates, and duplicate titles.

## Non-Goals

- replacing the model's internal memory mechanisms
- claiming direct token quantization or hosted KV-cache control
- building enterprise multi-tenant governance in the current scope
- solving all repository reasoning through compression alone

## Summary

Turbo Quant Memory is a practical MCP memory layer:

- local-first
- compact by default
- traceable on every retrieval
- explicit about when more context is opened
- easy to install and operate in normal developer workflows
