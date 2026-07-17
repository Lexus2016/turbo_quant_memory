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


def test_lint_reports_near_duplicate_notes(tmp_path: Path) -> None:
    from unittest.mock import patch

    from turbo_memory_mcp.server import remember_note_impl, semantic_search_impl

    class TwinEmbedder:
        """Notes sharing the keyword 'rotation' get identical vectors."""

        def encode(self, texts: list[str]) -> list[list[float]]:
            vectors = []
            for text in texts:
                lowered = text.lower()
                vector = [0.0] * 8
                vector[0] = 1.0 if "rotation" in lowered else 0.0
                vector[1] = 1.0 if "kubernetes" in lowered else 0.0
                vectors.append(vector)
            return vectors

    project_root, env = _test_env(tmp_path)
    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder",
        return_value=TwinEmbedder(),
    ):
        remember_note_impl("Token rotation (EN)", "Rotate tokens on login.", kind="lesson", environ=env)
        remember_note_impl("Ротація токенів (UK)", "Ротуємо rotation токени.", kind="lesson", environ=env)
        remember_note_impl("Kubernetes deploy", "Deploy to kubernetes.", kind="lesson", environ=env)
        # Force the retrieval index sync so lint sees materialized vectors.
        semantic_search_impl("rotation", scope="project", environ=env)

        payload = lint_knowledge_base_impl(max_issues=50, cwd=project_root, environ=env)

    assert payload["status"] == "ok"
    assert payload["summary"]["near_duplicate_note_count"] == 1
    dup_issues = [i for i in payload["issues"] if i["kind"] == "near_duplicate_notes"]
    assert len(dup_issues) == 1
    assert dup_issues[0]["similarity"] >= 0.99
    assert sorted(dup_issues[0]["titles"]) == ["Token rotation (EN)", "Ротація токенів (UK)"]


def test_near_duplicate_scan_survives_broken_embedder_and_skips_empty_probes(tmp_path: Path) -> None:
    from unittest.mock import patch

    from turbo_memory_mcp.knowledge_lint import _scan_near_duplicate_notes
    from turbo_memory_mcp.server import build_runtime_context, remember_note_impl

    class BrokenEmbedder:
        def encode(self, texts: list[str]) -> list[list[float]]:
            return [[float("nan")] * 4 for _ in texts]

    project_root, env = _test_env(tmp_path)
    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder",
        return_value=BrokenEmbedder(),
    ):
        remember_note_impl("Alpha rotation", "Rotate tokens on login.", kind="lesson", environ=env)
        remember_note_impl("Beta rotation", "Rotate tokens on logout.", kind="lesson", environ=env)
        _, store = build_runtime_context(cwd=project_root, environ=env)
        # NaN vectors must degrade to no findings, never raise.
        assert _scan_near_duplicate_notes(store) == []

    class ConstantEmbedder:
        def encode(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder",
        return_value=ConstantEmbedder(),
    ):
        remember_note_impl("x", ".", kind="lesson", environ=env)
        remember_note_impl("y", ".", kind="lesson", environ=env)
        _, store = build_runtime_context(cwd=project_root, environ=env)
        findings = _scan_near_duplicate_notes(store)
        # Empty-probe notes are excluded, so the degenerate identical probes
        # of "x"/"y" never report each other; the two real notes DO match
        # under this constant embedder — that is the expected signal here.
        flagged = {t for f in findings for t in f["titles"]}
        assert "x" not in flagged and "y" not in flagged


def test_near_duplicate_scan_cap_skips_entirely(tmp_path: Path) -> None:
    from unittest.mock import patch

    import turbo_memory_mcp.knowledge_lint as kl
    from turbo_memory_mcp.server import build_runtime_context, remember_note_impl

    class ConstantEmbedder:
        def encode(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

    project_root, env = _test_env(tmp_path)
    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder",
        return_value=ConstantEmbedder(),
    ):
        for i in range(3):
            remember_note_impl(f"Note number {i}", f"Body of note number {i}.", kind="lesson", environ=env)
        _, store = build_runtime_context(cwd=project_root, environ=env)
        with patch.object(kl, "_NEAR_DUPLICATE_SCAN_CAP", 2):
            assert kl._scan_near_duplicate_notes(store) == []
        with patch.object(kl, "_NEAR_DUPLICATE_SCAN_CAP", 3):
            assert kl._scan_near_duplicate_notes(store) != []
