# Phase 8: Memory resilience, migration stats, release rollout, and client refresh - Context

**Gathered:** 2026-04-03
**Status:** Ready for execution

## Phase Boundary

Ship the first post-v1 hardening slice that makes memory upgrades safer, telemetry more persuasive, and client rollout cleaner.

- Auto-rebuild derived indexes when an on-disk index format changes after a release.
- Keep telemetry separate from project/global memory so usage stats never pollute retrieval context.
- Track compact-vs-raw payload savings with an honest formula and optional cost basis.
- Refresh release-facing docs, smoke coverage, and client fixtures/configs for Claude Code, Codex, Gemini, and OpenCode.

## Locked Decisions

- `project` remains the safe default query mode; `global` stays available only through explicit promotion and explicit hybrid reads.
- Index upgrade handling must be versioned by format/schema, not by package version alone.
- Savings telemetry must be transparent: count bytes/tokens saved directly, and expose USD only when a pricing basis is explicitly configured.
- Agent-facing “marketing” should stay bounded and factual: short milestone headlines, not noisy payload spam.
