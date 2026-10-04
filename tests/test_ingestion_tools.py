from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from turbo_memory_mcp.ingestion import assess_project_index_freshness
from turbo_memory_mcp.server import _refresh_project_markdown_if_stale, build_runtime_context, index_paths_impl


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


def test_index_paths_respects_tqmemoryignore_patterns(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)

    # Create structure with duplicate workspace templates
    visible_doc = project_root / "README.md"
    visible_doc.write_text("# README\n\nMain doc.", encoding="utf-8")
    config_doc = project_root / "config" / "prompts" / "system.md"
    config_doc.parent.mkdir(parents=True)
    config_doc.write_text("# System Prompt\n\nPrompt text.", encoding="utf-8")

    # Create duplicate workspace dirs (simulates the openclaw pattern)
    for name in ("workspace-alice", "workspace-bob", "workspace-charlie"):
        ws = project_root / "data" / name
        ws.mkdir(parents=True)
        (ws / "AGENTS.md").write_text("# AGENTS\n\nDuplicate template.", encoding="utf-8")
        (ws / "TOOLS.md").write_text("# TOOLS\n\nDuplicate tools.", encoding="utf-8")

    # Create .tqmemoryignore
    ignore_file = project_root / ".tqmemoryignore"
    ignore_file.write_text("# Skip duplicate workspaces\nworkspace-*\n", encoding="utf-8")

    payload = index_paths_impl(paths=[str(project_root)], mode="full", cwd=project_root, environ=env)
    _, store = build_runtime_context(cwd=project_root, environ=env)

    # Should only index README.md and config/prompts/system.md, NOT workspace-* files
    assert payload["indexed_files"] == 2
    assert payload["changed_files"] == 2
    source_paths = sorted(m["source_path"] for m in store.list_markdown_file_manifests())
    assert source_paths == ["README.md", "config/prompts/system.md"]


def test_tqmemoryignore_supports_path_glob_patterns(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)

    (project_root / "docs").mkdir()
    (project_root / "docs" / "guide.md").write_text("# Guide\n\nKeep.", encoding="utf-8")
    reports_dir = project_root / "data" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "daily.md").write_text("# Daily\n\nSkip.", encoding="utf-8")

    ignore_file = project_root / ".tqmemoryignore"
    ignore_file.write_text("data/reports/*.md\n", encoding="utf-8")

    payload = index_paths_impl(paths=[str(project_root)], mode="full", cwd=project_root, environ=env)
    _, store = build_runtime_context(cwd=project_root, environ=env)

    assert payload["indexed_files"] == 1
    source_paths = [m["source_path"] for m in store.list_markdown_file_manifests()]
    assert source_paths == ["docs/guide.md"]


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


# --- root validation before registration (2026-10-04) ---
# A root used to be persisted before index_paths checked that it is a
# directory. A file or missing path then stayed registered after the call
# failed, and every later argument-free reindex raised on it.


@pytest.mark.parametrize(
    ("bad_name", "expected_error"),
    [("README.md", NotADirectoryError), ("no-such-dir", FileNotFoundError)],
)
def test_index_paths_rejected_root_is_not_registered(
    tmp_path: Path, bad_name: str, expected_error: type[Exception]
) -> None:
    project_root, env = _test_env(tmp_path)
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nAlpha text.", encoding="utf-8")
    (project_root / "README.md").write_text("# Readme\n\nA file, not a root.", encoding="utf-8")
    index_paths_impl(paths=[str(docs)], mode="full", cwd=project_root, environ=env)

    with pytest.raises(expected_error):
        index_paths_impl(paths=[str(project_root / bad_name)], cwd=project_root, environ=env)
    _, store = build_runtime_context(cwd=project_root, environ=env)

    assert [root["path"] for root in store.list_markdown_roots()] == [str(docs.resolve())]
    assert index_paths_impl(mode="incremental", cwd=project_root, environ=env)["status"] == "ok"


