---
phase: 3
slug: markdown-ingestion
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-25
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest 8.x` |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest -q tests/test_markdown_chunking.py tests/test_ingestion_store.py tests/test_ingestion_tools.py` |
| **Full suite command** | `uv run pytest -q && uv run python scripts/smoke_test.py` |
| **Estimated runtime** | ~25 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest -q tests/test_markdown_chunking.py tests/test_ingestion_store.py tests/test_ingestion_tools.py`
- **After every plan wave:** Run `uv run pytest -q`
- **Before `$gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 25 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | ING-01 | unit | `uv run pytest -q tests/test_ingestion_store.py -k roots` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | ING-01, ING-03 | unit | `uv run pytest -q tests/test_ingestion_store.py -k incremental` | ❌ W0 | ⬜ pending |
| 03-01-03 | 01 | 1 | ING-01 | contract | `uv run pytest -q tests/test_ingestion_tools.py -k registration` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 2 | ING-02 | unit | `uv run pytest -q tests/test_markdown_chunking.py -k headings` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 2 | ING-02 | unit | `uv run pytest -q tests/test_markdown_chunking.py -k block_id` | ❌ W0 | ⬜ pending |
| 03-03-01 | 03 | 3 | ING-01, ING-03 | contract | `uv run pytest -q tests/test_ingestion_tools.py -k index_paths` | ❌ W0 | ⬜ pending |
| 03-03-02 | 03 | 3 | ING-01, ING-02, ING-03 | e2e stdio | `uv run python scripts/smoke_test.py` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_markdown_chunking.py` — heading-aware section extraction, fallback splitting, deterministic `block_id`, and checksum coverage for `ING-02`
- [ ] `tests/test_ingestion_store.py` — root registry, per-file manifests, deleted-file cleanup, and incremental skip/rewrite coverage for `ING-01` and `ING-03`
- [ ] `tests/test_ingestion_tools.py` — `index_paths(paths, mode)` MCP contract and indexing result counts for `ING-01` and `ING-03`
- [ ] temp Markdown fixtures under `tests/fixtures/markdown/` or inline fixture builders for reproducible chunking cases
- [ ] `scripts/smoke_test.py` extension that seeds a temporary Markdown directory and validates one incremental reindex cycle

*Existing infrastructure already covers pytest setup, stdio MCP harness, and repo-level smoke execution.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Indexed roots behave sensibly on a real project docs tree | ING-01 | Small fixture corpora cannot prove ergonomics on a real repository documentation set | Point `index_paths` at a real docs folder, rerun without arguments, and confirm the previously registered roots are reused correctly |
| Chunk boundaries feel useful for downstream retrieval and hydration | ING-02 | Structural correctness is automatable, but usefulness of chunk boundaries still benefits from human review | Inspect a few stored block records from a real Markdown file with nested headings, code fences, and lists; confirm the boundaries preserve meaning |
| Incremental refresh avoids unnecessary rewrites on real filesystems | ING-03 | Filesystem timestamp behavior can vary across environments | Run one full index, rerun without content changes, then edit one file and delete another; confirm only affected file manifests and blocks change |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 25s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
