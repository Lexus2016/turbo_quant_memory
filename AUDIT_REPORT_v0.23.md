# Audit Report: turbo-memory-mcp v0.23.0 (Adversarial Consilium)

**Дата аудиту:** 2026-07-18
**Метод:** 6 паралельних adversarial-рев'юерів (домени: Security & Secrets, Correctness & Migrations, API & Design, CLI/UX/Ops, Tests & Verification, Claims vs Reality). Кожна знахідка верифікована читанням фактичного коду з цитатами `файл:рядок`; ключові сценарії (C1, R1) додатково відтворені виконанням коду у venv проєкту. Заяви про виправлення з попереднього аудиту (AUDIT_REPORT_v0.22.md) перевірені повторно там, де це було можливо.

## Executive Summary

**Загальна оцінка: 7/10.** Ядро (vault-криптографія, міграційний фреймворк, daemon RPC, стратегія corruption containment) залишається зрілим — критичних вразливостей не знайдено, криптографічних помилок немає. Але: (1) знайдено **детерміновану втрату даних** у парсері markdown (колізія `block_id`), відтворену емпірично; (2) фікс H1 (split-brain primary) закрив лише стартову гонку — **варіант із живим-але-недосяжним primary досі відкритий**; (3) **CI відсутній взагалі** — 530 тестів не запускаються автоматично, а Windows-код daemon-а не виконується ніколи; (4) два заголовкові маркетингові числа README (**"600-token summaries"**, **"save up to 60%"**) — фактично неправдиві, були вказані ще в аудиті v0.22 (D5, D11) і увійшли до релізу 0.23.0 трьома мовами без змін. Аудит-процес існує, але не має зубів: знахідки не є release-блокерами.

- Критичних: 0
- Високих: 4 (+2 claims)
- Середніх: ~15
- Низьких: ~30

## Статус знахідок попереднього аудиту (v0.22) — повторна верифікація

| Знахідка | Статус | Доказ |
|---|---|---|
| S1 symlink-exfil ingestion | ВИПРАВЛЕНО | symlink-escape guard `ingestion.py:351-363`, регресійні тести |
| S2 index_paths без confinement | ВИПРАВЛЕНО | `_root_is_allowed` + `TQMEMORY_ALLOW_EXTERNAL_ROOTS` `ingestion.py:318-323` — **але той самий клас багу живий у `lint_knowledge_base`, див. A4** |
| X1–X6 биті JSON валять інструменти | ЧАСТКОВО | quarantine є (`test_corrupt_file_resilience.py`), але ловить лише parse-помилки — валідний-JSON-неправильної-форми проходить, див. C3 |
| H1 split-brain primary (залишок F5) | **НЕ ВИПРАВЛЕНО** | wedged/socket-deleted primary все ще евіктиться — див. D1 |
| D5 "600-token summaries" | **НЕ ВИПРАВЛЕНО** | `README.md:18` (+ `.ru`, `.uk`); реальний ліміт 220 символів `retrieval.py:383` — див. R1 |
| D6 Hermes: неіснуюче ім'я пакета | **НЕ ВИПРАВЛЕНО** | `README.md:279` `uv tool install turbo-quant-memory`; справжнє ім'я `turbo-memory-mcp` — див. R4 |
| D11 "60% token budget" | **НЕ ВИПРАВЛЕНО** | `README.md:3`; метрика не вимірюється нічим у репозиторії — див. R2 |
| D8 telemetry без тестового покриття | НЕ ВИПРАВЛЕНО | 47+ тестових файлів, telemetry торкається лише інцидентно |
| "Self-Cleaning Graph" у README | ВИПРАВЛЕНО | видалено з усіх трьох README (grep-верифіковано) |
| Deployment contract pin | ВИПРАВЛЕНО | `TECHNICAL_SPEC.md:215-216` пінить `@v0.23.0` |

---

## §1. Correctness & Data Integrity

### C1. Дубльовані заголовки під одним батьком дають однаковий `block_id` — тиха втрата контенту — HIGH
`markdown_parser.py:73-76`: `location_key = f"{root_id}|{normalized_path}|{heading_key}|{int(chunk_index)}"` — без document offset. Дві секції `## Setup` під одним батьком отримують однаковий `heading_path` і `chunk_index=0`. **Відтворено емпірично:** обидві секції дають `mdblk-d1ce3b4254ad7a51f902`. `store.replace_blocks_for_file` (`store.py:765-788`) пише обидва записи в один шлях — контент першої секції знищується мовчки; manifest зберігає id двічі; LanceDB `merge_insert` бачить дубль ключа. Реальні документи з повторюваними `## Setup`/`## Notes`/`## Example` втрачають контент без жодної помилки.
**Фікс:** додати до `location_key` `line_index` заголовка (вже є у `_HeadingMarker`, губиться при побудові `_MarkdownSection`) або лічильник входжень per `heading_path`.

### C2. `read_markdown_neighborhood` сортує за `chunk_index`, що ресетиться per-секція — hydrate перемішує чужі секції — MEDIUM
`store.py:697-700`: сортування `(chunk_index, block_id)`. Для файлу, де секція A має чанки 0,1,2, а пізніша секція B — 0,1, порядок виходить A0,B0,A1,B1,A2. `neighbors_before`/`neighbors_after` (hydrate, `hydration.py:65-88`) віддають блоки з ІНШОЇ секції як "контекст". Абсолютний offset у схемі блоку не зберігається — порядок документа невідновлюваний. Тихо корумпує саме ті довгі документи, для яких chunking і існує.
**Фікс:** персистити start-line секції (або монотонний document ordinal) на блоці при ingestion; сортувати за ним.

### C3. Schema-corrupt (валідний JSON, неправильна форма) обходить quarantine і валить search/indexing — MEDIUM
`store.py:477-485`: quarantine-loader ловить лише `(OSError, ValueError, TypeError)`. Але downstream-фільтри індексують записи беззахисно: `manifest["root_id"]` (`store.py:626`), `block["root_id"]`/`block["source_path"]` (`store.py:680-682`), `manifest["source_path"]` (`ingestion.py:110`). Файл `{"foo": 1}` парситься успішно → `KeyError` прямо з `semantic_search`/`hydrate`/`server_info` — рівно те, що quarantine-коментар обіцяє неможливим. Той самий клас: `read_relations` повертає `data["relations"]` без перевірки типу (`store.py:866`); якщо це dict — `AttributeError` у `add_relation`.
**Фікс:** валідувати мінімальну форму у `_load_json_records_skipping_corrupt` (або `.get()` у фільтрах); `isinstance(..., list)` у `read_relations`.

