---
phase: 5
slug: hydration-and-write-back
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-26
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest 8.x` |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest -q tests/test_storage.py tests/test_hydration.py tests/test_namespace_tools.py tests/test_semantic_search.py tests/test_tools.py tests/test_smoke_contract.py` |
| **Full suite command** | `uv run pytest -q && uv run python scripts/smoke_test.py` |
| **Estimated runtime** | ~45 seconds |

---

## Sampling Rate

- **After every task commit:** Run the narrowest targeted pytest command for that task.
- **After every plan wave:** Run `uv run pytest -q`
- **Before `$gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 45 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | RET-03, RET-04 | unit | `uv run pytest -q tests/test_storage.py -k neighborhood` | ✅ | ✅ green |
| 05-01-02 | 01 | 1 | RET-03 | unit | `uv run pytest -q tests/test_hydration.py -k markdown` | ✅ | ✅ green |
| 05-01-03 | 01 | 1 | RET-04 | unit | `uv run pytest -q tests/test_hydration.py -k related_mode` | ✅ | ✅ green |
| 05-02-01 | 02 | 2 | MEM-01 | unit | `uv run pytest -q tests/test_namespace_tools.py -k note_kind` | ✅ | ✅ green |
| 05-02-02 | 02 | 2 | RET-03, MEM-01 | contract | `uv run pytest -q tests/test_namespace_tools.py tests/test_hydration.py -k hydrate` | ✅ | ✅ green |
| 05-02-03 | 02 | 2 | MEM-02 | contract | `uv run pytest -q tests/test_semantic_search.py -k note_kind` | ✅ | ✅ green |
| 05-03-01 | 03 | 3 | RET-03, RET-04, MEM-01, MEM-02 | contract | `uv run pytest -q tests/test_tools.py tests/test_smoke_contract.py` | ✅ | ✅ green |
| 05-03-02 | 03 | 3 | RET-03, RET-04, MEM-01, MEM-02 | e2e stdio | `uv run python scripts/smoke_test.py` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/test_hydration.py` — hydration behavior for Markdown blocks and notes
- [x] `tests/test_storage.py` — neighborhood lookup and typed note persistence helpers
- [x] `tests/test_namespace_tools.py` — typed write-back validation and live `hydrate(...)` wiring
- [x] `tests/test_semantic_search.py` — typed note recall remains searchable with Markdown
- [x] `tests/test_tools.py` — public tool catalog includes `hydrate`
- [x] `tests/test_smoke_contract.py` — hydration payload contract and note-kind envelope
- [x] `scripts/smoke_test.py` — real stdio hydration smoke path

*Existing infrastructure already covers pytest setup, MCP stdio harness, and repo-level smoke execution.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Hydrated Markdown context feels sufficient without dumping the file | RET-03, RET-04 | structural tests cannot judge usefulness | run `semantic_search(...)` on a real query, then `hydrate(...)` on the top block and confirm the payload is enough to act without requesting the whole file |
| Note kinds remain understandable for future agents | MEM-01 | semantic usefulness is hard to automate | create one note of each type and inspect search plus hydration outputs for clarity |
| Search still favors source evidence when note and doc are similarly relevant | MEM-02 | relative usefulness benefits from human review | seed docs and typed notes with overlapping language, then inspect `semantic_search(...)` ordering |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all missing references
- [x] No watch-mode flags
- [x] Feedback latency < 45s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** complete
