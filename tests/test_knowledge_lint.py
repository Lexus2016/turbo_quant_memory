from __future__ import annotations

from pathlib import Path

from turbo_memory_mcp.server import index_paths_impl, lint_knowledge_base_impl


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


def test_lint_detects_broken_links_orphans_and_duplicate_titles(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    docs = project_root / "docs"
    docs.mkdir()

    (docs / "index.md").write_text(
        "# Index\n\n"
        "- [Article A](article-a.md)\n"
        "- [Missing](missing.md)\n"
        "- [Duplicate A](dup-a.md)\n"
        "- [Duplicate B](dup-b.md)\n",
        encoding="utf-8",
    )
    (docs / "article-a.md").write_text("# Article A\n\nBacklink to [Index](index.md).", encoding="utf-8")
    (docs / "dup-a.md").write_text("# Shared Title\n\nA.", encoding="utf-8")
    (docs / "dup-b.md").write_text("# Shared Title\n\nB.", encoding="utf-8")
    (docs / "orphan.md").write_text("# Orphan\n\nNo links.", encoding="utf-8")

    payload = lint_knowledge_base_impl(paths=[str(docs)], max_issues=50, cwd=project_root, environ=env)

    assert payload["status"] == "ok"
    assert payload["summary"]["broken_link_count"] == 1
    assert payload["summary"]["duplicate_title_count"] == 1
    assert payload["summary"]["orphan_candidate_count"] == 1
    assert payload["truncated"] is False

    issues = payload["issues"]
    issue_kinds = {issue["kind"] for issue in issues}
    assert issue_kinds == {"broken_link", "duplicate_title", "orphan_candidate"}

    broken = next(issue for issue in issues if issue["kind"] == "broken_link")
    assert broken["source_path"] == "index.md"
    assert broken["target_path"] == "missing.md"


def test_lint_respects_max_issues_cap_and_sets_truncated_flag(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    docs = project_root / "docs"
    docs.mkdir()

    (docs / "index.md").write_text("# Index\n\nNo links.", encoding="utf-8")
    for idx in range(6):
        (docs / f"orphan-{idx}.md").write_text(f"# Orphan {idx}\n\nNo links.", encoding="utf-8")

    payload = lint_knowledge_base_impl(paths=[str(docs)], max_issues=2, cwd=project_root, environ=env)

    assert payload["status"] == "ok"
    assert payload["summary"]["orphan_candidate_count"] == 6
    assert payload["summary"]["issue_count"] == 6
    assert payload["truncated"] is True
    assert len(payload["issues"]) == 2


def test_lint_uses_registered_markdown_roots_when_paths_not_provided(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    docs = project_root / "docs"
    docs.mkdir()
    (docs / "index.md").write_text("# Index\n\n[Guide](guide.md)", encoding="utf-8")
    (docs / "guide.md").write_text("# Guide\n\nContent.", encoding="utf-8")

    index_paths_impl(paths=[str(docs)], mode="full", cwd=project_root, environ=env)
    payload = lint_knowledge_base_impl(cwd=project_root, environ=env)

    assert payload["status"] == "ok"
    assert payload["roots"][0]["path"] == str(docs)
    assert payload["summary"]["broken_link_count"] == 0
    assert payload["summary"]["duplicate_title_count"] == 0


def test_lint_resolves_obsidian_wikilinks_by_file_stem_lookup(tmp_path: Path) -> None:
    project_root, env = _test_env(tmp_path)
    docs = project_root / "docs"
    sub = docs / "notes"
    sub.mkdir(parents=True)

    (docs / "guide.md").write_text("# Guide\n\nCore guide content.", encoding="utf-8")
    (sub / "usage.md").write_text("# Usage\n\nSee [[Guide]] for details.", encoding="utf-8")

    payload = lint_knowledge_base_impl(paths=[str(docs)], cwd=project_root, environ=env)

    assert payload["status"] == "ok"
    assert payload["summary"]["broken_link_count"] == 0
