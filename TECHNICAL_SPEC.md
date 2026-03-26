# Turbo Quant Memory for AI Agents

## Technical Specification / Технічне завдання

## 1. Product Goal / Мета продукту

Build a local-first MCP server that acts as compressed long-term memory for AI coding agents. The server must reduce repeated token usage by moving cold context out of the active prompt and returning only compact, relevant context until fuller detail is explicitly requested.

Побудувати local-first MCP-сервер, який працює як стиснена довготривала пам'ять для AI coding-агентів. Сервер має зменшувати повторні витрати токенів за рахунок винесення "холодного" контексту з активного prompt і повертати лише компактний, релевантний контекст, поки не буде явно запрошено повніші деталі.

## 2. Product Thesis / Продуктова теза

TurboQuant shows that aggressive compression plus selective recovery can preserve utility while sharply reducing memory cost. This project applies the same idea at the MCP memory layer:

TurboQuant показує, що агресивне стиснення разом із вибірковим відновленням може зберігати корисність і різко знижувати вартість пам'яті. Цей проєкт застосовує ту саму ідею на рівні MCP-пам'яті:

- Do not keep all project knowledge in the model context.
- Не тримати всі знання проєкту в контексті моделі.
- Keep source material in local memory storage.
- Тримати вихідний матеріал у локальному memory storage.
- Return compressed context first.
- Спочатку повертати стиснений контекст.
- Hydrate fuller context only when needed.
- Повертати повніший контекст лише за потреби.

## 3. Important Reality Check / Важливе уточнення

This project does **not** directly quantize Claude tokens or Anthropic-hosted KV cache. It is an MCP-side memory architecture inspired by TurboQuant's compression strategy.

Цей проєкт **не** квантує напряму токени Claude або KV cache hosted-моделей Anthropic. Це архітектура пам'яті на боці MCP, натхненна стратегією стиснення TurboQuant.

## 4. Target Users / Цільові користувачі

- Engineers using Claude Code in real repositories.
- Інженери, які використовують Claude Code у реальних репозиторіях.
- Agent-heavy workflows that repeatedly read docs, logs, ADRs, notes, and Markdown knowledge bases.
- Agent-heavy workflow-и, які багаторазово читають docs, логи, ADR, нотатки і Markdown-бази знань.
- Teams that need easy local deployment and low operational overhead.
- Команди, яким потрібне просте локальне розгортання і низький операційний оверхед.

## 5. Core Principles / Базові принципи

1. **Local-first**: no hosted dependency for the core memory loop.
2. **Markdown-first**: human-readable source of truth.
3. **Compressed-first retrieval**: return small, high-signal answers before fuller context.
4. **Hydration on demand**: the agent can recover detail when confidence is low.
5. **Traceability always**: every answer must point to a source.
6. **Easy setup**: install and connect in minutes, not hours.

1. **Local-first**: без hosted-залежності для базового memory loop.
2. **Markdown-first**: human-readable джерело істини.
3. **Compressed-first retrieval**: спочатку повертати маленькі high-signal відповіді, а не повний контекст.
4. **Hydration on demand**: агент може відновити деталі, коли впевненість низька.
5. **Traceability always**: кожна відповідь має вказувати на джерело.
6. **Easy setup**: інсталяція і підключення за хвилини, а не за години.

## 6. Technical Stack / Технічний стек

- Language: Python 3.11+
- MCP framework: official MCP Python SDK
- Storage: local filesystem + LanceDB embedded store
- Embeddings: Sentence Transformers with a lightweight local model such as `all-MiniLM-L6-v2`
- Config: environment variables + typed settings
- Packaging: `uv` first, `pip` fallback

- Мова: Python 3.11+
- MCP-фреймворк: офіційний MCP Python SDK
- Сховище: локальна файлова система + embedded LanceDB
- Embeddings: Sentence Transformers з легкою локальною моделлю на кшталт `all-MiniLM-L6-v2`
- Конфіг: environment variables + typed settings
- Пакування: `uv` як основний шлях, `pip` як fallback

## 7. Functional Scope / Функціональний обсяг

### 7.1 Source Ingestion / Завантаження джерел

- Index one or more Markdown directories.
- Chunk files by headings with deterministic fallback chunk sizes.
- Persist metadata: file path, headings, block ID, timestamps, tags, hash.
- Support incremental reindex of changed content only.

- Індексувати одну або кілька Markdown-директорій.
- Ділити файли за heading-структурою з детермінованими fallback chunk sizes.
- Зберігати metadata: шлях до файлу, headings, block ID, timestamps, tags, hash.
- Підтримувати incremental reindex лише зміненого контенту.

### 7.2 Retrieval / Пошук і повернення контексту

