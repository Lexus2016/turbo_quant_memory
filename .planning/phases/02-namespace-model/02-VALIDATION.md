---
phase: 2
slug: namespace-model
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-25
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest 8.x` |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest -q` |
| **Full suite command** | `uv run pytest -q && uv run python scripts/smoke_test.py` |
| **Estimated runtime** | ~20 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest -q`
- **After every plan wave:** Run `uv run pytest -q && uv run python scripts/smoke_test.py`
- **Before `$gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 20 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | SCP-01 | unit | `uv run pytest -q tests/test_identity.py` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | SCP-01, SCP-02 | unit | `uv run pytest -q tests/test_storage.py` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | SCP-02 | static structure | `uv run pytest -q tests/test_storage.py -k manifest` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 2 | SCP-03 | unit | `uv run pytest -q tests/test_namespace_tools.py -k query` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 2 | SCP-04 | unit | `uv run pytest -q tests/test_namespace_tools.py -k provenance` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 2 | SCP-01, SCP-02, SCP-03, SCP-04 | contract | `uv run pytest -q tests/test_tools.py tests/test_smoke_contract.py` | ✅ | ⬜ pending |
| 02-03-01 | 03 | 3 | SCP-03, SCP-04 | docs/static | `rg -n \"project_id|promote_note|search_memory|~/.turbo-quant-memory|hybrid\" README.md MEMORY_STRATEGY.md` | ❌ W0 | ⬜ pending |
| 02-03-02 | 03 | 3 | SCP-01, SCP-02, SCP-03, SCP-04 | e2e stdio | `uv run python scripts/smoke_test.py` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_identity.py` — deterministic project identity coverage
- [ ] `tests/test_storage.py` — central store layout and atomic persistence coverage
- [ ] `tests/test_namespace_tools.py` — namespace-aware tool and precedence coverage
- [ ] test fixtures for temporary home/store directories

*Existing infrastructure covers framework setup, pytest config, and basic MCP smoke harness.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Project-scoped MCP host actually starts inside the intended repo context | SCP-01 | Client hosts may vary in cwd/workspace behavior | In Claude Code or Codex project scope, run the server from the checked-in config and confirm `server_info` reports the expected current project identity |
| User-level MCP host reuses the same `global` memory across repositories | SCP-02 | Needs two real repositories or workspaces, outside a single repo test fixture | Connect the same local server from two repos, write reusable knowledge in one flow, confirm it is visible through `global` or `hybrid` in the other |
| Hybrid behavior feels sensible to the operator | SCP-03 | Result quality involves human judgment about “clearly better” project hits | Seed one repo-local and one global note with overlapping language, query through `hybrid`, and confirm the project result comes first without hiding the global one |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 20s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
