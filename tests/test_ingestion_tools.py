from __future__ import annotations

from pathlib import Path

from turbo_memory_mcp.server import build_runtime_context, index_paths_impl


def _test_env(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    env = {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "project-alpha",
        "TQMEMORY_PROJECT_NAME": "Alpha Project",
    }
    return project_root, env


def test_index_paths_registers_roots_and_writes_block_records(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    docs_a = project_root / "docs-a"
    docs_b = project_root / "docs-b"
    docs_a.mkdir()
    docs_b.mkdir()
    (docs_a / "architecture.md").write_text("# Architecture\n\nA section.", encoding="utf-8")
    (docs_b / "overview.md").write_text("# Overview\n\nB section.", encoding="utf-8")

    payload = index_paths_impl(paths=[str(docs_a), str(docs_b)], mode="full", cwd=project_root, environ=env)
    _, store = build_runtime_context(cwd=project_root, environ=env)

    assert payload["status"] == "ok"
    assert payload["mode"] == "full"
    assert len(payload["registered_roots"]) == 2
    assert payload["indexed_files"] == 2
    assert payload["changed_files"] == 2
    assert payload["deleted_files"] == 0
    assert payload["block_count"] >= 2
    assert len(store.list_markdown_roots()) == 2
    assert len(store.list_markdown_blocks()) >= 2


def test_index_paths_incremental_rerun_skips_unchanged_and_cleans_deleted_files(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    docs = project_root / "docs"
    architecture_dir = docs / "architecture"
    architecture_dir.mkdir(parents=True)
    adr_file = architecture_dir / "adr-001.md"
    overview_file = docs / "overview.md"
    adr_file.write_text("# Architecture\n\nOld text.", encoding="utf-8")
    overview_file.write_text("# Overview\n\nKeep me until delete.", encoding="utf-8")

    full_payload = index_paths_impl(paths=[str(docs)], mode="full", cwd=project_root, environ=env)
    skipped_payload = index_paths_impl(mode="incremental", cwd=project_root, environ=env)

    adr_file.write_text("# Architecture\n\nNew text after edit.", encoding="utf-8")
    overview_file.unlink()
    changed_payload = index_paths_impl(mode="incremental", cwd=project_root, environ=env)
    _, store = build_runtime_context(cwd=project_root, environ=env)

    assert full_payload["changed_files"] == 2
    assert full_payload["indexed_files"] == 2

    assert skipped_payload["mode"] == "incremental"
    assert len(skipped_payload["registered_roots"]) == 1
    assert skipped_payload["indexed_files"] == 2
    assert skipped_payload["changed_files"] == 0
    assert skipped_payload["skipped_files"] == 2
    assert skipped_payload["deleted_files"] == 0

    assert changed_payload["mode"] == "incremental"
    assert changed_payload["indexed_files"] == 1
    assert changed_payload["changed_files"] == 1
    assert changed_payload["deleted_files"] == 1
    assert changed_payload["block_count"] >= 1

    assert [manifest["source_path"] for manifest in store.list_markdown_file_manifests()] == ["architecture/adr-001.md"]
    assert {block["source_path"] for block in store.list_markdown_blocks()} == {"architecture/adr-001.md"}


def test_index_paths_skips_default_ignored_subdirectories_and_cleans_existing_noise(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    visible_doc = project_root / "README.md"
    ignored_doc = project_root / ".planning" / "old-plan.md"
    ignored_doc.parent.mkdir(parents=True)
    visible_doc.write_text("# README\n\nVisible doc.", encoding="utf-8")
    ignored_doc.write_text("# Old Plan\n\nShould not stay in active retrieval.", encoding="utf-8")

    payload = index_paths_impl(paths=[str(project_root)], mode="full", cwd=project_root, environ=env)
    _, store = build_runtime_context(cwd=project_root, environ=env)

    assert payload["indexed_files"] == 1
    assert payload["changed_files"] == 1
    assert [manifest["source_path"] for manifest in store.list_markdown_file_manifests()] == ["README.md"]
    assert {block["source_path"] for block in store.list_markdown_blocks()} == {"README.md"}

    root_id = store.list_markdown_roots()[0]["root_id"]
    store.write_markdown_file_manifest(
        {
            "root_id": root_id,
            "source_path": ".planning/old-plan.md",
            "file_key": "stale-plan",
            "size": 10,
            "mtime_ns": 1,
            "source_checksum": "stale-checksum",
            "block_ids": ["stale-block"],
            "indexed_at": "2026-03-26T00:00:00Z",
        }
    )
    store.write_markdown_block(
        {
            "block_id": "stale-block",
            "root_id": root_id,
            "source_path": ".planning/old-plan.md",
            "heading_path": ["Old Plan"],
            "chunk_index": 0,
            "content_raw": "stale planning block",
            "block_checksum": "stale-block-checksum",
            "source_checksum": "stale-checksum",
            "updated_at": "2026-03-26T00:00:00Z",
        }
    )

    cleanup_payload = index_paths_impl(mode="incremental", cwd=project_root, environ=env)

    assert cleanup_payload["deleted_files"] == 1
    assert [manifest["source_path"] for manifest in store.list_markdown_file_manifests()] == ["README.md"]
    assert {block["source_path"] for block in store.list_markdown_blocks()} == {"README.md"}


def test_index_paths_full_with_explicit_paths_prunes_removed_roots(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    docs_a = project_root / "docs-a"
    docs_b = project_root / "docs-b"
    docs_a.mkdir()
    docs_b.mkdir()
    (docs_a / "a.md").write_text("# A\n\nAlpha text.", encoding="utf-8")
    (docs_b / "b.md").write_text("# B\n\nBeta text.", encoding="utf-8")

    first_payload = index_paths_impl(paths=[str(docs_a), str(docs_b)], mode="full", cwd=project_root, environ=env)
    second_payload = index_paths_impl(paths=[str(docs_b)], mode="full", cwd=project_root, environ=env)
    _, store = build_runtime_context(cwd=project_root, environ=env)

    assert len(first_payload["registered_roots"]) == 2
    assert len(second_payload["registered_roots"]) == 1
    assert second_payload["registered_roots"][0]["path"] == str(docs_b.resolve())
    assert second_payload["deleted_files"] == 1
    assert [root["path"] for root in store.list_markdown_roots()] == [str(docs_b.resolve())]
    assert [manifest["source_path"] for manifest in store.list_markdown_file_manifests()] == ["b.md"]
    assert {block["source_path"] for block in store.list_markdown_blocks()} == {"b.md"}
