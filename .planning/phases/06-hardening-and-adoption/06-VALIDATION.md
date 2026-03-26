---
phase: 6
slug: hardening-and-adoption
status: complete
nyquist_compliant: true
created: 2026-03-26
---

# Phase 6 — Validation Strategy

- Quick run: `uv run pytest -q tests/test_tools.py tests/test_smoke_contract.py`
- Full suite: `uv run pytest -q && uv run python scripts/smoke_test.py`

Checks:

- `server_info()` exposes `storage_stats` and `index_status`
- shared docs and smoke checklist mention hydration and troubleshooting
- real stdio smoke remains green

