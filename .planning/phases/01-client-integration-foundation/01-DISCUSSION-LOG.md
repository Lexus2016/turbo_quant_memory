# Phase 1: Client Integration Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in `01-CONTEXT.md` — this log preserves the alternatives considered.

**Date:** 2026-03-25
**Phase:** 1-Client Integration Foundation
**Areas discussed:** Install contract, Minimal tool surface, Client support tiering, Command and package shape

---

## Install Contract

| Option | Description | Selected |
|--------|-------------|----------|
| `uv` only | Simplest single path, but stricter environment dependency | |
| `uv` primary + `pip` fallback | Simple main path with a safe compatibility fallback | ✓ |
| `uv` primary + `pip` + `pipx` fallback | Maximum flexibility, more docs/support overhead | |
| Other | Freeform alternative | |

**User's choice:** `uv` primary + `pip` fallback
**Notes:** Also fixed `Python >=3.11`, blessed runtime `uv run turbo-memory-mcp serve`, and Phase 1 acceptance as `README + sample MCP configs + smoke test script`.

---

## Minimal Tool Surface

| Option | Description | Selected |
|--------|-------------|----------|
| `health` only | Proves the server starts, but not much product value | |
| `health + server_info + list_scopes` | Best narrow tool surface for a foundation phase | ✓ |
| `health + remember_note + search_memory` | More impressive demo, but starts leaking into later phases | |
| Other | Freeform alternative | |

**User's choice:** `health + server_info + list_scopes`
**Notes:** User initially pushed for a full minimal memory loop in Phase 1. This was explicitly redirected as a deferred product requirement so roadmap boundaries stay intact. `self_test` was approved as an explicit MCP tool in Phase 1.

---

## Client Support Tiering

| Option | Description | Selected |
|--------|-------------|----------|
| All 5 clients equally supported | Strong claim, high verification burden | |
| Tier 1 / Tier 2 | Honest and pragmatic support model | ✓ |
| Single primary client only | Simplest, but too narrow for the product vision | |
| Other | Freeform alternative | |

**User's choice:** Tiered support
**Notes:** Tier 1 = Claude Code, Codex, Cursor, OpenCode. Tier 2 = Antigravity with documented config and compatibility target. Tier 1 acceptance requires config example + connect check + `self_test`.

---

## Command and Package Shape

| Option | Description | Selected |
|--------|-------------|----------|
| Long descriptive package name | More exact, but weak UX for CLI/package use | |
| `turbo-memory-mcp` | Short and practical canonical package name | ✓ |
| `tqmemory` | Very short, but less self-explanatory | |
| Other | Freeform alternative | |

**User's choice:** `turbo-memory-mcp`
**Notes:** Canonical MCP server id is `tqmemory`. Canonical CLI shape is `turbo-memory-mcp serve`. Packaging in Phase 1 must include Python package + console script + pinned `uv` workflow docs.

---

## the agent's Discretion

- Internal package layout
- Exact MCP response schema details for Phase 1 tools
- Exact smoke test implementation format

## Deferred Ideas

- Full minimal memory loop in Phase 1 was requested, then deferred to early implementation phases to preserve roadmap scope.
- Antigravity full parity smoke test remains desirable, but is not mandatory for Tier 1 acceptance in Phase 1.
