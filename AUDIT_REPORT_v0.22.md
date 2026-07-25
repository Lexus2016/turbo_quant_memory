# Audit Report: turbo-memory-mcp v0.21/0.22 (Adversarial Consilium)

**Дата аудиту:** 2026-07-18
**Метод:** 6 паралельних adversarial-рев'юерів (домени: Security, Correctness & Concurrency, Retrieval & Search Quality, CLI & Agent UX, Docs vs Reality, Migrations & Data Integrity). Кожна знахідка верифікована читанням фактичного коду з цитатами `файл:рядок`; ключові сценарії (S1, M#1, M#2) додатково відтворені виконанням коду у venv проєкту. Усі заяви про виправлення з попереднього аудиту (AUDIT_REPORT.md, v0.15.0) перевірені повторно.

## Executive Summary

**Загальна оцінка: 7/10.** Ядро (vault-криптографія, daemon-транспорт, міграційний фреймворк) спроєктоване зріло й дисципліновано. Головні фікси попереднього аудиту (H1, H2, M1, M3, M5) — **справжні**, підробних виправлень не знайдено. Але виправлення застосовано рівно там, куди вказував попередній звіт, і ніде більше: ті самі класи багів живуть у сусідніх шляхах виконання. Знайдено реальний exfil-примітив у ingestion (symlinks + довільні root-и), регресію Windows-сумісності, внесену фіксом M1, вигадану фічу "Self-Cleaning Graph" у README трьома мовами та постійне фантомне попередження про міграції на кожному чистому встановленні.

- Критичних: 0
- Високих: 7
- Середніх: ~15
- Низьких: ~20

## Статус знахідок попереднього аудиту (v0.15.0) — повторна верифікація

| Знахідка | Статус | Доказ |
|---|---|---|
| H1 split-brain primary | ВИПРАВЛЕНО (залишок — F5) | retry-ping `daemon.py:591-623`, listener ready-event `daemon.py:559-562`, bind-до-міграції `server.py:986-997` |
| H2 git без timeout | ВИПРАВЛЕНО | `identity.py:306-323` (timeout=3.0 + fallback + кеш) |
| H3 битий note JSON | **ЧАСТКОВО** | quarantine є (`store.py:378-438`), але той самий клас багу живий у 6 інших шляхах — див. §1 |
| M1 vault RMW race | ВИПРАВЛЕНО (POSIX) | flock `.vault.lock` `secrets/store.py:140-160` — **але вніс регресію F1 (Windows)** |
| M2 тихе ковтання помилок retrieval | ВИПРАВЛЕНО | stderr-логи `retrieval_index.py:560,584`, `server.py:2041-2044` |
| M3 не-UTF-8 / гігантський .md | ВИПРАВЛЕНО (новий edge — R3) | `ingestion.py:48-66` (errors="replace" + size cap) |
| M4 O(corpus) re-embed на дрейф | ВИПРАВЛЕНО (застаріле зауваження — F7) | by-id diff repair `server.py:2071-2122` |
| M5 детермінований salt Argon2id | ВИПРАВЛЕНО | random salt у `meta.json` `secrets/store.py:180-184`; legacy-шлях збережено `secrets/crypto.py:74-78` |
| M6 (спростовано раніше) | підтверджено спростування | кеш містить лише агреговані лічильники; ключ з mtime_ns |
| M7 passphrase-in-RPC | задокументовано (код без змін, свідомий компроміс) | `TECHNICAL_SPEC.md:191-201` |

---

## §1. Cross-cutting: клас "один битий файл валить інструменти" (H3) не винищено

Фікс H3 застосували лише до note JSON у `list_notes`. Той самий патерн — незахищений `json.load`, що валить увесь tool call, — знайдено у **шести** інших місцях. Рекомендація: один спільний quarantine/guard-helper закриває всі шість.

### X1. Битий `manifest.json` у БУДЬ-ЯКОМУ project bucket валить УСІ tool calls — HIGH
`store.py:1065-1068` (`_read_json` кидає `JSONDecodeError` без обробки) ← `store.py:922` (`reconcile_project_identity` читає manifest кожного проєкту) ← `server.py:1629` (`build_runtime_context`, викликається усіма handlers). Також `store.py:180`, `store.py:972` (`detect_orphaned_buckets`).
**Сценарій:** краш посеред rename / ручне редагування → один битий байт у проєкті A вимикає `remember_note`, `semantic_search`, `health` і для проєкту B.
**Фікс:** try/except із skip + stderr-warning у `reconcile_project_identity` та `detect_orphaned_buckets`; у `write_project_manifest` нечитабельний власний manifest трактувати як `{}` (atomic rewrite самозалікує файл).

### X2. Один stale index row валить увесь `semantic_search` — HIGH
`retrieval.py:266` (`store.read_note` без захисту у `_decorate_candidate`) ← row у LanceDB переживає видалений/битий note JSON. By-id drift repair (`server.py:2071-2101`) підключено ЛИШЕ до write-шляхів; read-шлях (`_refresh_project_indexes_if_needed`, `server.py:1949-1969`) id не звіряє. Асиметрія: provenance-read за 8 рядків до того (`retrieval.py:177-181`) загорнуто у try/except "never break search" — decorate-read ні. Доки явно благословляють ручне редагування note-файлів (`retrieval.py:273-274`).
**Фікс:** (a) try/except навколо `read_note` у `_decorate_candidate`, кандидата пропускати; (b) викликати `_repair_project_retrieval_if_needed` з read-шляху — заодно закриває R5 (deprecated note, що лишився в індексі після невдалого sync, продовжує ранжуватися).

### X3. Битий note/block JSON валить `server_info` — MEDIUM
`server.py:2210-2218` (`_read_json_dir`: голий `json.load`) ← `_load_storage_snapshot` ← `collect_storage_stats` ← `server_info_impl` (`server.py:1041`). Тобто діагностичний інструмент, призначений ПОКАЗУВАТИ quarantine, падає від того ж файлу. `lru_cache` винятки не кешує — падає на кожен виклик.
**Фікс:** перевикористати `store._try_load_note_record` / `_load_json_records_skipping_corrupt` у `_read_json_dir`.

### X4. Битий `relations.json` вимикає увесь Knowledge Graph + `recent_context` — MEDIUM
`store.py:816` (`read_relations` → неперехоплений `JSONDecodeError`); callers: `add_relation`/`remove_relation` (`store.py:835,851`), `get_relations_for_entity` (`store.py:870` → `recent_context_impl` `server.py:1573` — викликається для кожної нотатки). `relations.json` перезаписується на кожен link — найчастіше перезаписуваний JSON після manifests.
**Фікс:** read-шляхи — catch + warn + трактувати як порожній; write-шляхи — цільовий виняток замість тихого перезапису битого-але-змістовного файлу порожнім набором.

### X5. Валідний-JSON-але-не-dict manifest валить recovery-CLI — MEDIUM
`migrations/runner.py:297-304` (`_version_from` викликає `manifest.get` без перевірки типу), `runner.py:198` ловить `OSError, ValueError, TypeError, KeyError` — але НЕ `AttributeError`. Відтворено: manifest `[1,2,3]` або `"hello"` → `AttributeError` → `migrate --status`, `migrate --apply`, `doctor` падають із traceback — рівно в сценарії пошкодження, для якого цей інструмент існує. Та ж дірка: `telemetry.py:16-20` (`load_usage_stats`).
**Фікс:** `isinstance(manifest, dict)` на read-site; тести з `[1,2]` та `"str"`.

