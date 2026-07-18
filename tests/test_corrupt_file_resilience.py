"""Regression tests for corrupt-file resilience (audit X1-X6).

One malformed JSON on a read path must not take down a whole tool call, and
write paths must not silently clobber a corrupt-but-present file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.store import (
    MemoryStore,
    detect_orphaned_buckets,
    reconcile_project_identity,
)


def _identity(root: Path) -> ProjectIdentity:
    return ProjectIdentity(
        project_id="corrupt00test01",
        project_name="Corrupt Test",
        project_root=root,
        identity_source="local/corrupt",
        identity_kind="local_path",
        remote_url=None,
    )


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(_identity(tmp_path / "repo"), storage_root=tmp_path / "store")
    s.ensure_layout()
    return s


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- X1: a corrupt manifest in one bucket must not break identity reconcile ---


def test_reconcile_skips_corrupt_bucket_manifest(store: MemoryStore, tmp_path: Path) -> None:
    store.write_project_manifest()  # our own valid bucket
    bad = store.storage_root / "projects" / "otherbucket00000"
    _write(bad / "manifest.json", "{ this is not valid json")
    # Must not raise — the corrupt sibling bucket is skipped with a warning.
    result = reconcile_project_identity(_identity(tmp_path / "repo"), store.storage_root)
    assert result.project_id


def test_detect_orphaned_skips_corrupt_bucket(store: MemoryStore) -> None:
    bad = store.storage_root / "projects" / "otherbucket00000"
    _write(bad / "manifest.json", "not json at all {{{")
    assert detect_orphaned_buckets(store.storage_root) == []  # no crash, skipped


# --- X5 / M#2: valid-JSON-but-wrong-type manifest must not AttributeError ---


@pytest.mark.parametrize("payload", ["[1, 2, 3]", '"hello"', "42", "true"])
def test_version_helpers_survive_wrong_type_manifest(payload: str) -> None:
    from turbo_memory_mcp.migrations.runner import (
        _legacy_v1_or_format_version,
        _version_from,
    )

    data = json.loads(payload)
    assert isinstance(_version_from(data), int)
    assert isinstance(_legacy_v1_or_format_version(data), int)


def test_load_usage_stats_survives_wrong_type(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from turbo_memory_mcp import telemetry

    monkeypatch.setattr(store, "read_usage_stats", lambda: [1, 2, 3])
    payload = telemetry.load_usage_stats(store)
    assert isinstance(payload, dict)
    assert "totals" in payload


@pytest.mark.parametrize("bad_version", ["oops", [], {"x": 1}, None])
def test_load_usage_stats_survives_bad_format_version(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch, bad_version: object
) -> None:
    from turbo_memory_mcp import telemetry

    monkeypatch.setattr(
        store,
        "read_usage_stats",
        lambda: {"format_version": bad_version, "totals": {}, "projects": {}},
    )
    payload = telemetry.load_usage_stats(store)  # must not raise on int() of a bad value
    assert isinstance(payload, dict)


# --- X4: corrupt relations.json — read tolerant, write strict (no clobber) ---


def test_read_relations_tolerant_on_corrupt(store: MemoryStore) -> None:
    _write(store.project_relations_path(), "{ broken json")
    assert store.read_relations() == []  # no crash, empty


def test_add_relation_refuses_to_clobber_corrupt(store: MemoryStore) -> None:
    _write(store.project_relations_path(), "{ broken json")
    with pytest.raises(ValueError):
        store.add_relation("note://a", "file://b.py", "fixes")
    # The corrupt file was not overwritten with an empty/partial set.
    assert store.project_relations_path().read_text(encoding="utf-8") == "{ broken json"
