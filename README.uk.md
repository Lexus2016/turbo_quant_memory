# Turbo Quant Memory for AI Agents

Інші мови: [Англійська](README.md) | [Російська](README.ru.md)

Turbo Quant Memory for AI Agents — це локально-орієнтований stdio MCP-сервер для кодових агентів, яким потрібен менший, дешевший і контрольованіший робочий контекст.

Він індексує Markdown-знання, зберігає типізовані нотатки проєкту, уміє промотити справді повторно вживані нотатки в глобальний простір імен і повертає компактні картки пошуку до явного `hydrate(...)`.

## Що робить проєкт

- Тримає пам’ять проєкту у просторі імен поточного репозиторію.
- Підтримує окремий глобальний простір імен для явно промотованих повторно вживаних нотаток.
- Використовує `semantic_search(...)` для компактного retrieval і `hydrate(...)` лише тоді, коли потрібен повніший контекст.
- Експортує стабільну MCP-поверхню з 9 інструментів: `health`, `server_info`, `list_scopes`, `self_test`, `remember_note`, `promote_note`, `semantic_search`, `hydrate`, `index_paths`.
- Зберігає дані локально в `~/.turbo-quant-memory/`.

## Виміряна економія

У репозиторії є реальні результати вимірювань у [benchmarks/latest.md](/Users/admin/_Projects/turbo_quant_mcp_memory/benchmarks/latest.md) та [benchmarks/latest.json](/Users/admin/_Projects/turbo_quant_mcp_memory/benchmarks/latest.json).

Знімок вимірювань від 2026-03-26 на корпусі цього репозиторію:

- Розмір корпусу: 117 Markdown-файлів, 1015 проіндексованих блоків
- Повна індексація: 17.11 с
- Порожній incremental index: 2.32 с
- Середня затримка `semantic_search`: 544.08 мс
- Середня затримка `hydrate`: 184.49 мс
- Середня економія по байтах для `semantic_search` без hydrate: 78.39%
- Середня економія по байтах для `semantic_search + hydrate(top1)`: 66.46%
- Середня економія по словах для `semantic_search` без hydrate: 83.25%
- Середня економія по словах для `semantic_search + hydrate(top1)`: 74.98%

Метод вимірювання:

- Базовий сценарій без MCP-guidance: відкрити повний текст кожного унікального Markdown-файлу, який входить у top-5 результатів пошуку по проєкту.
- Компактний MCP-сценарій: використовувати тільки JSON-відповідь `semantic_search`.
- Guided MCP-сценарій: використовувати `semantic_search`, а потім `hydrate` лише для найкращого Markdown-результату.
- Економія рахується за реальними UTF-8 byte counts і за кількістю слів, розділених пробілами, на корпусі цього репозиторію.

Ці цифри реальні для цього корпусу і цієї реалізації. Це не універсальна обіцянка вартості для будь-якого проєкту.

## Встановлення

Рекомендоване встановлення релізної версії з GitHub-тега:

```bash
uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.1.0
turbo-memory-mcp serve
```

Резервний шлях через `pip`:

```bash
python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.1.0
turbo-memory-mcp serve
```

Режим розробки з вихідного коду:

```bash
uv sync
uv run turbo-memory-mcp serve
```

Редагована `pip`-інсталяція з вихідного коду:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
python -m turbo_memory_mcp serve
```

## Підключення клієнтів

Ідентифікатор сервера:

- `tqmemory`

Команда запуску:

- `turbo-memory-mcp serve`

Швидкі приклади підключення:

- Claude Code: `claude mcp add --scope user tqmemory -- turbo-memory-mcp serve`
- Codex: `codex mcp add tqmemory -- turbo-memory-mcp serve`

Готові project-конфіги є для:

- [examples/clients/claude.project.mcp.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/claude.project.mcp.json)
- [examples/clients/codex.config.toml](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/codex.config.toml)
- [examples/clients/cursor.project.mcp.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/cursor.project.mcp.json)
- [examples/clients/opencode.config.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/opencode.config.json)
- [examples/clients/antigravity.mcp.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/antigravity.mcp.json)

Чекліст smoke-перевірки:

- [examples/clients/SMOKE_CHECKLIST.md](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/SMOKE_CHECKLIST.md)

## Модель просторів імен

- `project`: локальні для репозиторію нотатки поточної кодової бази
- `global`: повторно вживані нотатки, які явно промотовано з `project`
- `hybrid`: об’єднаний пошук по `project` і `global` із сильним пріоритетом `project`

Порядок визначення поточного проєкту:

1. Нормалізований URL `origin`
2. Hash від root path репозиторію, якщо remote відсутній
3. Явні overrides через `TQMEMORY_PROJECT_ROOT`, `TQMEMORY_PROJECT_ID` і `TQMEMORY_PROJECT_NAME`

## Retrieval Contract

Стандартний цикл роботи:

1. `index_paths(...)`
2. `semantic_search(query, scope="hybrid")`
3. `hydrate(item_id, scope, mode="default"|"related")` лише тоді, коли компактної картки вже недостатньо

Запис типізованих нотаток:

1. `remember_note(..., kind="decision"|"lesson"|"handoff"|"pattern", scope="project")`
2. `promote_note(note_id)` лише для справді reusable нотаток

`semantic_search(...)` повертає компактні картки з пріоритетом provenance замість сирих файлових дампів. `hydrate(...)` повертає повний цільовий елемент і обмежене сусідство для Markdown-результатів.

## Тестування

Команди перевірки репозиторію:

```bash
uv run pytest -q
uv run python scripts/smoke_test.py
uv run python scripts/benchmark_context_savings.py
```

Поточний стан релізу:

- Hydration-потік Phase 5 покритий тестами та реальним MCP smoke path.
- Benchmark-звіт будується з живого прогону по корпусу репозиторію.
- Runtime contract у `server_info()` і `self_test()` збігається з опублікованою документацією.

## Важливі обмеження

- Проєкт не заявляє прямого контролю над KV-cache hosted-моделей.
- Перший embedding-backed запуск може завантажити `sentence-transformers/all-MiniLM-L6-v2`, якщо локальний кеш холодний.
- Benchmark-звіт вимірює реальну економію для цього корпусу репозиторію, але не дає абсолютної гарантії вартості для будь-якого впровадження.

## Карта репозиторію

- Runtime contract і entry point: [src/turbo_memory_mcp/server.py](/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/server.py)
- Логіка hydration: [src/turbo_memory_mcp/hydration.py](/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/hydration.py)
- Модель зберігання: [src/turbo_memory_mcp/store.py](/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/store.py)
- Технічна специфікація: [TECHNICAL_SPEC.md](/Users/admin/_Projects/turbo_quant_mcp_memory/TECHNICAL_SPEC.md)
- Стратегія пам’яті: [MEMORY_STRATEGY.md](/Users/admin/_Projects/turbo_quant_mcp_memory/MEMORY_STRATEGY.md)
