# Phase 2: Namespace Model - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Define how memory works safely inside one repository and across all repositories on the same machine through explicit namespaces, deterministic precedence, stable project identity, and provenance-aware envelopes.

Визначити, як пам'ять безпечно працює в межах одного репозиторію і між усіма репозиторіями на тій самій машині через явні namespaces, детермінований пріоритет, стабільну ідентичність проєкту і provenance-aware envelopes.

This phase establishes namespace rules and metadata contracts. It does **not** yet implement Markdown ingestion, semantic retrieval quality, or full hydration behavior beyond the namespace-facing contract needed for later phases.

Ця фаза встановлює namespace-правила і metadata-контракти. Вона **ще не** реалізує Markdown-ingestion, повну якість semantic retrieval або повну hydration-поведінку поза namespace-контрактом, потрібним для наступних фаз.

</domain>

<decisions>
## Implementation Decisions

### Project Identity and Detection
- **D-01:** `project_id` must be derived from normalized git remote URL first, with fallback to a stable hash of the repo root path when no remote exists.
- **D-02:** The system must work without a git remote and must still produce a deterministic `project_id` for local-only repositories.
- **D-03:** An explicit project-level override for identity is allowed, but zero-config detection remains the default path.

### Storage Topology
- **D-04:** Phase 2 uses a central home-directory storage root under `~/.turbo-quant-memory/`.
- **D-05:** `project` and `global` data both live in the central storage root; the repository itself should only carry lightweight config or manifest data when needed.
- **D-06:** Project data must be partitioned under the central store by stable `project_id`, not by ad hoc directory naming.

### Hybrid Conflict Resolution
- **D-07:** Supported query modes remain `project`, `global`, and `hybrid`, with `hybrid` as the default mode.
- **D-08:** `hybrid` uses merged ranking with a strong project bias rather than strict fallback.
- **D-09:** A clearly better `project` hit must never be overridden by a `global` hit.
- **D-10:** Precedence must be deterministic. When scores are close, the tie-break order should prefer `project` over `global`, then newer `updated_at`, then stable item identity.

### Write and Promotion Policy
- **D-11:** All new writes default to `project` scope.
- **D-12:** `global` knowledge is created through explicit promotion from `project`, not through normal direct global writes.
- **D-13:** Public Phase 2 write paths should not expose a default direct-write-to-global flow for agents; this is intentionally kept out to prevent cross-project contamination.
- **D-14:** Promotion to `global` must preserve a link back to the original project-scoped source so later phases can explain where reusable knowledge came from.

### Provenance and Result Envelope
- **D-15:** The default metadata envelope should optimize for trust per token, not maximal debug volume.
- **D-16:** Every returned item must include at least: `scope`, `project_id`, `project_name`, `source_kind`, `item_id` or `block_id`, `source_path`, `updated_at`, `confidence`, and `can_hydrate`.
- **D-17:** `promoted_from` must be included when the item originated in `project` scope and was later promoted into `global`.
- **D-18:** Heavier metadata such as full lineage history, `created_at`, or extra debug fields should stay out of the default envelope and be exposed only when relevant or explicitly requested later.

### the agent's Discretion
- Exact normalization strategy for git remote URLs
- Exact hashing algorithm and manifest file naming
- Exact numeric project-bias weight for merged ranking
- Exact filesystem layout under `~/.turbo-quant-memory/`
- Exact field names for optional lineage/debug metadata beyond the required default envelope

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Product and Scope
- `.planning/PROJECT.md` — product thesis, local-first constraints, and the requirement that memory work both per-project and across all projects
- `.planning/REQUIREMENTS.md` — Phase 2 requirements `SCP-01` to `SCP-04`
- `.planning/ROADMAP.md` — Phase 2 boundary and acceptance criteria
- `.planning/STATE.md` — current project status and carried-forward decisions from Phase 1
- `.planning/phases/01-client-integration-foundation/01-CONTEXT.md` — locked Phase 1 decisions, especially the reserved scope names `project`, `global`, and `hybrid`

### Memory Design
- `MEMORY_STRATEGY.md` — recommended two-scope model, hybrid project bias, central storage root, promotion model, and namespace rationale
- `TECHNICAL_SPEC.md` — product-level technical scope, data model direction, and safety constraints relevant to namespace design
- `AGENTS.md` — project identity and memory usage policy, including project-first writes, global promotion, and hybrid search with project bias

### Published Runtime Contract
- `README.md` — public Phase 1 runtime contract already publishes `project`, `global`, and `hybrid` as the reserved scope vocabulary

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/turbo_memory_mcp/contracts.py` — central contract builder already exposes `project`, `global`, and `hybrid` through `build_scope_payload()`, making it the natural place to extend namespace metadata
- `src/turbo_memory_mcp/server.py` — `list_scopes` and `server_info` are the current MCP integration points where namespace semantics will start surfacing
- `tests/test_tools.py` — already locks the scope vocabulary and can be extended to lock Phase 2 namespace semantics
- `tests/test_smoke_contract.py` — already protects public metadata that docs and clients rely on

### Established Patterns
- The repo is already contract-first: payload shapes live in `contracts.py` and are reused by runtime and tests
- The repo is already docs-first: README, planning docs, and examples are treated as part of the product contract
- Phase 1 intentionally exposed reserved scopes before storage existed, so Phase 2 should fill in behavior without renaming the vocabulary

### Integration Points
- Namespace behavior must extend the existing MCP contract rather than creating a parallel metadata path
- Any Phase 2 storage layer must fit behind the existing local `stdio` server and `turbo-memory-mcp serve` runtime
- Public docs and smoke tests will need to stay aligned with whatever namespace contract Phase 2 locks

</code_context>

<specifics>
## Specific Ideas

- Use one central local store at `~/.turbo-quant-memory/`
- Keep `hybrid` as the default but never let `global` drown out the current project
- Treat `global` as a curated reusable layer, not a dumping ground
- Prefer a compact standard envelope that maximizes contextual trust while minimizing token volume

</specifics>

<deferred>
## Deferred Ideas

- Direct public writes into `global` scope
- Team scope between `project` and `global`
- Auto-promotion heuristics from `project` to `global`
- Full retrieval and hydration envelope expansion beyond the compact Phase 2 namespace contract

</deferred>

---
*Phase: 02-namespace-model*
*Context gathered: 2026-03-25*
