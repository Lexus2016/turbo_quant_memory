# Phase 2 Research: Namespace Model

**Researched:** 2026-03-25  
**Status:** Complete

## Research Goal

Determine the narrowest technically honest Phase 2 that proves:

- the current repository resolves to a deterministic `project` namespace;
- reusable cross-project memory can live in a separate `global` namespace;
- agents can search across `project`, `global`, and `hybrid` with deterministic precedence;
- every result carries enough provenance to be trustworthy without bloating token volume.

Визначити найвужчу технічно чесну Фазу 2, яка доводить:

- поточний репозиторій детерміновано резолвиться в `project` namespace;
- reusable cross-project memory може жити в окремому `global` namespace;
- агенти можуть шукати по `project`, `global` і `hybrid` з детермінованим пріоритетом;
- кожен результат має достатній provenance для довіри, але без зайвого token-bloat.

## Confirmed Findings

### 1. Git CLI is enough for deterministic project identity

- Official Git docs confirm `git rev-parse --show-toplevel` returns the absolute top-level working tree path.
- Official Git docs confirm `git remote get-url` returns the configured remote URL and expands `insteadOf`/`pushInsteadOf`, which is useful for deriving a stable identity string from configured remotes instead of raw config parsing.
- For local-only repositories, the resolved repo root path is a valid deterministic fallback input for `project_id`.
- This means Phase 2 does not need `GitPython` or another extra dependency just to resolve project identity.

- Офіційна документація Git підтверджує, що `git rev-parse --show-toplevel` повертає абсолютний шлях до top-level working tree.
- Офіційна документація Git підтверджує, що `git remote get-url` повертає налаштований remote URL і вже розгортає `insteadOf`/`pushInsteadOf`, що корисно для стабільного identity-string без ручного парсингу конфігів.
- Для локальних репозиторіїв без remote вже достатньо абсолютного repo-root path як детермінованого fallback-джерела для `project_id`.
- Отже, для Phase 2 не потрібен `GitPython` або інша додаткова залежність лише для project identity.

### 2. Central storage can stay file-backed and dependency-light in Phase 2

- The locked Phase 2 context already prefers one central storage root under `~/.turbo-quant-memory/`.
- Phase 2 only needs namespace safety and note persistence; it does **not** need full Markdown ingestion or vector search yet.
- Because of that boundary, Phase 2 can use stdlib-backed note/manifests files and defer LanceDB integration to the ingestion/retrieval phases where it starts paying for itself.
- This keeps Phase 2 aligned with the “easy local deployment” promise and avoids front-loading storage complexity before retrieval quality matters.

- Зафіксований context Фази 2 уже віддає пріоритет одному central storage root у `~/.turbo-quant-memory/`.
- Для Фази 2 потрібні лише namespace safety і note persistence; повний Markdown-ingestion або vector-search ще не потрібні.
- Через це Phase 2 може спиратися на stdlib-backed note/manifests files і відкласти LanceDB до фаз ingestion/retrieval, де вона вже реально дає користь.
- Це тримає Фазу 2 в рамках обіцянки “easy local deployment” і не тягне storage-complexity раніше часу.

### 3. Atomic file writes are available in the Python standard library

- Python docs warn against historical name-then-open temp file patterns and point to secure temp file creation through `NamedTemporaryFile()` or `mkstemp()`.
- Python docs state `os.replace()` performs an atomic replacement when source and destination are on the same filesystem.
- Together, that gives a simple Phase 2 persistence strategy: write manifests or notes to a temp file in the target directory, then replace the target atomically.
- This is sufficient for a local single-user Phase 2 foundation and can later be wrapped with heavier locking only if usage proves it necessary.

- Документація Python застерігає від історичного патерну “спочатку ім’я temp-файла, потім open” і рекомендує безпечне створення тимчасових файлів через `NamedTemporaryFile()` або `mkstemp()`.
- Документація Python прямо вказує, що `os.replace()` виконує атомарну заміну, якщо source і destination на одній файловій системі.
- Разом це дає просту стратегію для Фази 2: писати manifests або notes у temp-file в тій самій директорії, а потім атомарно міняти цільовий файл.
- Цього достатньо для local single-user foundation у Phase 2; важчий locking можна додати пізніше, якщо usage це реально вимагатиме.

### 4. The MCP SDK supports structured tool payloads without extra protocol work

- Official MCP Python SDK examples show that high-level tools can return dictionaries, `TypedDict`, or Pydantic models as structured output.
- The current repo already uses dict-based structured payloads in `contracts.py`, which fits the SDK guidance and avoids unnecessary tool-schema churn.
- That means Phase 2 can extend existing tools and add namespace-aware tools without changing transports, clients, or the overall server architecture.

- Офіційні приклади MCP Python SDK показують, що high-level tools можуть повертати словники, `TypedDict` або Pydantic-моделі як structured output.
- Поточний репозиторій уже використовує dict-based structured payloads у `contracts.py`, що добре збігається з цими рекомендаціями й не вимагає зайвої schema-churn.
- Отже, Phase 2 може розширити існуючі tools і додати namespace-aware tools без зміни transport, клієнтів або загальної архітектури сервера.

