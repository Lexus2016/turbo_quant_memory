# Turbo Quant Memory for AI Agents

Other languages: [Russian](README.ru.md) | [Ukrainian](README.uk.md)

Turbo Quant Memory for AI Agents is a local-first stdio MCP server that gives coding agents a smaller, cheaper, and more controlled working context.

It indexes Markdown knowledge, stores typed project notes, promotes reusable notes into a global namespace, and returns compact retrieval cards before escalating to explicit hydration.

## What It Does

- Keeps project memory in a repository-scoped namespace.
- Supports a separate global namespace for promoted reusable notes.
- Uses `semantic_search(...)` for compact retrieval and `hydrate(...)` for fuller context only when needed.
- Exposes a stable 9-tool MCP surface: `health`, `server_info`, `list_scopes`, `self_test`, `remember_note`, `promote_note`, `semantic_search`, `hydrate`, `index_paths`.
- Stores data locally under `~/.turbo-quant-memory/`.

## Measured Savings

The repository ships a real benchmark run in [benchmarks/latest.md](/Users/admin/_Projects/turbo_quant_mcp_memory/benchmarks/latest.md) and [benchmarks/latest.json](/Users/admin/_Projects/turbo_quant_mcp_memory/benchmarks/latest.json).

Benchmark snapshot recorded on 2026-03-26 against this repository corpus:

- Corpus size: 117 Markdown files, 1015 indexed blocks
- Full index time: 17.11 s
- Idle incremental index time: 2.32 s
- Average `semantic_search` latency: 544.08 ms
- Average `hydrate` latency: 184.49 ms
- Average byte savings with `semantic_search` only: 78.39%
- Average byte savings with `semantic_search + hydrate(top1)`: 66.46%
- Average word savings with `semantic_search` only: 83.25%
- Average word savings with `semantic_search + hydrate(top1)`: 74.98%

Benchmark method:

- Baseline without MCP guidance: open the full source text of every unique Markdown file represented in the top-5 project search hits.
- Compact MCP path: keep only the `semantic_search` JSON response.
- Guided MCP path: use `semantic_search` plus `hydrate` for the top Markdown hit.
- Savings are measured with real UTF-8 byte counts and whitespace-delimited word counts on this repository corpus.

These numbers are real for this corpus and this implementation. They are not a universal token-cost claim for every project.

## Install

Recommended release install from GitHub tag:

```bash
uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.1.0
turbo-memory-mcp serve
```

`pip` fallback:

```bash
python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.1.0
turbo-memory-mcp serve
```

Developer setup from source:

```bash
uv sync
uv run turbo-memory-mcp serve
```

Editable `pip` setup from source:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
python -m turbo_memory_mcp serve
```

## Client Setup

Server id:

- `tqmemory`

Runtime command:

- `turbo-memory-mcp serve`

Quick connect examples:

- Claude Code: `claude mcp add --scope user tqmemory -- turbo-memory-mcp serve`
- Codex: `codex mcp add tqmemory -- turbo-memory-mcp serve`

Project fixtures are included for:

- [examples/clients/claude.project.mcp.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/claude.project.mcp.json)
- [examples/clients/codex.config.toml](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/codex.config.toml)
- [examples/clients/cursor.project.mcp.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/cursor.project.mcp.json)
- [examples/clients/opencode.config.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/opencode.config.json)
- [examples/clients/antigravity.mcp.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/antigravity.mcp.json)

Smoke validation checklist:

- [examples/clients/SMOKE_CHECKLIST.md](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/SMOKE_CHECKLIST.md)

## Namespace Model

- `project`: repository-local notes for the current codebase
- `global`: reusable notes promoted explicitly from `project`
- `hybrid`: merged retrieval across `project` and `global` with a strong project bias

Current project resolution order:

1. Normalized `origin` remote URL
2. Repository root path hash when no remote exists
3. Explicit overrides through `TQMEMORY_PROJECT_ROOT`, `TQMEMORY_PROJECT_ID`, and `TQMEMORY_PROJECT_NAME`

## Retrieval Contract

The default loop is:

1. `index_paths(...)`
2. `semantic_search(query, scope="hybrid")`
3. `hydrate(item_id, scope, mode="default"|"related")` only when the compact card is not enough

Typed note write-back:

1. `remember_note(..., kind="decision"|"lesson"|"handoff"|"pattern", scope="project")`
2. `promote_note(note_id)` only when the note is truly reusable

`semantic_search(...)` returns compact, provenance-first cards instead of raw file dumps. `hydrate(...)` returns the full target item plus a bounded neighborhood for Markdown hits.

## Testing

Repository verification commands:

```bash
uv run pytest -q
uv run python scripts/smoke_test.py
uv run python scripts/benchmark_context_savings.py
```

Current release state:

- The Phase 5 hydration flow is covered by tests and a real MCP smoke path.
- The benchmark report is generated from a live run against the repository corpus.
- The runtime contract in `server_info()` and `self_test()` matches the shipped documentation.

## Important Limits

- This project does not claim direct KV-cache control over hosted models.
- The first embedding-backed run may download `sentence-transformers/all-MiniLM-L6-v2` if the local cache is cold.
- The benchmark report measures real savings for this repository corpus, not an absolute cost guarantee for every deployment.

## Repository Map

- Runtime contract and entry point: [src/turbo_memory_mcp/server.py](/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/server.py)
- Hydration logic: [src/turbo_memory_mcp/hydration.py](/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/hydration.py)
- Storage model: [src/turbo_memory_mcp/store.py](/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/store.py)
- Technical specification: [TECHNICAL_SPEC.md](/Users/admin/_Projects/turbo_quant_mcp_memory/TECHNICAL_SPEC.md)
- Memory strategy: [MEMORY_STRATEGY.md](/Users/admin/_Projects/turbo_quant_mcp_memory/MEMORY_STRATEGY.md)
