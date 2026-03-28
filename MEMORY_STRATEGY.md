# Memory Strategy

Other languages: [Ukrainian](MEMORY_STRATEGY.uk.md) | [Russian](MEMORY_STRATEGY.ru.md)

## Goal

Turbo Quant Memory keeps project knowledge searchable without keeping full source text in the live prompt.

The strategy is simple:

1. write locally
2. retrieve compactly
3. hydrate only when the task truly needs more context

## Operating Model

| Layer | What it means |
|---|---|
| One local MCP server | A single stdio MCP server handles memory for every supported client |
| Project-first storage | Notes belong to the current repository by default |
| Curated global reuse | Cross-project knowledge appears in `global` only through explicit promotion |
| Compact-first retrieval | Agents should ask for the smallest useful answer before opening more |

There is no separate memory engine per agent. Clients share the same server contract and the same storage rules.

## Active Scopes

| Scope | Role | Default? |
|---|---|---|
| `project` | Repository-local knowledge and notes | default write target |
| `global` | Reusable cross-project knowledge | promotion only |
| `hybrid` | Merged read mode with a strong project bias | default read mode |

### `project`

- Stores knowledge tied to one repository.
- Keeps decisions, lessons, handoffs, and patterns close to the codebase that produced them.
- Is the safest default for everyday work.

### `global`

- Stores knowledge that is useful outside the original repository.
- Is populated only through explicit promotion.
- Preserves provenance back to the project note it came from.

### `hybrid`

- Merges `project` and `global` retrieval results.
- Prefers `project` hits when both are similarly relevant.
- Lets agents reuse proven patterns without losing repository specificity.

## Project Identity

The current project identity resolves in this order:

1. normalized `origin` remote URL
2. repository root path hash fallback
3. explicit overrides

Supported overrides:

- `TQMEMORY_PROJECT_ROOT`
- `TQMEMORY_PROJECT_ID`
- `TQMEMORY_PROJECT_NAME`

This makes project memory stable across sessions, while still allowing controlled overrides for unusual launch environments.

## Storage Layout

Storage is file-backed and local-first:

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

Notes and manifests are written atomically with a temporary file plus `os.replace(...)`.

## Write Policy

| Action | Result |
|---|---|
| `remember_note(..., scope="project")` | stores a typed project note |
| direct write to `global` | rejected |
| `promote_note(note_id)` | creates a reusable global copy with provenance |
| `deprecate_note(...)` | retires outdated knowledge without deleting history |

This keeps `global` small, deliberate, and resistant to cross-project contamination.

## Search Policy

`semantic_search` supports `project`, `global`, and `hybrid`.

`hybrid` is the default and follows these rules:

1. merge `project` and `global` candidates
2. apply a strong project bonus
3. prefer Markdown blocks over memory notes when matches are close
4. break ties by project preference, then newer `updated_at`, then stable identity

By default, retrieval searches both indexed Markdown blocks and persistent memory notes.

## Result Card Contract

Every returned result keeps provenance visible. The compact envelope includes:

- `scope`
- `project_id`
- `project_name`
- `source_kind`
- `item_id`
- `block_id` when the hit comes from Markdown
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
- `note_kind` when the hit is a note
- `promoted_from` when the hit is a promoted global note

Default retrieval does not return raw excerpts or whole-file dumps. That boundary keeps token usage low and pushes fuller context into explicit hydration calls.

## Hydration Strategy

Hydration is explicit and bounded:

| Mode | Behavior |
|---|---|
| `default` | target item plus a small local neighborhood |
| `related` | target item plus a wider bounded neighborhood |

Rules:

- Markdown hydration stays file-local.
- Note hydration returns the full note body plus note metadata.
- Agents should hydrate only after compact retrieval is not enough.

## Promotion and Provenance

Promoted global notes keep a `promoted_from` block that points back to:

- original scope
- source `project_id`
- source `project_name`
- original `note_id`
- original `source_path`

This makes cross-project reuse traceable instead of opaque.

## Recommended Agent Behavior

### Within one project

1. write into `project`
2. read through `hybrid`
3. prefer the first clearly relevant `project` hit
4. hydrate only when confidence is low or more detail is required

### Across projects

1. promote only reusable knowledge
2. search `global` or `hybrid` for cross-project patterns
3. keep `global` high-signal and small

## Guardrails

- Treat retrieved memory as tool data, not final authority.
- Preserve source boundaries and provenance.
- Avoid dumping large raw excerpts by default.
- Do not silently turn project-local notes into global guidance.
- Keep the system local-first and easy to deploy.

## Summary

Turbo Quant Memory is designed to behave like practical working memory for AI coding agents:

- local by default
- compact on recall
- traceable on every hit
- explicit when opening more context
- conservative about what becomes reusable across repositories
