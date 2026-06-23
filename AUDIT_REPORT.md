# Audit Report: turbo-memory-mcp v0.15.0

**Дата аудиту:** 2026-06-09
**Обсяг:** повний статичний рев'ю основних модулів (`src/turbo_memory_mcp/**`), секретів, міграцій, daemon-транспорту.

## Executive Summary

**Загальна оцінка: 7.5/10.** Зріло спроєктований проєкт: атомарні записи JSON,
продумана міграційна система зі снапшотами, чесна криптографія без
самодіяльності (AES-256-GCM + Argon2id), задокументовані "DEFECT A–E"
у коментарях, 35 тестових файлів. Критичних проблем рівня "втрата даних
у штатному режимі" не знайдено. Але є 1 серйозна гонка в daemon-бутстрапі,
1 ризик повного зависання сервера та кілька сценаріїв, де один пошкоджений
файл ламає кілька MCP-інструментів одразу.

- Критичних: 0
- Високих: 3
- Середніх: 7
- Рекомендацій: 6

## Статус виконання (оновлено 2026-06-22)

Звіт складено 2026-06-09 як статичний рев'ю. Станом на 2026-06-22 частину
знахідок усунено комітами, а три середні — перекласифіковано після
верифікації коду (читання фактичних шляхів виконання, а не лише сигнатур).
Описи проблем нижче лишаю без змін як історичний запис; цей блок — поточна
істина щодо статусу.

**Усунено (підтверджено комітами):**
- **H1** split-brain primary — `43cacb1` (retry-ping перед evict), `7976b9a`
  (bound connect), `af96012` (bind listener до startup-міграції).
- **H2** git без timeout — `fd23534` (`timeout` + path-фолбек).
- **H3** один битий note JSON — `287a200` (quarantine замість винятку).
- **M2** тихе ковтання помилок retrieval — `e92da32`, `c347272`, `1746c6d`
  (видимі stderr-логи на кожному фолбеку/повному re-sync).
- **M3** не-UTF-8 / гігантський .md — `e92da32` (resilient indexing).
- **M4 (лише спостережуваність)** — `c347272`, `1746c6d` логують повні
  re-sync; власне усунення вартості (батчинг / diff-sync) відкладено.
- **M7** задокументовано в TECHNICAL_SPEC (EN/RU/UK): same-user daemon IPC /
  passphrase-in-every-RPC / pickle-RCE компроміс додано до "Out of scope"
  threat model. Код не змінюється (свідомий same-user компроміс).
- **M5** усунено зворотно-сумісно: нові env-vault отримують випадковий
  32-байтний salt у `meta.json`; старі vault (meta без `salt`) лишаються на
  детермінованому ключі й не перешифровуються — жоден існуючий секрет не
  втрачає доступ. Keyring-vault не зачеплені. Без міграції. Тести:
  `tests/test_secrets_salt.py`.

**Відкрито:** M1 (звужено), M6 (перекласифіковано).

### Корекції за результатами верифікації коду (2026-06-22)

- **M6 — спростовано як ризик пам'яті.** Ключ `_load_storage_snapshot`
  включає три `manifest_mtime_ns` (`server.py:1939-1941`), тож будь-який запис
  змінює mtime → новий ключ → свіжі дані: **staleness-багу немає**. А в кеші
  лежать **агреговані лічильники (~15 скалярів)**, а НЕ повні JSON нотаток —
  отже ні витоку, ні зростання пам'яті. Заголовок і "помітна резидентна
  пам'ять" у тілі M6 — неточні. Лишається щонайбільше дрібний CPU на перерахунок
  при cache miss, на не-гарячому шляху (`server_info`/snapshot). **Знято з
  runtime-ризиків.**
- **M1 — звужено до vault↔CLI.** `relations.json` пишеться лише через daemon →
  dispatch RLock серіалізує → гонитви немає. Реальний вектор lost-update —
  окремий процес `turbo-memory-mcp secret-set` (`cli.py: _handle_secret_set`),
  що пише `vault.tqv` ПОЗА daemon RLock одночасно з `set_secret` у daemon.
  `_atomic_write_bytes` (temp+rename) гарантує відсутність часткового файлу,
  але не захищає послідовність read→modify→write між двома процесами. `flock`
  доречний саме для vault; це defense-in-depth, низька частота.
