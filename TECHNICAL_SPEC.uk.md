# Технічна Специфікація

Інші мови: [English](TECHNICAL_SPEC.md) | [Russian](TECHNICAL_SPEC.ru.md)

## Мета Продукту

Побудувати local-first MCP-сервер, який працює як практична довготривала пам'ять для AI coding-агентів.

Сервер має зменшувати повторні витрати токенів за рахунок того, що:

- виносить "холодний" контекст з активного prompt
- спочатку повертає компактний контекст з посиланням на джерело
- відкриває повніші деталі тільки через явний hydration-запит

## Межі Продукту

| Що проєкт робить | Чого проєкт не робить |
|---|---|
| тримає знання проєкту доступними для пошуку на боці MCP | не керує KV-cache хостованих моделей |
| локально зберігає нотатки та проіндексований Markdown | не замінює внутрішню пам'ять моделі |
| повертає компактні картки retrieval з provenance | не обіцяє однакову економію для будь-якого репозиторію |
| підтримує project і cross-project recall | не намагається вирішити всі задачі лише стисканням |

## Цільові Користувачі

- Інженери, які працюють у реальних репозиторіях через Claude Code, Codex, Cursor, OpenCode та подібні MCP-клієнти.
- Команди, яким потрібне локальне розгортання з низьким операційним оверхедом.
- Agent-heavy workflow-и, які багаторазово повертаються до docs, notes, ADR та Markdown knowledge base.

## Базові Принципи

| Принцип | Значення |
|---|---|
| Local-first | Базовий memory loop має працювати на машині розробника |
| Markdown-first | Human-readable Markdown лишається джерелом істини |
| Compact-first retrieval | Спочатку повертається найменша корисна відповідь |
| Hydration on demand | Більші payload-и потрібно запитувати явно |
| Traceability always | Кожен результат має вказувати на джерело |
| Easy setup | Інсталяція і підключення мають займати хвилини, а не години |

## Технічний Стек

| Напрям | Вибір |
|---|---|
| Мова | Python 3.11+ |
| MCP-фреймворк | офіційний MCP Python SDK |
| Сховище | локальна файлова система плюс embedded LanceDB |
| Embeddings | Sentence Transformers з легкою локальною моделлю |
| Конфіг | environment variables плюс typed settings |
| Пакування | `uv` як основний шлях, `pip` як fallback |

## Функціональний Обсяг

### 1. Завантаження Джерел

- Індексувати один або кілька Markdown-root.
- Ділити контент за heading-структурою з детермінованими fallback-правилами.
- Зберігати source metadata: шлях, heading path, timestamps, tags, checksum і block identity.
- Підтримувати incremental re-index тільки для зміненого контенту.

### 2. Пошук

- Приймати довільні текстові запити через `semantic_search(...)`.
- Повертати компактні картки результатів замість сирих дампів повних файлів.
- Тримати видимими provenance, релевантність, confidence і key points.
- Явно попереджати про low-confidence або ambiguous retrieval.

### 3. Hydration

- Відкривати повніший excerpt тільки тоді, коли компактного retrieval вже недостатньо.
- Підтримувати обмежене локальне оточення навколо обраного hit.
- Зберігати передбачуваний token budget завдяки явним hydration-викликам.

### 4. Запис Пам'яті

- Зберігати рішення, уроки, handoff-и та reusable patterns.
- Писати нотатки з типом, тегами, timestamps і source refs.
- Дозволяти явну промоцію з `project` у `global`.
- Дозволяти deprecate-ити застарілі нотатки без втрати історії.

### 5. Експлуатація

- Показувати health і runtime metadata.
- Показувати лічильники сховища й freshness індексу.
- Давати швидкий self-test контракт.
- Тримати smoke-test інструкції для підтримуваних клієнтів.

### 6. Гігієна Бази Знань

- Запускати структурні lint-перевірки Markdown-корпусу.
- Виявляти биті внутрішні Markdown-посилання.
- Виявляти orphan candidates без вхідних і вихідних internal links.
- Виявляти дублікати нормалізованих заголовків, які підвищують неоднозначність retrieval.

### 7. Зв'язки графа знань (Knowledge Graph Relations)

- Зв'язувати нотатки, файли, завдання чи баги за допомогою кастомних типів зв'язків.
- Запитувати зв'язки для навігації по мережі знань.
- Автоматично збагачувати результати семантичного пошуку пов'язаними сутностями для кращого контексту.

## Набір MCP-Інструментів