### C4. Re-index із `paths=None` падає цілком, якщо хоч один зареєстрований root видалено — MEDIUM
`ingestion.py:103-107`: `raise FileNotFoundError` на першому відсутньому root. При `paths=None` `_resolve_roots` повертає ВСІ зареєстровані root-и (`ingestion.py:305-306`) — перейменуй одну директорію з доками, і кожен наступний `index_paths()` падає до індексації будь-чого, без skip-and-warn; мертвий root ніколи не чиститься. Примітно: `assess_project_index_freshness` ту саму ситуацію обробляє коректно (`missing_root_count += 1; continue`, `ingestion.py:253-255`) — два шляхи суперечать один одному.
**Фікс:** дзеркалити freshness-поведінку — рахувати, пропускати, звітувати у payload.

### C5. `RetrievalIndex.sync_project(project_id=X)` embed-ить нотатки DEFAULT-проєкту в таблицю X — LOW (latent)
`retrieval_index.py:175-181`: `_build_note_rows(PROJECT_SCOPE)` ігнорує `resolved_project_id` → `project_notes_dir()` без аргументу → дефолтний проєкт стора. Markdown-рядки параметр шанують, нотатки — ні. Зараз latent (всі виклики без аргументу, grep-верифіковано), але публічний метод із зарядженою пасткою.
**Фікс:** протягнути `resolved_project_id` у `_build_note_rows` → `list_notes`.

### C6. `promote_note` мовчки перезаписує існуючу глобальну нотатку з тим самим id — LOW
`store.py:505-518` → `write_global_note` → `_write_json_atomic(self.global_note_path(note_id), ...)` (`store.py:402`) без перевірки існування. Повторний promote — нормальна дія користувача — знищує попередню глобальну нотатку без слова.
**Фікс:** raise (або архівувати incumbent), якщо шлях існує і це не та сама логічна нотатка.

### C7. `migrations/io.py`: витік temp-файлів, фальшива обіцянка fsync, ghost-нотатки — LOW
`migrations/io.py:16-29`: (a) docstring каже "fsync via close" — `close()` не робить fsync; (b) на відміну від `store._write_json_atomic` (`store.py:1172-1188`, cleanup у `finally`), виняток між створенням і `os.replace` лишає temp-файл; (c) temp має `prefix=".tmp-", suffix=".json"`, а `list_notes` glob-ить `"*.json"` (`store.py:442`) — краш посеред запису note-міграції лишає `.tmp-*.json`, який `list_notes` завантажить як справжню нотатку. Непослідовність: `_dir_has_records` `.tmp-*` навмисно пропускає (`store.py:1137`), `list_notes` — ні.
**Фікс:** try/finally як у store-helper; виключити `.tmp-*` з glob.

### C8. Migration log ігнорує `TQMEMORY_HOME` — LOW
`migrations/log.py:24-25`: `Path.home() / ".turbo-quant-memory" / "migration.log"` — `resolve_storage_root` шанує `TQMEMORY_HOME` (`store.py:1066-1071`), `log_path()` — ні. Deployer з relocated storage root отримує stray `~/.turbo-quant-memory/migration.log`, а audit-trail міграцій відокремлений від даних, які він описує.
**Фікс:** дефолт від `resolve_storage_root()`.

### C9. `_open_scope_table` ковтає всі винятки — "нема таблиці" ≈ "корумпована БД" — LOW
`retrieval_index.py:521-524`: `except Exception: return None` → `count_rows` → 0 → search повертає порожне і `_ensure_scope_synced` запускає повний re-embed. Справжня корупція LanceDB (disk error, permissions) невідмітна від fresh install і без жодного warning — на відміну від search-lanes, які спеціально hardened логувати збої (`retrieval_index.py:560`). Persistent permission error = кожен пошук платить повний re-embed, який теж падає, мовчки.
**Фікс:** ловити лише "table not found"; решту — логувати.

### C10. Неузгодженості timestamps і форми manifest у runner — LOW
`runner._utc_now` (`runner.py:361-362`) емітить `+00:00`, `store.utc_now` (`store.py:1124-1125`) — `Z`: один manifest отримує обидва формати залежно від писальника. `_bump_manifest` для RETRIEVAL/SECRETS пише stripped manifest `{format_version, updated_at}` (`runner.py:354-358`) без `scope`/`source_kind`/`package_version`, які завжди є у `write_*_retrieval_manifest` (`store.py:299-306`) — NOTES-гілка спеціально цього уникає (`runner.py:334-341`), інші ні. `utc_now()` обрізає мікросекунди — дві нотатки в одну секунду ділять `updated_at`.
**Фікс:** єдиний timestamp-helper; повний manifest payload у всіх гілках.

### C11. Тиха втрата рядків, якщо embedder повернув менше векторів — LOW
`retrieval_index.py:446-448`: `for row, vector in zip(rows, vectors)` — `zip` обрізає. Embedder, що повернув менше (batching-баг, зміна backend), мовчки викидає хвіст рядків з індексу; sync рапортує успіх. Один `if len(vectors) != len(rows): raise` закриває весь клас.
**Фікс:** length-check з raise.

---

## §2. Daemon & Operations

### D1. Живий-але-недосяжний primary евіктиться → два primary, split-brain writes — HIGH
`daemon.py:657-667`: якщо ping падає — newcomer видаляє lockfile ЖИВОГО власника, стає primary і unlink-ить socket з-під primary, що ще слухає. `maybe_existing_endpoint` (`daemon.py:252-253`) вже підтвердив, що PID живий. Реальні тригери: (a) AF_UNIX socket у системному tmpdir (`daemon.py:162-163`) — `systemd-tmpfiles`/macOS periodic scripts чистять `/tmp`; довгоживучий primary (редактор відкритий днями) може втратити socket-файл, процес і існуючі з'єднання працюють далі; (b) справді wedged/overloaded primary, що не встигає відповісти за bounded connect window. Результат: два процеси, кожен впевнений, що він єдиний primary, обидва пишуть ті самі LanceDB-таблиці й JSON-store. Фікси H1 (ready-gate, retry window) закрили лише СТАРТОВУ гонку; wedged/socket-deleted варіант того самого багу відкритий (F5 попереднього аудиту).
**Фікс:** якщо `existing.pid` живий — НІКОЛИ не unlink; retry довше, потім abort з actionable error ("primary PID 1234 alive but unreachable — kill it or set TQMEMORY_DAEMON_DISABLE=1"). Auto-reclaim лише для мертвого PID.

