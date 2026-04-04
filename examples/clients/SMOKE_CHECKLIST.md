# Client Smoke Checklist

Other languages: [Ukrainian](SMOKE_CHECKLIST.uk.md) | [Russian](SMOKE_CHECKLIST.ru.md)

## Install Prerequisite

Install the packaged CLI before wiring the MCP server into a client.

| Method | Command |
|---|---|
| Primary | `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.3.1` |
| Fallback | `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.3.1` |
| Runtime | `turbo-memory-mcp serve` |

## Shared Validation Flow

Run this flow in every client after the server is connected:

1. `self_test`
2. `server_info`
3. `remember_note(title="Smoke Note", content="Phase 5 namespace smoke", kind="pattern", tags=["smoke"])`
4. `promote_note(note_id)`
5. `index_paths(paths=["."], mode="incremental")`
6. `semantic_search(query="namespace smoke", scope="hybrid")`
7. `lint_knowledge_base(paths=["."], max_issues=50)`
8. `hydrate(item_id, scope="project", mode="default")` on a Markdown hit

Expected pass signals:

- `self_test.tool_count = 11`
- `server_info.current_project` exists
- `server_info.default_query_mode = "project"`
- `server_info.index_status.project.freshness` becomes `fresh` after indexing
- `server_info.usage_stats` exists and reports cumulative retrieval/hydration activity
- `remember_note` returns `scope = "project"`
- `promote_note` returns `scope = "global"` with `promoted_from`
- `semantic_search(scope="hybrid")` returns compact cards with `compressed_summary`, `key_points`, and `confidence_state`
- `lint_knowledge_base(...)` returns `summary` and bounded `issues`
- `hydrate(...)` returns the full source item plus a bounded neighborhood
- `project` hits appear before promoted `global` hits when both are relevant

## Client-Specific Checks

### Claude Code

- Tier: `Tier 1`
- Fixture: [examples/clients/claude.project.mcp.json](claude.project.mcp.json)
- Load: put the fixture into project `.mcp.json` or run `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve`
- Confirm: `tqmemory` is visible in MCP status for the project
- Prompt: run the shared validation flow and print the JSON responses

### Codex

- Tier: `Tier 1`
- Fixture: [examples/clients/codex.config.toml](codex.config.toml)
- Load: merge the fixture into `.codex/config.toml` or `~/.codex/config.toml`, or run `codex mcp add tqmemory -- turbo-memory-mcp serve`
- Root note: launch Codex in the target repository, run `codex -C <repo-root> ...`, or set `TQMEMORY_PROJECT_ROOT` if the MCP process starts elsewhere
- Optional value tracking: add `TQMEMORY_INPUT_COST_PER_1M_TOKENS_USD` in `env` if you want `server_info.usage_stats` to estimate USD saved
- Confirm: `/mcp` shows `tqmemory`, and `server_info.current_project.project_root` points to the target repository
- Prompt: use only the `tqmemory` MCP server and run the shared validation flow

### Gemini CLI

- Tier: `Tier 1`
- Fixture: [examples/clients/gemini.settings.json](gemini.settings.json)
- Load: merge the fixture into `~/.gemini/settings.json`, or run `gemini mcp add tqmemory turbo-memory-mcp serve`
- Root note: launch Gemini CLI in the target repository, or set `TQMEMORY_PROJECT_ROOT` if the MCP process starts elsewhere
- Trust note: if Gemini shows the server as configured but not connected, trust the current folder and run `gemini mcp list` again
- Confirm: `gemini mcp list` or `/mcp list` shows `tqmemory`, and `server_info.current_project.project_root` points to the target repository
- Prompt: use only the `tqmemory` MCP server and run the shared validation flow

### Cursor

- Tier: `Tier 1`
- Fixture: [examples/clients/cursor.project.mcp.json](cursor.project.mcp.json)
- Load: place the fixture into `.cursor/mcp.json` for project scope or `~/.cursor/mcp.json` for user scope
- Confirm: Cursor shows `tqmemory` connected through `stdio`
- Prompt: run the shared validation flow and print the JSON responses

### OpenCode

- Tier: `Tier 1`
- Fixture: [examples/clients/opencode.config.json](opencode.config.json)
- Load: merge the `mcp.tqmemory` object into your OpenCode config
- Confirm: `tqmemory` appears in the MCP tool list and is enabled on startup
- Prompt: run the shared validation flow and print the JSON responses

### Antigravity

- Tier: `Tier 2`
- Fixture: [examples/clients/antigravity.mcp.json](antigravity.mcp.json)
- Load: import the raw JSON through the custom MCP flow in the UI
- Confirm: the UI recognizes `tqmemory` before starting an agent session
- Prompt: run `self_test`, then the shared validation flow
- Note: compatibility is documented, but still treat this as smoke-tested rather than fully production-proven