| Інструмент | Для чого |
|---|---|
| `health()` | базова перевірка стану |
| `server_info()` | runtime, project, storage та install contract |
| `list_scopes()` | доступні scopes і режими за замовчуванням |
| `self_test()` | швидка перевірка контракту |
| `remember_note(...)` | зберегти типізовану нотатку |
| `promote_note(note_id)` | скопіювати project-note в reusable global memory |
| `deprecate_note(...)` | вивести застаріле знання з активного обігу |
| `semantic_search(...)` | дістати компактний контекст |
| `hydrate(...)` | відкрити обмежений повніший контекст |
| `index_paths(...)` | індексувати або оновити Markdown-root |
| `lint_knowledge_base(...)` | запускати структурну перевірку цілісності посилань і узгодженості wiki |
| `link_entities(...)` | створити зв'язок між двома сутностями знань у графі |
| `unlink_entities(...)` | видалити зв'язок між двома сутностями знань у графі |
| `get_related_entities(...)` | отримати зв'язки для конкретної сутності за її URI |
| `set_secret(name, value)` | зберегти зашифрований секрет у vault'і активного проєкту |
| `get_secret(name)` | дістати секрет за точним іменем; значення повертається у виділеному полі `secret_value` |
| `list_secrets()` | список імен секретів активного проєкту; значення ніколи не повертаються |
| `delete_secret(name)` | видалити секрет за точним іменем |

## Модель Даних

### Markdown-блок

| Поле | Значення |
|---|---|
| `block_id` | стабільний ідентифікатор блока |
| `file_path` | шлях до вихідного Markdown |
| `heading_path` | ієрархія heading-ів |
| `content_raw` | повний вихідний текст |
| `content_compressed` | компактне представлення для retrieval |
| `embedding` | вектор для пошуку |
| `checksum` | виявлення змін |
| `created_at` / `updated_at` | часові metadata |
| `tags` | необов'язкові ярлики |
| `source_kind` | `markdown` |

### Нотатка Пам'яті

| Поле | Значення |
|---|---|
| `note_id` | ідентифікатор нотатки |
| `title` | назва |
| `content` | повний текст |
| `note_kind` | `decision`, `lesson`, `handoff` або `pattern` |
| `summary` | коротке представлення для retrieval |
| `tags` | необов'язкові ярлики |
| `session_id` | прив'язка до сесії, коли це релевантно |
| `project_id` | власник-проєкт |
| `created_at` | час створення |
| `source_refs` | provenance-посилання |

### Сховище секретів (project-scope, зашифроване)

Per-project зашифроване сховище, тримається повністю окремо від нотаток і markdown. Живе під `<storage_root>/projects/<project_id>/secrets/`; ніколи не читається `semantic_search`, `hydrate` або `lint_knowledge_base`.

| Файл | Значення |
|---|---|
| `vault.tqv` | AES-256-GCM blob із JSON `{version, entries: {name: {value, created_at, updated_at}}}`. 12-байтний random nonce на запис, 16-байтний GCM tag. Mode `0o600`. |
| `meta.json` | `{version, kdf, kdf_params, key_mode, vault_initialized, created_at, updated_at}`. KDF-параметри і key-resolution mode для діагностики; жодного key-матеріалу. Mode `0o600`. |
| `audit.jsonl` | Append-only audit-log. Один JSON-рядок на доступ: `{ts, action ∈ {set,get,list,delete}, name}`. `project_id` неявний з шляху; значення не логуються. Mode `0o600`. |

Subsystem-marker `<storage_root>/secrets-manifest.json` відстежує SECRETS migration chain (`format_version`); жодного секретного вмісту.

Майстер-ключ per project: 32 байти, розв'язується at call-time у пріоритеті
(1) env `TQMEMORY_SECRETS_PASSPHRASE` (Argon2id, project-specific salt
`sha256("tqv-salt-v1:" + project_id)`); (2) існуючий keyring entry
`service=turbo-quant-memory, account=secrets-master-<project_id>`;
(3) keyring auto-bootstrap (генерувати + покласти), якщо backend writable;
(4) hard fail зі setup-hint. Інтерактивного prompt-fallback немає — він
тихо вмирав би на reboot.

## Цільові Показники Продуктивності

- локальний старт має бути комфортним на ноутбуці розробника
- перша індексація має вкладатися в нормальний інтерактивний setup
- latency пошуку має залишатися інтерактивною для малих і середніх corpus
- recall-heavy workflow-и мають зазвичай економити значну частину контексту відносно naive full-file opening

## Безпека І Довіра