### D2. Немає lifecycle-команд daemon-а; README радить `pkill -f` і ручне видалення lock — MEDIUM
Немає `turbo-memory-mcp stop`/`status`/`doctor --fix`. Натомість `README.md:302-307,343`: `pkill -f turbo-memory-mcp` (матчить повний argv УСІХ процесів із цим рядком — усі proxy-клієнти, dev-інвокації, випадкові збіги) + `rm -f ~/.turbo-quant-memory/.daemon.lock` без liveness-перевірки — ручна версія D1. `doctor` (`cli.py:562-563`) принаймні гейтить свій hint staleness-чеком; README — ні.
**Фікс:** `turbo-memory-mcp daemon-stop` (lockfile → verify PID → SIGTERM → wait → report); README використовує його; пораду з `pkill` прибрати.

### D3. `prune-orphans --apply` рухає buckets без daemon-liveness перевірки — MEDIUM
`cli.py:162-169`: docstring стверджує "orphan buckets are by definition not the active project, so moving them is safe even while a daemon is running" — істинно лише для однієї машини. Власний help команди (`cli.py:128-131`) називає контрприклад: unmounted volume або shared storage. Якщо storage root спільний, daemon іншої машини може активно обслуговувати проєкт, чий root відсутній НА ЦІЙ машині — і `--apply` (`cli.py:200-204`) робить `shutil.move` з-під живого писальника. На відміну від `migrate --apply`, жодного `_daemon_lockfile_present`-чека. Move оборотний, тому medium, не high.
**Фікс:** lock-check перед `--apply`; warning про shared storage у виводі `--apply`, не лише в `--help`.

### D4. Інструкції torch-backend у README суперечать uv-tool install flow — MEDIUM
`README.md:69`: `uv tool install git+...@v0.23.0`; `README.md:134`: `pip install 'turbo-memory-mcp[torch]'`. Plain `pip install` потрапляє в активне оточення, а НЕ в uv-tool environment, яким реально запущений сервер. Користувач отримує `RuntimeError` з `_load_torch_embedder` (`retrieval_index.py:125-131`), який радить ту саму неправильну команду.
**Фікс:** задокументувати `uv tool install --force 'turbo-memory-mcp[torch] @ git+…@v0.23.0'` (або `--with sentence-transformers`); вирівняти hint у RuntimeError.

### D5. Socket у спільному `/tmp` за передбачуваним шляхом; unlink-then-bind має race — LOW
`daemon.py:142-163` (шлях = sha1(uid:storage) — повністю передбачуваний для будь-якого локального користувача), `daemon.py:221-226` (unlink з `except OSError: pass`). Squatting = cross-user DoS (bind падає `EADDRINUSE`, uncaught у `acquire_daemon_role`). Socket chmod-иться до 0600 ПІСЛЯ bind (`daemon.py:486-490`) — маленьке вікно, пом'якшене HMAC-handshake. (Перетинається з S4 попереднього аудиту — не виправлено.)
**Фікс:** `$XDG_RUNTIME_DIR`/`/var/run/user/<uid>` коли доступно; catch bind failure з fallback у standalone; restrictive umask навколо bind.

### D6. Windows `_is_pid_alive` трактує exit-code 259 як "живий" — LOW
`daemon.py:108-118`: процес, що справді завершився з кодом 259, рапортується живим. Self-healing на практиці (наступний connect падає), але відома Windows-пастка, варта коментаря або handle-identity чека. Мінор: `_unix_socket_path` (`daemon.py:157-158`) у Windows-гілці читає `os.environ` напряму замість переданого `environ`.

### D7. Bare invocation друкує help і виходить 0 — LOW
`cli.py:627-630`: `turbo-memory-mcp` без субкоманди — usage error, argparse-конвенція (і всі подібні CLI) — exit 2. Wrapper-скрипт `turbo-memory-mcp && next-step` мовчки продовжиться після no-op.

---

## §3. Security & Secrets

### S1. `get_secret` повертає plaintext у LLM-visible MCP response без маскування й фрикції — MEDIUM
`contracts.py:501-512`: `{"status": "ok", ..., "secret_value": secret_value}`. Коментар каже "Clients should render it masked by default" — але ніщо це не enforced. Tool result серіалізується в context window моделі дослівно: модель БАЧИТЬ секрет, він потрапляє в chat transcripts/logs хоста, і будь-який prompt-injection, що може індукувати виклик `get_secret`, отримує значення в-модель (звідти exfil через будь-який інший tool тривіальний). Рецепт AGENTS.md — policy, не mechanism. Частково inherent для MCP, але: немає opt-in confirm, немає CLI-only режиму читання.
**Фікс:** (a) промінентно задокументувати, що `get_secret` кладе значення в контекст моделі; (b) `secret-export NAME > file` CLI (0600) як основний шлях для agent workflows; (c) режим `TQMEMORY_SECRETS_AGENT_READ=0`, що змушує `get_secret` відмовляти MCP-читання.

### S2. Немає технічного бар'єра проти персистенсу секрета через `remember_note` — MEDIUM
`server.py:1375-1387`: `remember_note_impl` валідує title/kind/tier, але зберігає `content` дослівно — нотатки embed-яться й searchable. У поєднанні з S1: необережний або injected агент перманентно "відмиває" vault-секрет у memory corpus, де він sync-иться у LanceDB і виринає у майбутніх hybrid-пошуках. Гарантія "secrets are NEVER indexed" (`server.py:375-376`) стосується лише vault-файлів, не значень, що вийшли з vault. AGENTS.md забороняє — інструкційний рівень.
**Фікс:** на `remember_note` порівнювати content зі значеннями vault поточного проєкту (один vault-decrypt, exact-substring check) і refuse/warn на збігу.

