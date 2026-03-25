# Phase 1: Client Integration Foundation - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver the smallest useful local `stdio` MCP server foundation for Turbo Quant Memory for AI Agents, including packaging, startup contract, minimal introspection tools, client connection examples, and validation flows for supported agent clients.

Потрібно поставити найменший корисний фундамент локального `stdio` MCP-сервера для Turbo Quant Memory for AI Agents, включно з пакуванням, контрактом запуску, мінімальними introspection-tools, прикладами підключення клієнтів і validation-flow для підтримуваних agent-клієнтів.

This phase does **not** implement the real memory loop yet. Project/global namespaces belong to Phase 2. Markdown ingestion belongs to Phase 3. Retrieval and hydration belong to Phases 4-5.

Ця фаза **не** реалізує повний memory-loop. Project/global namespaces належать до Фази 2. Markdown ingestion належить до Фази 3. Retrieval і hydration належать до Фаз 4-5.

</domain>

<decisions>
## Implementation Decisions

### Install Contract
- **D-01:** The blessed install path for v1 is `uv` as the primary flow, with `pip` as the documented fallback.
- **D-02:** The minimum supported Python baseline is `>=3.11`.
- **D-03:** The blessed runtime command is `uv run turbo-memory-mcp serve`.
- **D-04:** Phase 1 acceptance must include `README quickstart + sample MCP configs + smoke test script`.

### Minimal Tool Surface
- **D-05:** Phase 1 MCP tools are limited to `health`, `server_info`, and `list_scopes`.
- **D-06:** `server_info` must expose at least: product name, version, runtime command, install contract, and supported clients.
- **D-07:** Phase 1 must expose a dedicated `self_test` MCP tool for client validation and onboarding.
- **D-08:** The full memory loop is a strong product requirement, but it is intentionally deferred out of Phase 1 to preserve the roadmap boundary.

### Client Support Tiering
- **D-09:** Client support is explicitly tiered in Phase 1 rather than claiming all clients are equally supported.
- **D-10:** Tier 1 clients for Phase 1 are: Claude Code, Codex, Cursor, and OpenCode.
- **D-11:** Antigravity is Tier 2 in Phase 1: documented config and compatibility target, but not required to pass the same hard acceptance bar as Tier 1.
- **D-12:** Tier 1 acceptance requires: config example, successful connect check, and `self_test` execution for each Tier 1 client.

### Command and Package Shape
- **D-13:** The canonical Python package and console-script name is `turbo-memory-mcp`.
- **D-14:** The canonical MCP server identifier in client configs is `tqmemory`.
- **D-15:** The canonical CLI server command is `turbo-memory-mcp serve`.
- **D-16:** Packaging for Phase 1 must include a Python package, a console script, and pinned `uv` workflow documentation.

### the agent's Discretion
- Internal Python package layout
- Exact implementation of `health`, `server_info`, `list_scopes`, and `self_test`
- Choice of packaging metadata files and build backend, as long as the install/runtime contract above is preserved
- Exact smoke-test structure per Tier 1 client

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Product and Scope
- `.planning/PROJECT.md` — product identity, local-first constraint, multi-client goal, and project/global memory strategy boundaries
- `.planning/REQUIREMENTS.md` — Phase 1 requirements `INT-01` to `INT-04` and cross-phase constraints
- `.planning/ROADMAP.md` — Phase 1 boundary and acceptance intent
- `.planning/STATE.md` — current project focus and preserved decisions

### Technical Direction
- `TECHNICAL_SPEC.md` — canonical product-level technical specification for the MCP server
- `MEMORY_STRATEGY.md` — project/global memory topology and why full memory behavior is deferred beyond Phase 1
- `CLIENT_INTEGRATIONS.md` — client-specific MCP config targets for Claude Code, Codex, Cursor, OpenCode, and Antigravity
- `AGENTS.md` — project identity, memory policy, and current planning context

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- No application source code exists yet — Phase 1 starts from a greenfield repository.

### Established Patterns
- Planning artifacts already establish a strong documentation-first and bilingual workflow.
- The product has already committed to one core MCP server reused across multiple clients.

### Integration Points
- Client integration points are the external MCP configuration surfaces documented in `CLIENT_INTEGRATIONS.md`.
- Packaging and runtime must align with the commands referenced in `TECHNICAL_SPEC.md`, `CLIENT_INTEGRATIONS.md`, and `AGENTS.md`.

</code_context>

<specifics>
## Specific Ideas

- Use one core local `stdio` MCP server with thin client-specific wrappers rather than per-client implementations.
- Keep the first phase honest: prove integration and deployability first, then add real memory behavior in later phases.
- The package/command naming should stay short and stable: `turbo-memory-mcp` + `tqmemory`.

</specifics>

<deferred>
## Deferred Ideas

- Full minimal memory loop in the MCP layer — explicitly deferred to early implementation phases after foundation, with strong bias to Phase 2/3.
- Antigravity full parity smoke test — desirable, but not required for Tier 1 acceptance in Phase 1.

None of these change Phase 1 scope; they are preserved so planning does not accidentally drop them.

</deferred>

---
*Phase: 01-client-integration-foundation*
*Context gathered: 2026-03-25*