### X6. Успішний search падає у telemetry-хвості — LOW
`server.py:1487` → `_sum_raw_source_bytes` (`server.py:2140-2152`) читає `read_note`/`read_markdown_block` по кожному результату без ізоляції помилок. Якщо row вказує на битий note — пошук успішний, а tool call падає на лічильнику статистики.
**Фікс:** try/except на кожне читання; telemetry ніколи не повинна валити вимірювану операцію.

---

## §2. Security

### S1. Ingestion іде за symlink-ами `.md` — читання й exfil файлів поза проєктом — HIGH
`ingestion.py:383-403` (`_iter_markdown_files`: `is_file()` йде за symlink-ами), `ingestion.py:51-66` (`read_text` на target). Жодної перевірки `is_symlink()`/containment у walk немає. **Відтворено емпірично:** `root/evil.md -> ../outside_target.md` індексується, вміст прочитано. Сценарій: клонований шкідливий репозиторій із `docs/leak.md -> ~/.aws/credentials`; звичайний `index_paths` або автоматичний `_refresh_project_markdown_if_stale` (`server.py:1933-1946`) читає credentials, embed-ить, персистить у `markdown_blocks` + LanceDB; будь-який наступний `semantic_search`/`hydrate` віддає секрет агенту → вміст лишає машину через model API. Той самий walk годує `knowledge_lint.py:178-192`. Єдина охорона walk — `is_inside_secrets_storage` (vault-субдерево), вона працює; більше нічого не захищене.
**Фікс:** пропускати `file_path.is_symlink()` або `resolve()` + перевірка входження у `root_path.resolve()`; той самий guard у `knowledge_lint.py`.

### S2. `index_paths` приймає будь-який абсолютний шлях — немає confinement — MEDIUM
`ingestion.py:335-341` (`_resolve_input_path` резолвить будь-який absolute path; єдина заборона — vault-субдерево `ingestion.py:311-316`); експоновано як MCP tool `server.py:278-290`.
**Сценарій:** prompt-injected агент (через шкідливий README/issue) викликає `index_paths(["/Users/admin"])`, потім `semantic_search` — кожен `*.md` на диску стає доступним через memory API без підтвердження. Разом із S1 досяжні й не-`.md` секрети.
**Фікс:** відхиляти root-и поза `store.project.project_root` за замовчуванням (opt-in flag для свідомих зовнішніх root-ів) + stderr-warning.

### S3. `.tqmemoryignore` basename-патерни мовчки не матчать вкладені файли — LOW
`ingestion.py:370-380` (`_matches_ignore` матчить повний відносний шлях і директорії `parts[:-1]` — але НЕ голий filename). Користувач пише `secrets.md` за gitignore-семантикою — `sub/dir/secrets.md` все одно індексується. Тиха відмова безпечного боку.
**Фікс:** для патернів без `/` додати `fnmatch.fnmatch(parts[-1], pattern)`.

### S4. Передбачуваний socket-шлях у спільному `/tmp` — socket-squat DoS (Linux) — LOW
`daemon.py:142-163` (`/tmp/tqm-<sha1(uid:storage_root)[:12]>.sock`, обидва входи вгадуються), `daemon.py:222-226` (unlink наявного шляху з проковтнутим `OSError`). На Linux інший локальний користувач може заздалегідь створити цей шлях → unlink тихо падає → bind падає → daemon не стартує (fallback у standalone — деградація, див. F6). На macOS `$TMPDIR` per-user — не стосується. Hijack неможливий (lockfile у `0600` storage root).
**Фікс:** socket у `0700` per-user runtime dir (`$XDG_RUNTIME_DIR` або `<storage_root>/.run/`), чітка помилка на bind failure.

### S5. Primary-side merge `_environ` приймає довільні ключі — LOW
`server.py:105-111` (`_FORWARDED_ENV_KEYS`, 5 ключів) — але приймальна сторона (`server.py:681-686`) мержить УСІ ключі з `_environ`, включно з `TQMEMORY_HOME`, який `resolve_storage_root` (`store.py:998-1003`) шанує per-call. Сьогодні socket authkey-gated і same-user — лише defense-in-depth, але асиметрія між задокументованим allowlist і нефільтрованим merge — латентний footgun.
**Фікс:** фільтрувати `environ_override` на primary-side теж.

### S6. Audit log росте безмежно — LOW
`secrets/audit.py:37-56` — append-only `audit.jsonl`, без ротації; `count()` (`:58-63`) читає увесь файл щоразу.
**Фікс:** ротація/кап (останні N рядків або size-based roll).

### S7. Немає перевірки мінімальної стійкості `TQMEMORY_SECRETS_PASSPHRASE` — LOW
`secrets/keyresolver.py:136-140` — будь-який непорожній рядок стає входом Argon2id. Пароль із 1 символу мовчки "захищає" vault (M5-фікс прибрав precomputation, але не brute-force слабкого пароля проти вкраденого vault).
**Фікс:** warning/відмова при створенні нового vault для пароля < 12 символів, за зразком `_maybe_warn_env_looks_like_raw_key`.

### Security — що зроблено добре
- Vault-крипто підручникове: AES-256-GCM зі свіжими 12-байтними nonce, Argon2id (64 MiB, t=3) через `argon2-cffi`, жодного саморобного crypto; key-fingerprint fail-fast (DEFECT B) перетворює wrong-key на структуровані помилки.
- Файлова гігієна скрізь правильна: vault/meta/audit/lockfile `0600`, vault-dir `0700`, atomic temp+rename + fsync, mode re-enforce після umask.
- Daemon IPC: HMAC-authkey challenge всередині `Listener.accept()` ДО будь-якого unpickle — pre-auth pickle deserialization відсутня; socket `0600`, lockfile `0600` із 32-байтним authkey.
- Секрети ніколи не потрапляють у логи/прозу: audit пише лише `(ts, action, name)`; `secret_value` — окреме payload-поле, не інтерполюється (`contracts.py:501-512`); warnings не echo-ять значення; daemon error replies містять names/paths, але ніколи values (перевірено кожен raise site).
- `telemetry.py` суто локальна — жодних network-імпортів у пакеті.
- `identity.py` git-виклики — argv-списки без shell, фіксовані аргументи, timeouts; `_ensure_safe_id` блокує traversal у кожному client-influenced id.
- `pyproject.toml` — мейнстрім-залежності зі здоровими upper bounds.

---

## §3. Correctness & Concurrency

### F1. `import fcntl` робить увесь сервер неімпортованим на Windows — HIGH
`secrets/store.py:35` (безумовний top-level `import fcntl`) ← `secrets/__init__.py:31` ← `server.py:60-65`. Водночас `daemon.py:7-13` явно реалізує й документує Windows-транспорт (`AF_PIPE`), `pyproject.toml:25` декларує `Operating System :: OS Independent`. На Windows `import fcntl` → `ModuleNotFoundError` при імпорті — КОЖНА точка входу (stdio server, CLI) мертва до `main()`. **Регресія, внесена фіксом M1.** Docstring `_vault_lock` каже "POSIX-only" (:150) — але шкода на імпорті, а не на lock.
**Фікс:** guard імпорту (`try/except ImportError` / platform check); у `_vault_lock` — `msvcrt.locking` на Windows або no-op CM з одноразовим stderr-warning (single-writer там уже тримає dispatch lock; flock — defense-in-depth для CLI-шляху).