### S3. Keyring auto-bootstrap довіряє будь-якому writable backend, включно з plaintext-file — MEDIUM
`secrets/keyresolver.py:179-192`: відхиляється лише `fail.Keyring`. Якщо середовище резолвить у writable-але-небезпечний backend — `keyrings.alt.file.PlaintextKeyring` або chainer, що провалюється в нього (типово на headless Linux без SecretService, де встановлено `keyrings.alt`) — 32-байтовий master key пишеться base64 у НЕШИФРОВАНИЙ файл, і кожен секрет vault такий же безпечний, як той файл.
**Фікс:** явно відхиляти відомі insecure backends; env-passphrase шлях як задокументований fallback.

### S4. Legacy детермінований KDF salt публічно виводиться — precomputed dictionaries для pre-M5 vaults — LOW
`secrets/crypto.py:74-78`: для pre-M5 vaults salt = `sha256("tqv-salt-v1:" + project_id)`, а дефолтний `project_id` = `sha256(remote_url або repo_path)[:16]` (`identity.py:256-259`) — передбачуваний для будь-якого публічного GitHub remote. Memory-hardness Argon2id лишається per-guess, тобто це enabler прекомпутації, не злам. Нові vaults мають random persisted salts — добре; але upgrade-path відсутній: legacy env-passphrase vault лишається на детермінованому salt назавжди.
**Фікс:** операція "re-key" (decrypt legacy → random salt → re-encrypt → persist у `meta.json`) через `migrate`/`doctor` для vaults без `salt` у meta.

### S5. Daemon RPC використовує pickle-десеріалізацію — LOW (defense-in-depth)
`daemon.py:257-264`: `conn.recv()` unpickle-ить довільні об'єкти. Аутентифікація — HMAC challenge-response з random 32-byte authkey у 0600 lockfile, socket 0600 — атакант уже має бути тим самим UID, а тоді він і так володіє vault-директорією. Не експлуатована дірка, але JSON-framed протокол прибрав би pickle gadget surface цілком (secret values для `set_secret` транзитять цим каналом — pickled, unencrypted, same-user only). Свідомо задокументовано у `TECHNICAL_SPEC.md:201` — визнано.

### S6. `TQMEMORY_STORAGE_HOME` forward-иться будь-яким proxy — redirect storage root per-call — LOW
`server.py:105-111,692-697`: `ENV_STORAGE_HOME` навмисно НЕ stripped з forwarded env — клієнт, що встановив його, змушує shared primary виконати call проти іншого storage root, включно з іншим secrets vault tree. З fingerprint-чеком (DEFECT E) — confusion, не escalation; але socket discriminator уже ключується на storage root, тож forwarding у існуючий primary несумісний із bootstrap-топологією — схоже на latent bug.
**Фікс:** strip-ати `ENV_STORAGE_HOME` як identity keys.

### S7. `_ensure_safe_id` — лише blacklist; дрібні robustness-прогалини — LOW
- `identity.py:26-38`: відхиляє лише `.`/`..`/separators/NUL. `TQMEMORY_PROJECT_ID` = `CON`, `.hidden`, trailing-dot проходять і стають іменами директорій для notes І vault. Traversal заблоковано — залишок: availability/confusion (Windows reserved names, приховані bucket-директорії). Фікс: whitelist-regex як для secret-name (`^[A-Za-z0-9_.-]{1,64}$` мінус leading `.`).
- `secrets/store.py:68-72`: `_KDF_PARAMS_RECORD` хардкодить параметри, дублюючи `crypto.py:42-44`; записані `kdf_params` у `meta.json` ніколи не використовуються при derivation. Зміна констант = кожен env-passphrase vault мовчки derive-ить неправильний ключ. Фікс: derive record з констант; шанувати записані params.
- `secrets/store.py:244-247`: `provision()` на існуючому vault перезаписує `key_mode` режимом ПОТОЧНО резолвнутого ключа без звірки fingerprint — неправильна shadowing-passphrase переписує `key_mode` на `env`, роблячи hint `VaultDecryptError` оманливим рівно в тому сценарії, для якого він створений. Фікс: зберігати наявний `key_mode`.

---

## §4. API & Design

### A1. `server.py` — god-object із шістьма непов'язаними відповідальностями — HIGH (maintainability)
2340 рядків: tool-схеми (`:127-427`), dispatch (`:640-660`), daemon failover state machine (`:726-878`), process lifecycle (`:985-1069`), 19 tool-імплементацій (`:1077-1841`), index sync/repair (`:1975-2164`), snapshot-caching (`:2206-2311`). Module docstring застарів: "Phase 4 stdio MCP server" — код на Phase 10. Чотири secrets-impl (`:1691-1841`) — 150 рядків copy-paste, що відрізняються лише vault-викликом.
**Фікс:** split на `server/tools_schema.py`, `server/dispatch.py`, `server/runtime.py`, `server/tools_{notes,secrets,graph}.py`, `index_sync.py`; secrets-impls — в один параметризований helper.

### A2. 8 із 19 tools мають порожній description для LLM-клієнта — MEDIUM
FastMCP derive-ить description з docstring; його немає у: `health`, `server_info`, `list_scopes`, `self_test` (`server.py:146-160`), `promote_note`, `deprecate_note` (`:208-229`), `hydrate` (`:266-275` — найгірше: `mode` приймає лише `"default"`/`"related"`, але модель дізнається enum лише з помилки), `lint_knowledge_base` (`:292-300`). Водночас `remember_note`/`semantic_search` мають відмінні docstrings — непослідовність видна моделі. Жоден tool не декларує JSON-schema enums для `scope`/`mode`/`kind` (усі plain `str`).
**Фікс:** мінімум one-line docstrings; `Literal[...]` annotations, щоб enums потрапили в JSON schema.

### A3. Немає size-bounds на note writes — unbounded content embed-иться двічі per write — MEDIUM
`server.py:1360-1363`: валідується лише непустота. `store.py:816-822` зберігає все дослівно; немає cap на content/title/tags/refs. На кожен write повний `title + content` embed-иться ДВІЧІ (`server.py:2040` index sync + `:1296-1302` similarity hints) — усередині global dispatch lock, що серіалізує всіх клієнтів. Патологічна multi-MB нотатка = self-DoS. Tags зберігаються без нормалізації — `["Python"]` і `["python"]` стають різними filter-значеннями, хоча docstring радить lowercase.
**Фікс:** `MAX_CONTENT_CHARS`/`MAX_TITLE_CHARS`/tag-count у `remember_note_impl`; lowercase+dedupe tags на write.