- Індексувати тільки явно вибрані шляхи.
- За замовчуванням обмежувати розмір output.
- Зберігати межі джерела й provenance.
- Сприймати notes і retrieval як tool data, а не як абсолютний авторитет.
- Уникати прихованих зовнішніх мережевих залежностей для core local flow — `src/` містить **нуль outbound-HTTP коду** (жодних `requests` / `httpx` / `aiohttp` / `urllib.request` / `urlopen` / raw `socket`).

### Threat-модель сховища секретів

У скоупі (від чого секретний vault зобов'язаний захистити):
- Випадкові бекапи з plaintext credentials (Time Machine, rsync, iCloud-sync home-директорії).
- Share-screen / скриншот-витоки збереженої credential на екрані.
- Випадковий `git add` файла з credentials під `~/`.

Поза скоупом (vault НЕ захищає; для ширшої threat-моделі — дедікований secret-manager):
- Компрометований root-користувач на локальній машині.
- Live-атакер, який вже захопив запущений daemon-процес.
- Same-user IPC-канал демона. `multiprocessing`-сокет демона — `0600`, захищений 32-байтним authkey у `0600`-lockfile, а `TQMEMORY_SECRETS_PASSPHRASE` форвардиться в primary на кожному RPC через цей pickle-канал. Same-user атакер, здатний прочитати authkey з lockfile, може впровадити pickle-payload (RCE) у primary і спостерігати passphrase. Прийнято в межах same-user моделі: передбачає атакера, що вже працює під тим самим користувачем, — сильніша позиція, яку поглинає рядок про захоплення daemon вище.
- Hardware-атаки (cold-boot, evil-maid, hardware key extraction).
- Будь-що, що вимагає multi-tenant ізоляції або compliance-сертифікацій.

Точки enforcement:
- AES-256-GCM at-rest з per-project майстер-ключами; nonce на кожен запис; MAC-failure піднімає `cryptography.exceptions.InvalidTag`.
- Indexer (`ingestion._resolve_roots`) і linter (`knowledge_lint._resolve_roots`) відмовляють у реєстрації будь-якого шляху всередині `<storage_root>/projects/<project_id>/secrets/`. Обидва `_iter_markdown_files` walkers пропускають файли під цим піддеревом як defense-in-depth.
- MCP-відповіді `set_secret` / `get_secret` / `list_secrets` / `delete_secret` тримають значення секретів виключно у виділеному полі `secret_value` на `get_secret` — ніколи не вшиваючи у описові `summary` / `message` поля.
- Audit-log пише доступ як `(timestamp, action, name)`, ніколи значення; sentinel-grep regression-тест перевіряє цей інваріант.

## Контракт Розгортання

| Крок | Очікуваний контракт |
|---|---|
| Рекомендоване встановлення | `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.4` |
| Fallback-встановлення | `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.4` |
| Команда запуску | `turbo-memory-mcp serve` |
| Приклад для Claude Code | `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve` |
| Еквівалентні приклади | у репозиторії є готові конфіги для Codex, Cursor, OpenCode та Antigravity |

## Стратегія Тестування

| Рівень | Покриття |
|---|---|
| Unit-тести | chunking, IDs, payload contracts, provenance mapping |
| Integration-тести | flow index -> search -> hydrate, note write-back і knowledge-base lint |
| Smoke-тести | чиста інсталяція, підключення клієнта, індексація, retrieval, hydration |
| Benchmarking | repository-level звіт про економію контексту з реальними вимірюваннями |

## Критерії Приймання

1. Новий користувач може встановити пакет і підключити його до підтримуваного клієнта за кілька хвилин.
2. Сервер може індексувати Markdown-файли і шукати їх семантично.
3. Дефолтний retrieval повертає компактний контекст із джерелом, а не сирі дампи.
4. Агенти можуть явно hydrate-ити повніший контекст за потреби.
5. Нотатки можна зберігати, промотувати, deprecate-ити і знаходити пізніше.
6. Оператор може швидко перевірити health, freshness і стан storage.
7. Оператор може lint-ити Markdown knowledge base на биті посилання, orphan candidates і duplicate titles.

## Нецілі

- замінити внутрішні механізми пам'яті самої моделі
- заявляти прямий контроль над токен-квантуванням чи hosted KV-cache
- будувати enterprise multi-tenant governance у поточному scope
- вирішувати всі задачі reasoning лише через стискання

## Підсумок

Turbo Quant Memory - це практичний MCP memory layer:

- local-first
- компактний за замовчуванням
- простежуваний у кожному retrieval
- явний у момент відкриття повнішого контексту
- простий в інсталяції та експлуатації у звичайному workflow розробника
