# Benchmark Results

- Generated at: `2026-03-26T18:49:53.978307+00:00`
- Corpus: `9` Markdown files, `138` indexed blocks
- Full index: `4004.26` ms
- Idle incremental: `677.8` ms

![Benchmark summary](summary-en.svg)

## Aggregate Savings

| Strategy | Average byte savings | Median byte savings | Average word savings |
|---|---:|---:|---:|
| `semantic_search` only | 78.02% | 78.51% | 83.84% |
| `semantic_search` + `hydrate(top1)` | 63.41% | 61.78% | 74.76% |

## Query Breakdown

| Query | Top hit | Full files bytes | Search bytes | Search+hydrate bytes | Search savings | Guided savings |
|---|---|---:|---:|---:|---:|---:|
| `namespace model project global hybrid` | `2. Active Namespaces / Активні namespace` | 23474 | 6209 | 11335 | 73.55% | 51.71% |
| `hydrate bounded neighborhood related mode` | `9.5 Hydration Strategy / Стратегія hydration` | 36130 | 6716 | 10796 | 81.41% | 70.12% |
| `current project resolution git remote overrides` | `3. Current Project Identity / Ідентичність поточного проєкту` | 23474 | 6736 | 9592 | 71.3% | 59.14% |
| `storage stats freshness index status` | `7.5 Operations / Експлуатація` | 42906 | 6507 | 10319 | 84.83% | 75.95% |
| `Claude Code Codex Cursor OpenCode integrations` | `Підключення клієнта` | 30615 | 6508 | 11845 | 78.74% | 61.31% |
| `remember note decision lesson handoff pattern` | `7.4 Write-Back Memory / Запис нової пам'яті` | 29842 | 6481 | 11261 | 78.28% | 62.26% |

## Method

- Baseline without MCP: open the full source text of every unique Markdown file represented in the top-5 project search hits.
- Compact MCP path: use the `semantic_search` response only.
- Guided MCP path: use `semantic_search` and then `hydrate` only for the top Markdown hit.
- Savings are measured against the baseline using real UTF-8 byte counts and whitespace-delimited word counts taken from this repository corpus.
