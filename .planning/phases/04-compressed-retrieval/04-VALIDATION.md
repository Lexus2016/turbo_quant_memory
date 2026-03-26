---
phase: 4
slug: compressed-retrieval
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-26
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest 8.x` |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest -q tests/test_retrieval_index.py tests/test_semantic_search.py tests/test_tools.py tests/test_smoke_contract.py` |
| **Full suite command** | `uv run pytest -q && uv run python scripts/smoke_test.py` |
| **Estimated runtime** | ~35 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest -q tests/test_retrieval_index.py tests/test_semantic_search.py tests/test_tools.py tests/test_smoke_contract.py`
- **After every plan wave:** Run `uv run pytest -q`
- **Before `$gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 35 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | RET-01 | unit | `uv run pytest -q tests/test_retrieval_index.py -k sync` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | RET-01, SAFE-01 | contract | `uv run pytest -q tests/test_semantic_search.py -k project_scope` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 2 | RET-01, RET-02, SAFE-01 | contract | `uv run pytest -q tests/test_semantic_search.py -k balanced_card` | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 | 2 | SAFE-02 | unit | `uv run pytest -q tests/test_semantic_search.py -k no_raw_excerpt` | ❌ W0 | ⬜ pending |
| 04-02-03 | 02 | 2 | RET-01, SAFE-01 | contract | `uv run pytest -q tests/test_tools.py tests/test_smoke_contract.py -k semantic_search` | ❌ W0 | ⬜ pending |
| 04-03-01 | 03 | 3 | RET-01, RET-02, SAFE-01, SAFE-02 | e2e stdio | `uv run python scripts/smoke_test.py` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_retrieval_index.py` — index sync coverage for markdown blocks and memory notes
- [ ] `tests/test_semantic_search.py` — ranking, balanced cards, markdown-first tie-breaks, and low-confidence behavior
- [ ] `tests/test_tools.py` — public tool catalog updated from `search_memory` to `semantic_search`
- [ ] `tests/test_smoke_contract.py` — semantic retrieval payload contract and warning envelope
- [ ] `scripts/smoke_test.py` — real stdio semantic-search smoke path

*Existing infrastructure already covers pytest setup, MCP stdio harness, and repo-level smoke execution.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Balanced cards feel high-signal on a real project docs corpus | RET-02 | Automated assertions can verify shape, but not whether the card preserves the right human meaning | Run `semantic_search(...)` against a real docs folder, inspect 5-10 returned cards, and confirm `compressed_summary` plus `key_points` is enough to choose the next action without hydration |
| Markdown-first precedence feels correct when notes and source blocks are both relevant | SAFE-01 | Relative usefulness can be asserted structurally, but still benefits from human review on ambiguous real queries | Seed notes that paraphrase indexed docs, run hybrid semantic queries, and confirm source blocks stay first unless the note is clearly more relevant |
| Low-confidence warnings are helpful without becoming noisy | SAFE-02 | Tone and usefulness of warnings are hard to score automatically | Probe ambiguous and weak queries, and confirm warning states are visible but compact |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 35s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