### A4. `lint_knowledge_base` приймає шляхи, які `index_paths` відхилив би — MEDIUM
`index_paths` обмежує root-и деревом проєкту (`ingestion.py:318-323`, фікс S2 попереднього аудиту). `knowledge_lint._resolve_roots` (`knowledge_lint.py:346-356`) застосовує лише secrets-vault check і сканує будь-який absolute path — `_iter_markdown_files` читає повний вміст файлів (`:193`), issue-payloads відлунюють назви/шляхи файлів звідусіль на диску. LLM-клієнт може пробувати filesystem поза проєктом через lint, тоді як indexing-шлях це явно забороняє. Security boundary застосовано в одному tool і забуто в його sibling.
**Фікс:** `_root_is_allowed` (або той самий env-override) у `knowledge_lint._resolve_roots`.

### A5. Два різні канали помилок: payload-level vs protocol-level — MEDIUM
Secrets-tools повертають структуровані in-payload помилки (`{"status": "error", "code": ...}`, `contracts.py:538-547`); усі інші tools кидають винятки → protocol-level tool error. Клієнту потрібні два роз'єднані шляхи обробки. Гірше: `link_entities`/`get_related_entities` payloads взагалі без поля `status` (`server.py:1216-1220,1273-1277`) — навіть success-marker непослідовний.
**Фікс:** одна конвенція — або всюди `{"status": ...}` envelopes, або secrets-помилки теж кидають dedicated exception type.

### A6. `get_related_entities` мовчки повертає `[]` на невалідному scope — MEDIUM
`link_entities_impl`/`unlink_entities_impl` валідують scope (`server.py:1200-1202,1232-1234`); `get_related_entities_impl` (`:1255-1277`) — ні, а store тихо трактує невідомий scope як "match nothing" (`store.py:940-943`). Опечатка `scope="projct"` → `{"relations": []}` — правдоподібно-порожній результат замість помилки. Для tool, чиє призначення — graph recall, тиха порожнеча є найгіршим failure mode.
**Фікс:** валідація scope у `get_related_entities_impl`.

### A7. Мертвий import: `mcp.server.mcpserver` не існує — LOW
`server.py:14-17`: try-path ніколи не виконується (верифіковано на mcp 1.26.0: `ModuleNotFoundError`); коментар інвертований — fallback І Є поточний stable SDK path. Нешкідливо, але фантомний API.
**Фікс:** імпортувати `FastMCP` напряму.

### A8. `contracts.py` рекламує tool, якого не існує — LOW
`contracts.py:21-22`: `PHASE_2_TOOL_NAMES` містить `search_memory` — немає в жодному phase ≥ 4 і в `TOOL_HANDLERS`; tuples експортовані в `__all__` як живі контракти. Нумерація фаз пропускає PHASE_8. Лише `CURRENT_TOOL_NAMES` піниться тестом (`test_tools.py:87`) — решта rot невидима для CI (якого нема — T1).
**Фікс:** лишити `CURRENT_TOOL_NAMES` (+ попередню фазу для compat); історичні tuples — private/deprecated.

### A9. Дубльована логіка по шару — LOW
`build_content_preview` (`server.py:1956-1960`) дублює `retrieval._build_compressed_summary` (`retrieval.py:383-391`); `tier_filter`-валідація реалізована двічі з різним порядком (`retrieval.py:68-84` vs `server.py:1581-1589`); `_resolve_input_path` copy-pasted (`ingestion.py:342-348` vs `knowledge_lint.py:360-364`); scope-validation preamble дубльований; чотири secrets-impl з ідентичним 3-arm except.
**Фікс:** спільні helpers (`validate_tier_filter`, `validate_scope`, `resolve_input_path`, secrets error-mapping decorator).

### A10. `hydrate` тригерить index refreshes, які ніколи не використовує — LOW
`server.py:1545-1547`: `hydrate_impl` викликає `_refresh_project_indexes_if_needed`, що може запустити ПОВНИЙ re-embed під global dispatch lock — але `hydrate()` (`hydration.py:41-55`) читає лише canonical store і ніколи не чіпає `RetrievalIndex`. Чистий read on-disk JSON може блокувати всіх клієнтів на хвилини роботою, чий результат hydrate ігнорує.
**Фікс:** прибрати refresh з `hydrate_impl`.

### A11. `recent_context` tier filter обходиться нотатками без tier — LOW
`server.py:1602-1604`: `if allowed_tiers is not None and note_tier and note_tier not in allowed_tiers: continue` — нотатка без `tier` проходить БУДЬ-ЯКИЙ фільтр (guard `and note_tier` робить відсутність tier implicit wildcard). `_normalize_note_record` (`store.py:832-839`) backfill-ить `note_kind`/`note_status`/`provenance`, але не `tier` — legacy-нотатки справді потрапляють сюди. `tier_filter=["durable"]` може повернути untiered episodic handoff.
**Фікс:** backfill `tier` через `tier_for_kind`, або порожній tier = fail для non-None фільтра.

### A12. Асиметрична валідація URI між link і unlink — LOW
`link_entities_impl` валідує обидва URI через `_validate_entity_uri` (`server.py:1205-1206`); `unlink_entities_impl` перевіряє лише непустоту (`:1235-1238`) — опечатка `note:abc` (саме те, для чого існує `_HIERARCHICAL_ENTITY_SCHEMES`) мовчки повертає `{"changed": false}`.
**Фікс:** `_validate_entity_uri` і в unlink.

### A13. Незадокументовані ліміти; немає pagination story — LOW
`semantic_search(limit=...)` мовчки клампиться до 20 (`retrieval.py:86`), `recent_context` — те саме (`server.py:1591`); жоден docstring це не згадує; клієнт із `limit=50` отримує 20 без маркера `truncated` (на відміну від `lint_knowledge_base`, `knowledge_lint.py:315`). Cursor/offset відсутні.
**Фікс:** задокументувати cap; `limit_max` у `server_info`.

