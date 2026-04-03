---
phase: 8
slug: memory-resilience-migration-stats-release-rollout-and-client-refresh
status: complete
nyquist_compliant: true
created: 2026-04-03
---

# Phase 8 — Validation Strategy

- Quick run: `uv run pytest -q tests/test_tools.py tests/test_smoke_contract.py tests/test_semantic_search.py tests/test_namespace_tools.py`
- Full suite: `uv run ruff check src tests scripts && uv run pytest -q && uv run python scripts/smoke_test.py`

Checks:

- Retrieval and markdown manifests expose current format versions.
- Search/hydrate auto-rebuild stale derived indexes after format mismatch.
- Usage telemetry persists across sessions and stays outside the memory namespaces.
- `server_info()` reports savings/impact with an explicit pricing basis when configured.
- Client fixtures and docs match the new release/install contract.

Verification results:

- `uv run ruff check src tests scripts` — pass
- `uv run pytest -q` — pass
- `uv run python scripts/smoke_test.py` — pass
