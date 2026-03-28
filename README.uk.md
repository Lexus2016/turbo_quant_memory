# Turbo Quant Memory for AI Agents

![Титульна ілюстрація Turbo Quant Memory](assets/readme-hero-uk.svg)

[![Latest release](https://img.shields.io/github/v/release/Lexus2016/turbo_quant_memory?display_name=tag&label=release)](https://github.com/Lexus2016/turbo_quant_memory/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/downloads/)
[![MCP server](https://img.shields.io/badge/MCP-stdio-0A7B83)](https://modelcontextprotocol.io/)
[![Local-first](https://img.shields.io/badge/storage-local--first-2F855A)](https://github.com/Lexus2016/turbo_quant_memory)

Інші мови: [English](README.md) | [Russian](README.ru.md)

Turbo Quant Memory — це local-first шар пам’яті для AI-агентів розробки, таких як Claude Code, Codex, Cursor та інших MCP-клієнтів.

Він допомагає агенту пам’ятати знання проєкту, спочатку шукати короткий контекст і відкривати більше тільки тоді, коли задача справді цього просить.

> Ідея проста: менше повторного читання, більше корисної роботи.

Швидкі посилання: [Що це робить](#що-це-робить) | [Встановлення](#встановлення) | [Підключення клієнта](#підключення-клієнта) | [Бенчмарки](#бенчмарки-з-цього-репозиторію) | [Технічна специфікація](TECHNICAL_SPEC.md) | [Стратегія пам’яті](MEMORY_STRATEGY.md)

## Що Це Робить

| Без шару пам’яті | З Turbo Quant Memory |
|---|---|
| Кожна задача знову відкриває файли й старі чати | Агент може стартувати з уже збережених знань проєкту |
| Рішення губляться в історії чату | Важливі рішення стають пошуковими нотатками |
| Повторне використання між проєктами робиться вручну | Хороші патерни можна промотити в `global` memory |
| Контекстне вікно забивається повторюваним матеріалом | `semantic_search` лишається компактним, а `hydrate` відкриває більше тільки за потреби |

## Як Це Працює

| Крок | Що відбувається |
|---|---|
| 1. Встановити один раз | Ви запускаєте MCP-сервер локально на своїй машині |
| 2. Підключити один клієнт | Claude Code, Codex, Cursor, OpenCode та інші MCP-клієнти можуть працювати з тим самим сервером |
| 3. Працювати нормально | Ви описуєте задачу звичайною мовою, а не shell-командами |
| 4. Спочатку шукати коротко | Агент може викликати `semantic_search` перед відкриттям повних файлів |
| 5. Зберігати важливе | Рішення, уроки, передачі контексту та патерни можна записувати назад у пам’ять |

## Бенчмарки З Цього Репозиторію

У репозиторії є реальний запуск бенчмарку в [benchmarks/latest.md](benchmarks/latest.md) та [benchmarks/latest.json](benchmarks/latest.json).

![Знімок benchmark](benchmarks/summary-uk.svg)

| Метрика | Результат | Що це означає |
|---|---:|---|
| Корпус | 9 файлів · 138 блоків | Це реальні дані репозиторію, а не іграшковий приклад |
| Повна індексація | 4.0 с | Початкове індексування коротке |
| Порожній incremental | 0.68 с | Оновлення після невеликих змін легке |
| Середній `semantic_search` | 75.14 мс | Достатньо швидко для дефолтного використання |
| Середній `hydrate` | 41.71 мс | Відкривати більше контексту теж дешево |
| Економія байтів, лише пошук | 78.02% | До моделі йде значно менше тексту |
| Економія байтів, пошук + hydrate | 63.41% | Навіть шлях із `hydrate` набагато менший за відкриття повних файлів |

Що важливо для людини:

- компактний шлях відчутно легший за наївне читання повних файлів
- навіть після відкриття найкращого збігу шлях із `hydrate` усе ще суттєво економить контекст
- більше контекстного бюджету лишається на міркування, а не на повторне читання

Це реальні вимірювання для цього репозиторію і цієї реалізації. Це не універсальна гарантія для будь-якої кодової бази.

## Встановлення

| Для чого | Команди |
|---|---|
| Release-встановлення через `uv` | `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`<br>`turbo-memory-mcp serve` |
| `pip` fallback | `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`<br>`turbo-memory-mcp serve` |
| Локальна розробка | `uv sync`<br>`uv run turbo-memory-mcp serve` |
| Editable-встановлення з source | `python -m venv .venv`<br>`. .venv/bin/activate`<br>`pip install -e .`<br>`python -m turbo_memory_mcp serve` |

## Підключення Клієнта

Ідентифікатор сервера: `tqmemory`  
Команда запуску: `turbo-memory-mcp serve`

| Клієнт | Швидкий старт | Готовий файл |
|---|---|---|
| Claude Code | `claude mcp add --scope user tqmemory -- turbo-memory-mcp serve` | [examples/clients/claude.project.mcp.json](examples/clients/claude.project.mcp.json) |
| Codex | `codex mcp add tqmemory -- turbo-memory-mcp serve` | [examples/clients/codex.config.toml](examples/clients/codex.config.toml) |
| Cursor | використайте готовий конфіг-файл | [examples/clients/cursor.project.mcp.json](examples/clients/cursor.project.mcp.json) |
| OpenCode | використайте готовий конфіг-файл | [examples/clients/opencode.config.json](examples/clients/opencode.config.json) |
| Antigravity | використайте готовий конфіг-файл | [examples/clients/antigravity.mcp.json](examples/clients/antigravity.mcp.json) |

Smoke checklist: [examples/clients/SMOKE_CHECKLIST.md](examples/clients/SMOKE_CHECKLIST.md)

Після підключення ви просто говорите з агентом нормальною мовою. Якщо пам’ять доречна, агент сам може викликати `tqmemory` у фоні.

## Корисні Запити Для Агента

| Ціль | Що сказати |
|---|---|
| Перший раз у репозиторії | `Проіндексуй цей репозиторій і скажи, яка пам'ять тепер доступна для наступних задач.` |
| Перед зміною коду | `Перш ніж щось змінювати, перевір пам'ять проєкту на попередні рішення щодо auth, sessions і retries, а потім коротко підсумуй головне.` |
| Спочатку знайти правильне джерело | `Знайди в цьому проєкті обробку payment webhook, відкрий найрелевантніший результат із пам'яті й поясни, як зараз працює реалізація.` |
| Зберегти рішення | `Збережи нотатку-рішення з назвою "Webhook retry policy" і коротким підсумком підходу, про який ми щойно домовилися.` |
| Використати знання в інших проєктах | `Якщо ця нотатка корисна і для інших проєктів, промотни її в глобальну пам'ять.` |

## Проста Ментальна Модель

| Інструмент | Людське пояснення |
|---|---|
| `semantic_search` | Спочатку знайти найменший корисний шматок контексту |
| `hydrate` | Відкрити більше тільки тоді, коли це потрібно |
| `remember_note` | Зберегти щось важливе на майбутнє |
| `promote_note` | Повторно використати перевірену нотатку між проєктами |
| `deprecate_note` | Прибрати застаріле знання без втрати історії |

## Коли Знання Застаріло

- Спочатку збережіть нове правильне знання як нову нотатку.
- Потім викличте `deprecate_note` для старої нотатки, якщо вона більше не повинна з’являтися в активному пошуку.
- Якщо для старої нотатки є пряма заміна, передайте id нової нотатки, і стара стане `superseded`, а не просто архівною.

## Технічні Деталі

| Простір | Значення |
|---|---|
| `project` | Локальні для репозиторію нотатки поточної кодової бази |
| `global` | Повторно вживані нотатки, які явно промотовано з `project` |
| `hybrid` | Об’єднаний пошук по `project` і `global` із сильним пріоритетом `project` |

Порядок визначення поточного проєкту:

1. Нормалізований URL `origin`
2. Hash кореневого шляху репозиторію, якщо remote відсутній
3. Явні overrides через `TQMEMORY_PROJECT_ROOT`, `TQMEMORY_PROJECT_ID` і `TQMEMORY_PROJECT_NAME`

| Інструмент | Для чого |
|---|---|
| `health` | Перевірити здоров’я сервера і сховища |
| `server_info` | Подивитися runtime і project info |
| `list_scopes` | Побачити доступні memory scopes |
| `self_test` | Швидко перевірити сервер |
| `remember_note` | Зберегти типізовану нотатку |
| `promote_note` | Перевикористати перевірену нотатку глобально |
| `deprecate_note` | Вивести застаріле знання з активного пошуку |
| `semantic_search` | Дістати компактний контекст |
| `hydrate` | Відкрити більше обраного результату |
| `index_paths` | Проіндексувати markdown roots |

Де лежать дані: `~/.turbo-quant-memory/`

Команди перевірки репозиторію:

```bash
uv run pytest -q
uv run python scripts/smoke_test.py
uv run python scripts/benchmark_context_savings.py
```

## Обмеження І Чесні Застереження

- Проєкт не заявляє прямого контролю над KV-cache хостованих моделей.
- Перший embedding-backed запуск може завантажити `sentence-transformers/all-MiniLM-L6-v2`, якщо локальний кеш холодний.
- Benchmark-звіт вимірює цей репозиторій і цю реалізацію, а не будь-який можливий сценарій використання.

## Карта Репозиторію

- Контракт рантайму і вхід у сервер: [src/turbo_memory_mcp/server.py](src/turbo_memory_mcp/server.py)
- Логіка hydration: [src/turbo_memory_mcp/hydration.py](src/turbo_memory_mcp/hydration.py)
- Модель зберігання: [src/turbo_memory_mcp/store.py](src/turbo_memory_mcp/store.py)
- Скрипт benchmark: [scripts/benchmark_context_savings.py](scripts/benchmark_context_savings.py)
- Технічна специфікація: [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md)
- Стратегія пам’яті: [MEMORY_STRATEGY.md](MEMORY_STRATEGY.md)