def test_index_paths_registers_nothing_when_any_path_is_invalid(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nAlpha text.", encoding="utf-8")
    (project_root / "README.md").write_text("# Readme", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        index_paths_impl(
            paths=[str(docs), str(project_root / "README.md")], cwd=project_root, environ=env
        )
    _, store = build_runtime_context(cwd=project_root, environ=env)

    assert store.list_markdown_roots() == []


def test_reindex_skips_vanished_root_and_still_indexes_the_others(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    docs_a = project_root / "docs-a"
    docs_b = project_root / "docs-b"
    docs_a.mkdir()
    docs_b.mkdir()
    (docs_a / "a.md").write_text("# A\n\nAlpha text.", encoding="utf-8")
    (docs_b / "b.md").write_text("# B\n\nBeta text.", encoding="utf-8")
    index_paths_impl(paths=[str(docs_a), str(docs_b)], mode="full", cwd=project_root, environ=env)
    (docs_a / "a.md").write_text("# A\n\nAlpha text, edited after the first index.", encoding="utf-8")
    shutil.rmtree(docs_b)

    payload = index_paths_impl(mode="incremental", cwd=project_root, environ=env)
    _, store = build_runtime_context(cwd=project_root, environ=env)

    assert payload["status"] == "ok"
    assert payload["changed_files"] == 1
    assert [root["path"] for root in payload["missing_roots"]] == [str(docs_b.resolve())]
    contents = [block["content_raw"] for block in store.list_markdown_blocks()]
    assert any("edited after the first index" in content for content in contents)


# --- search-time freshness (2026-10-04) ---


def test_freshness_ignores_touched_but_unchanged_files(tmp_path: Path) -> None:
    """The checksum comparison used a variable left over from an earlier loop
    (the last manifest in the store) instead of the file's own manifest, so a
    file whose mtime moved but whose content did not read as changed."""
    project_root, env = _test_env(tmp_path)
    docs = project_root / "docs"
    docs.mkdir()
    files = [docs / f"{name}.md" for name in ("a", "b", "c")]
    for path in files:
        path.write_text(f"# {path.stem}\n\nBody of {path.stem}.", encoding="utf-8")
    index_paths_impl(paths=[str(docs)], mode="full", cwd=project_root, environ=env)
    for path in files:
        later = path.stat().st_mtime_ns + 5_000_000_000
        os.utime(path, ns=(later, later))
    _, store = build_runtime_context(cwd=project_root, environ=env)

    report = assess_project_index_freshness(store, cwd=project_root)

    assert report["changed_file_count"] == 0
    assert report["is_stale"] is False


def test_touched_files_are_refreshed_once_then_skipped(tmp_path: Path) -> None:
    """A touched file's manifest keeps the old mtime until a reindex rewrites
    it; without that one reindex the file is re-read and re-hashed before
    every search."""
    project_root, env = _test_env(tmp_path)
    docs = project_root / "docs"
    docs.mkdir()
    readme = docs / "a.md"
    readme.write_text("# a\\n\\nBody.", encoding="utf-8")
    index_paths_impl(paths=[str(docs)], mode="full", cwd=project_root, environ=env)
    later = readme.stat().st_mtime_ns + 5_000_000_000
    os.utime(readme, ns=(later, later))
    _, store = build_runtime_context(cwd=project_root, environ=env)

    before = assess_project_index_freshness(store, cwd=project_root)
    _refresh_project_markdown_if_stale(store)
    after = assess_project_index_freshness(store, cwd=project_root)

    assert before["touched_file_count"] == 1
    assert before["needs_reindex"] is True
    assert after["touched_file_count"] == 0
    assert after["needs_reindex"] is False


def test_freshness_still_counts_a_real_content_change(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    docs = project_root / "docs"
    docs.mkdir()
    for name in ("a", "b", "c"):
        (docs / f"{name}.md").write_text(f"# {name}\n\nBody of {name}.", encoding="utf-8")
    index_paths_impl(paths=[str(docs)], mode="full", cwd=project_root, environ=env)
    (docs / "a.md").write_text("# a\n\nA genuinely different body.", encoding="utf-8")
    _, store = build_runtime_context(cwd=project_root, environ=env)

    report = assess_project_index_freshness(store, cwd=project_root)

    assert report["changed_file_count"] == 1
    assert report["needs_reindex"] is True


def _index_two_roots_then_remove_one(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    project_root, env = _test_env(tmp_path)
    docs_a = project_root / "docs-a"
    docs_b = project_root / "docs-b"
    docs_a.mkdir()
    docs_b.mkdir()
    (docs_a / "a.md").write_text("# A\n\nAlpha text.", encoding="utf-8")
    (docs_b / "b.md").write_text("# B\n\nBeta text.", encoding="utf-8")
    index_paths_impl(paths=[str(docs_a), str(docs_b)], mode="full", cwd=project_root, environ=env)
    shutil.rmtree(docs_b)
    return project_root, env, docs_a


def test_vanished_root_alone_does_not_trigger_search_time_reindex(tmp_path: Path) -> None:
    """A reindex cannot bring a vanished root back, so it must not run before
    every search; health keeps reporting the root via is_stale."""
    project_root, env, _ = _index_two_roots_then_remove_one(tmp_path)
    _, store = build_runtime_context(cwd=project_root, environ=env)

    report = assess_project_index_freshness(store, cwd=project_root)
    with patch(
        "turbo_memory_mcp.server.index_paths_with_sync_plan",
        side_effect=AssertionError("search-time reindex triggered by a vanished root"),
    ) as reindex:
        _refresh_project_markdown_if_stale(store)

    assert report["missing_root_count"] == 1
    assert report["is_stale"] is True
    assert report["needs_reindex"] is False
    reindex.assert_not_called()


def test_vanished_root_does_not_block_reindex_of_changed_live_root(tmp_path: Path) -> None:
    project_root, env, docs_a = _index_two_roots_then_remove_one(tmp_path)
    (docs_a / "a.md").write_text("# A\n\nAlpha text, edited after the root vanished.", encoding="utf-8")
    _, store = build_runtime_context(cwd=project_root, environ=env)

    _refresh_project_markdown_if_stale(store)

    contents = [block["content_raw"] for block in store.list_markdown_blocks()]
    assert any("edited after the root vanished" in content for content in contents)