- **M4 — перекласифіковано: рідкісний дорогий фолбек, а не вартість на кожному
  записі.** Усі мутації (`remember`→`_sync_project_note_change`,
  `deprecate`→`_remove_retrieval_note`, delete) синхронізують індекс
  інкрементально, тож лічильники тримаються консистентними у штатному режимі.
  `_repair_*_retrieval_if_needed` спрацьовує лише при справжньому дрейфі
  (часткова синхронізація, ручне редагування файлів, quarantine з H3, краш
  посеред sync). Тобто повний `O(corpus)` re-embed під RLock — це рідкісна,
  але дорога подія, НЕ "хвилини CPU посеред кожного `remember_note`". Containment
  (батч + перенесення у фон + rate-limit фолбеку) безпечний, бо re-sync лише
  ВІДНОВЛЮЄ те, що інкрементальні шляхи вже роблять коректно. **Обов'язкова
  передумова: відтворюючий тест на дрейф перед зміною логіки запобіжника.**
  **УСУНЕНО (diff-based repair):** `_repair_*_retrieval_if_needed` тепер
  звіряє множини id (`existing_item_ids` vs notes∪blocks) і синхронізує лише
  дельту — видаляє зайве через `delete_items`, ре-ембедить лише відсутнє через
  `sync_*_notes`/`sync_project_blocks`. Повний `O(corpus)` re-embed на дрейф
  прибрано; новий шлях ще й коректніший (ловить розбіжність id за однакового
  count). Тести: `tests/test_index_drift_repair.py`. Залишок (низький):
  батчинг `_merge_scope_rows` при ПОВНОМУ rebuild (format-міграція / initial /
  after-error) — рідкісний шлях, мікрооптимізація, відкладено свідомо.

## High Priority

### H1. Split-brain гонка при захопленні ролі primary
**Файли:** `daemon.py: acquire_daemon_role()`, `make_primary_endpoint()`; `server.py: _run_primary()`

Між моментом, коли процес A захоплює lockfile (`_try_claim_lockfile`), і моментом,
коли він реально запускає listener (це відбувається пізніше, у
`server.py:_run_primary → listener.start()`), є вікно. Процес B у цьому вікні:
читає lockfile A → ping провалюється (сокет ще не слухає) → B unlink-ає lockfile
живого A, а `make_primary_endpoint()` додатково unlink-ає детермінований шлях
сокета (`/tmp/tqm-<hash>.sock`), який A от-от створить або вже створив.

**Наслідок:** два процеси вважають себе primary, обидва тримають LanceDB-хендли —
інваріант single-writer зламано, можливе пошкодження retrieval-індексу.

**Фікс:**
1. Перед unlink стороннього lockfile — 2–3 ретраї ping із backoff ~100–300 мс
   (новий primary міг просто не встигнути підняти listener).
2. Не видаляти socket-файл у `make_primary_endpoint`, якщо lockfile існує і PID живий.
3. В ідеалі — bind listener ДО запису lockfile.

### H2. `subprocess git` без timeout блокує весь сервер
**Файл:** `identity.py: _run_git_command()`

`build_runtime_context()` викликається на кожен tool call і породжує два
git-сабпроцеси (`rev-parse --show-toplevel`, `remote get-url origin`). Якщо git
зависає (NFS/мережевий диск, зависле fsmonitor, credential helper), сабпроцес
висить вічно — а оскільки весь dispatch серіалізовано одним RLock, зависають
УСІ клієнти всіх проєктів, що сидять на цьому daemon.

**Фікс:** додати `timeout=3` у `subprocess.run(...)` з фолбеком на path-identity;
закешувати результат за `cwd` хоча б на кілька секунд (це ще й прибере 2 fork-и
на кожен виклик).

### H3. Один пошкоджений note JSON ламає search / recent_context / sync
**Файли:** `store.py: list_notes()`, `_normalize_note_record()`, `_read_json()`;
`retrieval.py: _updated_epoch()`

`_read_json` кидає `JSONDecodeError` на битому JSON-файлі, а
`normalize_note_status()` кидає `ValueError` на невідомому статусі.
`list_notes()` ітерує всі файли без ізоляції помилок, і його викликають
`recent_context`, `_ensure_scope_synced`, `_repair_project_retrieval_if_needed`
тощо. Один частково записаний / відредагований руками файл нотатки ламає
одразу кілька інструментів без зрозумілої діагностики. Та сама родина:
`_updated_epoch()` впаде на некоректному `updated_at` з рядка індексу.

**Фікс:** skip-with-warning для нечитабельних нотаток ("quarantine"),
список карантинних файлів показувати в `server_info`.

## Medium Priority

### M1. Read-modify-write без блокування для `relations.json` і `vault.tqv`
**Файли:** `store.py: add_relation()/remove_relation()`; `secrets/store.py: set()/delete()`

У режимі daemon усе серіалізує dispatch lock, але зі `TQMEMORY_DAEMON_DISABLE=1`
(або під час split-brain з H1) два процеси загублять оновлення один одного.
Запис атомарний, але цикл "читання → зміна → запис" — ні.
**Фікс:** `fcntl.flock` навколо RMW хоча б для vault.

### M2. Тихе "ковтання" помилок у retrieval
**Файли:** `retrieval_index.py: _safe_vector_search()/_safe_fts_search()`;
`server.py: _apply_project_index_sync_plan()`, `_sync_project_note_change()` та ін.

Десятки `except Exception: pass / return []`. Disk full чи пошкоджена
Lance-таблиця виглядатимуть як "нічого не знайдено" або як вічні повні ресинки.
**Фікс:** мінімум — лог у stderr (`[tqmemory] ...`) при кожному фолбеку.

### M3. Один не-UTF-8 або гігантський .md зриває індексацію
**Файл:** `ingestion.py: index_paths_with_sync_plan()`

