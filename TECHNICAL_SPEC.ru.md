# Техническая Спецификация

Другие языки: [English](TECHNICAL_SPEC.md) | [Ukrainian](TECHNICAL_SPEC.uk.md)

## Цель Продукта

Построить local-first MCP-сервер, который работает как практическая долговременная память для AI coding-агентов.

Сервер должен снижать повторные расходы токенов за счёт того, что:

- выносит "холодный" контекст из активного prompt
- сначала возвращает компактный контекст с указанием источника
- открывает более полный контекст только через явный hydration-запрос

## Границы Продукта

| Что проект делает | Чего проект не делает |
|---|---|
| держит знания проекта доступными для поиска на стороне MCP | не управляет KV-cache хостованных моделей |
| локально хранит заметки и проиндексированный Markdown | не заменяет внутреннюю память модели |
| возвращает компактные карточки retrieval с provenance | не обещает одинаковую экономию для любого репозитория |
| поддерживает project и cross-project recall | не пытается решать все задачи только за счёт сжатия |

## Целевые Пользователи

- Инженеры, работающие в реальных репозиториях через Claude Code, Codex, Cursor, OpenCode и похожие MCP-клиенты.
- Команды, которым нужно локальное развёртывание с низким операционным overhead.
- Agent-heavy workflow-и, которые постоянно возвращаются к docs, notes, ADR и Markdown knowledge base.

## Базовые Принципы

| Принцип | Значение |
|---|---|
| Local-first | Базовый memory loop должен работать на машине разработчика |
| Markdown-first | Human-readable Markdown остаётся источником истины |
| Compact-first retrieval | Сначала возвращается минимально полезный ответ |
| Hydration on demand | Более крупные payload-ы должны запрашиваться явно |
| Traceability always | Каждый результат должен указывать на источник |
| Easy setup | Установка и подключение должны занимать минуты, а не часы |

## Технический Стек

| Направление | Выбор |
|---|---|
| Язык | Python 3.11+ |
| MCP-фреймворк | официальный MCP Python SDK |
| Хранилище | локальная файловая система плюс embedded LanceDB |
| Embeddings | Sentence Transformers с лёгкой локальной моделью |
| Конфиг | environment variables плюс typed settings |
| Пакетирование | `uv` как основной путь, `pip` как fallback |

## Функциональный Объём

### 1. Загрузка Источников

- Индексировать один или несколько Markdown-root.
- Делить контент по heading-структуре с детерминированными fallback-правилами.
- Сохранять source metadata: путь, heading path, timestamps, tags, checksum и block identity.
- Поддерживать incremental re-index только для изменённого контента.

### 2. Поиск

- Принимать свободные текстовые запросы через `semantic_search(...)`.
- Возвращать компактные карточки результатов вместо сырых дампов полных файлов.
- Сохранять видимыми provenance, релевантность, confidence и key points.
- Явно предупреждать о low-confidence или ambiguous retrieval.

### 3. Hydration

- Открывать более полный excerpt только тогда, когда компактного retrieval уже недостаточно.
- Поддерживать ограниченное локальное окружение вокруг выбранного hit.
- Держать предсказуемый token budget за счёт явных hydration-вызовов.

### 4. Запись Памяти

- Сохранять решения, уроки, handoff-и и reusable patterns.
- Писать заметки с типом, тегами, timestamps и source refs.
- Разрешать явную promotion из `project` в `global`.
- Разрешать deprecate-ить устаревшие заметки без потери истории.

### 5. Эксплуатация

- Показывать health и runtime metadata.
- Показывать счётчики хранилища и freshness индекса.
- Давать быстрый self-test контракт.
- Держать smoke-test инструкции для поддерживаемых клиентов.

### 6. Гигиена Базы Знаний

- Запускать структурные lint-проверки Markdown-корпуса.
- Выявлять битые внутренние Markdown-ссылки.
- Выявлять orphan candidates без входящих и исходящих internal links.
- Выявлять дубликаты нормализованных заголовков, повышающие неоднозначность retrieval.

### 7. Связи графа знаний (Knowledge Graph Relations)

- Связывать заметки, файлы, задачи или баги с помощью кастомных типов связей.
- Запрашивать связи для навигации по сети знаний.
- Автоматически обогащать результаты семантического поиска связанными сущностями для лучшего контекста.

