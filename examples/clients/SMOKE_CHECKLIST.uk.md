# Чекліст Smoke-Перевірки Клієнтів

Інші мови: [English](SMOKE_CHECKLIST.md) | [Russian](SMOKE_CHECKLIST.ru.md)

## Передумова Встановлення

Спочатку встановіть packaged CLI, а вже потім підключайте MCP-сервер до клієнта.

| Спосіб | Команда |
|---|---|
| Основний | `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.3` |
| Резервний | `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.3` |
| Запуск | `turbo-memory-mcp serve` |

## Спільний Потік Перевірки

Після підключення сервера до кожного клієнта пройдіть цей сценарій:

1. `self_test`
2. `server_info`
3. `remember_note(title="Smoke Note", content="Phase 5 namespace smoke", kind="pattern", tags=["smoke"])`
4. `promote_note(note_id)`
5. `index_paths(paths=["."], mode="incremental")`
6. `semantic_search(query="namespace smoke", scope="hybrid")`
7. `hydrate(item_id, scope="project", mode="default")` для Markdown-hit

Очікувані сигнали успіху:

- `self_test.tool_count = 10`
- `server_info.current_project` існує
- `server_info.index_status.project.freshness` стає `fresh` після індексації
- `remember_note` повертає `scope = "project"`
- `promote_note` повертає `scope = "global"` разом із `promoted_from`
- `semantic_search(scope="hybrid")` повертає компактні картки з `compressed_summary`, `key_points` і `confidence_state`
- `hydrate(...)` повертає повний source item і обмежене локальне оточення
- `project` hit-и йдуть раніше за promoted `global` hit-и, коли обидва релевантні

## Перевірки По Клієнтах

### Claude Code

- Рівень: `Tier 1`
- Фікстура: [examples/clients/claude.project.mcp.json](claude.project.mcp.json)
- Підключення: покласти фікстуру в project `.mcp.json` або виконати `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve`
- Підтвердження: `tqmemory` видно в MCP status для проєкту
- Промпт: виконати спільний validation flow і надрукувати JSON-відповіді

### Codex

- Рівень: `Tier 1`
- Фікстура: [examples/clients/codex.config.toml](codex.config.toml)
- Підключення: змержити фікстуру в `.codex/config.toml` або `~/.codex/config.toml`, або виконати `codex mcp add tqmemory -- turbo-memory-mcp serve`
- Примітка про repo root: запускати Codex у цільовому репозиторії, через `codex -C <repo-root> ...`, або явно задати `TQMEMORY_PROJECT_ROOT`, якщо MCP стартує в іншому місці
- Підтвердження: у `/mcp` видно `tqmemory`, а `server_info.current_project.project_root` вказує на цільовий репозиторій
- Промпт: використовувати тільки MCP-сервер `tqmemory` і пройти спільний validation flow

### Cursor

- Рівень: `Tier 1`
- Фікстура: [examples/clients/cursor.project.mcp.json](cursor.project.mcp.json)
- Підключення: покласти фікстуру в `.cursor/mcp.json` для project scope або в `~/.cursor/mcp.json` для user scope
- Підтвердження: Cursor показує `tqmemory` як підключений через `stdio`
- Промпт: пройти спільний validation flow і надрукувати JSON-відповіді

### OpenCode

- Рівень: `Tier 1`
- Фікстура: [examples/clients/opencode.config.json](opencode.config.json)
- Підключення: додати об'єкт `mcp.tqmemory` до вашого OpenCode config
- Підтвердження: `tqmemory` є в MCP tools list і увімкнений на старті
- Промпт: пройти спільний validation flow і надрукувати JSON-відповіді

### Antigravity

- Рівень: `Tier 2`
- Фікстура: [examples/clients/antigravity.mcp.json](antigravity.mcp.json)
- Підключення: імпортувати raw JSON через custom MCP flow в UI
- Підтвердження: UI розпізнає `tqmemory` до старту агентської сесії
- Промпт: виконати `self_test`, а потім спільний validation flow
- Примітка: сумісність задокументована, але це все ще рівень smoke-tested, а не повністю production-proven
