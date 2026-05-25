# AGENTS.md

## Project Identity

- Project name: `Turbo Quant Memory for AI Agents`
- Project slug: `turbo_quant_memory`
- Memory user_id: `tq_memory_agents_20260325_default`
- Default sessionId: `default`

## Memory Usage Policy

- Use project-scoped memory by default.
- Use global memory only for reusable cross-project knowledge.
- Prefer hybrid search with project bias when reading.
- Require explicit promotion from project to global memory.
- Every memory result must preserve provenance.
- Before editing an existing area, search project memory first.
- Use `hydrate` only when compact retrieval is not enough.
- After important decisions, debugging outcomes, or handoffs, store one concise note.
- After README, config, workflow, or architecture changes, refresh indexed docs.
- When old knowledge is replaced, write the new note and deprecate the old one.
- Do not keep smoke, temporary, or validation notes active.
- Treat `.planning`, `.serena`, and generated benchmark artifacts as historical workflow context, not as primary user-facing memory.
- Use Knowledge Graph relations (`link_entities`, `unlink_entities`, `get_related_entities`) to build associations between notes, source files, issues, or tasks, enabling association-based semantic retrieval.

## Accessing Project Secrets (Phase 9)

The four secrets tools (`set_secret`, `get_secret`, `list_secrets`, `delete_secret`) are deliberately narrow. Follow this recipe:

1. **Discover what's available** via project memory, not a transcript search:
   `semantic_search(query="connect to prod db", scope="project")` → look for `pattern`-kind notes that explain which `get_secret(name)` call to make and how to use it.
2. **Fetch only when needed**: call `get_secret(name)` and read the value from the dedicated `secret_value` field on the response. Pass it through programmatically (env var injection, subprocess argument, in-memory) — never echo it into chat summaries, never persist it into `remember_note`.
3. **Never write secrets via set_secret with a value that came from the chat transcript**. If a user pastes a credential, ask them to set it themselves via `keyring set turbo-quant-memory secrets-master-<project_id>` followed by `set_secret` on a clean session, OR via a future CLI command. The chat transcript is logged by the MCP client and the value should never live there.
4. **Errors are structured**: a `master_key_unavailable` response carries a `setup_hint` field with the exact shell commands the user should run. Surface it verbatim and stop.
5. **Audit is on by default**: every `set` / `get` / `list` / `delete` is recorded as `(ts, action, name)` in `~/.turbo-quant-memory/projects/<project_id>/secrets/audit.jsonl`. The value is never logged.

## Documentation Policy

- Keep user-facing documentation available in English, Ukrainian, and Russian.
- Keep the design simple, local-first, and easy to deploy.
- Avoid product claims that imply direct KV-cache control over hosted models.

## Current Planning Context

- See `.planning/PROJECT.md`
- See `.planning/REQUIREMENTS.md`
- See `.planning/ROADMAP.md`
- See `TECHNICAL_SPEC.md`
- See `MEMORY_STRATEGY.md`
