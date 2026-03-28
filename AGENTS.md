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
