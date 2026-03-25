# Phase 1 Research: Client Integration Foundation

**Researched:** 2026-03-25  
**Status:** Complete

## Research Goal

Determine the smallest technically honest Phase 1 that proves:

- one local `stdio` MCP server can be shared across multiple coding-agent clients;
- the server can be packaged and launched with a very low-friction Python workflow;
- client-specific differences are mostly configuration differences, not server forks.

Визначити найменшу технічно чесну Фазу 1, яка доводить:

- один локальний `stdio` MCP-сервер можна використовувати з кількома coding-agent клієнтами;
- сервер можна пакувати і запускати через low-friction Python workflow;
- відмінності між клієнтами здебільшого лежать у конфігах, а не в окремих реалізаціях сервера.

## Confirmed Findings

### 1. MCP Python SDK is already sufficient for Phase 1

- The current high-level Python API is `MCPServer`; the SDK migration docs state that `FastMCP` was renamed to `MCPServer`.
- A local server can run directly over `stdio`, which matches the Phase 1 requirement for local child-process startup.
- The same SDK also ships a stdio client, so an in-repo smoke test can launch the packaged server and call tools over real MCP instead of mocking the protocol.

- Поточний high-level API у Python SDK це `MCPServer`; migration docs прямо вказують, що `FastMCP` було перейменовано на `MCPServer`.
- Локальний сервер можна запускати напряму через `stdio`, що прямо відповідає вимозі Phase 1 про local child-process startup.
- Той самий SDK також має stdio client, тому в репозиторії можна зробити реальний smoke test через MCP, а не лише мокати протокол.

### 2. One `stdio` core server is viable across the target clients

- Claude Code supports local `stdio` MCP servers via `claude mcp add ... -- <command>` and project-shared `.mcp.json`.
- Codex supports MCP in both CLI and IDE, stores config in `config.toml`, and supports project-scoped `.codex/config.toml` in trusted projects.
- Cursor supports editor and CLI MCP from the same config surface and recognizes project `.cursor/mcp.json` plus global `~/.cursor/mcp.json`.
- OpenCode supports local MCP definitions under `mcp` with a command array and per-agent enabling.
- Antigravity currently has a documented manual custom-MCP flow through its UI/raw-config surface, which is strong compatibility evidence but weaker than scriptable CLI support.

- Claude Code підтримує локальні `stdio` MCP-сервери через `claude mcp add ... -- <command>` і project-shared `.mcp.json`.
- Codex підтримує MCP і в CLI, і в IDE, зберігає конфіг у `config.toml`, і має project-scoped `.codex/config.toml` для trusted projects.
- Cursor підтримує MCP і в editor, і в CLI через один конфіг-шар та читає project `.cursor/mcp.json` і global `~/.cursor/mcp.json`.
- OpenCode підтримує локальні MCP-оголошення під `mcp` з command-array і per-agent enable/disable.
- Antigravity наразі має документований manual custom-MCP flow через UI/raw-config, що є сильним сигналом сумісності, але слабшим за scriptable CLI support.

### 3. Phase 1 should stay `stdio` only

- All target clients already support local-process MCP startup.
- Adding HTTP in Phase 1 would increase surface area without helping the "easy deploy" promise.
- A single local process keeps packaging, docs, and smoke testing simpler.

- Усі цільові клієнти вже підтримують запуск локального MCP-процесу.
- Додавання HTTP у Phase 1 лише збільшить surface area і не допоможе обіцянці "easy deploy".
- Один локальний процес робить пакування, документацію і smoke testing значно простішими.

### 4. The minimal tool surface should be introspection-first

- The chosen Phase 1 tools (`health`, `server_info`, `list_scopes`, `self_test`) are enough to prove discoverability, runtime integrity, and future namespace direction without pretending that the memory loop already exists.
- `list_scopes` is useful even before Phase 2 if it is treated as a contract/reserved-surface tool, not as active storage behavior.
- `self_test` is especially valuable because every target client can use it to prove that the server is reachable and that the tool catalog is wired correctly.

- Обрані для Phase 1 tools (`health`, `server_info`, `list_scopes`, `self_test`) достатні, щоб довести discoverability, runtime integrity і напрямок namespace-моделі, не вдаючи, що memory loop уже існує.
- `list_scopes` корисний ще до Phase 2, якщо трактувати його як contract/reserved-surface tool, а не як активну storage-поведінку.
- `self_test` особливо цінний, бо кожен цільовий клієнт може використати його для перевірки доступності сервера і правильного wired-up tool catalog.

## Planning Implications

### Server Runtime

- Canonical runtime remains `uv run turbo-memory-mcp serve`.
- Python baseline remains `>=3.11`.
- `uv` stays primary, `pip` stays fallback.
- The implementation should prefer the high-level SDK API and avoid unnecessary runtime dependencies for CLI orchestration.

- Канонічний runtime лишається `uv run turbo-memory-mcp serve`.
- Python baseline лишається `>=3.11`.
- `uv` лишається primary, `pip` лишається fallback.
- Реалізація має віддати перевагу high-level SDK API і не тягнути зайві runtime-залежності для CLI orchestration.

### Package Shape

- A standard Python package plus console script is enough for Phase 1.
- The implementation can stay local-first and CPU-only because no vector store, embedding model, or external DB is needed yet.
- The repo should gain a minimal `src/` package, `pyproject.toml`, and pytest-based tests in the first execution plan.

- Для Phase 1 достатньо стандартного Python package + console script.
- Реалізація може лишитися local-first і CPU-only, бо поки не потрібні ні vector store, ні embedding model, ні зовнішня БД.
- У першому execution plan репозиторій має отримати мінімальний `src/` package, `pyproject.toml` і pytest-based тести.

