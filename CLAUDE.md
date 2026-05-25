# CLAUDE.md

## tqmemory Workflow

- Treat `tqmemory` as always-available project memory for this repository.
- Before editing an existing area, search project memory first.
- Prefer compact retrieval first and use deeper hydration only when needed.
- After important decisions, debugging outcomes, or handoffs, save one concise note.
- After README, config, workflow, setup, or architecture changes, refresh indexed docs.
- When old knowledge becomes stale, write the new note first and then deprecate the old note.
- Promote to global memory only if the knowledge is reusable outside this repository.
- Do not leave smoke or temporary notes active in memory.

## Practical Rule

- Use live project docs and current notes as the source of truth.
- Treat `.planning`, `.serena`, and generated benchmark artifacts as historical context, not as the main operational contract.

## Secrets Vault (Phase 9)

- The vault is project-scoped and lives under `~/.turbo-quant-memory/projects/<project_id>/secrets/`. Never index or search it.
- Always discover the right `get_secret(name)` call from a `pattern`-kind recipe note via `semantic_search`. Do not guess names from chat history.
- Pass `secret_value` through programmatically only — never echo into summaries, never store via `remember_note`.
- If you encounter a `master_key_unavailable` error response, surface its `setup_hint` verbatim to the user and stop; do not try to recover by guessing keys.
- Never call `set_secret` with a value already visible in the chat transcript. Ask the user to set it themselves on a clean session.
