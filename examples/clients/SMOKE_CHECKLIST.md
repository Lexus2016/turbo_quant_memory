# Client Smoke Checklist / Чекліст smoke-перевірки клієнтів

## Install Prerequisite / Передумова інсталяції

Install the packaged CLI before wiring the MCP server into a client:

Спочатку встановіть packaged CLI, а вже потім підключайте MCP-сервер до клієнта:

1. Primary / Основний шлях: `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`
2. Fallback / Резервний шлях: `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`

Pinned release install / Інсталяція з release tag:

- `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`

Run contract after install / Команда запуску після інсталяції:

- `turbo-memory-mcp serve`

## Shared Phase 5 Validation / Спільна перевірка Phase 5

Run this semantic-retrieval flow in every client after the server connects:

Запустіть цей semantic-retrieval flow у кожному клієнті після підключення сервера:

1. `server_info`
2. `remember_note(title="Smoke Note", content="Phase 5 namespace smoke", kind="pattern", tags=["smoke"])`
3. `promote_note(note_id)`
4. `index_paths(paths=["."], mode="incremental")`
5. `semantic_search(query="namespace smoke", scope="hybrid")`
6. `hydrate(item_id, scope="project", mode="default")` on a Markdown search hit

Pass signals / Сигнали успіху:

- `server_info.current_project` exists
- `server_info.storage_root` points to `~/.turbo-quant-memory/` or your local override
- `server_info.index_status.project.freshness` becomes `fresh` after indexing
- `remember_note` returns `scope = "project"`
- `remember_note` returns a valid `note_kind`
- `promote_note` returns `scope = "global"` with `promoted_from`
- `semantic_search(scope="hybrid")` returns compact cards with `compressed_summary`, `key_points`, and `confidence_state`
- `hydrate(...)` returns the full source item plus a bounded neighborhood
- `semantic_search(scope="hybrid")` returns the `project` hit before the promoted `global` hit when both are relevant

## Claude Code

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/claude.project.mcp.json`
- Load / Підключення: place the fixture contents into project `.mcp.json` or run `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve`
- Confirm / Підтвердження: start Claude Code in this repo and confirm `tqmemory` is visible in MCP status for the project
- Prompt / Промпт: `Run server_info, then remember_note with kind="pattern", promote it, run index_paths on the repo, call semantic_search(scope="hybrid"), and hydrate a Markdown hit. Print the JSON responses.`
- Pass signal / Сигнал успіху: `self_test.tool_count = 10`, `server_info.current_project` exists, and the hydration-enabled retrieval flow succeeds end-to-end

## Codex

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/codex.config.toml`
- Load / Підключення: merge the fixture into `.codex/config.toml` or `~/.codex/config.toml`, or run `codex mcp add tqmemory -- turbo-memory-mcp serve`
- Root note / Примітка про repo root: do not add the repository path to `args`; `turbo-memory-mcp serve` resolves the project from the process working directory. Start Codex in the target repo, run `codex -C <repo-root> ...`, or set `TQMEMORY_PROJECT_ROOT` explicitly if the MCP process is launched elsewhere.
- Confirm / Підтвердження: launch `codex`, open `/mcp`, verify `tqmemory` appears as a configured MCP server, and confirm `server_info.current_project.project_root` points to the target repository
- Prompt / Промпт: `Use the tqmemory MCP server only. Run self_test, then server_info, then remember_note(title="Codex Smoke Note", content="Phase 5 namespace smoke", kind="pattern", tags=["smoke"]), promote_note(note_id), index_paths(paths=["."], mode="incremental"), semantic_search(query="namespace smoke", scope="hybrid"), and hydrate a Markdown hit with scope="project" and mode="default". Print the JSON responses.`
- CLI smoke command / Команда CLI smoke: `codex exec --dangerously-bypass-approvals-and-sandbox -C <repo-root> -c 'mcp_servers.tqmemory.command="turbo-memory-mcp"' -c 'mcp_servers.tqmemory.args=["serve"]' '<prompt above>'`
- Pass signal / Сигнал успіху: `tool_count = 10`, `server_info.current_project` exists, `server_info.index_status.project.freshness = "fresh"`, and the shared hydration flow passes

## Cursor

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/cursor.project.mcp.json`
- Load / Підключення: place the fixture contents into `.cursor/mcp.json` for project scope or `~/.cursor/mcp.json` for user scope
- Confirm / Підтвердження: restart Cursor or run `agent mcp list` and verify `tqmemory` is connected through `stdio`
- Prompt / Промпт: `Run self_test, then execute the shared Phase 5 hydration validation flow from tqmemory and print the JSON responses.`
- Pass signal / Сигнал успіху: Cursor shows `tqmemory` as connected, `tool_count = 10`, and `hybrid` returns the project hit first

## OpenCode

- Tier / Рівень: `Tier 1`
- Fixture / Фікстура: `examples/clients/opencode.config.json`
- Load / Підключення: merge the `mcp.tqmemory` object from the fixture into your OpenCode config and restart OpenCode
- Confirm / Підтвердження: verify `tqmemory` appears in the MCP tools list and is enabled on startup
- Prompt / Промпт: `Use tqmemory, run self_test, then execute the shared Phase 5 hydration validation flow and print the JSON responses.`
- Pass signal / Сигнал успіху: OpenCode exposes `tqmemory` tools, `tool_count = 10`, and the project/global/hybrid flow completes

## Antigravity

- Tier / Рівень: `Tier 2`
- Fixture / Фікстура: `examples/clients/antigravity.mcp.json`
- Load / Підключення: open the Agent side panel, go to MCP server management, choose custom MCP import, and paste the raw JSON from `examples/clients/antigravity.mcp.json`
- Confirm / Підтвердження: verify the UI recognizes `tqmemory` as a custom MCP target before starting an agent session
- Prompt / Промпт: `Run self_test from tqmemory, then try the shared Phase 5 hydration validation flow and print the JSON responses.`
- Pass signal / Сигнал успіху: the server is recognized, `tool_count = 10`, and the hydration-aware retrieval flow works; this remains a documented compatibility target, not equal proof to Tier 1
