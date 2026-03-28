# Чеклист Smoke-Проверки Клиентов

Другие языки: [English](SMOKE_CHECKLIST.md) | [Ukrainian](SMOKE_CHECKLIST.uk.md)

## Предпосылка Установки

Сначала установите packaged CLI, а уже потом подключайте MCP-сервер к клиенту.

| Способ | Команда |
|---|---|
| Основной | `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.3` |
| Резервный | `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.3` |
| Запуск | `turbo-memory-mcp serve` |

## Общий Поток Проверки

После подключения сервера к каждому клиенту пройдите этот сценарий:

1. `self_test`
2. `server_info`
3. `remember_note(title="Smoke Note", content="Phase 5 namespace smoke", kind="pattern", tags=["smoke"])`
4. `promote_note(note_id)`
5. `index_paths(paths=["."], mode="incremental")`
6. `semantic_search(query="namespace smoke", scope="hybrid")`
7. `hydrate(item_id, scope="project", mode="default")` для Markdown-hit

Ожидаемые сигналы успеха:

- `self_test.tool_count = 10`
- `server_info.current_project` существует
- `server_info.index_status.project.freshness` становится `fresh` после индексации
- `remember_note` возвращает `scope = "project"`
- `promote_note` возвращает `scope = "global"` вместе с `promoted_from`
- `semantic_search(scope="hybrid")` возвращает компактные карточки с `compressed_summary`, `key_points` и `confidence_state`
- `hydrate(...)` возвращает полный source item и ограниченное локальное окружение
- `project` hit-и идут раньше promoted `global` hit-ов, когда оба релевантны

## Проверки По Клиентам

### Claude Code

- Уровень: `Tier 1`
- Фикстура: [examples/clients/claude.project.mcp.json](claude.project.mcp.json)
- Подключение: положить фикстуру в project `.mcp.json` или выполнить `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve`
- Подтверждение: `tqmemory` видно в MCP status для проекта
- Промпт: выполнить общий validation flow и вывести JSON-ответы

### Codex

- Уровень: `Tier 1`
- Фикстура: [examples/clients/codex.config.toml](codex.config.toml)
- Подключение: смержить фикстуру в `.codex/config.toml` или `~/.codex/config.toml`, либо выполнить `codex mcp add tqmemory -- turbo-memory-mcp serve`
- Примечание о repo root: запускать Codex в целевом репозитории, через `codex -C <repo-root> ...`, или явно задать `TQMEMORY_PROJECT_ROOT`, если MCP запускается в другом месте
- Подтверждение: в `/mcp` видно `tqmemory`, а `server_info.current_project.project_root` указывает на целевой репозиторий
- Промпт: использовать только MCP-сервер `tqmemory` и пройти общий validation flow

### Cursor

- Уровень: `Tier 1`
- Фикстура: [examples/clients/cursor.project.mcp.json](cursor.project.mcp.json)
- Подключение: положить фикстуру в `.cursor/mcp.json` для project scope или в `~/.cursor/mcp.json` для user scope
- Подтверждение: Cursor показывает `tqmemory` как подключённый через `stdio`
- Промпт: пройти общий validation flow и вывести JSON-ответы

### OpenCode

- Уровень: `Tier 1`
- Фикстура: [examples/clients/opencode.config.json](opencode.config.json)
- Подключение: добавить объект `mcp.tqmemory` в ваш OpenCode config
- Подтверждение: `tqmemory` есть в MCP tools list и включён на старте
- Промпт: пройти общий validation flow и вывести JSON-ответы

### Antigravity

- Уровень: `Tier 2`
- Фикстура: [examples/clients/antigravity.mcp.json](antigravity.mcp.json)
- Подключение: импортировать raw JSON через custom MCP flow в UI
- Подтверждение: UI распознаёт `tqmemory` до старта агентской сессии
- Промпт: выполнить `self_test`, а затем общий validation flow
- Примечание: совместимость задокументирована, но это всё ещё уровень smoke-tested, а не полностью production-proven
