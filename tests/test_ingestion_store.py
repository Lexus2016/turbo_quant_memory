from __future__ import annotations

import json
from pathlib import Path

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.store import MARKDOWN_SOURCE_KIND, PROJECT_SCOPE, MemoryStore, sha256_text


def _project_identity(project_root: Path) -> ProjectIdentity:
    return ProjectIdentity(
        project_id="proj1234567890abc",
        project_name="Turbo Quant Memory",
        project_root=project_root,
        identity_source="github.com/example/turbo-quant-memory",
        identity_kind="git_remote",
        remote_url="git@github.com:example/turbo-quant-memory.git",
    )


def _build_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(_project_identity(tmp_path / "repo"), storage_root=tmp_path / "central-store")


def test_markdown_layout_and_roots_persist_under_project_storage(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    docs_root = (tmp_path / "repo" / "docs").resolve()
    root_record = store.write_markdown_root(
        {
            "root_id": "docs-root",
            "path": str(docs_root),
            "path_hash": "path-hash-001",
            "registered_at": "2026-03-26T10:00:00Z",
            "updated_at": "2026-03-26T10:00:00Z",
        }
    )

    manifest_path = store.project_markdown_manifest_path()
    root_path = store.project_markdown_root_path("docs-root")
    root_payload = json.loads(root_path.read_text(encoding="utf-8"))

    assert manifest_path == store.storage_root / "projects" / "proj1234567890abc" / "markdown" / "manifest.json"
    assert root_path == store.storage_root / "projects" / "proj1234567890abc" / "markdown" / "roots" / "docs-root.json"
    assert root_record["scope"] == PROJECT_SCOPE
    assert root_payload["project_id"] == "proj1234567890abc"
    assert root_payload["path"] == str(docs_root)
    assert root_payload["path_hash"] == "path-hash-001"
    assert [root["root_id"] for root in store.list_markdown_roots()] == ["docs-root"]


def test_markdown_manifests_and_blocks_persist_expected_payload_fields(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    store.write_markdown_root(
        {
            "root_id": "docs-root",
            "path": str((tmp_path / "repo" / "docs").resolve()),
            "path_hash": "path-hash-001",
        }
    )

    manifest = store.write_markdown_file_manifest(
        {
            "root_id": "docs-root",
            "source_path": "architecture/adr-001.md",
            "file_key": "docs-architecture-adr-001",
            "size": 128,
            "mtime_ns": 123456789,
            "source_checksum": "source-checksum-001",
            "block_ids": ["block-a", "block-b"],
            "indexed_at": "2026-03-26T10:05:00Z",
        }
    )
    block = store.write_markdown_block(
        {
            "block_id": "block-a",
            "root_id": "docs-root",
            "source_path": "architecture/adr-001.md",
            "heading_path": ["Architecture", "Storage"],
            "chunk_index": 0,
            "content_raw": "# Architecture\n\nStorage ADR",
            "block_checksum": sha256_text("# Architecture\n\nStorage ADR"),
            "source_checksum": "source-checksum-001",
            "updated_at": "2026-03-26T10:05:00Z",
        }
    )

    manifest_payload = json.loads(
        store.project_markdown_file_path("docs-architecture-adr-001").read_text(encoding="utf-8")
    )
    block_payload = json.loads(store.project_markdown_block_path("block-a").read_text(encoding="utf-8"))

    assert manifest["file_key"] == "docs-architecture-adr-001"
    assert manifest_payload["root_id"] == "docs-root"
    assert manifest_payload["source_path"] == "architecture/adr-001.md"
    assert manifest_payload["source_checksum"] == "source-checksum-001"
    assert manifest_payload["block_ids"] == ["block-a", "block-b"]
    assert block["source_kind"] == MARKDOWN_SOURCE_KIND
    assert block_payload["block_id"] == "block-a"
    assert block_payload["heading_path"] == ["Architecture", "Storage"]
    assert block_payload["source_checksum"] == "source-checksum-001"
    assert [item["file_key"] for item in store.list_markdown_file_manifests(root_id="docs-root")] == [
        "docs-architecture-adr-001"
    ]
    assert [item["block_id"] for item in store.list_markdown_blocks(root_id="docs-root")] == ["block-a"]


def test_delete_file_manifest_and_targeted_block_cleanup_preserves_unaffected_records(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    store.write_markdown_root(
        {
            "root_id": "docs-root",
            "path": str((tmp_path / "repo" / "docs").resolve()),
            "path_hash": "path-hash-001",
        }
    )

    architecture_key = "docs-architecture-adr-001"
    overview_key = "docs-overview-index"

    store.write_markdown_file_manifest(
        {
            "root_id": "docs-root",
            "source_path": "architecture/adr-001.md",
            "file_key": architecture_key,
            "size": 128,
            "mtime_ns": 123456789,
            "source_checksum": "source-checksum-001",
            "block_ids": ["arch-old", "arch-shared"],
        }
    )
    store.write_markdown_file_manifest(
        {
            "root_id": "docs-root",
            "source_path": "overview/index.md",
            "file_key": overview_key,
            "size": 64,
            "mtime_ns": 987654321,
            "source_checksum": "source-checksum-002",
            "block_ids": ["overview-block"],
        }
    )
    store.write_markdown_block(
        {
            "block_id": "arch-old",
            "root_id": "docs-root",
            "source_path": "architecture/adr-001.md",
            "heading_path": ["Architecture"],
            "chunk_index": 0,
            "content_raw": "old architecture block",
            "block_checksum": sha256_text("old architecture block"),
            "source_checksum": "source-checksum-001",
        }
    )
    store.write_markdown_block(
        {
            "block_id": "arch-shared",
            "root_id": "docs-root",
            "source_path": "architecture/adr-001.md",
            "heading_path": ["Architecture"],
            "chunk_index": 1,
            "content_raw": "unaffected architecture block",
            "block_checksum": sha256_text("unaffected architecture block"),
            "source_checksum": "source-checksum-001",
        }
    )
    store.write_markdown_block(
        {
            "block_id": "overview-block",
            "root_id": "docs-root",
            "source_path": "overview/index.md",
            "heading_path": ["Overview"],
            "chunk_index": 0,
            "content_raw": "unaffected overview block",
            "block_checksum": sha256_text("unaffected overview block"),
            "source_checksum": "source-checksum-002",
        }
    )

    replaced_block_ids = store.replace_blocks_for_file(
        "docs-root",
        "architecture/adr-001.md",
        [
            {
                "block_id": "arch-new",
                "root_id": "docs-root",
                "source_path": "architecture/adr-001.md",
                "heading_path": ["Architecture"],
                "chunk_index": 0,
                "content_raw": "rewritten architecture block",
                "block_checksum": sha256_text("rewritten architecture block"),
                "source_checksum": "source-checksum-003",
            }
        ],
    )
    store.write_markdown_file_manifest(
        {
            "root_id": "docs-root",
            "source_path": "architecture/adr-001.md",
            "file_key": architecture_key,
            "size": 144,
            "mtime_ns": 2233445566,
            "source_checksum": "source-checksum-003",
            "block_ids": replaced_block_ids,
        }
    )

    assert replaced_block_ids == ["arch-new"]
    assert not store.project_markdown_block_path("arch-old").exists()
    assert not store.project_markdown_block_path("arch-shared").exists()
    assert store.project_markdown_block_path("arch-new").exists()
    assert store.project_markdown_block_path("overview-block").exists()

    deleted_block_ids = store.delete_blocks_for_file("docs-root", "overview/index.md")
    delete_file_manifest = store.delete_markdown_file_manifest
    delete_file_manifest(overview_key)

    assert deleted_block_ids == ["overview-block"]
    assert not store.project_markdown_block_path("overview-block").exists()
    assert not store.project_markdown_file_path(overview_key).exists()
    assert store.project_markdown_block_path("arch-new").exists()
    assert [block["block_id"] for block in store.list_markdown_blocks(root_id="docs-root")] == ["arch-new"]


def _write_block(store: MemoryStore, block_id: str, source_path: str = "docs/a.md") -> None:
    store.write_markdown_block(
        {
            "block_id": block_id,
            "root_id": "docs-root",
            "source_path": source_path,
            "heading_path": ["A"],
            "chunk_index": 0,
            "content_raw": f"content of {block_id}",
            "block_checksum": sha256_text(block_id),
            "source_checksum": "source-checksum",
        }
    )


def test_list_markdown_block_ids_matches_stored_blocks(tmp_path: Path) -> None:
    """Block ids come from file names, so listing them reads no block file."""
    store = _build_store(tmp_path)
    assert store.has_markdown_blocks() is False
    assert store.list_markdown_block_ids() == []

    for block_id in ("block-c", "block-a", "block-b"):
        _write_block(store, block_id)
    blocks_dir = store.project_markdown_blocks_dir()
    # Leftovers that are not block records: an atomic-write temp file and a stray.
    (blocks_dir / "tmpabc123").write_text("{", encoding="utf-8")
    (blocks_dir / "notes.txt").write_text("x", encoding="utf-8")

    assert store.has_markdown_blocks() is True
    assert store.list_markdown_block_ids() == ["block-a", "block-b", "block-c"]
    assert store.list_markdown_block_ids() == sorted(block["block_id"] for block in store.list_markdown_blocks())


def test_iter_markdown_blocks_yields_the_listed_blocks_and_skips_corrupt(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    for block_id in ("block-a", "block-b"):
        _write_block(store, block_id)
    (store.project_markdown_blocks_dir() / "block-bad.json").write_text("{", encoding="utf-8")

    iterator = store.iter_markdown_blocks()

    assert iter(iterator) is iterator  # a generator, not a materialized list
    assert [block["block_id"] for block in iterator] == ["block-a", "block-b"]
    assert [block["block_id"] for block in store.list_markdown_blocks()] == ["block-a", "block-b"]
