# Client Smoke Checklist / Чекліст smoke-перевірки клієнтів

## Claude Code

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/claude.project.mcp.json`
- Load / Підключення: place the fixture contents into project `.mcp.json` or run `claude mcp add --scope project tqmemory -- uv run turbo-memory-mcp serve`
- Confirm / Підтвердження: start Claude Code in this repo and confirm `tqmemory` is visible in MCP status for the project
- Prompt / Промпт: `Run self_test from tqmemory and print the JSON response.`
- Pass signal / Сигнал успіху: `status = "ok"`, `tool_count = 4`, and `tool_names = ["health", "server_info", "list_scopes", "self_test"]`

## Codex

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/codex.config.toml`
- Load / Підключення: merge the fixture into `.codex/config.toml` or `~/.codex/config.toml`, or run `codex mcp add tqmemory -- uv run turbo-memory-mcp serve`
- Confirm / Підтвердження: launch `codex`, open `/mcp`, and verify `tqmemory` appears as a configured MCP server
- Prompt / Промпт: `Run self_test from tqmemory and print the JSON response.`
- Pass signal / Сигнал успіху: the MCP panel lists `tqmemory`, and the tool response reports `status = "ok"` with all four tool names

## Cursor

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/cursor.project.mcp.json`
- Load / Підключення: place the fixture contents into `.cursor/mcp.json` for project scope or `~/.cursor/mcp.json` for user scope
- Confirm / Підтвердження: restart Cursor or run `agent mcp list` and verify `tqmemory` is connected through `stdio`
- Prompt / Промпт: `Run self_test from tqmemory and print the JSON response.`
- Pass signal / Сигнал успіху: Cursor shows `tqmemory` as connected, and `self_test` returns the four-tool contract

## OpenCode

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/opencode.config.json`
- Load / Підключення: merge the `mcp.tqmemory` object from the fixture into your OpenCode config and restart OpenCode
- Confirm / Підтвердження: verify `tqmemory` appears in the MCP tools list and is enabled on startup
- Prompt / Промпт: `Use tqmemory and run self_test. Print the JSON response.`
- Pass signal / Сигнал успіху: OpenCode exposes `tqmemory` tools, and `self_test` returns `status = "ok"` with `tool_count = 4`

## Antigravity

- Tier / Рівень: `Tier 2`
- Fixture / Фікстура: `examples/clients/antigravity.mcp.json`
- Load / Підключення: open the Agent side panel, go to MCP server management, choose custom MCP import, and paste the raw JSON from `examples/clients/antigravity.mcp.json`
- Confirm / Підтвердження: verify the UI recognizes `tqmemory` as a custom MCP target before starting an agent session
- Prompt / Промпт: `Run self_test from tqmemory and print the JSON response.`
- Pass signal / Сигнал успіху: the server is recognized and `self_test` returns the expected four-tool contract; this remains a documented compatibility target in Phase 1, not equal proof to Tier 1

