# Memory Strategy / Стратегія пам'яті

## 1. Implemented Shape / Реалізована форма

The implemented shape is **one core local stdio MCP server** with **thin client-specific configs**.

Реалізована форма: **один core local stdio MCP server** з **тонкими client-specific config-обгортками**.

There is no separate memory implementation per agent.

Окремої реалізації пам'яті під кожного агента немає.

## 2. Active Namespaces / Активні namespace

Phase 2 implements two stored namespaces and one merged read mode:

Phase 2 реалізує два stored namespace і один merged read mode:

1. `project`
2. `global`
3. `hybrid`

### `project`

- default write target
- repository-local knowledge
- used for notes tied to one codebase

- дефолтна ціль для запису
- knowledge, прив’язане до конкретного репозиторію
- використовується для notes, пов’язаних з одним codebase

### `global`

- reusable knowledge across repositories
- populated only through explicit promotion
- keeps provenance back to the original project note

- reusable knowledge між різними репозиторіями
- поповнюється лише через явну промоцію
- зберігає provenance до оригінальної project-note

### `hybrid`

- default read mode
- merges project and global results
- applies a strong project bias with deterministic ordering

- дефолтний режим читання
- зливає project і global результати
- застосовує сильний project bias з детермінованим порядком

## 3. Current Project Identity / Ідентичність поточного проєкту

Current project identity is resolved in this order:

Ідентичність поточного проєкту резолвиться в такому порядку:

1. normalized `origin` remote URL
2. repo-root path hash fallback
3. explicit overrides when provided

1. нормалізований `origin` remote URL
2. fallback на hash від root path репозиторію
3. явні overrides, якщо їх передано

Supported overrides:

Підтримувані overrides:

- `TQMEMORY_PROJECT_ROOT`
- `TQMEMORY_PROJECT_ID`
- `TQMEMORY_PROJECT_NAME`

## 4. Physical Storage / Фізичне зберігання

Current storage is file-backed and local-first:

Поточне зберігання є file-backed і local-first:

```text
~/.turbo-quant-memory/
  projects/
    <project_id>/
      manifest.json
      notes/
        <note_id>.json
  global/
    manifest.json
    notes/
      <note_id>.json
```

Notes and manifests are written atomically via `NamedTemporaryFile(...)` + `os.replace(...)`.

Notes і manifests пишуться атомарно через `NamedTemporaryFile(...)` + `os.replace(...)`.

## 5. Write Policy / Політика запису

The implemented write policy is:

Реалізована політика запису:

- `remember_note(..., scope="project")` stores a project note
- direct public writes to `global` are rejected
- `promote_note(note_id)` creates the reusable global copy

- `remember_note(..., scope="project")` зберігає project-note
- прямі публічні записи в `global` відхиляються
- `promote_note(note_id)` створює reusable global-copy

This keeps `global` curated and prevents cross-project contamination.

Це тримає `global` curated і запобігає cross-project contamination.

## 6. Default Search Behaviour / Дефолтна поведінка пошуку

`semantic_search` supports:

`semantic_search` підтримує:

- `project`
- `global`
- `hybrid`

`hybrid` is the default and follows these rules:

`hybrid` є дефолтом і працює за такими правилами:

1. merge project and global candidates
2. apply a strong project bonus
3. inside each scope, prefer Markdown blocks over memory notes when matches are close
4. final tie-break by project preference, then newer `updated_at`, then stable item identity

1. злити project і global candidates
2. застосувати сильний project bonus
3. всередині кожного scope віддавати перевагу Markdown-блокам над memory notes, коли матчі близькі
4. фінальний tie-break: спочатку project preference, потім новіший `updated_at`, потім стабільний item identity

`semantic_search` searches both indexed Markdown blocks and persistent memory notes.

`semantic_search` шукає і по проіндексованих Markdown-блоках, і по persistent memory notes.

## 7. Result Envelope / Формат результату

Every returned semantic result includes compact provenance fields:

Кожен повернений semantic-result містить компактні provenance-поля:

- `scope`
- `project_id`
- `project_name`
- `source_kind`
- `item_id`
- `block_id` when relevant / коли релевантно
- `source_path`
- `title`
- `heading_path`
- `updated_at`
- `score`
- `confidence`
- `confidence_state`
- `compressed_summary`
- `key_points`
- `can_hydrate`
- `promoted_from` when relevant / коли релевантно

Default retrieval does **not** include raw excerpts or whole-file dumps. That boundary keeps token volume low and leaves fuller hydration to Phase 5.

Дефолтний retrieval **не** містить raw excerpts або дампів цілих файлів. Ця межа тримає token volume низьким і залишає повніше hydration для Phase 5.

Balanced cards add lightweight usability fields:

Balanced cards додають легкі usability-поля:

- `compressed_summary`
- максимум `2-3` `key_points`
- warning when `confidence_state` is `low` or `ambiguous`

This keeps trust high without paying for heavy debug metadata or raw excerpts on every result.

Це тримає trust високим без оплати важких debug metadata або raw excerpts у кожному результаті.

## 8. Promotion Provenance / Provenance промоції

Promoted global notes preserve `promoted_from`, which points back to:

Promoted global-notes зберігають `promoted_from`, який вказує назад на:

- original `project` scope
- source `project_id`
- source `project_name`
- original `note_id`
- original `source_path`

This makes cross-project reuse traceable instead of opaque.

Це робить cross-project reuse traceable, а не opaque.

## 9. How Agents Should Use It / Як агенти мають це використовувати

### Within one project / В межах одного проєкту

1. write into `project`
2. query with `hybrid`
3. prefer the first `project` hit when it is clearly relevant

1. записувати в `project`
2. запитувати через `hybrid`
3. віддавати пріоритет першому `project` hit, коли він явно релевантний

### Across many projects / Між багатьма проєктами

1. promote only reusable knowledge
2. search `global` when you need cross-project patterns
3. keep `global` small and high-signal

1. промотувати лише reusable knowledge
2. шукати в `global`, коли потрібні cross-project patterns
3. тримати `global` маленьким і high-signal