- Semantic search by free-text query through `semantic_search(...)`.
- Return balanced result cards with:
  - `scope`
  - `project_id`
  - `project_name`
  - `source_kind`
  - `item_id`
  - `block_id` when the hit comes from Markdown
  - file path
  - title or heading path
  - relevance score
  - confidence and `confidence_state`
  - `compressed_summary`
  - up to `2-3` `key_points`
  - explicit source provenance
- Keep raw excerpts out of the default Phase 4 payload.
- Surface explicit warnings for low-confidence or ambiguous retrievals.

- Semantic search за довільним текстовим запитом через `semantic_search(...)`.
- Повернення balanced result cards з:
  - `scope`
  - `project_id`
  - `project_name`
  - `source_kind`
  - `item_id`
  - `block_id`, коли hit походить з Markdown
  - шляхом до файлу
  - title або heading path
  - relevance score
  - confidence і `confidence_state`
  - `compressed_summary`
  - максимум `2-3` `key_points`
  - явним provenance до джерела
- Не повертати raw excerpts у дефолтному payload Phase 4.
- Явно сигналізувати про low-confidence або неоднозначні retrieval-и.

### 7.3 Hydration / Відновлення повнішого контексту

- Fetch a fuller block excerpt when the compressed answer is not enough.
- Fetch nearby blocks or a bounded section of the source file.
- Preserve token discipline by requiring explicit calls for larger payloads.

- Повертати повніший excerpt блока, коли стислої відповіді недостатньо.
- Повертати сусідні блоки або обмежену секцію вихідного файлу.
- Зберігати token discipline, вимагаючи явного виклику для більших payload.

Phase 5 completes balanced-card retrieval with explicit hydration.

Phase 5 завершує balanced-card retrieval явним hydration.

### 7.4 Write-Back Memory / Запис нової пам'яті

- Save decisions, findings, summaries, and reusable snippets.
- Use fixed note kinds: `decision`, `lesson`, `handoff`, `pattern`.
- Tag and timestamp notes.
- Reindex saved notes automatically or on explicit refresh.

- Зберігати рішення, висновки, summary і reusable snippets.
- Використовувати фіксовані типи нотаток: `decision`, `lesson`, `handoff`, `pattern`.
- Додавати теги та часові мітки.
- Переіндексувати збережені notes автоматично або за явним refresh.

### 7.5 Operations / Експлуатація

- Health/status tool
- Index freshness and counts
- Smoke test instructions
- Troubleshooting guidance
- `server_info()` exposes storage stats and freshness snapshots

- Health/status tool
- Freshness індексу і лічильники
- Інструкція smoke test
- Troubleshooting guidance
- `server_info()` показує storage stats і freshness-зрізи

## 8. MCP Tool Surface / Набір MCP-інструментів

### Live v1 tools / Живі v1-інструменти

1. `index_paths(paths, mode="incremental"|"full")`
2. `semantic_search(query, limit=5, scope=None)`
3. `remember_note(title, content, kind, tags=[], source_refs=None, scope="project")`
4. `promote_note(note_id)`
5. `deprecate_note(note_id, scope="project"|"global", replacement_note_id=None, replacement_scope=None, reason=None)`
6. `hydrate(item_id, scope, mode="default"|"related")`
7. `health()`
8. `server_info()`
9. `list_scopes()`
10. `self_test()`

### Planned for Phase 6+ / Заплановано для Phase 6+

1. `get_compressed_block(block_id, style="brief"|"standard")`
2. `memory_stats()`
3. `delete_note(note_id)`

## 9. Data Model / Модель даних

### Source block / Блок джерела

- `block_id`
- `file_path`
- `heading_path`
- `content_raw`
- `content_compressed`
- `embedding`
- `checksum`
- `created_at`
- `updated_at`
- `tags`
- `source_kind` (`markdown` | `memory_note`)

### Memory note / Нотатка пам'яті

- `note_id`
- `title`
- `content`
- `note_kind`
- `summary`
- `tags`
- `session_id`
- `project_id`
- `created_at`
- `source_refs`

## 10. Compression Strategy / Стратегія стиснення

### MVP strategy / MVP-стратегія

- Structural compression, not model-level quantization.
- Strip repeated whitespace, boilerplate links, long low-signal sections.
- Prefer headings, bullets, signatures, and key assertions.
- Preserve source path and block identity.
- Never discard the original source; only compress the returned view.

- Структурне стиснення, а не model-level quantization.
- Прибирати повторні пробіли, boilerplate links, довгі low-signal секції.
- Віддавати пріоритет headings, bullets, signatures і ключовим твердженням.
- Зберігати source path і block identity.
- Ніколи не викидати оригінальне джерело; стискати лише повернене представлення.

### Future strategy / Майбутня стратегія

- Hierarchical summaries
- Optional local summarizer
- Query-aware compression profiles
- Token-savings benchmarking

- Ієрархічні summary
- Optional local summarizer
- Query-aware compression profiles
- Benchmarking економії токенів

## 11. Quality Guardrails / Запобіжники якості

