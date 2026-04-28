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
| Gemini CLI | production-ready | `gemini mcp add tqmemory turbo-memory-mcp serve` | [examples/clients/gemini.settings.json](examples/clients/gemini.settings.json) | підтримує `settings.json`, `gemini mcp add` і перевірку MCP status |
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

### Gemini CLI

- Підтримує `~/.gemini/settings.json`, `gemini mcp add ...` і `gemini mcp list`.
- Gemini CLI слід запускати в цільовому репозиторії або явно задавати `TQMEMORY_PROJECT_ROOT`, якщо MCP стартує в іншому місці.
- Якщо Gemini показує сервер як налаштований, але не підключений, потрібно довірити поточну папку для stdio MCP-підключення.
- Підхоплює project-промпти з `AGENTS.md` і `GEMINI.md`, коли при merge збережено блок `context.fileName` із фікстури; готова фікстура вже перелічує обидва імені, тож multi-agent контракти з `AGENTS.md` доходять до Gemini CLI без дублікату `GEMINI.md`.

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

## Спільна Project Memory Між Клієнтами

У стандартному локальному встановленні спільна project memory працює з коробки.

- Не потрібен окремий sync-сервіс.
- Не потрібен export/import handoff.
- Не потрібен окремий memory-backend під конкретного клієнта.

Codex, Gemini CLI та інші MCP-клієнти можуть продовжувати ту саму project memory, коли вони:

1. використовують той самий серверний контракт `tqmemory`
2. працюють на одній машині з тим самим локальним storage root
3. відкривають той самий репозиторій або визначають той самий `TQMEMORY_PROJECT_ROOT`

Це спільна локальна памʼять, а не віддалена хмарна синхронізація.

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
