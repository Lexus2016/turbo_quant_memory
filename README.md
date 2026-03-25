# Turbo Quant Memory for AI Agents

Local-first MCP server for AI coding agents that need a smaller, cheaper working context.

Local-first MCP-сервер для AI coding-агентів, яким потрібен менший і дешевший робочий контекст.

## Quickstart

EN: Canonical install path for the current namespace-enabled server.

UK: Канонічний шлях інсталяції для поточного namespace-enabled сервера.

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
- Tool catalog / Каталог інструментів: `health`, `server_info`, `list_scopes`, `self_test`, `remember_note`, `promote_note`, `search_memory`
- Storage root / Корінь сховища: `~/.turbo-quant-memory/`
- Query modes / Режими запиту: `project`, `global`, `hybrid`
- Default write scope / Дефолтний scope запису: `project`
- Default query mode / Дефолтний режим пошуку: `hybrid`
- Reality check / Важливе уточнення: this project does not claim direct KV-cache control over hosted models; TurboQuant is inspiration for the memory architecture, not a hosted-model hook. / цей проєкт не заявляє прямого контролю над KV-cache hosted-моделей; TurboQuant тут є джерелом ідей для memory-архітектури, а не hook у hosted-модель.

## Namespace Model

EN: Phase 2 makes the reserved scopes real.

UK: Phase 2 робить зарезервовані scope реальними.

- `project`: repository-local notes for the current codebase
- `global`: reusable notes promoted explicitly from `project`
- `hybrid`: merged search across `project` and `global` with a strong project bias

### Current Project Resolution / Резолв поточного проєкту

EN:

1. Use normalized `origin` remote URL first.
2. Fall back to the repo root path hash when no remote exists.
3. Allow explicit overrides with `TQMEMORY_PROJECT_ROOT`, `TQMEMORY_PROJECT_ID`, and `TQMEMORY_PROJECT_NAME`.

UK:

1. Спочатку використовувати нормалізований `origin` remote URL.
2. Якщо remote немає, використовувати hash від root path репозиторію.
3. Дозволяти явні overrides через `TQMEMORY_PROJECT_ROOT`, `TQMEMORY_PROJECT_ID` і `TQMEMORY_PROJECT_NAME`.

### Physical Storage / Фізичне зберігання

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

## Note Flow

EN: The safe write/query loop is:

UK: Безпечний write/query loop такий:

1. `remember_note(..., scope="project")`
2. `promote_note(note_id)` when the note is truly reusable
3. `search_memory(query, scope="hybrid")` for default recall

EN: Direct public writes to `global` are intentionally rejected.

UK: Прямі публічні записи в `global` навмисно заборонені.

## Result Envelope

EN: Search and write results return compact item cards with provenance:

UK: Результати пошуку і запису повертають компактні item cards з provenance:

- `scope`
- `project_id`
- `project_name`
- `source_kind`
- `item_id`
- `source_path`
- `updated_at`
- `confidence`
- `can_hydrate`
- `promoted_from` when relevant / коли релевантно

EN: The default payload also includes lightweight `title`, `content_preview`, and `tags` so agents can act without hydrating full source immediately.

UK: У дефолтному payload також є легкі `title`, `content_preview` і `tags`, щоб агент міг діяти без негайного hydration повного джерела.

## Smoke Path

EN: The repo smoke script validates the real namespace contract over MCP stdio:

UK: Repo smoke script перевіряє реальний namespace contract поверх MCP stdio:

1. connect and list the 7 tools;
2. inspect `server_info.current_project` and `storage_root`;
3. write a project note with `remember_note`;
4. promote it into `global`;
5. query it through `search_memory(scope="hybrid")` and confirm the `project` hit comes first.

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

EN: Tier 1 means the repo ships a checked-in config fixture, a documented connect path, and a concrete namespace smoke target.

UK: Tier 1 означає, що в репозиторії є готовий config fixture, задокументований шлях підключення і конкретна namespace smoke-ціль.

- Claude Code: use `examples/clients/claude.project.mcp.json` or `claude mcp add --scope project tqmemory -- uv run turbo-memory-mcp serve`
- Codex: use `examples/clients/codex.config.toml` or `codex mcp add tqmemory -- uv run turbo-memory-mcp serve`
- Cursor: use `examples/clients/cursor.project.mcp.json` in `.cursor/mcp.json` or `~/.cursor/mcp.json`
- OpenCode: use `examples/clients/opencode.config.json` under the local `mcp` config surface
- Smoke checklist / Smoke checklist: `examples/clients/SMOKE_CHECKLIST.md`

## Tier 2

EN: Antigravity remains a documented compatibility target, not equal proof to Tier 1. The fixture is intentionally raw JSON because the current path is manual custom-MCP import through the product UI.

UK: Antigravity лишається documented compatibility target, а не рівнозначно доведеною підтримкою Tier 1. Фікстура навмисно дана як raw JSON, бо поточний шлях підключення проходить через ручний custom-MCP import у UI продукту.

- Antigravity fixture / Antigravity фікстура: `examples/clients/antigravity.mcp.json`
- Manual validation path / Шлях ручної валідації: see `examples/clients/SMOKE_CHECKLIST.md`