### A14. Error messages несуть абсолютні локальні шляхи (але не stack traces) — LOW
Добре: daemon серіалізує лише `type(exc).__name__` + `str(exc)` (`daemon.py:567-572`), FastMCP — без tracebacks. Але повідомлення типу `store.py:894-897` і `knowledge_lint.py:177` віддають absolute home-directory шляхи в контекст LLM. Для local-first single-user інструмента — arguably фіча, але рішення про information flow варто ухвалити свідомо й задокументувати.

### A15. Telemetry не failure-isolated на write-шляху — LOW
`server.py:1524-1531,1549-1554`: `record_*_usage` викликаються без захисту; `store.write_usage_stats` може кинути на disk-full/permissions — перетворюючи вже успішний пошук на tool error. Точний антипатерн, якого `_sum_raw_source_bytes` свідомо уникає (`server.py:2195-2202` коментар: "Telemetry byte-count must never break the search"). (Перетин із X6 попереднього аудиту — фікс застосовано лише до byte-count.)
**Фікс:** `_sync_with_warning`-style guard навколо обох record-викликів.

---

## §5. Tests & Verification

### T1. CI відсутній взагалі — HIGH
Немає `.github/workflows/`, `.gitlab-ci.yml`, `Makefile`, `tox.ini`, `.pre-commit-config.yaml` (верифіковано `git ls-files`). Реліз 0.23.0 tagged без жодного записаного автоматичного прогону, без OS-matrix, без Python-matrix. Конкретний наслідок: Windows-специфічний daemon-код (named pipes) має тести, що self-skip-аються (`test_daemon.py:376,556`) — Windows-шлях не виконується НІДЕ, навіть локально. `pyproject.toml:25` при цьому claims "OS Independent".
**Фікс:** мінімальний GitHub Actions: `pytest` на {ubuntu, macos, windows} × {3.11, 3.12, 3.13} на push/PR і перед release-tagging.

### T2. Весь `semantic_search`-сьют мокає dense-embedding lane — MEDIUM
`test_semantic_search.py:48-51`: autouse-fixture підміняє embedder на 13-слівний bag-of-words `KeywordEmbedder`. Усі 14 semantic-search тестів — core value proposition проєкту — вправляють лише RRF/BM25-пламбінг над синтетичними векторами. Те саме у `test_retrieval_index.py:11-18` (StaticEmbedder = sha256) і `test_fusion_gating.py`. Реальну ranking-коректність тримають рівно 2 тести (`test_embedding_backend.py:69,98`), і parity-тест self-skip-ається через `importorskip` — у мінімальному встановленні НІЩО не стеріже fastembed-upgrade, що мовчки змінить pooling/normalization. Warning у власному виводі сьюта показує, що ризик живий: "The model ... now uses mean pooling instead of CLS embedding".
**Фікс:** малий фіксований corpus + pinned expected top-1 проти реального fastembed (маркер `slow` за потреби).

### T3. `test_migrations.py` спустошує глобальний migration registry і не відновлює — MEDIUM
`test_migrations.py:56-61`: autouse `clear_registry()` без restore. `REGISTRY` (`migrations/registry.py:62`) — module-global, популяється один раз при import. Після цього модуля registry ПОРОЖНІЙ для всіх наступних тестових модулів у тому самому процесі. Sibling-модуль демонструє правильний патерн (`test_fresh_install_migrations.py:39-44` save/restore з явним коментарем). Небезпека latent сьогодні; `health()`/`server_info` викликають реальний `detect_status` (`server.py:471-474`) — будь-який майбутній тест "pending migration appears in health", що йтиме після, може пройти ВАКУУМНО.
**Фікс:** save/restore `REGISTRY` як у `test_fresh_install_migrations.py`.

### T4. `test_tools.py` запускає живий сервер проти РЕАЛЬНОГО memory home розробника — MEDIUM
`test_tools.py:57-63`: env без `TQMEMORY_HOME` — spawned server резолвить реальний `~/.turbo-quant-memory` (і реальний repo як проєкт); assertions досить лояльні, щоб поглинути будь-який стан машини (`:128-129`). Коментар `:54-56` показує, що автори зрозуміли machine-dependence для daemon-а, але пропустили home directory. На машині з корумпованим real store або з `TQMEMORY_MIGRATE_ON_STARTUP=1` тест може мутувати або впасти на реальних даних користувача.
**Фікс:** `"TQMEMORY_HOME": str(tmp_path / "home")` у env.

### T5. Headline "byte savings" benchmark не може повідомити про провал і має strawman baseline — MEDIUM
`scripts/benchmark_context_savings.py`: (a) 6 hand-curated queries, усі з термінів, дослівно присутніх у корпусі; (b) `:131` `raise AssertionError` на query без хітів — retrieval-провал КРАШИТЬ benchmark замість запису — опублікований звіт не може містити miss, survivorship by construction; (c) baseline = "відкрити повний текст КОЖНОГО унікального файлу з top-5" (`:145-151`) — strawman, реальний агент відкриває один-два; (d) рівно один timing sample per query, без warmup, без repeats; перший запит поглинає cold-start; звіт — лише mean/median по 6 запитах, жодної variance. Отримані "83.79%" опубліковані як headline у `benchmarks/latest.md:19`. Пом'якшення: метод чесно дисклеймлений у `latest.md:50`.
**Фікс:** записувати failures як zero-savings rows; реалістичний baseline (top-1 file або top-hit + hydrate); N≥5 прогонів з warmup, звітувати min/median/max.

### T6. Real-model тести мовчки вимагають network + multi-hundred-MB download — LOW
`test_embedding_backend.py:98-134`: без `importorskip`, без маркера — вантажить реальну fastembed/ONNX модель. На чистій машині сьют качає модель з HuggingFace; `pyproject.toml:72-74` не реєструє маркерів — немає `-m "not slow"` escape hatch. Сьют не hermetic: offline CI або заблокований HF endpoint валять прогін.
**Фікс:** маркер `model`/`slow`, deselect by default у fast-рівні CI.

### T7. Немає coverage-вимірювання; кілька scripts повністю без тестів — LOW
Жодного `pytest-cov`/`coverage` у dev-deps. Меппінг API→тести: MCP-поверхня покрита справді (кожен tool у ≥2 тестових файлах, крім `self_test_impl` — лише опосередковано), але `scripts/refresh_readme_stats.py`, `benchmark_embedder_ab.py`, `benchmark_paraphrase.py`, `smoke_test.py` — НУЛЬОВЕ покриття.
**Фікс:** pytest-cov + `--cov-fail-under` ratchet; мінімум smoke-import scripts.

