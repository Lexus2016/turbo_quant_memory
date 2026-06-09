from __future__ import annotations

from turbo_memory_mcp.retrieval import _updated_epoch


def test_updated_epoch_parses_iso_timestamp() -> None:
    assert _updated_epoch("2026-06-09T00:00:00Z") > 0.0


def test_updated_epoch_falls_back_on_malformed_timestamp() -> None:
    # A corrupt updated_at must not crash retrieval ordering (audit H3).
    assert _updated_epoch("not-a-timestamp") == 0.0
    assert _updated_epoch("") == 0.0