### Client Support Model

- Tier 1 remains: Claude Code, Codex, Cursor, OpenCode.
- Tier 2 remains: Antigravity.
- Phase 1 acceptance must distinguish "documented config exists" from "real smoke-tested connect flow exists".

- Tier 1 лишається: Claude Code, Codex, Cursor, OpenCode.
- Tier 2 лишається: Antigravity.
- Acceptance для Phase 1 має чітко розрізняти "є задокументований конфіг" і "є реально прогнаний smoke-tested connect flow".

### Documentation Strategy

- README should carry the quickstart and compatibility summary.
- Client-specific fixtures should live under an examples directory instead of being embedded only in prose.
- The repo should ship a manual smoke checklist per client because true client-connect acceptance is partly external to the codebase.

- README має нести quickstart і compatibility summary.
- Client-specific fixtures варто покласти в examples-directory, а не лише в prose.
- Репозиторій має ship-нути manual smoke checklist для кожного клієнта, бо справжній client-connect acceptance частково лежить поза кодовою базою.

## Risks and Mitigations

### Risk: documentation drift against the actual runtime contract

- Mitigation: make `server_info` return the exact runtime/install/client contract and reuse those same values in docs and smoke tests.

### Risk: Phase 1 accidentally promises real memory behavior

- Mitigation: keep tools introspection-only and explicitly mark scopes as reserved/planned rather than active storage backends.

### Risk: client support claims get ahead of evidence

- Mitigation: keep tiering visible in README and smoke checklist; Antigravity stays documented-compatible in Phase 1, not "equally proven".

### Risk: too much tool surface creates context waste

- Mitigation: hold the tool catalog to four tools in Phase 1 and defer all storage/search operations.

### Ризик: документація розійдеться з реальним runtime contract

- Пом’якшення: нехай `server_info` повертає точний runtime/install/client contract, а документація і smoke tests використовують ті самі значення.

### Ризик: Phase 1 випадково пообіцяє реальну memory-поведінку

- Пом’якшення: тримати tools лише introspection-oriented і явно позначати scopes як reserved/planned, а не як активні storage backends.

### Ризик: заяви про client support випередять докази

- Пом’якшення: залишити tiering видимим у README і smoke checklist; Antigravity у Phase 1 лишається documented-compatible, а не "однаково proven".

### Ризик: завеликий tool surface почне марно їсти контекст

- Пом’якшення: жорстко обмежити catalog чотирма tools у Phase 1 і відкласти всі storage/search operations.

## Recommended Phase Split

### Plan 01 — Core package and MCP server

- Bootstrap Python packaging, CLI contract, core MCP server, and local tests.

### Plan 02 — Multi-client docs and config fixtures

- Publish the README quickstart and canonical config examples for each client.

### Plan 03 — Smoke and contract validation

- Add a real stdio smoke test plus manual client validation checklists.

### Plan 01 — Core package and MCP server

- Запакувати Python package, CLI contract, core MCP server і локальні тести.

### Plan 02 — Multi-client docs and config fixtures

- Опублікувати README quickstart і канонічні config examples для кожного клієнта.

### Plan 03 — Smoke and contract validation

- Додати реальний stdio smoke test і manual client validation checklists.

## Validation Architecture

### Automation Boundary

- Local automation should prove server startup, tool discovery, contract shape, and `self_test`.
- External GUI/editor integration should be validated with manual smoke steps, because those hosts are outside the repo and not reliably scriptable in CI.

- Локальна автоматизація має доводити startup сервера, discovery tools, shape контракту і `self_test`.
- Зовнішні GUI/editor інтеграції треба валідувати manual smoke steps, бо ці хости лежать поза репозиторієм і не є надійно scriptable у CI.

### Fast Feedback Loop

- Quick automated loop: `uv run pytest -q`
- Full automated loop: `uv run pytest -q && uv run python scripts/smoke_test.py`

- Швидкий автоматизований цикл: `uv run pytest -q`
- Повний автоматизований цикл: `uv run pytest -q && uv run python scripts/smoke_test.py`

### Required Manual Acceptance

- Claude Code: connect, inspect MCP status, run `self_test`
- Codex: add MCP server, confirm it appears in MCP management, run `self_test`
- Cursor: load `mcp.json`, confirm server visibility, run `self_test`
- OpenCode: load config, confirm server visibility, run `self_test`
- Antigravity: import raw config and confirm the server is recognized as a custom MCP target

- Claude Code: підключити, перевірити MCP status, виконати `self_test`
- Codex: додати MCP server, підтвердити появу в MCP management, виконати `self_test`
- Cursor: підвантажити `mcp.json`, підтвердити видимість сервера, виконати `self_test`
- OpenCode: підвантажити конфіг, підтвердити видимість сервера, виконати `self_test`
- Antigravity: імпортувати raw config і підтвердити, що сервер розпізнається як custom MCP target

## Source Map

- MCP Python SDK: `https://github.com/modelcontextprotocol/python-sdk` via Context7 `/modelcontextprotocol/python-sdk`
- Claude Code MCP docs: `https://code.claude.com/docs/en/mcp`
- Codex MCP docs: `https://developers.openai.com/codex/mcp`
- Cursor MCP docs: `https://cursor.com/docs/mcp`
- Cursor CLI MCP docs: `https://cursor.com/docs/cli/mcp`
- OpenCode MCP docs: `https://opencode.ai/docs/mcp-servers/`
- Flutter MCP client guide with Antigravity/Cursor/OpenCode/Claude/Codex setup references: `https://docs.flutter.dev/ai/mcp-server`

---
*Phase: 01-client-integration-foundation*  
*Research completed: 2026-03-25*