### T8. Немає store-layer concurrent-write тестів — LOW
Конкурентність тестується лише на daemon-рівні (`test_daemon.py:308-344,719-761` — добре). Але `TQMEMORY_DAEMON_DISABLE` — підтримувана конфігурація: два standalone MCP-процеси (Claude Code + Cursor на одному проєкті) можуть read-modify-write (`deprecate_note` переписує нотатку + supersession links) конкурентно. `_write_json_atomic` робить single-file writes атомарними, але multi-file invariants без тестів.
**Фікс:** тест із двома процесами, interleaved deprecate/promote на одній нотатці.

### T9. Немає golden-fixture upgrade-тесту з реального історичного релізу — LOW
Migration-тести будують синтетичні "старі" стори вручну. Fresh-install покрито, fixtures розумні — але ніщо не round-trip-ить checked-in snapshot РЕАЛЬНОГО v0.x-ера home через `apply_pending`. Якщо минулий реліз писав поле, яке синтетичний fixture не відтворює, upgrade-шлях регресує мовчки.
**Фікс:** commit-нути мінімальний tarball реального pre-0.20 store як fixture.

---

## §6. Claims vs Reality (README / docs / benchmarks)

### R1. "600-token summaries" — завищено у ~10 разів; вказано в аудиті v0.22, досі не виправлено — HIGH (claims)
`README.md:18` (+ `.ru:18`, `.uk:18`): "fetch only highly-relevant **600-token summaries**". Реальність — `retrieval.py:383-391`: `_build_compressed_summary(..., limit: int = 220)` — cap **220 символів** (~55 токенів) плюс до 3 коротких key_points. Жодного "600" у `src/` (grep; єдині 600 — `0o600` file modes). Аудит v0.22 (D5) flag-нув рівно цей рядок; 0.23.0 випустив "Docs"-фікси для інших claims, а цей лишив трьома мовами.
**Фікс:** реальний розмір envelope, або виміряний середній з `benchmarks/latest.json`.

### R2. "Save up to 60% of your token budget" — не вимірюється; суперечить власній таблиці — HIGH (claims)
`README.md:3`. Жоден benchmark у репозиторії не вимірює "token budget". Єдине вимірювання — BYTE savings retrieval-payload vs naive full-file opening: **83.79%** (`benchmarks/latest.json`), яке той самий README цитує у `:22`. Hero-число (60%) суперечить таблиці (83.79%), і жодне не міряє фактичний token budget користувача. Аудит v0.22 (D11) — не виправлено.
**Фікс:** прибрати число з tagline або вирівняти з виміряною метрикою з одиницями ("~84% fewer retrieval bytes vs. opening full files").

### R3. Headline benchmark-числа реальні, але stale — подані як поточні — MEDIUM
`README.md:22-23`: "~83.79% fewer bytes", "~400 ms (incl. CPU query embedding)" — з `benchmarks/latest.md`, згенерованого **2026-05-29**: ~8 релізів тому, ДО перемикання дефолтного embedder з PyTorch на fastembed/ONNX у 0.21.0. Latency-claim "incl. CPU query embedding" виміряний на СТАРОМУ дефолтному backend; поточний дефолт для README ніколи не перевимірювали.
**Фікс:** перезапустити `benchmark_context_savings.py` на fastembed-дефолті; date-stamp таблицю.

### R4. Hermes-секція встановлює неіснуюче ім'я пакета — MEDIUM
`README.md:279`: `uv tool install turbo-quant-memory`; `pyproject.toml:6`: `name = "turbo-memory-mcp"`. Усі інші секції встановлюють з git URL; ця команда 404-ить. Flag-нуто як D6 у v0.22 — не виправлено.
**Фікс:** `uv tool install turbo-memory-mcp` (або git URL, консистентно з рештою README).

### R5. TECHNICAL_SPEC відстав від імплементації — MEDIUM
`TECHNICAL_SPEC.md:103-122`: таблиця інструментів містить 18 tools і оминає `recent_context` (існує, `server.py:403-404`; `README.md:160` правильно каже "19") — "contract"-документ неправильний. `:49`: "Embeddings | Sentence Transformers" — неправда з 0.21.0; `fastembed` — core dependency і дефолт.
**Фікс:** рядок `recent_context`; оновити stack-таблицю.

### R6. "Turbo Quant" — жодної квантизації в коді — MEDIUM
Grep `quant|Quant` по `src/` — лише брендинг-рядки. Немає vector quantization (LanceDB defaults), scalar/product quantization, token quantization — уся "компресія" = 220-char truncation + key-point extraction. Визнано: `TECHNICAL_SPEC.md:244` чесно перелічує "claiming direct token quantization" як non-goal, AGENTS.md забороняє KV-cache claims — документи дисклеймлять те, що рекламує назва. Користувач, що читає "Turbo Quant Memory", резонно очікує quantized embeddings і не знаходить жодних.
**Фікс:** один чесний рядок у README ("'Quant' refers to token-budget reduction, not weight/vector quantization") або rename.

### R7. "Verify it yourself" grep у README не чистий; privacy-секція оминає first-run download — MEDIUM
`README.md:170`: пропонує `grep -rE 'requests|httpx|urllib\.request|aiohttp' src/'` як доказ "clean" — запуск дає збіг у docstring (`retrieval_index.py`) і у `.pyc`. Сутність істинна (zero outbound HTTP верифіковано; єдині sockets — local AF_UNIX IPC), але запропонований доказ падає як написано. Та сама privacy-важка секція не згадує, що **перший запуск качає ~0.2 GB ONNX-модель з HuggingFace** (`retrieval_index.py:142-144`) — outbound HTTPS fetch, дисклеймлений лише в CHANGELOG.
**Фікс:** виправити grep (`--include='*.py'`, патерн на `import ...`); один рядок про first-run model download у privacy-секції.

### R8. "empty 28-byte encrypted blob" — насправді 57 байт — LOW
`README.md:165`. Empty vault plaintext = 29 байт (`secrets/store.py:237-243`); encrypted blob = 12 (nonce) + 29 + 16 (GCM tag) = **57 байт** (`secrets/crypto.py:38-54`). Плюс завжди є `meta.json` — "the only thing on disk" неправильне удвічі. Дрібниця, але перевірюваний факт, сказаний із фальшивою точністю.

