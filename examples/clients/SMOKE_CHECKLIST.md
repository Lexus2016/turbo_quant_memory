# Client Smoke Checklist / Чекліст smoke-перевірки клієнтів

## Shared Phase 2 Validation / Спільна перевірка Phase 2

Run this namespace flow in every client after the server connects:

Запустіть цей namespace-flow у кожному клієнті після підключення сервера:

1. `server_info`
2. `remember_note(title="Smoke Note", content="Phase 2 namespace smoke", tags=["smoke"])`
3. `promote_note(note_id)`
4. `search_memory(query="namespace smoke", scope="hybrid")`

Pass signals / Сигнали успіху:

- `server_info.current_project` exists
- `server_info.storage_root` points to `~/.turbo-quant-memory/` or your local override
- `remember_note` returns `scope = "project"`
- `promote_note` returns `scope = "global"` with `promoted_from`
- `search_memory(scope="hybrid")` returns the `project` hit before the promoted `global` hit

## Claude Code

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/claude.project.mcp.json`
- Load / Підключення: place the fixture contents into project `.mcp.json` or run `claude mcp add --scope project tqmemory -- uv run turbo-memory-mcp serve`
- Confirm / Підтвердження: start Claude Code in this repo and confirm `tqmemory` is visible in MCP status for the project
- Prompt / Промпт: `Run server_info, then remember_note with a smoke note, promote it, and search_memory(scope="hybrid"). Print the JSON responses.`
- Pass signal / Сигнал успіху: `self_test.tool_count = 7`, `server_info.current_project` exists, and the namespace flow succeeds end-to-end

## Codex

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/codex.config.toml`
- Load / Підключення: merge the fixture into `.codex/config.toml` or `~/.codex/config.toml`, or run `codex mcp add tqmemory -- uv run turbo-memory-mcp serve`
- Confirm / Підтвердження: launch `codex`, open `/mcp`, and verify `tqmemory` appears as a configured MCP server
- Prompt / Промпт: `Run self_test, then run the shared Phase 2 namespace validation flow from tqmemory and print the JSON responses.`
- Pass signal / Сигнал успіху: the MCP panel lists `tqmemory`, `tool_count = 7`, and the shared namespace flow passes

## Cursor

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/cursor.project.mcp.json`
- Load / Підключення: place the fixture contents into `.cursor/mcp.json` for project scope or `~/.cursor/mcp.json` for user scope
- Confirm / Підтвердження: restart Cursor or run `agent mcp list` and verify `tqmemory` is connected through `stdio`
- Prompt / Промпт: `Run self_test, then execute the shared Phase 2 namespace validation flow from tqmemory and print the JSON responses.`
- Pass signal / Сигнал успіху: Cursor shows `tqmemory` as connected, `tool_count = 7`, and `hybrid` returns the project hit first

## OpenCode

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/opencode.config.json`
- Load / Підключення: merge the `mcp.tqmemory` object from the fixture into your OpenCode config and restart OpenCode
- Confirm / Підтвердження: verify `tqmemory` appears in the MCP tools list and is enabled on startup
- Prompt / Промпт: `Use tqmemory, run self_test, then execute the shared Phase 2 namespace validation flow and print the JSON responses.`
- Pass signal / Сигнал успіху: OpenCode exposes `tqmemory` tools, `tool_count = 7`, and the project/global/hybrid flow completes

## Antigravity

- Tier / Рівень: `Tier 2`
- Fixture / Фікстура: `examples/clients/antigravity.mcp.json`
- Load / Підключення: open the Agent side panel, go to MCP server management, choose custom MCP import, and paste the raw JSON from `examples/clients/antigravity.mcp.json`
- Confirm / Підтвердження: verify the UI recognizes `tqmemory` as a custom MCP target before starting an agent session
- Prompt / Промпт: `Run self_test from tqmemory, then try the shared Phase 2 namespace validation flow and print the JSON responses.`
- Pass signal / Сигнал успіху: the server is recognized, `tool_count = 7`, and the namespace flow works; this remains a documented compatibility target, not equal proof to Tier 1
