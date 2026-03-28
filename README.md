# Turbo Quant Memory for AI Agents

![Turbo Quant Memory hero](assets/readme-hero-en.svg?v=20260328b)

[![Latest release](https://img.shields.io/github/v/release/Lexus2016/turbo_quant_memory?display_name=tag&label=release)](https://github.com/Lexus2016/turbo_quant_memory/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/downloads/)
[![MCP server](https://img.shields.io/badge/MCP-stdio-0A7B83)](https://modelcontextprotocol.io/)
[![Local-first](https://img.shields.io/badge/storage-local--first-2F855A)](https://github.com/Lexus2016/turbo_quant_memory)

Other languages: [Russian](README.ru.md) | [Ukrainian](README.uk.md)

Turbo Quant Memory is a local-first memory layer for AI coding agents such as Claude Code, Codex, Cursor, and other MCP clients.

It helps the agent remember project knowledge, search smaller context first, and open more only when the task really needs it.

> The goal is simple: less repeated reading, more useful work.

Quick links: [What it does](#what-it-does) | [Install](#install) | [Connect a client](#connect-a-client) | [Benchmarks](#benchmarks-from-this-repository) | [Technical spec](TECHNICAL_SPEC.md) | [Memory strategy](MEMORY_STRATEGY.md)

## What It Does

| Without a memory layer | With Turbo Quant Memory |
|---|---|
| Every task starts by reopening files and old chats | The agent can start from saved project knowledge |
| Decisions disappear into chat history | Important decisions become searchable notes |
| Reuse across projects is manual | Good patterns can be promoted into `global` memory |
| Context windows fill with repeated material | `semantic_search` stays compact and `hydrate` opens more only when needed |

## How It Works

| Step | What happens |
|---|---|
| 1. Install once | Run the MCP server locally on your machine |
| 2. Connect one client | Claude Code, Codex, Cursor, OpenCode, and other MCP clients can use the same server |
| 3. Work normally | You describe the task in plain language, not as shell commands |
| 4. Search small first | The agent can call `semantic_search` before opening full files |
| 5. Save what matters | Decisions, lessons, handoffs, and patterns can be written back as notes |

## Benchmarks From This Repository

The repository includes a real benchmark run in [benchmarks/latest.md](benchmarks/latest.md) and [benchmarks/latest.json](benchmarks/latest.json).

![Benchmark summary](benchmarks/summary-en.svg?v=20260328b)

| Metric | Result | Why it matters |
|---|---:|---|
| Corpus | 9 files · 138 blocks | Real repository data, not a toy example |
| Full index | 4.0 s | Initial indexing is short |
| Idle incremental | 0.68 s | Refresh after small changes is light |
| Avg `semantic_search` | 75.14 ms | Fast enough to use by default |
| Avg `hydrate` | 41.71 ms | Opening more context stays cheap |
| Search-only byte savings | 78.02% | Much less text goes to the model |
| Search + hydrate byte savings | 63.41% | Even the guided path stays much smaller than opening full files |

What to take from these numbers:

- the compact path is dramatically smaller than naive full-file reading
- even after opening the best hit, the guided path still saves a lot of context
- more context budget stays available for reasoning instead of repeated reading

These are real measurements for this repository and this implementation. They are not a universal guarantee for every codebase.

## Install

| Best for | Commands |
|---|---|
| Released install with `uv` | `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`<br>`turbo-memory-mcp serve` |
| `pip` fallback | `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`<br>`turbo-memory-mcp serve` |
| Local development | `uv sync`<br>`uv run turbo-memory-mcp serve` |
| Editable source install | `python -m venv .venv`<br>`. .venv/bin/activate`<br>`pip install -e .`<br>`python -m turbo_memory_mcp serve` |

## Connect a Client

Server id: `tqmemory`  
Runtime command: `turbo-memory-mcp serve`

| Client | Quick start | Ready file |
|---|---|---|
| Claude Code | `claude mcp add --scope user tqmemory -- turbo-memory-mcp serve` | [examples/clients/claude.project.mcp.json](examples/clients/claude.project.mcp.json) |
| Codex | `codex mcp add tqmemory -- turbo-memory-mcp serve` | [examples/clients/codex.config.toml](examples/clients/codex.config.toml) |
| Cursor | Use the fixture file | [examples/clients/cursor.project.mcp.json](examples/clients/cursor.project.mcp.json) |
| OpenCode | Use the fixture file | [examples/clients/opencode.config.json](examples/clients/opencode.config.json) |
| Antigravity | Use the fixture file | [examples/clients/antigravity.mcp.json](examples/clients/antigravity.mcp.json) |

Smoke checklist: [examples/clients/SMOKE_CHECKLIST.md](examples/clients/SMOKE_CHECKLIST.md)

After setup, you just talk to the agent normally. If memory is relevant, the agent can call `tqmemory` automatically in the background.

## Useful Prompts

| Goal | Say this |
|---|---|
| First time in a repository | `Index this repository and tell me what memory is now available for future tasks.` |
| Before changing code | `Before you edit anything, check this project's memory for previous decisions about auth, sessions, and retries, then summarize the important points.` |
| Find the right source first | `Find the payment webhook flow in this project, open the most relevant memory hit, and explain what the current implementation does.` |
| Save a decision | `Save a decision note titled "Webhook retry policy" with the summary of the approach we just agreed on.` |
| Reuse knowledge across projects | `If the note we just created is reusable across projects, promote it to global memory.` |

## Simple Mental Model

| Tool | Human meaning |
|---|---|
| `semantic_search` | Find the smallest useful piece of context first |
| `hydrate` | Open more of that context only when needed |
| `remember_note` | Save something important for later |
| `promote_note` | Reuse a proven note across projects |
| `deprecate_note` | Retire old knowledge without deleting history |

## When Memory Gets Outdated

- Save the new correct knowledge as a fresh note.
- Use `deprecate_note` on the old note when it should stop appearing in active search.
- If the old note has a direct replacement, pass the replacement note id so the old record becomes `superseded` instead of just archived.

## Technical Details

| Namespace | Meaning |
|---|---|
| `project` | Repository-local notes for the current codebase |
| `global` | Reusable notes promoted explicitly from `project` |
| `hybrid` | Merged retrieval across `project` and `global` with a strong project bias |

Current project resolution order:

1. Normalized `origin` remote URL
2. Repository root path hash when no remote exists
3. Explicit overrides through `TQMEMORY_PROJECT_ROOT`, `TQMEMORY_PROJECT_ID`, and `TQMEMORY_PROJECT_NAME`

| Tool | Purpose |
|---|---|
| `health` | Check server and storage health |
| `server_info` | Show runtime and project info |
| `list_scopes` | List available memory scopes |
| `self_test` | Validate the server quickly |
| `remember_note` | Save a typed note |
| `promote_note` | Reuse a proven note globally |
| `deprecate_note` | Retire outdated knowledge |
| `semantic_search` | Retrieve compact context |
| `hydrate` | Open more of a selected result |
| `index_paths` | Index markdown roots |

Storage location: `~/.turbo-quant-memory/`

Repository verification commands:

```bash
uv run pytest -q
uv run python scripts/smoke_test.py
uv run python scripts/benchmark_context_savings.py
```

## Limits and Reality Check

- This project does not claim direct KV-cache control over hosted models.
- The first embedding-backed run may download `sentence-transformers/all-MiniLM-L6-v2` if the local cache is cold.
- The benchmark report measures this repository and this implementation, not every possible deployment.

## Repository Map

- Runtime contract and server entry point: [src/turbo_memory_mcp/server.py](src/turbo_memory_mcp/server.py)
- Hydration logic: [src/turbo_memory_mcp/hydration.py](src/turbo_memory_mcp/hydration.py)
- Storage model: [src/turbo_memory_mcp/store.py](src/turbo_memory_mcp/store.py)
- Benchmark script: [scripts/benchmark_context_savings.py](scripts/benchmark_context_savings.py)
- Technical specification: [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md)
- Memory strategy: [MEMORY_STRATEGY.md](MEMORY_STRATEGY.md)