### R9. "≈ 980,000 input tokens saved" — це bytes÷4, недисклеймлено в README — LOW
`README.md:37` подає як виміряне число; реальність (`telemetry.py:72`): `ceil(estimated_bytes_saved / 4)`. У `server_info` є чесний `measurement_basis` (`telemetry.py:47`) — у README-таблиці загублено. Евристика 4 bytes/token помітно неточна для Cyrillic/CJK.

### R10. "Retrieval quality is identical to the legacy PyTorch backend" — "identical" завищує ≥0.99 guard — LOW
`README.md:129`. Доказ — parity-тест із per-phrase cosine ≥ 0.99 (`test_embedding_backend.py:69-92`): similarity floor, не identity; skip-ається на bare client installs. "measured MRR parity" з CHANGELOG 0.21.0 не має committed MRR-артефакту: `benchmark_embedder_ab.py` порівнює MiniLM vs bge-m3, не fastembed vs torch. Має бути "equivalent within measured tolerance".

### R11. MEMORY_STRATEGY contract drift — LOW
`MEMORY_STRATEGY.md:113-132` "Result Card Contract" оминає поля, які код справді повертає (`relations`, `tier`, `note_status`, `provenance`, `warning`); `:72-83` storage layout оминає `retrieval/`, `secrets/`, usage-stats; search-policy секція ніколи не згадує `source_filter` — headline-фічу 0.22.0.

### R12. Puffery — LOW
`README.md:3`: "The first self-installable, trilingual local-first memory & knowledge graph" — неверифіковний суперлатив; "hyper-fast" для ~400 ms average search — щедро. Нешкідливий маркетинг, але сидить у тому самому реченні, що й неверифіковані 60%.

---

## Що зроблено правильно (верифіковано, не припущено)

- **Vault-криптографія зразкова:** AES-256-GCM зі свіжим 12-byte nonce per message (`crypto.py:49-54`), Argon2id t=3/64 MiB/p=4 за RFC 9106, random persisted salts для нових vaults, доменно-розділений key fingerprint для fail-fast на неправильному ключі, read-шляхи ніколи не mint-ять ключ (DEFECT D), жодної саморобної крипто.
- **Permissions дисципліновано скрізь:** vault/meta 0600 через mkstemp+atomic rename, secrets dir 0700, audit log 0600 O_APPEND (і ніколи не логує значень), daemon lockfile 0600 O_EXCL, socket 0600.
- **Міграції crash-safe:** version bump суворо останнім, failure зупиняє прогін негайно, registry детектить chain gaps/duplicates; snapshot/restore зі staging + atomic rename + rollback + keep-count floor 2; CLI snapshot-ить перед restore (restore не one-way door) і відмовляє під живим daemon через PID-liveness.
- **Daemon RPC:** idempotency-aware retry split (`PrimaryUnreachable` vs mid-call `ConnectionError`) — неідемпотентні tools ніколи не ретраяться мовчки; wedged-primary connect bounded; promoted primaries проходять міграції до writes; є справжній Windows AF_PIPE шлях.
- **Corruption containment як принцип:** per-note quarantine, tolerant-vs-strict relation reads (write-шлях КИДАЄ замість мовчазного перезапису битого файлу — рідкісний правильний вибір), orphan/ghost rows у LanceDB реконсиляються by-id diff.
- **Security boundaries з зубами:** path traversal блокується у source (`identity.py:204-211`), symlink-escape у ingestion, vault ізольований від indexing трьома шарами (root-refusal + per-file skip + lint guards).
- **Local-first — правда:** zero outbound HTTP у `src/` (grep-верифіковано); telemetry — локальний JSON-лічильник, нічого не транзитить.
- **Тести:** 530 passed / 0 failed; справжні adversarial crypto-тести (bit-flipped ciphertext І nonce); parametrized path-traversal регресії; end-to-end contract-тест, що пінить точний 19-tool каталог; backend-parity тест з multilingual phrases.
- **Benchmark-чесність вища за середню:** verbatim-query bias дисклеймлений і надрукований у самому звіті; strawman baseline розкритий у `baseline_definition`; числа 83.79%/400 ms справді відтворювані (`benchmark_context_savings.py` output збігається з `latest.json`); вигадану фічу "Self-Cleaning Graph" прибрано з усіх README.
- **Обидва попередні аудит-звіти чесно трекають власні знахідки** зі статусами виконання — підробних фіксів не знайдено й цього разу.

## Severity Summary

| Рівень | К-сть | Знахідки |
|---|---|---|
| Critical | 0 | — |
| High | 6 | C1 (block_id collision → втрата даних), D1 (split-brain primary), A1 (god-object), T1 (нема CI), R1 (600-token claim), R2 (60% claim) |
| Medium | 15 | C2, C3, C4, D2, D3, D4, S1, S2, S3, A2, A3, A4, A5, A6, T2, T3, T4, T5, R3, R4, R5, R6, R7 (деякі об'єднано) |
| Low | ~30 | C5–C11, D5–D7, S4–S7, A7–A15, T6–T9, R8–R12 |

## Рекомендовані release-блокери (перед 0.24.0)

1. **C1** — колізія `block_id`: детермінована, досяжна звичайним markdown, відтворена, втрачає контент користувача без сигналу.
2. **D1** — живий primary ніколи не повинен евіктитися: один `if pid_alive: abort` закриває клас.
3. **T1** — мінімальний CI (pytest на 3 ОС); без нього кожен фікс вище — акт віри.
4. **R1/R2/R4** — три claims, вже flag-нуті власним аудитом v0.22, не повинні пережити другий реліз.
5. **A4** — застосувати `_root_is_allowed` у `lint_knowledge_base` (однорядковий фікс для вже оплаченої security boundary).

**Мета-знахідка:** найкрасномовніший патерн аудиту — не жоден окремий баг, а те, що аудит v0.22 ідентифікував D5, D6, D11 — і 0.23.0 випустив усі три трьома мовами. Аудит-процес існує, але не має зубів. Рекомендація: "audit findings closed або consciously deferred" як явний release-gate у CHANGELOG-процесі.