## Набор MCP-Инструментов

| Инструмент | Для чего |
|---|---|
| `health()` | базовая проверка состояния |
| `server_info()` | runtime, project, storage и install contract |
| `list_scopes()` | доступные scopes и режимы по умолчанию |
| `self_test()` | быстрая проверка контракта |
| `remember_note(...)` | сохранить типизированную заметку |
| `promote_note(note_id)` | скопировать project-note в reusable global memory |
| `deprecate_note(...)` | вывести устаревшее знание из активного обращения |
| `semantic_search(...)` | достать компактный контекст |
| `hydrate(...)` | открыть ограниченный более полный контекст |
| `index_paths(...)` | индексировать или обновить Markdown-root |
| `lint_knowledge_base(...)` | запускать структурную проверку целостности ссылок и согласованности wiki |
| `link_entities(...)` | создать связь между двумя сущностями знаний в графе |
| `unlink_entities(...)` | удалить связь между двумя сущностями знаний в графе |
| `get_related_entities(...)` | получить связи для конкретной сущности по её URI |
| `set_secret(name, value)` | сохранить зашифрованный секрет в vault'е активного проекта |
| `get_secret(name)` | достать секрет по точному имени; значение возвращается в выделенном поле `secret_value` |
| `list_secrets()` | список имён секретов активного проекта; значения никогда не возвращаются |
| `delete_secret(name)` | удалить секрет по точному имени |

## Модель Данных

### Markdown-блок

| Поле | Значение |
|---|---|
| `block_id` | стабильный идентификатор блока |
| `file_path` | путь к исходному Markdown |
| `heading_path` | иерархия heading-ов |
| `content_raw` | полный исходный текст |
| `content_compressed` | компактное представление для retrieval |
| `embedding` | вектор для поиска |
| `checksum` | обнаружение изменений |
| `created_at` / `updated_at` | временные metadata |
| `tags` | необязательные ярлыки |
| `source_kind` | `markdown` |

### Заметка Памяти

| Поле | Значение |
|---|---|
| `note_id` | идентификатор заметки |
| `title` | заголовок |
| `content` | полный текст |
| `note_kind` | `decision`, `lesson`, `handoff` или `pattern` |
| `summary` | краткое представление для retrieval |
| `tags` | необязательные ярлыки |
| `session_id` | привязка к сессии, когда это релевантно |
| `project_id` | владелец-проект |
| `created_at` | время создания |
| `source_refs` | provenance-ссылки |

### Хранилище секретов (project-scope, зашифрованное)

Per-project зашифрованное хранилище, держится полностью отдельно от заметок и markdown. Живёт под `<storage_root>/projects/<project_id>/secrets/`; никогда не читается `semantic_search`, `hydrate` или `lint_knowledge_base`.

| Файл | Значение |
|---|---|
| `vault.tqv` | AES-256-GCM blob с JSON `{version, entries: {name: {value, created_at, updated_at}}}`. 12-байтный random nonce на запись, 16-байтный GCM tag. Mode `0o600`. |
| `meta.json` | `{version, kdf, kdf_params, key_mode, vault_initialized, created_at, updated_at}`. KDF-параметры и key-resolution mode для диагностики; никакого key-материала. Mode `0o600`. |
| `audit.jsonl` | Append-only audit-log. Одна JSON-строка на доступ: `{ts, action ∈ {set,get,list,delete}, name}`. `project_id` неявный из пути; значения не логируются. Mode `0o600`. |

Subsystem-marker `<storage_root>/secrets-manifest.json` отслеживает SECRETS migration chain (`format_version`); никакого секретного содержимого.

Мастер-ключ per project: 32 байта, разрешается at call-time в приоритете
(1) env `TQMEMORY_SECRETS_PASSPHRASE` (Argon2id, project-specific salt
`sha256("tqv-salt-v1:" + project_id)`); (2) существующий keyring entry
`service=turbo-quant-memory, account=secrets-master-<project_id>`;
(3) keyring auto-bootstrap (сгенерировать + положить), если backend writable;
(4) hard fail с setup-hint. Интерактивного prompt-fallback нет — он
бы тихо умирал на reboot.

## Целевые Показатели Производительности

