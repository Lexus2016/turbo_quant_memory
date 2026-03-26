# Turbo Quant Memory for AI Agents

Другие языки: [Английский](README.md) | [Украинский](README.uk.md)

Turbo Quant Memory for AI Agents — это локально-ориентированный stdio MCP-сервер для кодовых агентов, которым нужен меньший, дешевле управляемый и более контролируемый рабочий контекст.

Он индексирует Markdown-знания, хранит типизированные заметки проекта, умеет продвигать действительно переиспользуемые заметки в глобальное пространство имён и возвращает компактные карточки поиска до явного `hydrate(...)`.

## Что делает проект

- Держит память проекта в пространстве имён текущего репозитория.
- Поддерживает отдельное глобальное пространство имён для явно продвинутых переиспользуемых заметок.
- Использует `semantic_search(...)` для компактного retrieval и `hydrate(...)` только когда нужен более полный контекст.
- Экспортирует стабильную MCP-поверхность из 9 инструментов: `health`, `server_info`, `list_scopes`, `self_test`, `remember_note`, `promote_note`, `semantic_search`, `hydrate`, `index_paths`.
- Хранит данные локально в `~/.turbo-quant-memory/`.

## Измеренная экономия

В репозитории лежат реальные результаты измерений в [benchmarks/latest.md](/Users/admin/_Projects/turbo_quant_mcp_memory/benchmarks/latest.md) и [benchmarks/latest.json](/Users/admin/_Projects/turbo_quant_mcp_memory/benchmarks/latest.json).

Снимок измерений от 2026-03-26 на корпусе этого репозитория:

- Размер корпуса: 117 Markdown-файлов, 1015 индексированных блоков
- Полная индексация: 17.11 с
- Пустой incremental index: 2.32 с
- Средняя задержка `semantic_search`: 544.08 мс
- Средняя задержка `hydrate`: 184.49 мс
- Средняя экономия по байтам для `semantic_search` без hydrate: 78.39%
- Средняя экономия по байтам для `semantic_search + hydrate(top1)`: 66.46%
- Средняя экономия по словам для `semantic_search` без hydrate: 83.25%
- Средняя экономия по словам для `semantic_search + hydrate(top1)`: 74.98%

Метод измерения:

- Базовый сценарий без MCP-guidance: открыть полный текст каждого уникального Markdown-файла, который попал в top-5 результатов поиска по проекту.
- Компактный MCP-сценарий: использовать только JSON-ответ `semantic_search`.
- Guided MCP-сценарий: использовать `semantic_search`, а затем `hydrate` только для лучшего Markdown-результата.
- Экономия считается по реальным UTF-8 byte counts и по количеству слов, разделённых пробелами, на корпусе этого репозитория.

Эти цифры реальны для данного корпуса и текущей реализации. Это не универсальное обещание стоимости для любого проекта.

## Установка

Рекомендуемая установка релизной версии из GitHub-тега:

```bash
uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.1.0
turbo-memory-mcp serve
```

Резервный путь через `pip`:

```bash
python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.1.0
turbo-memory-mcp serve
```

Режим разработки из исходников:

```bash
uv sync
uv run turbo-memory-mcp serve
```

Редактируемая `pip`-установка из исходников:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
python -m turbo_memory_mcp serve
```

## Подключение клиентов

Идентификатор сервера:

- `tqmemory`

Команда запуска:

- `turbo-memory-mcp serve`

Быстрые примеры подключения:

- Claude Code: `claude mcp add --scope user tqmemory -- turbo-memory-mcp serve`
- Codex: `codex mcp add tqmemory -- turbo-memory-mcp serve`

Готовые project-конфиги есть для:

- [examples/clients/claude.project.mcp.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/claude.project.mcp.json)
- [examples/clients/codex.config.toml](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/codex.config.toml)
- [examples/clients/cursor.project.mcp.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/cursor.project.mcp.json)
- [examples/clients/opencode.config.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/opencode.config.json)
- [examples/clients/antigravity.mcp.json](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/antigravity.mcp.json)

Чеклист smoke-проверки:

- [examples/clients/SMOKE_CHECKLIST.md](/Users/admin/_Projects/turbo_quant_mcp_memory/examples/clients/SMOKE_CHECKLIST.md)

## Модель пространств имён

- `project`: локальные для репозитория заметки текущей кодовой базы
- `global`: переиспользуемые заметки, явно продвинутые из `project`
- `hybrid`: объединённый поиск по `project` и `global` с сильным приоритетом `project`

Порядок определения текущего проекта:

1. Нормализованный URL `origin`
2. Hash от корневого пути репозитория, если remote отсутствует
3. Явные overrides через `TQMEMORY_PROJECT_ROOT`, `TQMEMORY_PROJECT_ID` и `TQMEMORY_PROJECT_NAME`

## Retrieval Contract

Стандартный цикл работы:

1. `index_paths(...)`
2. `semantic_search(query, scope="hybrid")`
3. `hydrate(item_id, scope, mode="default"|"related")` только когда компактной карточки уже недостаточно

Запись типизированных заметок:

1. `remember_note(..., kind="decision"|"lesson"|"handoff"|"pattern", scope="project")`
2. `promote_note(note_id)` только для действительно переиспользуемых заметок

`semantic_search(...)` возвращает компактные карточки с приоритетом provenance вместо сырых дампов файлов. `hydrate(...)` возвращает полный целевой элемент и ограниченное соседство для Markdown-результатов.

## Тестирование

Команды проверки репозитория:

```bash
uv run pytest -q
uv run python scripts/smoke_test.py
uv run python scripts/benchmark_context_savings.py
```

Текущее состояние релиза:

- Hydration-поток Phase 5 покрыт тестами и реальным MCP smoke path.
- Benchmark-отчёт строится из живого прогона по корпусу репозитория.
- Runtime contract в `server_info()` и `self_test()` совпадает с опубликованной документацией.

## Важные ограничения

- Проект не заявляет прямого контроля над KV-cache hosted-моделей.
- Первый embedding-backed запуск может скачать `sentence-transformers/all-MiniLM-L6-v2`, если локальный кеш холодный.
- Benchmark-отчёт измеряет реальную экономию для этого корпуса репозитория, но не даёт абсолютной гарантии стоимости для любого внедрения.

## Карта репозитория

- Runtime contract и entry point: [src/turbo_memory_mcp/server.py](/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/server.py)
- Логика hydration: [src/turbo_memory_mcp/hydration.py](/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/hydration.py)
- Модель хранения: [src/turbo_memory_mcp/store.py](/Users/admin/_Projects/turbo_quant_mcp_memory/src/turbo_memory_mcp/store.py)
- Техническая спецификация: [TECHNICAL_SPEC.md](/Users/admin/_Projects/turbo_quant_mcp_memory/TECHNICAL_SPEC.md)
- Стратегия памяти: [MEMORY_STRATEGY.md](/Users/admin/_Projects/turbo_quant_mcp_memory/MEMORY_STRATEGY.md)
