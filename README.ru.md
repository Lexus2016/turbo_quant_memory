# Turbo Quant Memory for AI Agents

![Заглавная иллюстрация Turbo Quant Memory](assets/readme-hero-ru.svg?v=20260328b)

[![Latest release](https://img.shields.io/github/v/release/Lexus2016/turbo_quant_memory?display_name=tag&label=release)](https://github.com/Lexus2016/turbo_quant_memory/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/downloads/)
[![MCP server](https://img.shields.io/badge/MCP-stdio-0A7B83)](https://modelcontextprotocol.io/)
[![Local-first](https://img.shields.io/badge/storage-local--first-2F855A)](https://github.com/Lexus2016/turbo_quant_memory)

Другие языки: [English](README.md) | [Украинский](README.uk.md)

Turbo Quant Memory — это local-first слой памяти для AI-агентов разработки, таких как Claude Code, Codex, Cursor и других MCP-клиентов.

Он помогает агенту помнить знания проекта, сначала искать короткий контекст и открывать больше только тогда, когда задача действительно этого требует.

> Идея простая: меньше повторного чтения, больше полезной работы.

Быстрые ссылки: [Что это делает](#что-это-делает) | [Установка](#установка) | [Подключение клиента](#подключение-клиента) | [Бенчмарки](#бенчмарки-из-этого-репозитория) | [Техническая спецификация](TECHNICAL_SPEC.md) | [Стратегия памяти](MEMORY_STRATEGY.md)

## Что Это Делает

| Без слоя памяти | С Turbo Quant Memory |
|---|---|
| Каждая задача снова открывает файлы и старые чаты | Агент может стартовать из уже сохранённых знаний проекта |
| Решения теряются в истории чата | Важные решения становятся поисковыми заметками |
| Переиспользование между проектами делается вручную | Хорошие паттерны можно продвигать в `global` memory |
| Контекстное окно забивается повторяющимся материалом | `semantic_search` остаётся компактным, а `hydrate` открывает больше только по необходимости |

## Как Это Работает

| Шаг | Что происходит |
|---|---|
| 1. Установить один раз | Вы запускаете MCP-сервер локально на своей машине |
| 2. Подключить один клиент | Claude Code, Codex, Cursor, OpenCode и другие MCP-клиенты могут работать с тем же сервером |
| 3. Работать нормально | Вы описываете задачу обычным языком, а не shell-командами |
| 4. Сначала искать коротко | Агент может вызвать `semantic_search` перед открытием полных файлов |
| 5. Сохранять важное | Решения, уроки, передачи контекста и паттерны можно записывать обратно в память |

## Бенчмарки Из Этого Репозитория

В репозитории есть реальный запуск бенчмарка в [benchmarks/latest.md](benchmarks/latest.md) и [benchmarks/latest.json](benchmarks/latest.json).

![Снимок benchmark](benchmarks/summary-ru.svg?v=20260328b)

| Метрика | Результат | Что это означает |
|---|---:|---|
| Корпус | 9 файлов · 138 блоков | Это реальные данные репозитория, а не игрушечный пример |
| Полная индексация | 4.0 с | Первичная индексация короткая |
| Пустой incremental | 0.68 с | Обновление после небольших изменений лёгкое |
| Средний `semantic_search` | 75.14 мс | Достаточно быстро для дефолтного использования |
| Средний `hydrate` | 41.71 мс | Открывать больше контекста тоже дёшево |
| Экономия байтов, только поиск | 78.02% | В модель уходит заметно меньше текста |
| Экономия байтов, поиск + hydrate | 63.41% | Даже путь с `hydrate` намного меньше, чем открытие полных файлов |

Что важно для человека:

- компактный путь заметно легче наивного чтения полных файлов
- даже после открытия лучшего совпадения путь с `hydrate` всё ещё сильно экономит контекст
- больше контекстного бюджета остаётся на рассуждение, а не на повторное чтение

Это реальные измерения для этого репозитория и этой реализации. Это не универсальная гарантия для любой кодовой базы.

## Установка

| Для чего | Команды |
|---|---|
| Release-установка через `uv` | `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`<br>`turbo-memory-mcp serve` |
| `pip` fallback | `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`<br>`turbo-memory-mcp serve` |
| Локальная разработка | `uv sync`<br>`uv run turbo-memory-mcp serve` |
| Editable-установка из source | `python -m venv .venv`<br>`. .venv/bin/activate`<br>`pip install -e .`<br>`python -m turbo_memory_mcp serve` |

## Подключение Клиента

Идентификатор сервера: `tqmemory`  
Команда запуска: `turbo-memory-mcp serve`

| Клиент | Быстрый старт | Готовый файл |
|---|---|---|
| Claude Code | `claude mcp add --scope user tqmemory -- turbo-memory-mcp serve` | [examples/clients/claude.project.mcp.json](examples/clients/claude.project.mcp.json) |
| Codex | `codex mcp add tqmemory -- turbo-memory-mcp serve` | [examples/clients/codex.config.toml](examples/clients/codex.config.toml) |
| Cursor | используйте готовый конфиг-файл | [examples/clients/cursor.project.mcp.json](examples/clients/cursor.project.mcp.json) |
| OpenCode | используйте готовый конфиг-файл | [examples/clients/opencode.config.json](examples/clients/opencode.config.json) |
| Antigravity | используйте готовый конфиг-файл | [examples/clients/antigravity.mcp.json](examples/clients/antigravity.mcp.json) |

Smoke checklist: [examples/clients/SMOKE_CHECKLIST.md](examples/clients/SMOKE_CHECKLIST.md)

После подключения вы просто говорите с агентом нормальным языком. Если память уместна, агент сам может вызвать `tqmemory` в фоне.

## Полезные Запросы Для Агента

| Цель | Что сказать |
|---|---|
| Первый раз в репозитории | `Проиндексируй этот репозиторий и скажи, какая память теперь доступна для следующих задач.` |
| Перед изменением кода | `Перед тем как что-то менять, проверь память проекта на прошлые решения по auth, sessions и retries, а потом коротко суммируй главное.` |
| Сначала найти правильный источник | `Найди в этом проекте обработку payment webhook, открой самый релевантный результат из памяти и объясни, как сейчас работает реализация.` |
| Сохранить решение | `Сохрани заметку-решение с названием "Webhook retry policy" и коротким итогом подхода, о котором мы только что договорились.` |
| Использовать знания в других проектах | `Если эта заметка полезна и для других проектов, продвинь её в глобальную память.` |

## Простая Ментальная Модель

| Инструмент | Человеческое объяснение |
|---|---|
| `semantic_search` | Сначала найти самый маленький полезный кусок контекста |
| `hydrate` | Открыть больше только тогда, когда это нужно |
| `remember_note` | Сохранить что-то важное на будущее |
| `promote_note` | Переиспользовать проверенную заметку между проектами |
| `deprecate_note` | Убрать устаревшее знание без потери истории |

## Когда Знание Устарело

- Сначала сохраните новую корректную информацию как новую заметку.
- Потом вызовите `deprecate_note` для старой заметки, если она больше не должна появляться в активном поиске.
- Если у старой заметки есть прямая замена, передайте id новой заметки, и старая будет помечена как `superseded`, а не просто архивирована.

## Технические Детали

| Пространство | Значение |
|---|---|
| `project` | Локальные для репозитория заметки текущей кодовой базы |
| `global` | Переиспользуемые заметки, явно продвинутые из `project` |
| `hybrid` | Объединённый поиск по `project` и `global` с сильным приоритетом `project` |

Порядок определения текущего проекта:

1. Нормализованный URL `origin`
2. Hash корневого пути репозитория, если remote отсутствует
3. Явные overrides через `TQMEMORY_PROJECT_ROOT`, `TQMEMORY_PROJECT_ID` и `TQMEMORY_PROJECT_NAME`

| Инструмент | Для чего |
|---|---|
| `health` | Проверить здоровье сервера и хранилища |
| `server_info` | Посмотреть runtime и project info |
| `list_scopes` | Увидеть доступные memory scopes |
| `self_test` | Быстро проверить сервер |
| `remember_note` | Сохранить типизированную заметку |
| `promote_note` | Переиспользовать проверенную заметку глобально |
| `deprecate_note` | Убрать устаревшее знание из активного поиска |
| `semantic_search` | Достать компактный контекст |
| `hydrate` | Открыть больше выбранного результата |
| `index_paths` | Проиндексировать markdown roots |

Где лежат данные: `~/.turbo-quant-memory/`

Команды проверки репозитория:

```bash
uv run pytest -q
uv run python scripts/smoke_test.py
uv run python scripts/benchmark_context_savings.py
```

## Ограничения И Честные Оговорки

- Проект не заявляет прямого контроля над KV-cache хостованных моделей.
- Первый embedding-backed запуск может скачать `sentence-transformers/all-MiniLM-L6-v2`, если локальный кеш холодный.
- Benchmark-отчёт измеряет этот репозиторий и эту реализацию, а не любой возможный сценарий внедрения.

## Карта Репозитория

- Контракт рантайма и вход в сервер: [src/turbo_memory_mcp/server.py](src/turbo_memory_mcp/server.py)
- Логика hydration: [src/turbo_memory_mcp/hydration.py](src/turbo_memory_mcp/hydration.py)
- Модель хранения: [src/turbo_memory_mcp/store.py](src/turbo_memory_mcp/store.py)
- Скрипт benchmark: [scripts/benchmark_context_savings.py](scripts/benchmark_context_savings.py)
- Техническая спецификация: [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md)
- Стратегия памяти: [MEMORY_STRATEGY.md](MEMORY_STRATEGY.md)
