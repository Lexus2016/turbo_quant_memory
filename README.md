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
- Tool catalog / Каталог інструментів: `health`, `server_info`, `list_scopes`, `self_test`, `remember_note`, `promote_note`, `semantic_search`, `index_paths`
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
3. `semantic_search(query, scope="hybrid")` for default recall

EN: Direct public writes to `global` are intentionally rejected.

UK: Прямі публічні записи в `global` навмисно заборонені.

## Result Envelope

EN: Phase 4 retrieval returns balanced cards with provenance-first fields:

UK: У Phase 4 retrieval повертає balanced cards з provenance-first полями:

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

EN: `semantic_search(...)` covers both indexed Markdown blocks and persistent notes. Default responses do not include raw excerpts, `content_raw`, or whole-file dumps; fuller hydration is deferred to Phase 5.

UK: `semantic_search(...)` працює і по проіндексованих Markdown-блоках, і по persistent notes. Дефолтні відповіді не містять raw excerpts, `content_raw` або дампів цілих файлів; повніше hydration відкладено до Phase 5.

EN: Hybrid ranking keeps a strong `project` bias, and within each scope close matches prefer Markdown source blocks over memory notes.

UK: Hybrid ranking зберігає сильний `project` bias, а всередині кожного scope близькі матчі віддають пріоритет Markdown source-блокам над memory notes.

EN: Low-confidence or ambiguous retrievals return cautious results with explicit warnings so the agent can decide whether hydration is needed.

UK: Low-confidence або неоднозначні retrieval-и повертаються з явними warning-полями, щоб агент міг вирішити, чи потрібне hydration.

## Smoke Path

EN: The repo smoke script validates the real Phase 4 contract over MCP stdio:

UK: Repo smoke script перевіряє реальний контракт Phase 4 поверх MCP stdio:

1. connect and list the 8 tools;
2. inspect `server_info.current_project` and `storage_root`;
3. index a small Markdown root with `index_paths(mode="full")`;
4. write and promote a project note with `remember_note` and `promote_note`;
5. call `semantic_search(scope="project")` and confirm the top hit is a Markdown balanced card;
6. call `semantic_search(scope="hybrid")` and confirm the `project` note appears before the promoted `global` note.

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
