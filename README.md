# Turbo Quant Memory for AI Agents

Local-first MCP server for AI coding agents that need a smaller, cheaper working context.

Local-first MCP-сервер для AI coding-агентів, яким потрібен менший і дешевший робочий контекст.

## Quickstart

EN: Canonical install path for Phase 1.

UK: Канонічний шлях інсталяції для Phase 1.

```bash
uv sync
uv run turbo-memory-mcp serve
```

EN: `pip` fallback if `uv` is not available.

UK: `pip`-fallback, якщо `uv` недоступний.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
python -m turbo_memory_mcp serve
```

EN: Local verification loop.

UK: Локальний цикл перевірки.

```bash
uv run python scripts/smoke_test.py
uv run pytest -q
```

## Runtime Contract

- Server id / Ідентифікатор сервера: `tqmemory`
- Canonical runtime / Канонічний runtime: `uv run turbo-memory-mcp serve`
- `pip` fallback / `pip` fallback: `python -m turbo_memory_mcp serve`
- Transport / Транспорт: `stdio`
- Phase 1 tools / Інструменти Phase 1: `health`, `server_info`, `list_scopes`, `self_test`
- Reserved scopes / Зарезервовані scope: `project`, `global`, `hybrid`
- Phase 1 scope / Межі Phase 1: integration and introspection only; real memory storage, indexing, retrieval, and hydration arrive later. / лише інтеграція та introspection; реальне memory storage, indexing, retrieval і hydration з'являться пізніше.
- Reality check / Важливе уточнення: this project does not claim direct KV-cache control over hosted models; TurboQuant is inspiration for the memory architecture, not a hosted-model hook. / цей проєкт не заявляє прямого контролю над KV-cache hosted-моделей; TurboQuant тут є джерелом ідей для memory-архітектури, а не hook у hosted-модель.

## Supported Clients

| Client | Tier | Typical config target | Fixture |
|---|---|---|---|
| Claude Code | Tier 1 | `.mcp.json` | `examples/clients/claude.project.mcp.json` |
| Codex | Tier 1 | `.codex/config.toml` or `~/.codex/config.toml` | `examples/clients/codex.config.toml` |
| Cursor | Tier 1 | `.cursor/mcp.json` or `~/.cursor/mcp.json` | `examples/clients/cursor.project.mcp.json` |
| OpenCode | Tier 1 | OpenCode config JSON under `mcp` | `examples/clients/opencode.config.json` |
| Antigravity | Tier 2 | Raw custom MCP import in the Agent UI | `examples/clients/antigravity.mcp.json` |

EN: All fixtures use the same server id `tqmemory` and the same launch contract `uv run turbo-memory-mcp serve`.

UK: Усі фікстури використовують однаковий server id `tqmemory` і однаковий launch contract `uv run turbo-memory-mcp serve`.

## Tier 1

EN: Tier 1 means the repo ships a checked-in config fixture, a documented connect path, and a concrete `self_test` validation target.

UK: Tier 1 означає, що в репозиторії є готовий config fixture, задокументований шлях підключення і конкретна ціль валідації через `self_test`.

- Claude Code: use `examples/clients/claude.project.mcp.json` or `claude mcp add --scope project tqmemory -- uv run turbo-memory-mcp serve`
- Codex: use `examples/clients/codex.config.toml` or `codex mcp add tqmemory -- uv run turbo-memory-mcp serve`
- Cursor: use `examples/clients/cursor.project.mcp.json` in `.cursor/mcp.json` or `~/.cursor/mcp.json`
- OpenCode: use `examples/clients/opencode.config.json` under the local `mcp` config surface
- Smoke checklist / Smoke checklist: `examples/clients/SMOKE_CHECKLIST.md`

## Tier 2

EN: Antigravity is a documented compatibility target in Phase 1, not equal proof to Tier 1. The fixture is intentionally raw JSON because the current path is manual custom-MCP import through the product UI.

UK: Antigravity у Phase 1 є documented compatibility target, а не рівнозначно доведеною підтримкою Tier 1. Фікстура навмисно дана як raw JSON, бо поточний шлях підключення проходить через ручний custom-MCP import у UI продукту.

- Antigravity fixture / Antigravity фікстура: `examples/clients/antigravity.mcp.json`
- Manual validation path / Шлях ручної валідації: see `examples/clients/SMOKE_CHECKLIST.md`

