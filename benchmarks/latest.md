# Benchmark Results

- Generated at: `2026-05-29T06:20:34.132360+00:00`
- Corpus: `18` Markdown files, `384` indexed blocks
- Full index: `8687.48` ms
- Idle incremental: `397.33` ms

![Benchmark summary](summary-en.svg)

## At a Glance

| Metric | Result | Why it matters |
|---|---:|---|
| Corpus | 18 files · 384 blocks | This is measured on the real repository corpus |
| Full index | 8.69 s | First-time indexing is short |
| Idle incremental | 0.4 s | Re-indexing after small changes is light |
| Avg `semantic_search` | 401.06 ms | Fast enough to use as the default retrieval path |
| Avg `hydrate` | 367.71 ms | Opening more context stays cheap |
| Search-only byte savings | 83.79% | Much less text is sent to the model |
| Search + hydrate byte savings | 74.5% | Even the guided path stays clearly smaller than opening full files |

## What These Results Mean

- The compact retrieval path is much smaller than naive full-file opening.
- Even after hydrating the top hit, the guided path still saves a lot of context.
- More of the model's context window stays available for reasoning instead of repeated reading.

## Aggregate Savings

| Strategy | Average byte savings | Median byte savings | Average word savings |
|---|---:|---:|---:|
| `semantic_search` only | 83.79% | 84.86% | 88.28% |
| `semantic_search` + `hydrate(top1)` | 74.5% | 75.16% | 81.72% |

## Query Breakdown

| Query | Top hit | Full files bytes | Search bytes | Search+hydrate bytes | Search savings | Guided savings |
|---|---|---:|---:|---:|---:|---:|
| `namespace model project global hybrid` | ``hybrid`` | 32911 | 6213 | 9243 | 81.12% | 71.92% |
| `hydrate bounded neighborhood related mode` | `Hydration Strategy` | 44730 | 6700 | 10007 | 85.02% | 77.63% |
| `current project resolution git remote overrides` | `Project Identity` | 48925 | 6397 | 9510 | 86.92% | 80.56% |
| `storage stats freshness index status` | `Added` | 41418 | 6331 | 11308 | 84.71% | 72.7% |
| `Claude Code Codex Cursor OpenCode integrations` | `👋 What is this awesome tool? (For Humans)` | 59152 | 6566 | 10504 | 88.9% | 82.24% |
| `remember note decision lesson handoff pattern` | `2. Memory Writing Discipline` | 27488 | 6578 | 10467 | 76.07% | 61.92% |

## Method

| Strategy | Why it matters |
|---|---|
| Baseline without MCP | Open the full source text of every unique Markdown file represented in the top-5 project search hits |
| Compact MCP path | Use the `semantic_search` response only |
| Guided MCP path | Use `semantic_search` and then `hydrate` only for the top Markdown hit |

Savings are measured against the baseline using real UTF-8 byte counts and whitespace-delimited word counts taken from this repository corpus.