### F5. Evicted-but-alive primary ніколи не дізнається про eviction → тихий dual-writer — MEDIUM (залишок H1)
`daemon.py:660-667` (процес після 4 невдалих ping-ів unlink-ає lockfile ЖИВОГО primary), `daemon.py:222-226` (`make_primary_endpoint` безумовно unlink-ає socket — пункт #2 оригінального H1-фіксу так і не реалізовано), і головне: **жоден код не ревалідовує володіння lockfile після startup** (grep: нема watchdog/ownership re-check). Primary з wedged accept-loop (deadlock handler на dispatch RLock, >20 s GC/ONNX stall) не відповідає на ping-и → racer видаляє його lockfile і socket, стає primary → старий primary оживає й продовжує обслуговувати вже під'єднаних клієнтів: last-writer-wins на `relations.json`/manifests + LanceDB commit-конфлікти. `release_daemon_lock` (`daemon.py:707-710`) захищає лише новий lockfile на exit.
**Фікс:** (a) primary-side watchdog (timer-thread перечитує `.daemon.lock`; якщо pid не мій — відмовляти writes зі структурованою помилкою й виходити); (b) unlink socket лише коли немає живого lockfile, що на нього вказує.

### F6. "Give up and go standalone" мовчки ламає single-writer invariant — MEDIUM
`daemon.py:680-685` (після `max_retries=5` → `BootstrapResult(role="standalone")`), мовчки споживається `server.py:968-972` і `server.py:813-817` (`ProxyRuntime._failover`). Standalone не має міжпроцесної координації для notes/relations/manifests/LanceDB (flock отримав лише secrets vault). Під сустейненою contention (кілька MCP-клієнтів cold-start при повільному primary) процес деградує до standalone й пише ТОЙ САМИЙ storage root паралельно зі справжнім primary — рівно той lost-update сценарій, що M1 документувала для vault, але для всіх файлів, і лише з одним рядком stderr.
**Фікс:** на retry exhaustion — структурована помилка "daemon unavailable" замість некоординованого запису (або storage-wide `fcntl.flock` навколо writes у standalone, за зразком `_vault_lock`).

### F7. `_repair_project_retrieval_if_needed` — повний corpus-scan на КОЖНИЙ запис — MEDIUM
Викликається безумовно з `_sync_project_note_change` (`server.py:2005`), `_sync_global_note_change` (:2017), `_remove_retrieval_note` (:2027-2029); тіло `server.py:2071-2101` читає КОЖЕН note JSON, КОЖЕН markdown block JSON і всю id-колонку Lance-таблиці (`retrieval_index.py:390-401`, `table.to_arrow()`). M4-фікс прибрав O(corpus) embedding на запис, але замінив його на O(corpus) disk+table I/O на запис — під єдиним dispatch RLock, тобто всі клієнти всіх проєктів стоять. Квадратично для batch-import. Не correctness-баг, але "incremental" заява втрачає сенс на метриці, що важлива для інтерактивного сервера (latency-under-lock).
**Фікс:** пропускати drift check після чистого incremental op (лічильник: кожні K writes або раз на сесію per scope), або трекати очікувані id інкрементально.

### F8. Failover-promoted primary пропускає startup migration і readiness gating — MEDIUM
`server.py:791-809` (`ProxyRuntime._failover` primary branch): listener БЕЗ `ready_event`, немає `_startup_auto_migrate()` / `_warn_about_pending_migrations()` — на відміну від нормального шляху (`server.py:997-1001`). Після self-promotion proxy одразу обслуговує writes проти storage з можливими pending migrations (саме для цього існують `TQMEMORY_MIGRATE_ON_STARTUP` і ready-gate). Наслідок, наприклад: retrieval v3-таблиці отримують incremental merge замість обов'язкового v4 re-embed — id-sets "збігаються", векторні простори різні.
**Фікс:** винести post-bind блок `_run_primary` (migrate → warn → ready) у helper і викликати з `_failover` теж.

### F10. Залишок connection-leak на connect-timeout — LOW
`daemon.py:344-365` (`_connect_bounded`): на timeout caller кидає виняток, але worker-thread живий; якщо `_connect()` зрештою встигає, `result["conn"]` встановлюється після відмови caller і з'єднання ніколи не закривається (+ `_serve_conn` на primary висить до 5 s hello-timeout). Повторні bootstrap-спроби проти wedged primary течуть по одній socket/fd парі у кожному proxy-процесі.
**Фікс:** прапорець `timed_out`; у `_worker` закривати з'єднання, якщо прапорець встановлено.

### F11. `_open_scope_table` мовчки ковтає помилки відкриття → тихий full-table overwrite — LOW
`retrieval_index.py:518-524` (`except Exception: return None`, без логу); callers трактують `None` як "нема таблиці": `_merge_scope_rows` (:457-463) → `create_table(mode="overwrite")`, `server.py:2001-2003` → `sync_project()`. Self-heal корумпованої таблиці — ок, але без жодної діагностики повторювана корупція виглядає як "незрозумілі повільні записи". Це рівно те, на що скаржилися M2/M4.
**Фікс:** логувати `[tqmemory] failed to open retrieval table at <path>: <err>` перед `return None`.

### F12. Дрібні верифіковані пункти — LOW
- `_write_json_atomic` (`store.py:1071-1087`) — ні fsync temp-файлу, ні fsync директорії навколо `os.replace` (див. також M#5).
- Свіжий `lancedb.connect()` на операцію, ніколи не закривається (`retrieval_index.py:455,488,520`); два open на `search` (:296-297). CPython refcounting прибирає, але per-call dataset re-open — latency під глобальним lock. **UNVERIFIED:** чи тримає lancedb fd/mmap достатньо довго, щоб мати значення у long-lived daemon — варто `lsof` під навантаженням.
- `_load_storage_snapshot` cache key лише на manifest `st_mtime_ns` (`server.py:2162-2166,2244-2247`): на FS з 1-секундною гранулярністю mtime (HFS+, деякі мережеві mount-и) дві мутації за одну секунду дають stale counts.

### Correctness — що зроблено добре
- Єдиний dispatch RLock коректно прошитий через stdio і socket dispatchers; lock-order inversion відсутня; deadlock-шлях сконструювати не вдалося.
- `PrimaryUnreachable` vs mid-call `ConnectionError` (`daemon.py:282-291,385-433`) — підручниково правильна семантика RPC-retry для неідемпотентних tools.
- Дизайн H1-мітігації (bind-before-migrate + HELLO не гейтиться + bounded connect) продуманий; коментарі чесно маркують залишковий ризик.
- `_try_claim_lockfile` O_EXCL + pid-liveness + protocol-version; `release_daemon_lock` з pid-ownership guard — коректно.
- Id-based drift repair строго коректніший за попередній count-based; падіння incremental sync → full rebuild З логуванням.
- Vault-locking реалізовано правильно (стабільний lock path, RMW повністю у критичній секції, lock-free reads поверх atomic rename).

---

## §4. Retrieval & Search Quality

### R2. Видалений markdown root назавжди отруює індекс і блокує refresh усіх інших root-ів — MEDIUM
`ingestion.py:105-106` (кидає `FileNotFoundError` на ПЕРШОМУ зниклому root, до обробки решти), `server.py:1938-1945` (catch → тихий `return`), `ingestion.py:94` (pruning лише коли `paths is not None and mode == "full"`).
**Сценарій:** видалити зареєстровану docs-директорію → `is_stale` назавжди → sync plan падає на першому missing root → (1) блоки видаленого root випливають у search безстроково, (2) кожен наступний search наново платить freshness walk і знову падає, (3) зміни у ЗДОРОВИХ root-ах ніколи не підхоплюються. Єдине відновлення — ручний `index_paths(paths=[...], mode="full")`, на який немає жодного сигналу.
**Фікс:** у incremental/paths=None режимі missing root = prune даних цього root (перевикористати `_prune_removed_roots` per-root); raise лишити для явно переданих paths.

### R3. Файл, що виріс понад 5 MiB, вічно віддає старий індексований знімок — MEDIUM
`ingestion.py:133-136` (`source_text is None` → `continue`, наявні блоки й manifest не чіпаються), `ingestion.py:274-276` (freshness check теж `continue` на oversized → не "changed" → `is_stale` false). M3-фікс (size cap) породив цей edge: файл, проіндексований на 1 MiB, виростає понад 5 MiB → пропускається на reindex, старі блоки лишаються, search віддає stale-знімок, freshness каже "не stale".
**Фікс:** коли `_read_source_text` повертає None І manifest існує — видаляти blocks+manifest (як removed), рахувати в `deleted_files`; дзеркально у `assess_project_index_freshness`.

### R4. Падіння embedder = повна відмова search; lexical fallback ніколи не вмикається — MEDIUM
`retrieval_index.py:311` (`query_vector = self._embed_texts([query])[0]` — ПОЗА будь-яким guard), `retrieval.py:159-160` (fallback лише коли `rows` порожні). `_safe_vector_search` захищає від помилок LanceDB, але embed-виклик відбувається раніше. Невдалий ONNX-download / корумпований model cache → `semantic_search` hard-fail, хоча повноцінний lexical-шлях існує. У файлі, де деінде написано "advisory, never breaks search".
**Фікс:** try/except навколо embed+vector-search у `RetrievalIndex.search`; на embed failure — stderr-лог і `return []`, щоб існуючий `_query_scope` lexical fallback увімкнувся.

### R5. Deprecated note може далі ранжуватися — MEDIUM
`server.py:1450` + `1925-1930` (`_sync_with_warning` ковтає падіння у warning-рядок), `server.py:2022-2031`, + read-path gap із X2. Якщо Lance кидає на delete І fallback full rebuild теж кидає — stale row живе; жоден read-шлях id не звіряє → deprecated note ранжується (із `note_status: archived` у payload, але в top-k). Root cause спільний із X2; фікс X2(b) закриває і це.

### R6. `tier_filter` мовчки байпаситься для rows без tier — LOW
`retrieval.py:334-336` (`if row_tier and row_tier not in allowed`), `server.py:1560-1562` (той самий патерн у `recent_context`), `store.py:785-792` (`_normalize_note_record` нормалізує kind/status/provenance, але НЕ `tier`). У SQL-lane `tier IN (...)` відсікає NULL; у lexical fallback і `recent_context` note без `tier` проходить БУДЬ-ЯКИЙ фільтр — включно з `tier_filter=["episodic"]`. Mirror-rows завжди мають tier, тож б'є лише по hand-edited/legacy, але семантика двох lane-ів одного search різна.
**Фікс:** missing tier = `tier_for_kind` default, або виключати — консистентно у всіх трьох місцях.

### R7. `knowledge_lint` ігнорує `.tqmemoryignore`; ingestion шанує — LOW
`knowledge_lint.py:366-381` (нема `_load_ignore_patterns`/`_matches_ignore`) vs `ingestion.py:384,398-401`. Файли, свідомо виключені з індексації, все одно скануються лінтером → orphan/broken-link/duplicate-title шум для контенту, який користувач явно виключив.
**Фікс:** поділити `_iter_markdown_files` з ingestion замість розбіжної копії.

### R8. Зміна embedding-моделі мовчки деградує search до BM25-only, назавжди для read-only — LOW
`retrieval_index.py:552-561` (dimension-mismatch ковтається per query), `server.py:2125-2136` (rebuild trigger дивиться лише `format_version`, не model/dimension). Задокументовано ("switching the model requires reset + reindex"), але ніщо це не ДЕТЕКТИТЬ: після зміни `TQMEMORY_EMBEDDING_MODEL` vector lane кидає на кожен search, ловиться, один рядок stderr — і користувач має FTS-only результати без payload-warning. Якщо агент лише читає — error-triggered rebuild ніколи не запуститься.
**Фікс:** записувати dimension/model у retrieval manifest і трактувати mismatch як format_version change.

### R9. Показаний `score`/`confidence` не включає бонуси, що визначили ранжування — LOW
`retrieval.py:183-196` (ranking key = `effective_score` = base + project bias + kind + provenance; payload експонує лише `score` = base + lexical). У hybrid scope item може бути вищим з НИЖЧИМ показаним `score`, без поля, що пояснює чому (невидимі бонуси до +0.23). Агенти, що калібрують довіру за `confidence`, читають не те число, яке впорядкувало список.
**Фікс:** додати `effective_score`/`ranking_score` у payload.

### Retrieval — що зроблено добре
- Vector-gating/RRF дизайн задокументований, протестований, чесний щодо некаліброваних порогів; SQL-quoting, scope isolation (проєктні retrieval DB per-`project_id`, hybrid лише current project + global), secrets-vault exclusion — коректні. Витоків scope isolation не знайдено.
- RRF math (`retrieval_index.py:654-712`) — стандартний weighted RRF; FTS-only synthetic distance — задокументована свідома евристика.
- Тести `test_fusion_gating.py` (gate 0.82, down-weight 0.3), `test_fts_tokenizer.py`, `test_embedder_dimension.py` відповідають коду — drift-у тест/імплементація не знайдено.
- Deprecated notes виключаються з re-indexing (`store.py:403`, `retrieval_index.py:200-201`).

---

## §5. CLI & Agent UX

### U1. 8 із 20 MCP tools мають ПОРОЖНІЙ description для LLM — HIGH
`server.py:147-160` (`health`, `server_info`, `list_scopes`, `self_test`), `server.py:209` (`promote_note`), `server.py:213` (`deprecate_note`), `server.py:267` (`hydrate`), `server.py:293` (`lint_knowledge_base`) — жодного docstring. FastMCP ставить `description = fn.__doc__ or ""` — **верифіковано live через `list_tools()`**: порожні описи рівно в цих 8. Description — єдина in-band документація, яку бачить агент. Найгірше `hydrate`: core workflow tool з обов'язковим `scope` без default/enum і нульового тексту — агент мусить вгадати `"project"`/`"global"` і звідки брати `item_id`. Добрі docstring-и на `remember_note`/`semantic_search` доводять, що автори розуміють важливість — покриття зроблене наполовину.
**Фікс:** docstring-и всім 8; для `hydrate` розписати `scope` ("скопіюйте поле `scope` із semantic_search hit"), значення `mode`, коли НЕ hydrate-ити.

### U2. `migrate --restore-from` незворотно руйнує live storage без підтвердження і без pre-restore snapshot — HIGH
`cli.py:424-443` — ні prompt, ні snapshot; `migrations/snapshot.py:173` — після успіху staging dir із ЄДИНОЮ копією pre-restore стану видаляється `rmtree`. Help (`cli.py:82`) каже лише "Restore live storage from the given snapshot directory". `--apply` загорнутий у snapshot-then-mutate з надрукованою rollback-командою; `--restore-from` — сам інструмент rollback — такого захисту не має. Tab-completion не на той snapshot = безповоротна втрата всіх нотаток, написаних після нього, і CLI активно знищує докази.
**Фікс:** `create_snapshot()` live-стану перед restore (перевикористати шлях `--apply`), надрукувати шлях як undo-handle, оновити help.

### U3. Нуль parameter descriptions у всій tool-схемі — MEDIUM
Верифіковано live: напр. `hydrate` inputSchema показує лише `{"title": "Scope", "type": "string"}`; жоден tool не використовує `Field(description=...)`/`Annotated` (`server.py:146-425`). FastMCP не парсить docstring-и у per-parameter descriptions. Агенти мусять вгадувати, що `tier_filter` хоче tier names, `source_filter` приймає рівно `"notes"`/`"markdown"`, `paths` у `index_paths` — директорії відносно client cwd.
**Фікс:** `Annotated[str, Field(description=...)]` на high-traffic params (`scope`, `tier_filter`, `source_filter`, `mode`, `paths`, `note_id`).

### U4. Невідомий `note_id` видає сирий errno + абсолютний внутрішній шлях — MEDIUM
`promote_note` → `store.py:448` → `_read_json` (`store.py:1060-1062`) кидає як є. Відтворено: `FileNotFoundError: [Errno 2] No such file or directory: '/Users/admin/.turbo-quant-memory/projects/<hash>/notes/<id>.json'`. Те саме через `deprecate_note` (`store.py:482`) і `hydrate` global (`hydration.py:43` → `store.py:361-362`). Асиметрія: project-hydrate шлях має чисте `Project item not found: <id>` (`store.py:619`) — патерн фіксу існує, його не застосували.
**Фікс:** ловити `FileNotFoundError` у `promote_note`/`deprecate_note`/`read_global_note`, re-raise `ValueError` із scope і підказкою перевірити id через `semantic_search`/`recent_context`.

### U5. Docstring `remember_note` натякає на більше kinds, ніж код приймає — MEDIUM
`server.py:173`: "lesson / decision / pattern / handoff / ..." — але `NOTE_KINDS` рівно ці чотири (`store.py:25`). "..." провокує агента вигадати `"note"`/`"summary"`, отримати `ValueError` (`server.py:1323-1325`) і спалити round-trip.
**Фікс:** прибрати "...", перерахувати всі чотири.

### U6. `secret-set` пускає runtime-context падіння як сирі traceback-и — MEDIUM
`cli.py:486` викликає `build_runtime_context()` без захисту; `_handle_migrate` той самий виклик загортє у try/except із чистим `error:` рядком (`cli.py:225-229`). Та ж дірка: `_handle_prune_orphans` (`cli.py:176`, незахищений `resolve_storage_root()`). Видалений cwd / unwritable `TQMEMORY_HOME` → traceback на першому `secret-set` — найгірший onboarding-момент.
**Фікс:** той самий `except Exception` → `error: cannot resolve storage context: ...` → exit 1, як у `migrate`.

### U7. `secret-set` валідує name ПІСЛЯ того, як користувач увів секрет — LOW
`cli.py:473-489`: getpass/stdin → empty check → `build_runtime_context()` → `vault.set()`, а валідація name живе всередині `vault.set` (`secrets/store.py:364-365` via `:99-103`). На TTY користувач вводить секрет на прихованому prompt, потім дізнається `'bad name!!' must match [A-Za-z0-9_.-]{1,128}` — і вводить заново.
**Фікс:** валідувати name у `_handle_secret_set` до prompt.

### U8. Семантика default-ів `index_paths` і різниця full/incremental не задокументовані — LOW
Docstring (`server.py:282-289`) не каже, що `paths=None` ре-індексує вже зареєстровані root-и (`ingestion.py:304-305`), ні чим `mode="full"` відрізняється від `"incremental"` (full = повний re-embed, дорого). Без root-ів і paths помилка `index_paths requires at least one Markdown root path.` (`ingestion.py:85`) без підказки "pass paths=['.']".
**Фікс:** задокументувати None-default і обидва mode; додати hint у помилку.

### U9. Server-level `instructions` не згадують `recent_context` і secrets tools — LOW
`server.py:130-141` згадує remember/promote/deprecate/hydrate/index/lint/.tqmemoryignore, але не `recent_context` — tool, який власний AGENTS.md проєкту наказує викликати ПЕРШИМ на старті сесії, — і не чотири secrets tools. `instructions` — єдиний канал, який кожен MCP-клієнт читає на connect; сторонні агенти ніколи не знайдуть session-bootstrap flow.
**Фікс:** по рядку на `recent_context` ("call at session start") і secrets ("set via CLI `secret-set`, fetch via `get_secret`").

### U10. Невалідне secret name видається кодом `vault_error` у полі `setup_hint` — LOW
`secrets/store.py:345` кидає `ValueError` на bad name у `get`; `server.py:1709-1715` backstop загортє як `code="vault_error"` з message у `setup_hint`. Клієнти гілкуються за `code`; typo з боку клієнта отримує код, що натякає на серверну корупцію vault, а `setup_hint` не містить жодних setup-кроків.
**Фікс:** ловити `ValueError` окремо у чотирьох secret impls → `code="invalid_name"`.

### U11. Підказка `doctor` про pending migrations вказує на `--status`, який лише переписує вже показане — LOW
`cli.py:590-591`: `Run: turbo-memory-mcp migrate --status`. Користувач уже знає, що migrations pending; actionable крок — `--dry-run` (огляд) або `--apply`.
**Фікс:** змінити hint на `migrate --dry-run` (потім `--apply`).

### U12. `contracts.__all__` не експортує два найновіші phase-списки — LOW
`contracts.py:550-592` експортує `PHASE_1..6` і `PHASE_9`, але не `PHASE_7_TOOL_NAMES`/`PHASE_10_TOOL_NAMES`. Star-import мовчки втрачає найновіші константи. Косметика сьогодні, пастка завтра.
**Фікс:** додати обидва імені.

### CLI/UX — що зроблено добре
- `prune-orphans` — зразковий destructive-op UX: dry-run за замовчуванням, `--apply` переміщує у reversible staging, ніколи не hard-delete.
- `migrate` action group mutually exclusive, default read-only `--status`, `--apply` друкує точну rollback-команду на failure.
- Stale-daemon-lock через PID liveness у `migrate` і `doctor`.
- `secret-set` TTY/pipe duality, прихований getpass, audit parity з MCP-шляхом, окремі задокументовані exit codes (2/3/4/130).
- Структуровані secret error payloads з verbatim `setup_hint` + окреме поле `secret_value` — саме те, що треба агенту.
- Similarity hints у `remember_note`, post-write `hints`, auto-linked `source_refs` — продуманий agent-guidance дизайн.

---

## §6. Docs vs Reality & Functionality Gaps

### D1. "Self-Cleaning Graph" / "smart flagging" — вигадана фіча — HIGH
- `README.md:26` — "Self-Cleaning Graph … Stale relationships are deprecated or unlinked automatically"
- `README.md:99` — "If a linked note grows stale and is deprecated via `deprecate_note()`, the entire connected graph path is smartly flagged as outdated"
- `README.md:101` — "`lint_knowledge_base()` … automatically runs integrity checks on the graph, pinpointing 'orphan' relations"

Реальність: `deprecate_note` (`store.py:473-524`) лише фліпає status; relations ніколи не чіпає. Relations — прості dict-и у `relations.json` (`store.py:834-867`) без state-поля. `get_relations_for_entity` (`store.py:870-880`) повертає як є. У `knowledge_lint.py` **нуль** входжень слова "relation" (grep-верифіковано): лінтер перевіряє markdown links, orphan markdown, duplicate titles, stale episodic, near-duplicate notes — ніколи graph relations. Search/hydration enrichment (`retrieval.py:284-289`) і далі віддає relations, що вказують на deprecated notes, без жодного попередження.
**Фікс:** або реалізувати (a) relation-state inheritance у `get_relations_for_entity` (анотувати relations із deprecated/неіснуючим `note://` endpoint) і (b) issue-kind `dangling_relation` у `lint_knowledge_base` — або видалити claims із усіх трьох README.

### D2. README-приклади `link_entities` вчать відхиленій URI-схемі, неправильним іменам параметрів і тихо-неспрацьовуючим file URI — HIGH
`README.md:254-255` (+ ru `:247-248`, uk `:247-248`): `link_entities(source="note:[note_id]", target="file:///absolute/path/to/src/auth.py", …)`.
- Фактична сигнатура — `source_uri`/`target_uri` (`server.py:303-308`).
- `note:[id]` **відхиляється** валідатором: hierarchical schemes вимагають `//` (`server.py:1118-1134`, помилка `:1141-1145`). README вчить рівно тій typo, проти якої код захищений.
- `file:///absolute/path` проходить валідацію, але **мовчки ніколи ні з чим не матчиться**: індексовані блоки зберігають ВІДНОСНІ шляхи (`ingestion.py:116`), enrichment шукає `file://{relative_path}` (`retrieval.py:282`; tool docstring `server.py:313` описує правильно). Relation, створений за README, пишеться у чорну діру.

Аудиторія цих інструкцій — AI-агенти: кожен relation за документацією або hard-error, або silent no-op. Флагманський workflow фічі неюзабельний за задокументованим шляхом.
**Фікс:** виправити всі три README на `link_entities(source_uri="note://<id>", target_uri="file://relative/path.py", …)` + примітка про project-root-relative; опційно warning у `link_entities` на абсолютний `file://` URI.

### D3. Version drift: README пінить v0.20.1 при релізі 0.22.0 — і суперечить сам собі — MEDIUM
`README.md:1` (title v0.20.1), `:33` (stats v0.20.1), `:69` (`uv tool install …@v0.20.1`); `pyproject.toml:7` = `0.22.0`; RU/UK README теж v0.20.1. Гірше: README §4 (`:128-136`) описує "ONNX Runtime (fastembed) by default — no PyTorch" — фіча 0.21.0; користувач за quickstart тієї ж README ставить 0.20.1, де default досі PyTorch. Install-команда суперечить опису фічі за два екрани.
**Фікс:** бампати версійні піни у трьох README на релізі (автоматизувати у `scripts/refresh_readme_stats.py` або release checklist); quickstart на `@v0.22.0` або без тегу.

### D4. TECHNICAL_SPEC застарів за трьома осями — MEDIUM
- Tool surface table (`TECHNICAL_SPEC.md:103-122`) — 18 tools, без `recent_context` (реалізований `server.py:393+`; README:160 коректно каже 19). Контрактний документ — і він неправильний.
- Tech stack: "Embeddings | Sentence Transformers with a lightweight local model" (`:49`) — неправда з 0.21.0; fastembed — core dep, sentence-transformers — opt-in `[torch]` extra (`pyproject.toml:38-52`).
- Deployment contract пінить install на `@v0.2.4` (`:215-216`) — стародавній тег до ери tiers/relations/secrets/migrations.

**Фікс:** рядок `recent_context`, оновити embeddings row, прибрати/бампнути тег.

### D5. "600-token summaries" — завищено на порядок — MEDIUM
`README.md:18` — "fetch only highly-relevant 600-token summaries". Фактично: `_build_compressed_summary(title, text, limit=220)` — 220 **символів** (`retrieval.py:373`, truncation `:381`); `recent_context` previews теж 220 chars (`server.py:1582`). Це ~55 токенів, не 600. Цифра годує наратив "Cost-Saving Magic", на якому тримається README.
**Фікс:** виправити README на реальний розмір envelope або цитувати виміряний середній розмір з `benchmarks/latest.json`.

### D6. Hermes-секція використовує неіснуюче ім'я пакета — MEDIUM
`README.md:279` — `uv tool install turbo-quant-memory`. Дистрибутив — `turbo-memory-mcp` (`pyproject.toml:6`; `dist/turbo_memory_mcp-0.21.0-*.whl`). Якщо опубліковано на PyPI під реальним ім'ям — команда 404-ить; якщо ні — падає в будь-якому разі; усі інші секції ставлять із git URL.
**Фікс:** `uv tool install turbo-memory-mcp` (або git URL, консистентно з рештою README).

### D7. Немає delete / export / backup для user memory (GDPR-style gap) — MEDIUM
MCP-surface (`server.py:146-403`) не має `delete_note`; `deprecate_note` лише архівує (`store.py:510-512`; TECHNICAL_SPEC:79 явно обіцяє "without deleting history"). Secrets отримали `delete_secret`; notes — ні. Немає export tool, немає backup tool (migration snapshots — внутрішні rollback-aid, незадокументовані як backup), немає задокументованого способу стерти вміст нотатки. Користувач, що вставив персональні дані у note, мусить руками редагувати JSON у `~/.turbo-quant-memory` — ніде не задокументовано.
**Фікс:** `delete_note` tool (hard delete + retrieval-index removal) або щонайменше задокументувати ручний шлях і відсутність export.

### D8. `telemetry.py` — модуль за trust-headline README — без власних тестів — LOW
47 тестових файлів; telemetry з'являється лише інцидентно у 3 (path existence). Жоден тест не покриває `record_search`/`record_hydration`, `_build_headline` (`telemetry.py:232`), cost-basis math, `_migrate_usage_stats` (`telemetry.py:138`). При цьому `README.md:31` робить `usage_stats.headline` якірцем довіри "verify it yourself".
**Фікс:** `test_telemetry.py`: інкременти лічильників, форматування headline, міграція stats.

### D9. User-facing env vars незадокументовані — LOW
`TQMEMORY_INPUT_COST_PER_1M_TOKENS_USD` (`telemetry.py:10` — потрібна для USD-економії, яку рекламує README), `TQMEMORY_HOME` (`store.py:19`), `TQMEMORY_DAEMON_DISABLE` (`daemon.py:18`), `TQMEMORY_EPISODIC_STALE_DAYS` (`knowledge_lint.py:107`), `TQMEMORY_SNAPSHOTS_KEEP` (`migrations/snapshot.py:19`), `TQMEMORY_EMBEDDING_MODEL` (`retrieval_index.py:25`) — у ЖОДНОМУ з чотирьох user docs (grep-верифіковано).
**Фікс:** секція configuration/env-var reference у README або TECHNICAL_SPEC.

### D10. MEMORY_STRATEGY result-card contract роз'їхався — LOW
Задокументовані поля card ("Result Card Contract") не включають фактично повернені: `relations`, `tier`, `note_status`, `provenance`, `warning` (`retrieval.py:250-289`). `source_filter` (headline-фіча 0.22.0) задокументований у README/AGENTS.md, але не у search-policy MEMORY_STRATEGY.
**Фікс:** синхронізувати список полів і search-policy.

### D11. Benchmark-числа реальні, але застарілі відносно claims — LOW
83.79% / ~400 ms чесно походять з `benchmarks/latest.json` — добре. Але файл згенеровано 2026-05-29, ~8 релізів тому, на PyTorch backend; README подає "~400 ms … incl. CPU query embedding" (`:23`) як поточне, хоча default embedder змінено на fastembed у 0.21.0. Latency нового default для README ніколи не перемірювали. Tagline "Save up to 60%" (`:3`) суперечить headline 83.79% нижче.
**Фікс:** перепустити `scripts/benchmark_context_savings.py` на fastembed default; вирівняти tagline з виміряним.

### Docs — що зроблено добре
- Env-var claims, що існують, сходяться: `TQMEMORY_FTS_LANGUAGE` із safe fallback, `TQMEMORY_MIGRATE_ON_STARTUP` із `migration_auto_result` у `health()`, secrets-доки (`secret_value`, threat model, keyring-vs-passphrase warning) точні.
- MCP tool count (19) точний; усі задокументовані tools існують.
- Atomic-write claim у MEMORY_STRATEGY реальний (`store.py:1071-1084`).
- `prune-orphans` консервативний і reversible; tiered-memory defaults у README відповідають `retrieval.py:71-83`.
- CHANGELOG-записи 0.21.0/0.22.0 точні відносно коду.

---

## §7. Migrations & Data Integrity

### M#1. Чисті інсталяції НАЗАВЖДИ звітують "pending migrations" для NOTES і SECRETS — HIGH
`store.py:54-55` (`NOTES_FORMAT_VERSION = 1`, `SECRETS_FORMAT_VERSION = 2`), `store.py:186` (`max(existing, NOTES_FORMAT_VERSION)` штампує свіжі manifests v1), `migrations/runner.py:259-280` (SECRETS евристика: є `projects/<id>/` + нема marker ⇒ v1), `migrations/registry.py:130-154` (`latest_version` = 2 для обох).
**Відтворено емпірично** (свіжий storage root, один `write_project_note`, реальний registry): `notes: current=1 latest=2 pending=1`, `secrets: current=1 latest=2 pending=1`. Кожне нове встановлення друкує migration warning на кожен daemon startup (`server.py:971,1000`), звітує `migrations.pending=True` у кожному `server_info` і валить `doctor` check (`cli.py:586-594`) — вічно, доки користувач не запустить `migrate --apply` на store, що ніколи не був застарілим. Коментар-обґрунтування `store.py:56-57` неправильний: legacy-інсталяції ловить `_legacy_v1_or_format_version` (`runner.py:284-294`) за відсутністю поля, незалежно від константи. Тестовий набір це маскує: autouse `clear_registry()` (`tests/test_migrations.py:56-61`) змушує `test_server_info_migration_collection_clean_store` (:1553) проходити лише тому, що `latest_version` у тестах колапсує до константи. Найгірше: це тренує всіх користувачів ігнорувати migration warnings — коли прийде СПРАВЖНЯ міграція, її відхилять як звичний шум.
**Фікс:** `NOTES_FORMAT_VERSION` → 2 (legacy manifests без поля все одно читаються як v1); для SECRETS — `provision()`/перший vault touch пише `secrets-manifest.json` v2. Регресійний тест: чиста інсталяція звітує нуль pending БЕЗ очищення registry.

### M#2. `detect_status` падає на valid-JSON-wrong-type manifests — MEDIUM
(детально — §1 X5). Hardening `runner.py:199-202` покриває `JSONDecodeError`, але hand-edited manifest із валідним JSON неправильного типу валить `migrate --status/--apply` і `doctor` сирим traceback-ом. Та ж дірка у `telemetry.py:16-20`.
**Фікс:** `isinstance`-guard + тести з `[1,2]`, `"str"`.

### M#3. `migrate --snapshot-only` пропускає live-daemon check — torn LanceDB backups — MEDIUM
`cli.py:407-410` (`_migrate_snapshot_only` викликає `create_snapshot` напряму). Порівняти `--apply` (`cli.py:332-341`) і `--restore-from` (`cli.py:427-435`) — обидва викликають `_daemon_lockfile_present`. `migrations/snapshot.py:89-97` копіює live-файли без гарантій консистентності. Snapshot живого store може бути внутрішньо torn (часткові Lance-фрагменти); пізніший `--restore-from` вірно відновить цю корупцію — restore не має жодної перевірки цілісності snapshot. Повідомлення "Snapshot created at: …" дає повну впевненість у потенційно марному backup.
**Фікс:** `_daemon_lockfile_present` у `_migrate_snapshot_only` (warn або `--force`); snapshot metadata має фіксувати daemon state, restore — попереджати.

### M#4. RETRIEVAL v1→v4 chain: два повних wipe+re-embed цикли, помножені на кількість проєктів — MEDIUM
`migrations/upgrades.py:51-70` (v1→v2: `reset_scope` project+global, потім `sync_project`+`sync_global`) і `upgrades.py:141-161` (v3→v4: ідентичний reset+full resync). Runner застосовує обидва поспіль для v1-інсталяції. RETRIEVAL manifests per-project (`store.py:123-124`), global-таблиця спільна — `runner.py:231-241` бере `min(current_project, global)`. Legacy v1 платить `reset+re-embed-всього` у v1→v2, а потім ЗНОВУ у v3→v4 — робота v1→v2 викидається за хвилини. Re-embedding — найдорожча операція системи (docstring `upgrades.py:150-152` визнає "slowest retrieval migration"). Per-project дизайн множить: кожен проєкт мігрує при своєму запуску, і кожен такий запуск ТАКОЖ reset-ить і re-embed-ить СПІЛЬНУ global-таблицю (2 повних global re-embeds на проєкт, N проєктів ⇒ 2N). Плюс повний storage-root snapshot перед кожним запуском (`cli.py:358`) — дублювання мульти-GB LanceDB-директорій.
**Фікс:** колапсувати послідовні destructive rebuilds (прапорець `reset_only` на v1→v2, коли chain продовжується до v3→v4); global-таблицю мігрувати раз на storage root, гейтити за global manifest, а не `min()` із поточним проєктом.

### M#5. "Atomic" JSON-записи ніколи не fsync-ять; docstring `io.py` стверджує зворотнє — MEDIUM
`migrations/io.py:16-29` — docstring каже *"fsync via close"*; `close()` нічого такого не робить. `store.py:1071-1087` — `os.replace` без fsync файлу чи директорії. Контраст: `secrets/store.py:114` fsync РОБИТЬ — кодова база знає правильний патерн і застосовує його неконсистентно. Manifests — "source of truth" (`runner.py:12-14`), уся crash-recovery історія ("manifest оновлюється ОСТАННІМ") припускає durability rename. На power loss після `os.replace` directory entry може не дійти до диска — manifest зникає або revert-иться у zero-length файл (який потім б'є по X5). Note JSON (user data) — той самий ризик.
**Фікс:** `tmp.flush(); os.fsync(tmp.fileno())` перед close в обох writers + fsync батьківської директорії після `os.replace`; виправити docstring `io.py`. (macOS: `fcntl F_FULLFSYNC` для справжньої durability, plain fsync — портативний baseline.)

### M#6. Краш посеред restore лишає live root порожнім; staging прихований і ніколи не прибирається — MEDIUM
`migrations/snapshot.py:133-171` — крок 1 переміщує ВЕСЬ live state у `.snapshots/.restore_staging_<stamp>/`, крок 2 копіює snapshot назад. Hard crash (SIGKILL, power loss) між кроками → `storage_root` містить лише `.snapshots/`. `.restore_staging_*` dot-prefixed → `list_snapshots` їх виключає (`snapshot.py:51-56`), жодна команда їх не показує, ніщо їх не видаляє (`_prune_old` ітерує лише `list_snapshots`, `:187-194`). In-exception rollback реалізований добре і протестований (`test_migrations.py:491-519`) — але винятки не єдиний failure mode; kill-window між кроками триває секунди й покриває весь store. Після такого крашу користувач бачить ПОРОЖНІЙ store, а дані лежать у прихованій директорії, про яку жодна команда не згадує. Окремо: перервані restore течуть staging dirs на повний розмір store назавжди.
**Фікс:** на startup / `migrate --status` / `doctor` — детектити `.restore_staging_*` і або автозавершувати rollback, або друкувати явні recovery-інструкції; той самий sweep для `.building_*`.

### M#7. Помилки `log_event` не ізольовані — повний диск валить apply ПІСЛЯ успіху — LOW
`migrations/log.py:41-43` (append може кинути `OSError`). Виклики без захисту: `runner.py:129` (перед роботою), `runner.py:173` (УСЕРЕДИНІ failure handler — disk-full-індукована помилка міграції буде ЗАМАСКОВАНА винятком логера), `runner.py:185` (після успішного кроку + bump manifest). `cli.py:372` викликає `apply_pending` без try/except; `cli.py:618` без top-level handler. Підсумок: міграція успішна, manifest бампнуто, CLI падає з traceback — користувач вважає міграцію невдалою і тягнеться до `--restore-from`, відкочуючи хорошу міграцію.
**Фікс:** try/except навколо `log_event` (log-to-nowhere) або `log_event` ніколи не кидає.

### M#8. Безмежні append-only логи — LOW
`migrations/log.py:40-43` (`migration.log`, без ротації) + `secrets/audit.py` (див. S6). `migration.log` low-volume — переважно косметика; `audit.jsonl` росте з рутинним agent-usage назавжди, per project; snapshots копіюють ці дедалі більші логи.
**Фікс:** size-capped ротація для обох.

### M#9. `TQMEMORY_SNAPSHOTS_KEEP=1` мовчки ламає keep-≥2 safety design — LOW
`migrations/snapshot.py:20-23` документує, чому keep має бути ≥2 (re-run невдалого `--apply` snapshot-ить напівмігрований стан і не повинен викидати чистий pre-migration snapshot). Але `_keep_count()` (`:27-35`) клампить `max(1, n)` — дозволяючи рівно ту небезпечну конфігурацію.
**Фікс:** кламп `max(2, n)` або гучний warning при keep < 2.

### M#10. `migrations/io.py` тече temp-файлами на write failure; залишки забруднюють note scans — LOW
`io.py:16-29` без try/finally cleanup (на відміну від `store.py:1085-1087`). Tempfile з `prefix=".tmp-", suffix=path.suffix` → крашнутий migration write лишає `.tmp-XXXX.json` у notes dir; pathlib glob `*.json` МАТЧИТЬ dotfiles → `list_notes` (`store.py:395`) пробує його парсити, `scan_quarantined_notes` (`store.py:405-420`) репортить як корупцію вічно.
**Фікс:** дзеркалити `store.py` finally-cleanup; note scans пропускають `.tmp-*`.

### M#11. Version-skip / downgrade drift звітується як "OK" — LOW (інформаційно)
`runner.py:210-224` — якщо `current > latest` (store новішого релізу, відкритий старим бінарником) або `current < latest` з порожнім chain (USAGE_STATS v1 без зареєстрованих кроків) — статус `pending=[]`, тобто "up to date". Для USAGE_STATS self-heal через eager migration (`telemetry.py:20-23`), але справжній downgrade — старий бінарник мовчки читає newer-format store без жодного warning.
**Фікс:** `current > latest` — явний "store newer than this binary" warning у `--status` і `doctor`.

### Migrations — що зроблено добре
- Manifest-bumped-last ordering з idempotent, resumable кроками + тест, що доводить семантику retry невдалого кроку (`test_migrations.py:222-251`).
- Snapshot staging з atomic publish (`.building_` + `os.replace`) — напівзаписані snapshots не вибираються; restore відкладає live state і відкочується на copy failure — обидва failure-шляхи мають реальні тести.
- Live-PID lockfile check перед `--apply`/`--restore-from` зі stale-lock reclamation, тестований на dead і live PID.
- `write_*_manifest` зберігає вищий on-disk `format_version` з dedicated регресійним тестом.
- Registry validation: single-step enforcement, duplicate detection, gap detection — усе тестовано.
- Legacy (безполеві) manifests детектяться як v1 end-to-end.

---

## §8. Рекомендований порядок виправлень

1. **S1 + S2** (ingestion confinement — єдиний реальний exfil-примітив).
2. **Сім'я corrupt-file (X1–X6)** — один спільний quarantine/guard-helper, ~день роботи, закриває шість знахідок включно з HIGH X1/X2.
3. **F1** (Windows import guard — тривіально), **M#1** (фантомні міграції — дешево, повертає довіру до warning-каналу).
4. **U1/U3 + D1/D2** (docstring-и, schema descriptions; реалізувати або видалити graph-claims; виправити README-приклади) — agent-facing поверхня наполовину не написана, а флагманський graph-workflow падає для всіх, хто слухняно читає доки.
5. **U2** (snapshot перед restore) + **M#5/M#6** (fsync, restore-staging recovery) — захист даних у нештатних сценаріях.
6. **F5/F6/F8** (single-writer escape hatches — потребує дизайн-рішення: watchdog + refuse-writes-on-standalone, або storage-wide flock).
7. Решта — hardening (R2–R9, M#3/M#4, F7/F8, D3–D11, низькі UX).

## Підсумковий вердикт

Кодова база чесніша за документацію, що її продає. Інженерія ядра (vault, daemon, migrations) — вище середньої для проєкту такого розміру; попередній аудит опрацьовано сумлінно, без імітації виправлень. Але: (1) фікси застосовуються точково, без пошуку сусідніх екземплярів того самого класу дефекту — сім'я X1–X6 це демонструє; (2) ingestion-межа довіри (symlinks, довільні root-и) не сприймалася як attack surface; (3) документація пишеться аспіраційно і не регресійно звіряється з кодом — "self-cleaning graph", неправильні README-приклади, "600-token summaries"; (4) agent-facing discovery surface (tool descriptions, parameter schemas, server instructions) — найслабша частина продукту, чия цінність саме у тому, що "агенти використовують це добре". Жодна знахідка не потребує архітектурної революції; більшість закривається малими, добре локалізованими змінами.