`file_path.read_text(encoding="utf-8")` без обробки `UnicodeDecodeError` і без
ліміту розміру: весь `index_paths` падає на одному файлі; великий файл цілком
читається в пам'ять і йде в ембеддер.
**Фікс:** `errors="replace"` або skip+warning, плюс cap на розмір (2–5 МБ).

### M4. Повна ресинхронізація як фолбек = O(corpus) ембеддингів
**Файли:** `server.py: _repair_project_retrieval_if_needed()` та аналоги;
`retrieval_index.py: _merge_scope_rows()`

`count != expected → sync_project()` (повний re-embed). На великому корпусі
будь-який транзієнтний розсинхрон лічильників тихо коштує хвилин CPU посеред
звичайного `remember_note`. `_merge_scope_rows` ембедить усі рядки одним
викликом без батчингу — пік пам'яті на великих корпусах.

### M5. Детермінований сіль Argon2id з project_id
**Файл:** `secrets/crypto.py: derive_key_from_passphrase()`

`project_id = sha256(remote_url)[:16]` — для публічного репозиторію він
обчислюваний. Сіль передбачувана, тож атакувальник із копією `vault.tqv` може
заздалегідь будувати словник під конкретний проєкт. Argon2id (64 МіБ × 3)
робить це дорогим, але краще: випадкова сіль у `meta.json` (вона й так
зберігається поряд із vault і не є секретом). Для старих vault-ів — фолбек
на детерміновану сіль (версіонування вже закладене).

### M6. Кеш `_load_storage_snapshot` тримає повні JSON нотаток у пам'яті
**Файл:** `server.py: _load_storage_snapshot()` (`lru_cache(maxsize=32)`)

Ключ включає mtime маніфестів, але старі записи кешу не інвалідовуються і не
звільняються до витіснення; на 32 комбінаціях великих проєктів це помітна
резидентна пам'ять у довгоживучому daemon.

### M7. Passphrase секретів подорожує в кожному RPC
**Файл:** `server.py: _FORWARDED_ENV_KEYS`, `make_proxy_dispatcher()`

`TQMEMORY_SECRETS_PASSPHRASE` форвардиться в кожному виклику через pickle-канал
multiprocessing. Trust boundary (authkey 32 байти у `.daemon.lock` 0600, сокет
0600, той самий користувач) ок і задокументований як свідомий компроміс, але
варто пам'ятати: компрометація lockfile = можливість pickle-RCE у primary.
Це в межах same-user моделі загроз, проте заслуговує на рядок у TECHNICAL_SPEC.

## Logic Analysis (спостереження)

- Логіка retrieval (vector-first gating + down-weighted BM25 + RRF із
  синтетичною distance для FTS-only хітів) — продумана і пояснена в коментарях;
  пороги чесно позначені як некалібровані.
- Міграції коректно бамплять manifest ПІСЛЯ успіху кроку, тож crash-recovery
  працює як заявлено.
- Нюанс: retrieval-міграція v3→v4 ре-ембедить лише поточний проєкт + global;
  інші проєкти доздоганяються ліниво через перевірку `format_version` при
  пошуку. Працює, але перший пошук у "сплячому" проєкті після апгрейду буде
  дуже повільним (повний re-embed) без попередження користувачу.
- У `recent_context` зв'язки для глобальної нотатки читаються з relations.json
  проєкту-походження, а не поточного — схоже на навмисне, але варто
  зафіксувати в документації.
- `retrieval_index.py: reset_scope()` має дубльований try/except з ідентичним
  викликом — мертвий код.

## Recommendations

1. Закрити H1 (порядок bind→claim або retry-ping) і H2 (`timeout=` для git) —
   дві найдешевші правки з найбільшим зниженням ризику.
2. Ввести "quarantine" для нечитабельних note/block JSON замість винятку,
   з репортом у `server_info` (закриває H3 і частину M2).
3. Логувати кожен silent fallback у retrieval (stderr з префіксом
   `[tqmemory]` — інфраструктура вже є).
4. Батчинг ембеддингів (наприклад, по 64) у `_merge_scope_rows` + ліміт
   розміру .md при індексації.
5. Випадкова сіль для Argon2id у `meta.json` з фолбеком для старих vault-ів.
6. Кешувати project identity за cwd на короткий TTL — мінус 2 fork-и на
   кожен tool call.

## Appendix: Files Reviewed

Повністю: `pyproject.toml`, `__init__.py`, `__main__.py`, `cli.py`,
`contracts.py`, `server.py`, `store.py`, `daemon.py`, `identity.py`,
`retrieval.py`, `retrieval_index.py`, `ingestion.py`, `hydration.py`,
`telemetry.py`, `markdown_parser.py`,
`secrets/{crypto,keyresolver,store,audit,paths,__init__}.py`,
`migrations/{runner,snapshot,upgrades}.py`.

Не читались детально: `knowledge_lint.py`, `migrations/{io,log,registry}.py`,
тести (35 файлів — покриття за переліком виглядає сильним).