### 5. Phase 2 should ship note-centric namespace behavior, not final retrieval quality

- The roadmap scopes Phase 2 to namespaces and precedence, while Phase 3 owns ingestion and Phase 4 owns retrieval quality.
- Therefore, Phase 2 should prove namespaced persistence and query routing over stored notes or memory records, not over the future Markdown ingestion corpus.
- The most stable way to avoid premature API lock-in is to add note-centric storage/query behavior now and leave full source-block retrieval and compression behavior to later phases.

- ROADMAP відносить Phase 2 до namespaces і precedence, тоді як Phase 3 відповідає за ingestion, а Phase 4 — за retrieval quality.
- Тому Фаза 2 має довести namespaced persistence і query routing на stored notes або memory records, а не на майбутньому Markdown-ingestion corpus.
- Найстабільніший спосіб не зацементувати передчасний API — додати note-centric storage/query behavior зараз, а повний source-block retrieval і compression залишити на наступні фази.

## Planning Implications

### Identity and Config

- Use the Git CLI through `subprocess`, not a new Git library dependency.
- Resolve the repo root first, then remote URL, then normalize into a canonical identity string.
- Support explicit overrides through environment variables or a lightweight repo-level manifest/config, but keep zero-config as the default path.
- Persist the resolved `project_id`, `project_name`, and source identity inside a project manifest under the central store.

- Використовувати Git CLI через `subprocess`, а не нову Git-бібліотеку.
- Спочатку резолвити repo root, потім remote URL, а далі нормалізувати це у canonical identity string.
- Підтримувати explicit overrides через environment variables або легкий repo-level manifest/config, але zero-config лишити дефолтом.
- Зберігати резолвлені `project_id`, `project_name` і source identity у project manifest всередині central store.

### Storage Layout

- Keep the central root at `~/.turbo-quant-memory/`.
- Use deterministic partitions such as `projects/<project_id>/...` and `global/...`.
- Phase 2 can store notes and manifests as JSON files plus directory structure; vector storage can wait until Phase 3/4.
- The repository should only need a lightweight local config or manifest when an override is necessary.

- Тримати central root у `~/.turbo-quant-memory/`.
- Використовувати детерміновані partition-и на кшталт `projects/<project_id>/...` і `global/...`.
- У Фазі 2 notes і manifests можна зберігати як JSON-файли + directory structure; vector storage може зачекати до Phase 3/4.
- Репозиторію потрібен лише легкий local config або manifest, коли справді потрібен override.

### Query Behavior

- `project`, `global`, and `hybrid` remain the public query modes.
- `hybrid` should merge results from both scopes with a strong project bonus instead of hiding global hits behind a hard fallback.
- Deterministic tie-breaking should prefer `project`, then newer `updated_at`, then stable item identity.
- Phase 2 query can stay lexical/note-oriented as long as the envelope and precedence contract already match the final namespace design.

- `project`, `global` і `hybrid` лишаються публічними режимами запиту.
- `hybrid` має зливати результати з обох scopes із сильним project-bonus, а не ховати global hits за жорстким fallback.
- Детермінований tie-break має віддавати пріоритет `project`, потім новішому `updated_at`, а потім стабільному item identity.
- Query у Фазі 2 може лишатися lexical/note-oriented, якщо envelope і precedence contract уже відповідають фінальному namespace-design.

### Write / Promotion Model

- Default writes should go to `project`.
- `global` should be populated through explicit promotion from an existing project note, preserving provenance through `promoted_from`.
- Direct public writes to `global` are not needed to satisfy Phase 2 and would weaken the safety model too early.

- Дефолтні записи мають іти в `project`.
- `global` має поповнюватися через explicit promotion з існуючої project-note зі збереженням provenance через `promoted_from`.
- Прямі публічні записи в `global` не потрібні для виконання Phase 2 і занадто рано послабили б safety model.

### Result Envelope

- The default result envelope should stay compact but trustworthy.
- Required default fields: `scope`, `project_id`, `project_name`, `source_kind`, `item_id`/`block_id`, `source_path`, `updated_at`, `confidence`, `can_hydrate`, and `promoted_from` when relevant.
- Heavier lineage/debug fields should remain optional, not part of every response.

- Дефолтний result envelope має лишатися компактним, але trustworthy.
- Обов’язкові поля за замовчуванням: `scope`, `project_id`, `project_name`, `source_kind`, `item_id`/`block_id`, `source_path`, `updated_at`, `confidence`, `can_hydrate` і `promoted_from`, коли це релевантно.
- Важчі lineage/debug-поля мають бути optional, а не частиною кожної відповіді.

## Risks and Mitigations

### Risk: project identity drifts across clone URLs or transport variants

- Mitigation: normalize remote URLs before hashing, strip `.git`, normalize host casing, and support explicit override for edge cases.

### Risk: Phase 2 accidentally locks in the wrong long-term retrieval API

