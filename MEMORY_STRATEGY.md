# Memory Strategy / Стратегія пам'яті

## 1. One Server, Many Clients / Один сервер, багато клієнтів

The right shape is **one core MCP memory server** and **thin client-specific configs**, not different memory implementations per agent.

Правильна форма тут: **один core MCP memory server** і **тонкі client-specific config-обгортки**, а не різні реалізації пам'яті для кожного агента.

### Verified integration targets / Підтверджені цілі інтеграції

| Client | Verified integration pattern | Recommended scope model |
|--------|------------------------------|--------------------------|
| Claude Code | `claude mcp add ...` and `.mcp.json` | project + user |
| Codex | `codex mcp add ...` and config file support | project + user |
| Cursor | `.cursor/mcp.json` or user-level Cursor MCP config | project + user |
| OpenCode | `mcp` config with local or remote server definitions | project + user |
| Antigravity | Custom MCP server config in the agent UI/raw config flow | project + user |

## 2. Memory Topology / Топологія пам'яті

I recommend **two scopes from day one**:

Я рекомендую **два scopes з першого дня**:

1. **Project scope**
2. **Global scope**

### Project scope / Project scope

Used for repository-specific knowledge:

Використовується для knowledge, специфічного для конкретного репозиторію:

- project docs
- ADRs
- local conventions
- project TODOs
- session conclusions tied to one codebase

### Global scope / Global scope

Used for reusable knowledge across repositories:

Використовується для reusable knowledge між різними репозиторіями:

- coding preferences
- deployment recipes
- reusable debugging playbooks
- template prompts
- architectural patterns that are not project-specific

## 3. Default Search Behavior / Поведінка пошуку за замовчуванням

The default should be **hybrid with project bias**:

Дефолт має бути **hybrid із project bias**:

1. Search the current project namespace first.
2. Search the global namespace second.
3. Merge results with a strong score bonus for the current project.
4. Never let global memory override a clearly better project-local result.

1. Спочатку шукати в namespace поточного проєкту.
2. Потім шукати в global namespace.
3. Зливати результати з сильним score-bonus для поточного проєкту.
4. Ніколи не дозволяти global memory перекривати явно кращий project-local результат.

### Supported query modes / Підтримувані режими запиту

- `project`
- `global`
- `hybrid` (default)

## 4. Write Policy / Політика запису

The safe default is:

Безпечний дефолт:

- all new notes go to `project` scope;
- only explicitly reusable knowledge is promoted to `global`.

- усі нові notes ідуть у `project` scope;
- лише явно reusable knowledge переводиться в `global`.

### Promotion rule / Правило промоції

Add an explicit tool or parameter for promotion:

Додати явний tool або параметр для промоції:

- `remember_note(..., scope="project")`
- `promote_note(note_id, to="global")`

This prevents accidental pollution of cross-project memory.

Це запобігає випадковому засміченню cross-project memory.

## 5. Physical Storage Proposal / Пропозиція щодо фізичного зберігання

Recommended base directory:

Рекомендована базова директорія:

`~/.turbo-quant-memory/`

Suggested structure:

Запропонована структура:

```text
~/.turbo-quant-memory/
  store/
    lancedb/
  projects/
    <project_id>/
      notes/
      manifests/
  global/
    notes/
    manifests/
  cache/
  logs/
```

### Project identity / Ідентичність проєкту

Each repository should have a stable `project_id`, derived from:

Кожен репозиторій має мати стабільний `project_id`, який походить із:

- repo remote URL if available;
- otherwise repo root path hash;
- plus a human-readable project name.

- remote URL репозиторію, якщо вона є;
- інакше hash від root path репозиторію;
- плюс human-readable назва проєкту.

## 6. Result Envelope / Формат результату

Every retrieval result should include:

Кожен retrieval-result має містити:

- `scope`
- `project_id`
- `project_name`
- `block_id`
- `source_path`
- `heading_path`
- `score`
- `confidence`
- `can_hydrate`

This is critical for trust and debugging.

Це критично для довіри і дебагу.

## 7. How Agents Should Use It / Як агенти мають це використовувати

### Within one project / В межах одного проєкту

- Use `hybrid` search by default.
- Prefer project hits.
- Hydrate only when compressed recall is insufficient.
- Write decisions back into the same project namespace.

- Використовувати `hybrid` search за замовчуванням.
- Віддавати пріоритет project hits.
- Робити hydrate лише коли compressed recall недостатній.
- Записувати рішення назад у namespace цього ж проєкту.

### Across all projects / Між усіма проєктами

- Search global memory only for reusable patterns or user preferences.
- Promote project notes to global only after explicit confirmation or rule-based validation.
- Keep global memory small and high-signal.

- Шукати global memory лише для reusable patterns або user preferences.
- Промотувати project notes у global лише після явного підтвердження або rule-based validation.
- Тримати global memory маленькою і high-signal.

## 8. Why This Is Better Than a Single Flat Memory / Чому це краще за одну плоску пам'ять

Flat shared memory causes contamination:

Плоска shared memory створює contamination:

- one repo leaks assumptions into another;
- reusable patterns drown in local noise;
- agents lose confidence in what is actually current.

- один репозиторій "протікає" своїми припущеннями в інший;
- reusable patterns тонуть у локальному шумі;
- агенти втрачають впевненість у тому, що саме є актуальним.

The two-scope model keeps memory useful without making it dangerous.

Модель із двома scopes зберігає memory корисною, не роблячи її небезпечною.

## 9. Rollout Proposal / Пропозиція по rollout

### Stage 1

- Local stdio MCP server
- Claude Code + Codex + Cursor + OpenCode + Antigravity config examples
- Project and global scopes
- Hybrid retrieval with project bias

### Stage 2

- Optional remote HTTP deployment for team-shared memory
- Optional OAuth or API-key auth
- Team scope between `project` and `global`

### Stage 3

- Benchmarks for token savings and answer quality
- Quality-aware hydration policies
- Promotion heuristics from project to global

## 10. My Recommendation / Моя рекомендація

For v1, I would build:

Для v1 я б будував:

1. One local stdio MCP server.
2. One embedded storage engine.
3. Two namespaces: `project` and `global`.
4. Hybrid search as the default.
5. Explicit promotion from project to global.
6. Documented configs for the five target clients.

1. Один local stdio MCP server.
2. Один embedded storage engine.
3. Два namespaces: `project` і `global`.
4. Hybrid search як дефолт.
5. Явну промоцію з project у global.
6. Задокументовані конфіги для п'яти цільових клієнтів.
