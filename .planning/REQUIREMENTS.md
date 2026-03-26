# Requirements: Turbo Quant Memory for AI Agents

**Defined:** 2026-03-25
**Core Value:** Agents can offload cold project context and recover only the minimum high-signal context needed to act correctly.

## v1 Requirements

### Integration

- [x] **INT-01**: User can run the memory server locally over stdio so Claude Code can connect to it as an MCP server. / Користувач може запустити memory server локально через stdio, щоб Claude Code підключався до нього як до MCP-сервера.
- [x] **INT-02**: User can install and run the MVP without a separate database service or mandatory GPU dependency. / Користувач може встановити і запустити MVP без окремого database-сервісу та без обов'язкової GPU-залежності.
- [x] **INT-03**: User can connect the server to Claude Code using a documented `claude mcp add ...` or `.mcp.json` flow. / Користувач може підключити сервер до Claude Code через задокументований сценарій `claude mcp add ...` або `.mcp.json`.
- [x] **INT-04**: User has documented MCP connection examples for Claude Code, Codex, Cursor, OpenCode, and Antigravity. / Користувач має задокументовані приклади MCP-підключення для Claude Code, Codex, Cursor, OpenCode і Antigravity.

### Scope

- [x] **SCP-01**: User can store and query project-scoped memory tied to the current repository. / Користувач може зберігати і запитувати project-scoped memory, прив'язану до поточного репозиторію.
- [x] **SCP-02**: User can store and query global memory shared across all projects on the same machine. / Користувач може зберігати і запитувати global memory, спільну для всіх проєктів на одній машині.
- [x] **SCP-03**: Agent can search in `project`, `global`, or `hybrid` mode with deterministic precedence rules. / Агент може шукати в режимах `project`, `global` або `hybrid` з детермінованими правилами пріоритету.
- [x] **SCP-04**: Every returned result identifies its scope and project origin to prevent accidental cross-project contamination. / Кожен повернений результат ідентифікує свій scope і project origin, щоб запобігти випадковому змішуванню проєктів.

### Ingestion

- [x] **ING-01**: User can register one or more Markdown directories for indexing. / Користувач може зареєструвати одну або кілька Markdown-директорій для індексації.
- [x] **ING-02**: Server splits Markdown into stable retrievable blocks using heading-aware chunking with fallback size limits. / Сервер ділить Markdown на стабільні retrievable-блоки через heading-aware chunking із fallback size limits.
- [x] **ING-03**: User can reindex only changed content instead of rebuilding the entire memory store every time. / Користувач може переіндексувати лише змінений контент, а не перебудовувати весь memory store щоразу.

### Retrieval

- [ ] **RET-01**: Agent can run semantic search and receive the top relevant blocks with score, source path, and block ID. / Агент може виконати semantic search і отримати top-relevant блоки зі score, source path і block ID.
- [ ] **RET-02**: Agent can request a compressed context card for a block that removes low-signal noise while preserving the key meaning. / Агент може запросити compressed context card для блока, яка прибирає low-signal шум і зберігає ключовий зміст.
- [ ] **RET-03**: Agent can hydrate a fuller block or source excerpt on demand when the compressed result is insufficient. / Агент може за потреби hydrate-нути повніший блок або source excerpt, коли стислого результату недостатньо.
- [ ] **RET-04**: Agent can request related blocks around a result to recover local neighbourhood context. / Агент може запитати related blocks навколо результату, щоб відновити локальний neighbourhood context.

### Memory

- [ ] **MEM-01**: Agent can write back an important decision, lesson, or reusable note into persistent memory. / Агент може записати назад у persistent memory важливе рішення, урок або reusable note.
- [ ] **MEM-02**: Stored memory notes are indexed and searchable together with Markdown source content. / Збережені memory notes індексуються і шукаються разом із вихідним Markdown-контентом.

### Safety

- [ ] **SAFE-01**: Every returned result includes source provenance that lets the agent or user trace it back to the original file and block. / Кожен повернений результат містить provenance до джерела, щоб агент або користувач могли відстежити його до оригінального файла і блока.
- [ ] **SAFE-02**: The server avoids returning full raw files by default and instead requires explicit hydration calls for larger context. / Сервер не повертає повні сирі файли за замовчуванням і вимагає явного hydration-виклику для більшого контексту.

### Operations

- [ ] **OPS-01**: User can inspect basic health, index freshness, and store statistics from the server. / Користувач може переглядати базовий health, freshness індексу і статистику store безпосередньо із сервера.
- [ ] **OPS-02**: User has a documented smoke test that proves install, indexing, search, and hydration work end-to-end. / Користувач має задокументований smoke test, який підтверджує, що install, indexing, search і hydration працюють end-to-end.

## v2 Requirements

### Compression Intelligence

- **CMP-01**: Agent can request multi-level summaries from one source block (brief, standard, deep). / Агент може запитувати multi-level summaries для одного source-блока (brief, standard, deep).
- **CMP-02**: Server can use an optional local summarizer such as Ollama for higher-quality compression. / Сервер може використовувати optional local summarizer на кшталт Ollama для кращого стиснення.

### Retrieval Quality

- **QLT-01**: Server can apply reranking to improve precision on ambiguous searches. / Сервер може застосовувати reranking для кращої precision на неоднозначних запитах.
- **QLT-02**: Server can benchmark token savings versus quality loss on a curated task suite. / Сервер може benchmark-нути економію токенів проти втрати якості на curated task suite.

### Collaboration

- **COL-01**: Multiple named memory spaces can coexist for separate projects or teams. / Кілька іменованих memory spaces можуть співіснувати для різних проєктів або команд.
- **COL-02**: Notes can carry richer relationship metadata beyond tags. / Нотатки можуть нести багатші relationship metadata, ніж прості теги.

## Out of Scope

| Feature | Reason |
|---------|--------|
| Direct Anthropic or Claude KV-cache quantization / Пряма квантизація KV-cache Anthropic або Claude | Not available at the MCP integration layer / Це недоступно на рівні MCP-інтеграції |
| Hosted SaaS control plane / Hosted SaaS control plane | Opposes the easy local deployment goal / Суперечить цілі простого локального розгортання |
| Mandatory GPU-only inference path / Обов'язковий GPU-only шлях інференсу | Too much setup friction for MVP / Для MVP це створює зайве тертя під час запуску |
| Rich web admin UI / Багатий web admin UI | Not required before the core memory loop proves value / Не потрібен, поки не доведена цінність базового memory loop |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| INT-01 | Phase 1 | Complete |
| INT-02 | Phase 1 | Complete |
| INT-03 | Phase 1 | Complete |
| INT-04 | Phase 1 | Complete |
| SCP-01 | Phase 2 | Complete |
| SCP-02 | Phase 2 | Complete |
| SCP-03 | Phase 2 | Complete |
| SCP-04 | Phase 2 | Complete |
| ING-01 | Phase 3 | Complete |
| ING-02 | Phase 3 | Complete |
| ING-03 | Phase 3 | Complete |
| RET-01 | Phase 4 | Pending |
| RET-02 | Phase 4 | Pending |
| SAFE-01 | Phase 4 | Pending |
| SAFE-02 | Phase 4 | Pending |
| RET-03 | Phase 5 | Pending |
| RET-04 | Phase 5 | Pending |
| MEM-01 | Phase 5 | Pending |
| MEM-02 | Phase 5 | Pending |
| OPS-01 | Phase 6 | Pending |
| OPS-02 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 21 total
- Mapped to phases: 21
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-25*
*Last updated: 2026-03-26 after Phase 3 execution*
