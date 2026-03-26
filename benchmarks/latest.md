# Benchmark Results

- Generated at: `2026-03-26T16:07:01.091747+00:00`
- Corpus: `117` Markdown files, `1015` indexed blocks
- Full index: `17111.02` ms
- Idle incremental: `2315.79` ms

## Aggregate Savings

| Strategy | Average byte savings | Median byte savings | Average word savings |
|---|---:|---:|---:|
| `semantic_search` only | 78.39% | 79.42% | 83.25% |
| `semantic_search` + `hydrate(top1)` | 66.46% | 67.41% | 74.98% |

## Query Breakdown

| Query | Top hit | Full files bytes | Search bytes | Search+hydrate bytes | Search savings | Guided savings |
|---|---|---:|---:|---:|---:|---:|
| `namespace model project global hybrid` | `Namespace Model` | 44707 | 5748 | 9244 | 87.14% | 79.32% |
| `hydrate bounded neighborhood related mode` | `First Hydrate Boundary` | 21484 | 6870 | 10165 | 68.02% | 52.69% |
| `current project resolution git remote overrides` | `Project Identity and Detection` | 31542 | 6835 | 10631 | 78.33% | 66.3% |
| `storage stats freshness index status` | `7.5 Operations / Експлуатація` | 31781 | 6191 | 10005 | 80.52% | 68.52% |
| `Claude Code Codex Cursor OpenCode integrations` | `Source Map` | 23524 | 6881 | 10557 | 70.75% | 55.12% |
| `remember note decision lesson handoff pattern` | `Pattern 4: Typed Write-Back Should Be First-Class` | 49157 | 7073 | 11390 | 85.61% | 76.83% |

## Method

- Baseline without MCP: open the full source text of every unique Markdown file represented in the top-5 project search hits.
- Compact MCP path: use the `semantic_search` response only.
- Guided MCP path: use `semantic_search` and then `hydrate` only for the top Markdown hit.
- Savings are measured against the baseline using real UTF-8 byte counts and whitespace-delimited word counts taken from this repository corpus.
