# Benchmark Results

- Generated at: `2026-03-28T17:20:15.198620+00:00`
- Corpus: `17` Markdown files, `247` indexed blocks
- Full index: `5553.57` ms
- Idle incremental: `844.25` ms

![Benchmark summary](summary-en.svg)

## At a Glance

| Metric | Result | Why it matters |
|---|---:|---|
| Corpus | 17 files · 247 blocks | This is measured on the real repository corpus |
| Full index | 5.55 s | First-time indexing is short |
| Idle incremental | 0.84 s | Re-indexing after small changes is light |
| Avg `semantic_search` | 68.13 ms | Fast enough to use as the default retrieval path |
| Avg `hydrate` | 41.63 ms | Opening more context stays cheap |
| Search-only byte savings | 63.96% | Much less text is sent to the model |
| Search + hydrate byte savings | 44.1% | Even the guided path stays clearly smaller than opening full files |

## What These Results Mean

- The compact retrieval path is much smaller than naive full-file opening.
- Even after hydrating the top hit, the guided path still saves a lot of context.
- More of the model's context window stays available for reasoning instead of repeated reading.

## Aggregate Savings

| Strategy | Average byte savings | Median byte savings | Average word savings |
|---|---:|---:|---:|
| `semantic_search` only | 63.96% | 65.1% | 75.02% |
| `semantic_search` + `hydrate(top1)` | 44.1% | 45.67% | 63.51% |

## Query Breakdown

| Query | Top hit | Full files bytes | Search bytes | Search+hydrate bytes | Search savings | Guided savings |
|---|---|---:|---:|---:|---:|---:|
| `namespace model project global hybrid` | ``hybrid`` | 9151 | 5486 | 8409 | 40.05% | 8.11% |
| `hydrate bounded neighborhood related mode` | `Hydration Strategy` | 22069 | 5963 | 8845 | 72.98% | 59.92% |
| `current project resolution git remote overrides` | `Project Identity` | 17837 | 6102 | 9114 | 65.79% | 48.9% |
| `storage stats freshness index status` | `Acceptance Criteria` | 15996 | 5913 | 9206 | 63.03% | 42.45% |
| `Claude Code Codex Cursor OpenCode integrations` | `OpenCode` | 18582 | 6613 | 10905 | 64.41% | 41.31% |
| `remember note decision lesson handoff pattern` | `Memory note` | 25147 | 5659 | 9072 | 77.5% | 63.92% |

## Method

| Strategy | Why it matters |
|---|---|
| Baseline without MCP | Open the full source text of every unique Markdown file represented in the top-5 project search hits |
| Compact MCP path | Use the `semantic_search` response only |
| Guided MCP path | Use `semantic_search` and then `hydrate` only for the top Markdown hit |

Savings are measured against the baseline using real UTF-8 byte counts and whitespace-delimited word counts taken from this repository corpus.
