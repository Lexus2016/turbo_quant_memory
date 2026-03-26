# Roadmap: Turbo Quant Memory for AI Agents

**Defined:** 2026-03-25
**Requirements mapped:** 21 / 21
**Status:** All v1 phases completed; ready for production validation

## Overview

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|------------------|
| 1 | Client Integration Foundation | Start a local stdio MCP server that multiple agent clients can connect to with a minimal install path | INT-01, INT-02, INT-03, INT-04 | 4 |
| 2 | Namespace Model | Define and implement project and global memory scopes with safe precedence rules | SCP-01, SCP-02, SCP-03, SCP-04 | 4 |
| 3 | Markdown Ingestion | Build deterministic Markdown chunking and incremental indexing into local memory storage | ING-01, ING-02, ING-03 | 4 |
| 4 | Compressed Retrieval | Return compact, provenance-rich retrieval results instead of raw file dumps | RET-01, RET-02, SAFE-01, SAFE-02 | 4 |
| 5 | Hydration and Write-Back | Add fuller-context recovery and persistent session memory | RET-03, RET-04, MEM-01, MEM-02 | 4 |
| 6 | Hardening and Adoption | Add observability, smoke tests, and easy operator guidance for real use | OPS-01, OPS-02 | 4 |

## Phase Details

### Phase 1: Client Integration Foundation

**Goal:** Establish the smallest useful server that can run locally, expose initial tools, and be connected to supported agent clients without custom infrastructure.

**Status:** Complete (2026-03-25)

**Requirements:** `INT-01`, `INT-02`, `INT-03`, `INT-04`

**UI hint:** no

**Success criteria:**
1. A developer can install dependencies and launch the server locally over stdio.
2. Claude Code can connect to the server using a documented `claude mcp add ...` or `.mcp.json` configuration.
3. The server exposes a minimal, discoverable MCP tool surface for future memory operations.
4. Documented setup examples exist for Claude Code, Codex, Cursor, OpenCode, and Antigravity, and the setup path does not require a separate DB service or mandatory GPU dependency.

### Phase 2: Namespace Model

**Goal:** Make memory safe and useful across one project and all projects simultaneously through explicit namespaces and merge rules.

**Status:** Complete (2026-03-25)

**Requirements:** `SCP-01`, `SCP-02`, `SCP-03`, `SCP-04`

**UI hint:** no

**Success criteria:**
1. The current repository resolves to a deterministic project memory namespace.
2. A machine-wide global namespace exists for reusable cross-project knowledge.
3. Search can run in `project`, `global`, and `hybrid` modes with documented precedence.
4. Every result carries scope and origin metadata so agents can tell where the memory came from.

### Phase 3: Markdown Ingestion

**Goal:** Convert Markdown knowledge into stable, indexed memory blocks with deterministic provenance and incremental refresh behavior.

**Status:** Complete (2026-03-26)

**Requirements:** `ING-01`, `ING-02`, `ING-03`

**UI hint:** no

**Success criteria:**
1. User can point the server at one or more Markdown directories for indexing.
2. Files are split into stable blocks with reproducible block IDs and source metadata.
3. Reindexing changed content updates affected blocks without rebuilding untouched content.
4. The index persists locally between sessions.

### Phase 4: Compressed Retrieval

**Goal:** Let agents retrieve compact high-signal memory results with clear provenance while avoiding default raw-file dumps.

**Status:** Complete (2026-03-26)

**Requirements:** `RET-01`, `RET-02`, `SAFE-01`, `SAFE-02`

**UI hint:** no

**Success criteria:**
1. Semantic search returns relevant blocks with score, path, and block ID.
2. Agents can request compressed context cards that preserve meaning and traceability.
3. Every retrieval result clearly points back to the original source block.
4. Large raw context is not returned unless explicitly requested through a hydration path.

### Phase 5: Hydration and Write-Back

**Goal:** Recover fuller local context when compression is insufficient and persist new learnings for future sessions.

**Status:** Complete (2026-03-26)

**Requirements:** `RET-03`, `RET-04`, `MEM-01`, `MEM-02`

**UI hint:** no

**Success criteria:**
1. Agent can hydrate a fuller block or excerpt when the compressed card is insufficient.
2. Agent can request neighboring or related blocks to restore local context.
3. Agent can save important notes or decisions into persistent memory with metadata.
4. Saved notes become searchable alongside the original Markdown corpus.

### Phase 6: Hardening and Adoption

**Goal:** Make the system safe to adopt in day-to-day work through observability, verification, and operator-facing documentation.

**Status:** Complete (2026-03-26)

**Requirements:** `OPS-01`, `OPS-02`

**UI hint:** no

**Success criteria:**
1. User can inspect server health, index freshness, and basic storage statistics.
2. A documented smoke test validates install, indexing, search, and hydration end-to-end.
3. The integration guide is simple enough for a new project to adopt without reverse engineering.
4. Common failure modes are documented with first-response troubleshooting steps.

## Coverage Check

- All v1 requirements map to exactly one phase.
- Усі v1-вимоги замаплено рівно на одну фазу.
- The roadmap is biased toward fast adoption and minimal deployment friction.
- Roadmap навмисно зміщений у бік швидкого adoption і мінімального тертя при розгортанні.

---
*Last updated: 2026-03-26 after Phase 6 execution*