- Every result must include provenance.
- Кожен результат має містити provenance.
- Compression must be reversible through hydration.
- Стиснення має бути оборотним через hydration.
- The server must surface low-confidence or ambiguous retrievals.
- Сервер має сигналізувати про low-confidence або неоднозначні retrievals.
- Notes are untrusted content, not system instructions.
- Notes є untrusted content, а не системними інструкціями.

## 12. Performance Targets / Цільові показники продуктивності

### MVP targets / Цілі MVP

- Local startup: under 5 seconds on a developer laptop.
- Initial indexing of a small knowledge base: acceptable within interactive setup expectations.
- Search latency: interactive for small and medium corpora.
- Typical token reduction on recall-heavy tasks: target 60%+ versus naive raw-file inclusion.

- Локальний старт: до 5 секунд на ноутбуці розробника.
- Початкова індексація малої бази знань: прийнятна в межах інтерактивного setup.
- Затримка пошуку: інтерактивна для малих і середніх corpus.
- Типова економія токенів на recall-heavy tasks: ціль 60%+ відносно naive включення сирих файлів.

## 13. Security and Trust / Безпека і довіра

- Restrict indexing to explicitly allowed paths.
- Limit output sizes by default.
- Preserve source boundaries.
- Treat retrieved memory as tool data, not authority.
- Avoid silent outbound network requirements for the core flow.

- Обмежувати індексацію явно дозволеними шляхами.
- Обмежувати output sizes за замовчуванням.
- Зберігати межі джерела.
- Трактувати retrieved memory як tool data, а не як авторитет.
- Уникати прихованих зовнішніх мережевих залежностей для core flow.
- Не індексувати історичні low-signal каталоги на кшталт `.planning`, `.serena` і generated benchmark artifacts у стандартному project-root ingestion flow.

## 14. Deployment and Integration / Розгортання та інтеграція

### Deployment requirement / Вимога до розгортання

- One recommended install path:
  - `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`
  - `turbo-memory-mcp serve`
- One documented Claude Code connection path:
  - `claude mcp add tqmemory --scope project -- turbo-memory-mcp serve`
  - or a `.mcp.json` example with stdio transport
- Equivalent client examples must also exist for Codex, Cursor, OpenCode, and Antigravity.
- Також мають існувати еквівалентні client examples для Codex, Cursor, OpenCode і Antigravity.

- Один рекомендований шлях інсталяції:
  - `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.2.2`
  - `turbo-memory-mcp serve`
- Один задокументований шлях підключення до Claude Code:
  - `claude mcp add tqmemory --scope project -- turbo-memory-mcp serve`
  - або `.mcp.json` приклад зі stdio transport

## 15. Testing Strategy / Стратегія тестування

- Unit tests:
  - chunking
  - stable ID generation
  - compression rendering
  - provenance mapping
- Integration tests:
  - index -> search -> compressed block -> hydrate flow
  - remember note -> reindex -> search flow
- Smoke test:
  - fresh install
  - connect Claude Code
  - index sample docs
  - retrieve and hydrate

- Unit-тести:
  - chunking
  - stable ID generation
  - compression rendering
  - provenance mapping
- Integration-тести:
  - flow index -> search -> compressed block -> hydrate
  - flow remember note -> reindex -> search
- Smoke test:
  - чиста інсталяція
  - підключення Claude Code
  - індексація sample docs
  - retrieval і hydrate

## 16. Acceptance Criteria / Критерії приймання

1. A new user can install and connect the server to Claude Code in minutes.
2. The server can index Markdown files and search them semantically.
3. Retrieval returns compact, source-backed context instead of default raw dumps.
4. The agent can explicitly hydrate fuller context when needed.
5. The agent can write and later retrieve session learnings.
6. Operators can inspect health, freshness, and follow a smoke test.

1. Новий користувач може встановити і підключити сервер до Claude Code за кілька хвилин.
2. Сервер може індексувати Markdown-файли і шукати їх семантично.
3. Retrieval повертає компактний, підкріплений джерелом контекст замість default raw dump.
4. Агент може явно hydrate-нути повніший контекст за потреби.
5. Агент може записувати і пізніше діставати висновки із сесій.
6. Оператор може переглядати health, freshness і пройти smoke test.

## 17. Non-Goals / Нецілі

- Replacing the model's own memory internals
- Заміна внутрішньої пам'яті самої моделі
- Solving all repository reasoning with compression only
- Вирішення всіх задач reasoning у репозиторії лише через стиснення
- Multi-tenant enterprise memory governance in MVP
- Multi-tenant enterprise memory governance у MVP

## 18. Suggested MVP Name / Запропонована назва MVP

**Turbo Memory MCP**

**Turbo Memory MCP**

---
*Prepared on 2026-03-25 from user intent, official MCP and agent-client documentation, and current TurboQuant research.*
