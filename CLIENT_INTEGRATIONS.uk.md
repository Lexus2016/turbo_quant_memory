# Інтеграції Клієнтів

Інші мови: [English](CLIENT_INTEGRATIONS.md) | [Russian](CLIENT_INTEGRATIONS.ru.md)

## Мета

Використовувати один локальний stdio MCP-сервер скрізь, а потім адаптувати його до кожного клієнта через максимально тонкий конфіг.

Спільний runtime-контракт:

- ідентифікатор сервера: `tqmemory`
- команда запуску: `turbo-memory-mcp serve`
- дефолтний запис: `project`
- дефолтний режим читання: `hybrid`

## Матриця Інтеграцій

| Клієнт | Статус | Швидке підключення | Готовий файл | Примітка |
|---|---|---|---|---|
| Claude Code | production-ready | `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve` | [examples/clients/claude.project.mcp.json](examples/clients/claude.project.mcp.json) | підтримує project і user MCP scopes |
| Codex | production-ready | `codex mcp add tqmemory -- turbo-memory-mcp serve` | [examples/clients/codex.config.toml](examples/clients/codex.config.toml) | Codex краще запускати з цільового репозиторію |
| Cursor | production-ready | використайте готовий файл | [examples/clients/cursor.project.mcp.json](examples/clients/cursor.project.mcp.json) | project config є найнадійнішим дефолтом |
| OpenCode | production-ready | використайте готовий файл | [examples/clients/opencode.config.json](examples/clients/opencode.config.json) | локальний MCP-конфіг під ключем `mcp` |
| Antigravity | compatibility target | використайте готовий файл | [examples/clients/antigravity.mcp.json](examples/clients/antigravity.mcp.json) | архітектурно сумісний, але його треба smoke-тестити на реальному застосунку |

## Нотатки По Клієнтах

### Claude Code

- Підтримує `claude mcp add ...`, `.mcp.json` і project або user scopes.
- Project scope кращий, коли пам'ять має лишатися прив'язаною до конкретного репозиторію.
- Варто використовувати спільний runtime-контракт без зайвих обгорток.

### Codex

- Підтримує MCP-конфігурацію і `codex mcp add ...`.
- Codex потрібно запускати з цільового репозиторію або явно задавати `TQMEMORY_PROJECT_ROOT`.
- Не треба передавати шлях репозиторію в MCP `args`; сервер сам визначає проєкт із process working directory.

### Cursor

- Підтримує project `.cursor/mcp.json` і user `~/.cursor/mcp.json`.
- Project config варто використовувати тоді, коли пам'ять має лишатися repo-specific.
- User config має сенс лише тоді, коли міжпроєктний сценарій справді запланований.

### OpenCode

- Підтримує локальні MCP-описання під ключем `mcp`.
- У репозиторії вже є готовий до merge конфіг.
- Команду слід тримати локальною і простою: `["turbo-memory-mcp", "serve"]`.

### Antigravity

- Поточні гіди та integration-сигнали показують сумісний custom MCP flow.
- Репозиторій містить raw config приклад.
- Antigravity варто вважати архітектурно сумісним, але production-proven його можна називати тільки після реального smoke test.

## Правила Стандартизації

У всіх клієнтах має діяти один і той самий контракт:

| Елемент | Стандарт |
|---|---|
| Ім'я MCP-сервера | `tqmemory` |
| Команда запуску | `turbo-memory-mcp serve` |
| Словник scope | `project`, `global`, `hybrid` |
| Install guidance | спочатку release install, потім source install |

Ця однаковість важлива, бо тоді docs, prompts, smoke-тести й поведінка агентів не розходяться між клієнтами.

## Рекомендований Набір Для Поставки

Разом мають постачатися:

1. по одному готовому файлу для кожного підтримуваного клієнта
2. один smoke checklist для всіх клієнтів
3. один install contract, прив'язаний до поточного release
4. один server id і одна launch-команда скрізь

## Підсумок

Стратегія інтеграції навмисно проста:

- один сервер
- одна команда запуску
- один словник scope
- лише тонкі client-specific обгортки там, де вони справді потрібні