- Mitigation: make Phase 2 note-centric and namespace-centric, while keeping final retrieval-quality work deferred to Phases 3-5.

### Risk: global memory becomes a dumping ground

- Mitigation: keep default writes project-scoped and require explicit promotion with provenance preservation.

### Risk: persistence becomes brittle or partially written on crashes

- Mitigation: use stdlib temp-file creation and `os.replace()` for atomic writes in the same directory.

### Risk: default metadata gets too heavy and wastes tokens

- Mitigation: keep the compact standard envelope as the default and leave full lineage/debug expansions for explicit or later-phase calls.

### Ризик: project identity “пливе” між clone URL або transport variants

- Пом’якшення: нормалізувати remote URLs перед hashing, відрізати `.git`, нормалізувати host casing і підтримати explicit override для edge cases.

### Ризик: Phase 2 випадково зацементує неправильний довгостроковий retrieval API

- Пом’якшення: зробити Phase 2 note-centric і namespace-centric, а всю retrieval-quality роботу лишити на Phases 3-5.

### Ризик: global memory перетвориться на dumping ground

- Пом’якшення: тримати default writes у `project` і вимагати explicit promotion зі збереженням provenance.

### Ризик: persistence буде крихкою або частково записуватиметься при падіннях

- Пом’якшення: використовувати stdlib temp-file creation і `os.replace()` для атомарних записів у тій самій директорії.

### Ризик: default metadata стане надто важкою і марно спалюватиме токени

- Пом’якшення: залишити compact standard envelope дефолтом, а повні lineage/debug expansions винести в explicit або пізніші фази.

## Recommended Phase Split

### Plan 01 — Identity and central namespace store foundation

- Resolve deterministic project identity, central storage paths, manifests, note records, and promotion lineage primitives.

### Plan 02 — Namespace-aware MCP contract and query behavior

- Extend server contracts and tools so notes can be written, promoted, and queried across `project`, `global`, and `hybrid` with deterministic precedence.

### Plan 03 — Namespace docs and smoke validation

- Update operator docs and smoke paths so the new namespace behavior is visible, testable, and aligned with the published runtime contract.

### Plan 01 — Foundation identity/store

- Резолвити детерміновану project-identity, central storage paths, manifests, note records і primitives для promotion lineage.

### Plan 02 — Namespace-aware MCP contract

- Розширити server contracts і tools, щоб notes можна було писати, promote-ити і запитувати через `project`, `global` і `hybrid` з детермінованим precedence.

### Plan 03 — Docs and smoke validation

- Оновити operator docs і smoke paths, щоб нова namespace-behavior була видимою, тестованою і узгодженою з опублікованим runtime contract.

## Validation Architecture

### Automation Boundary

- Local automation should prove deterministic project resolution, central-store persistence, promotion lineage, hybrid precedence, and compact provenance envelopes.
- External editor/client hosts remain out-of-repo concerns; Phase 2 validation inside the repo should focus on server/runtime behavior and smoke the MCP process directly.

- Локальна автоматизація має доводити deterministic project resolution, central-store persistence, promotion lineage, hybrid precedence і compact provenance envelopes.
- Зовнішні editor/client hosts залишаються поза репозиторієм; у межах репозиторію Phase 2 validation має фокусуватися на server/runtime behavior і напряму smoke-ити MCP-процес.

### Fast Feedback Loop

- Quick automated loop: `uv run pytest -q`
- Full automated loop: `uv run pytest -q && uv run python scripts/smoke_test.py`

- Швидкий автоматизований цикл: `uv run pytest -q`
- Повний автоматизований цикл: `uv run pytest -q && uv run python scripts/smoke_test.py`

### Required Automated Focus

- Identity resolution tests should cover: remote-first identity, no-remote fallback, and explicit override.
- Storage tests should cover: central root layout, atomic note writes, and promotion lineage.
- MCP contract tests should cover: scope statuses, namespace-aware note/query tools, deterministic precedence, and compact provenance envelopes.
- Smoke validation should exercise the live stdio server over the same `uv run turbo-memory-mcp serve` contract used in docs.

- Тести identity мають покривати: remote-first identity, no-remote fallback і explicit override.
- Тести storage мають покривати: central root layout, atomic note writes і promotion lineage.
- MCP contract тести мають покривати: scope statuses, namespace-aware note/query tools, deterministic precedence і compact provenance envelopes.
- Smoke validation має проганяти живий stdio server через той самий `uv run turbo-memory-mcp serve`, який уже використовується в документації.

## Source Map

- MCP Python SDK via Context7: `/modelcontextprotocol/python-sdk`
- Git `rev-parse` docs: `https://git-scm.com/docs/git-rev-parse`
- Git `remote` docs: `https://git-scm.com/docs/git-remote`
- Python `tempfile` docs: `https://docs.python.org/3/library/tempfile.html`
- Python `os.replace` docs: `https://docs.python.org/3/library/os.html`

---
*Phase: 02-namespace-model*  
*Research completed: 2026-03-25*
