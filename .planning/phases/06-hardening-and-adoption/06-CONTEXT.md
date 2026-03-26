# Phase 6: Hardening and Adoption - Context

**Gathered:** 2026-03-26
**Status:** Ready for execution

## Phase Boundary

Close the final operator-facing adoption gaps for v1 without widening the MCP surface unnecessarily.

- Reuse `server_info()` instead of adding a dedicated stats tool.
- Prove install -> index -> search -> hydrate through the shared smoke path.
- Document the first-response troubleshooting steps for the most likely local runtime issues.

## Locked Decisions

- `server_info()` should expose storage counts and freshness snapshots.
- `self_test()` stays lightweight and keeps the published tool catalog stable.
- The most important troubleshooting note is the first-run Hugging Face model download/cache behavior.

