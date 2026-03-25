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

## Documentation Policy

- Keep documentation bilingual: English and Ukrainian.
- Keep the design simple, local-first, and easy to deploy.
- Avoid product claims that imply direct KV-cache control over hosted models.

## Current Planning Context

- See `.planning/PROJECT.md`
- See `.planning/REQUIREMENTS.md`
- See `.planning/ROADMAP.md`
- See `TECHNICAL_SPEC.md`
- See `MEMORY_STRATEGY.md`
