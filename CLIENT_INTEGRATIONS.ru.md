# Интеграции Клиентов

Другие языки: [English](CLIENT_INTEGRATIONS.md) | [Ukrainian](CLIENT_INTEGRATIONS.uk.md)

## Цель

Использовать один локальный stdio MCP-сервер везде, а затем адаптировать его к каждому клиенту через максимально тонкий конфиг.

Общий runtime-контракт:

- идентификатор сервера: `tqmemory`
- команда запуска: `turbo-memory-mcp serve`
- запись по умолчанию: `project`
- режим чтения по умолчанию: `hybrid`

## Матрица Интеграций

| Клиент | Статус | Быстрое подключение | Готовый файл | Примечание |
|---|---|---|---|---|
| Claude Code | production-ready | `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve` | [examples/clients/claude.project.mcp.json](examples/clients/claude.project.mcp.json) | поддерживает project и user MCP scopes |
| Codex | production-ready | `codex mcp add tqmemory -- turbo-memory-mcp serve` | [examples/clients/codex.config.toml](examples/clients/codex.config.toml) | Codex лучше запускать из целевого репозитория |
| Cursor | production-ready | используйте готовый файл | [examples/clients/cursor.project.mcp.json](examples/clients/cursor.project.mcp.json) | project config является самым надёжным вариантом по умолчанию |
| OpenCode | production-ready | используйте готовый файл | [examples/clients/opencode.config.json](examples/clients/opencode.config.json) | локальный MCP-конфиг под ключом `mcp` |
| Antigravity | compatibility target | используйте готовый файл | [examples/clients/antigravity.mcp.json](examples/clients/antigravity.mcp.json) | архитектурно совместим, но требует smoke-теста в реальном приложении |

## Примечания По Клиентам

### Claude Code

- Поддерживает `claude mcp add ...`, `.mcp.json` и project либо user scopes.
- Project scope лучше, когда память должна оставаться привязанной к конкретному репозиторию.
- Используйте общий runtime-контракт без лишних обёрток.

### Codex

- Поддерживает MCP-конфигурацию и `codex mcp add ...`.
- Codex нужно запускать из целевого репозитория или явно задавать `TQMEMORY_PROJECT_ROOT`.
- Не нужно передавать путь репозитория в MCP `args`; сервер сам определяет проект из process working directory.

### Cursor

- Поддерживает project `.cursor/mcp.json` и user `~/.cursor/mcp.json`.
- Project config стоит использовать, когда память должна оставаться repo-specific.
- User config имеет смысл только тогда, когда межпроектный сценарий действительно нужен.

### OpenCode

- Поддерживает локальные MCP-описания под ключом `mcp`.
- В репозитории уже есть готовый к merge конфиг.
- Команду стоит держать локальной и простой: `["turbo-memory-mcp", "serve"]`.

### Antigravity

- Текущие гайды и integration-сигналы показывают совместимый custom MCP flow.
- Репозиторий содержит raw config пример.
- Antigravity стоит считать архитектурно совместимым, но production-proven его можно называть только после реального smoke test.

## Правила Стандартизации

Во всех клиентах должен действовать один и тот же контракт:

| Элемент | Стандарт |
|---|---|
| Имя MCP-сервера | `tqmemory` |
| Команда запуска | `turbo-memory-mcp serve` |
| Словарь scope | `project`, `global`, `hybrid` |
| Install guidance | сначала release install, потом source install |

Эта одинаковость важна, потому что docs, prompts, smoke-тесты и поведение агентов не расходятся между клиентами.

## Рекомендуемый Набор Для Поставки

Вместе должны поставляться:

1. по одному готовому файлу для каждого поддерживаемого клиента
2. один smoke checklist для всех клиентов
3. один install contract, привязанный к текущему release
4. один server id и одна launch-команда везде

## Итог

Стратегия интеграции намеренно простая:

- один сервер
- одна команда запуска
- один словарь scope
- только тонкие client-specific обёртки там, где они действительно нужны