- локальный старт должен быть комфортным на ноутбуке разработчика
- первая индексация должна укладываться в нормальный интерактивный setup
- latency поиска должна оставаться интерактивной для малых и средних corpus
- recall-heavy workflow-и обычно должны экономить значительную часть контекста по сравнению с naive full-file opening

## Безопасность И Доверие

- Индексировать только явно выбранные пути.
- По умолчанию ограничивать размер output.
- Сохранять границы источника и provenance.
- Рассматривать notes и retrieval как tool data, а не как абсолютный авторитет.
- Избегать скрытых внешних сетевых зависимостей для core local flow — `src/` содержит **ноль outbound-HTTP кода** (никаких `requests` / `httpx` / `aiohttp` / `urllib.request` / `urlopen` / raw `socket`).

### Threat-модель хранилища секретов

В скоупе (от чего секретный vault обязан защитить):
- Случайные бэкапы с plaintext credentials (Time Machine, rsync, iCloud-sync home-директории).
- Share-screen / скриншот-утечки сохранённой credential на экране.
- Случайный `git add` файла с credentials под `~/`.

Вне скоупа (vault НЕ защищает; для более широкой threat-модели — дедикированный secret-manager):
- Скомпрометированный root-пользователь на локальной машине.
- Live-атакующий, уже захвативший запущенный daemon-процесс.
- Hardware-атаки (cold-boot, evil-maid, hardware key extraction).
- Что-либо требующее multi-tenant изоляции или compliance-сертификаций.

Точки enforcement:
- AES-256-GCM at-rest с per-project мастер-ключами; nonce на каждую запись; MAC-failure поднимает `cryptography.exceptions.InvalidTag`.
- Indexer (`ingestion._resolve_roots`) и linter (`knowledge_lint._resolve_roots`) отказывают в регистрации любого пути внутри `<storage_root>/projects/<project_id>/secrets/`. Оба `_iter_markdown_files` walkers пропускают файлы под этим поддеревом как defense-in-depth.
- MCP-ответы `set_secret` / `get_secret` / `list_secrets` / `delete_secret` держат значения секретов исключительно в выделенном поле `secret_value` на `get_secret` — никогда не вшивая в описательные `summary` / `message` поля.
- Audit-log пишет доступ как `(timestamp, action, name)`, никогда значение; sentinel-grep regression-тест проверяет этот инвариант.

## Контракт Развёртывания

| Шаг | Ожидаемый контракт |
|---|---|
| Рекомендуемая установка | `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.4` |
| Fallback-установка | `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.4` |
| Команда запуска | `turbo-memory-mcp serve` |
| Пример для Claude Code | `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve` |
| Эквивалентные примеры | в репозитории есть готовые конфиги для Codex, Cursor, OpenCode и Antigravity |

## Стратегия Тестирования

| Уровень | Покрытие |
|---|---|
| Unit-тесты | chunking, IDs, payload contracts, provenance mapping |
| Integration-тесты | flow index -> search -> hydrate, note write-back и knowledge-base lint |
| Smoke-тесты | чистая установка, подключение клиента, индексация, retrieval, hydration |
| Benchmarking | repository-level отчёт об экономии контекста с реальными измерениями |

## Критерии Приёмки

1. Новый пользователь может установить пакет и подключить его к поддерживаемому клиенту за несколько минут.
2. Сервер может индексировать Markdown-файлы и искать их семантически.
3. Стандартный retrieval возвращает компактный контекст с источником, а не сырые дампы.
4. Агенты могут явно hydrate-ить более полный контекст при необходимости.
5. Заметки можно сохранять, продвигать, deprecate-ить и находить позже.
6. Оператор может быстро проверить health, freshness и состояние storage.
7. Оператор может lint-ить Markdown knowledge base на битые ссылки, orphan candidates и duplicate titles.

## Не-Цели

- заменить внутренние механизмы памяти самой модели
- заявлять прямой контроль над квантованием токенов или hosted KV-cache
- строить enterprise multi-tenant governance в текущем scope
- решать все задачи reasoning только через сжатие

## Итог

Turbo Quant Memory - это практический MCP memory layer:

- local-first
- компактный по умолчанию
- прослеживаемый в каждом retrieval
- явный в момент открытия более полного контекста
- простой в установке и эксплуатации в обычном workflow разработчика
