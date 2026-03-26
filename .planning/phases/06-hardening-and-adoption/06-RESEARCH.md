# Phase 6: Hardening and Adoption - Research

**Researched:** 2026-03-26
**Confidence:** HIGH

## Summary

The smallest change that satisfies the remaining production-readiness gap is:

1. extend `server_info()` with `storage_stats` and `index_status`,
2. keep `health()` and `self_test()` compact,
3. publish troubleshooting notes for indexing and local embedding cache behavior,
4. prove the full flow through the shared stdio smoke script.

This closes `OPS-01` and hardens `OPS-02` without adding another public MCP tool.

