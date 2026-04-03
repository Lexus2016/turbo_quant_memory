from __future__ import annotations

import json
from pathlib import Path

import pytest

from turbo_memory_mcp.server import (
    deprecate_note_impl,
    hydrate_impl,
    index_paths_impl,
    promote_note_impl,
    remember_note_impl,
    semantic_search_impl,
    server_info_impl,
)
from turbo_memory_mcp.store import RETRIEVAL_FORMAT_VERSION
from unittest.mock import patch


class KeywordEmbedder:
    KEYWORDS = ("auth", "refresh", "login", "global", "project", "install", "package", "runtime")

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vector = [0.0] * 384
            for index, keyword in enumerate(self.KEYWORDS):
                vector[index] = 1.0 if keyword in lowered else 0.0
            vectors.append(vector)
        return vectors


@pytest.fixture(autouse=True)
def _fake_embedder() -> None:
    with patch("turbo_memory_mcp.retrieval_index.build_default_embedder", return_value=KeywordEmbedder()):
        yield


def _test_env(tmp_path: Path) -> dict[str, str]:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    return {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "project-alpha",
        "TQMEMORY_PROJECT_NAME": "Alpha Project",
    }


def test_remember_note_defaults_to_project_scope(tmp_path: Path) -> None:
    env = _test_env(tmp_path)

    payload = remember_note_impl(
        "Auth Context",
        "JWT refresh logic stays project-local until promoted.",
        kind="decision",
        tags=["auth", "jwt"],
        environ=env,
    )

    assert payload["status"] == "ok"
    assert payload["action"] == "stored"
    assert payload["item"]["scope"] == "project"
    assert payload["item"]["project_id"] == "project-alpha"
    assert payload["item"]["project_name"] == "Alpha Project"
    assert payload["item"]["note_kind"] == "decision"
    assert Path(payload["item"]["source_path"]).exists()


def test_remember_note_rejects_direct_global_writes(tmp_path: Path) -> None:
    env = _test_env(tmp_path)

    with pytest.raises(ValueError, match="Direct global writes are disabled"):
        remember_note_impl("Global Note", "Should fail.", kind="pattern", scope="global", environ=env)


def test_remember_note_rejects_unknown_note_kind(tmp_path: Path) -> None:
    env = _test_env(tmp_path)

    with pytest.raises(ValueError, match="remember_note requires kind"):
        remember_note_impl("Bad Kind", "Should fail.", kind="memo", environ=env)


def test_promote_note_preserves_promoted_from(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Reusable Pattern",
        "Promote only explicit reusable patterns.",
        kind="pattern",
        tags=["pattern"],
        environ=env,
    )

    promoted = promote_note_impl(stored["item"]["item_id"], environ=env)

    assert promoted["status"] == "ok"
    assert promoted["action"] == "promoted"
    assert promoted["item"]["scope"] == "global"
    assert promoted["item"]["note_kind"] == "pattern"
    assert promoted["item"]["promoted_from"]["scope"] == "project"
    assert promoted["item"]["promoted_from"]["note_id"] == stored["item"]["item_id"]


def test_semantic_search_project_scope_returns_only_project_notes(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Auth Flow",
        "Project auth flow uses JWT refresh rotation.",
        kind="lesson",
        tags=["auth"],
        environ=env,
    )
    promote_note_impl(stored["item"]["item_id"], environ=env)

    payload = semantic_search_impl("auth refresh", scope="project", environ=env)

    assert payload["status"] == "ok"
    assert payload["scope"] == "project"
    assert payload["result_count"] == 1
    assert [item["scope"] for item in payload["items"]] == ["project"]
    assert payload["items"][0]["source_kind"] == "memory_note"
    assert payload["items"][0]["note_kind"] == "lesson"


def test_semantic_search_global_scope_returns_only_global_notes(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Auth Flow",
        "Project auth flow uses JWT refresh rotation.",
        kind="lesson",
        tags=["auth"],
        environ=env,
    )
    promote_note_impl(stored["item"]["item_id"], environ=env)

    payload = semantic_search_impl("auth refresh", scope="global", environ=env)

    assert payload["status"] == "ok"
    assert payload["scope"] == "global"
    assert payload["result_count"] == 1
    assert [item["scope"] for item in payload["items"]] == ["global"]
    assert payload["items"][0]["promoted_from"]["scope"] == "project"


def test_semantic_search_hybrid_prefers_project_hits_when_relevance_is_close(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Auth Flow",
        "JWT refresh login flow for the current repository.",
        kind="handoff",
        tags=["auth", "login"],
        environ=env,
    )
    promote_note_impl(stored["item"]["item_id"], environ=env)

    payload = semantic_search_impl("auth refresh login", scope="hybrid", limit=5, environ=env)

    assert payload["status"] == "ok"
    assert payload["scope"] == "hybrid"
    assert payload["result_count"] == 2
    assert [item["scope"] for item in payload["items"][:2]] == ["project", "global"]
    assert payload["items"][1]["promoted_from"]["scope"] == "project"


def test_hydrate_impl_returns_full_note_payload(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Release Handoff",
        "Carry auth refresh caveats into production verification.",
        kind="handoff",
        tags=["deploy"],
        environ=env,
    )

    payload = hydrate_impl(stored["item"]["item_id"], scope="project", mode="related", environ=env)

    assert payload["status"] == "ok"
    assert payload["source_kind"] == "memory_note"
    assert payload["item"]["note_kind"] == "handoff"
    assert payload["item"]["note_status"] == "active"
    assert payload["item"]["content"] == "Carry auth refresh caveats into production verification."
    assert payload["neighbors_before"] == []
    assert payload["neighbors_after"] == []


def test_deprecate_note_impl_supersedes_old_note_and_hides_it_from_search(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    old_note = remember_note_impl(
        "Install Flow",
        "Use uv run turbo-memory-mcp serve.",
        kind="lesson",
        tags=["install"],
        environ=env,
    )
    replacement = remember_note_impl(
        "Install Flow",
        "Use turbo-memory-mcp serve after installing the package.",
        kind="lesson",
        tags=["install"],
        environ=env,
    )

    payload = deprecate_note_impl(
        old_note["item"]["item_id"],
        scope="project",
        replacement_note_id=replacement["item"]["item_id"],
        reason="Packaged install contract replaced the dev runtime.",
        environ=env,
    )
    search = semantic_search_impl("install package runtime", scope="project", limit=5, environ=env)

    assert payload["status"] == "ok"
    assert payload["action"] == "superseded"
    assert payload["item"]["note_status"] == "superseded"
    assert payload["item"]["superseded_by"]["note_id"] == replacement["item"]["item_id"]
    assert [item["item_id"] for item in search["items"]] == [replacement["item"]["item_id"]]


def test_hydrate_impl_refreshes_stale_markdown_before_loading(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    project_root = Path(env["TQMEMORY_PROJECT_ROOT"])
    docs = project_root / "docs"
    docs.mkdir()
    auth_doc = docs / "auth.md"
    auth_doc.write_text("# Auth\n\nAlpha block.", encoding="utf-8")

    index_paths_impl(paths=[str(docs)], mode="full", cwd=project_root, environ=env)
    auth_doc.write_text("# Auth\n\nBeta block.", encoding="utf-8")

    search = semantic_search_impl("beta block", scope="project", environ=env)
    payload = hydrate_impl(search["items"][0]["item_id"], scope="project", mode="default", environ=env)

    assert payload["status"] == "ok"
    assert payload["source_kind"] == "markdown"
    assert "Beta block." in payload["item"]["content"]


def test_explicit_hybrid_search_can_include_highly_relevant_global_note(tmp_path: Path) -> None:
    env_a = _test_env(tmp_path)
    env_b = dict(env_a)
    env_b["TQMEMORY_PROJECT_ID"] = "project-beta"
    env_b["TQMEMORY_PROJECT_NAME"] = "Beta Project"

    remember_note_impl(
        "Local Note",
        "Project-only auth note.",
        kind="lesson",
        tags=["auth"],
        environ=env_a,
    )
    promoted = remember_note_impl(
        "Reusable Best Practice",
        "Critical zebra strategy best practice from global memory.",
        kind="pattern",
        tags=["best-practice", "zebra"],
        environ=env_b,
    )
    promote_note_impl(promoted["item"]["item_id"], environ=env_b)

    payload = semantic_search_impl("zebra strategy best practice", scope="hybrid", limit=5, environ=env_a)

    assert payload["scope"] == "hybrid"
    assert any(item["scope"] == "global" for item in payload["items"])
    assert any(item["title"] == "Reusable Best Practice" for item in payload["items"])


def test_server_info_reports_usage_stats_after_search_and_hydrate(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Auth Flow",
        "Project auth flow uses JWT refresh rotation.",
        kind="lesson",
        tags=["auth"],
        environ=env,
    )

    semantic_search_impl("auth refresh", scope="project", environ=env)
    hydrate_impl(stored["item"]["item_id"], scope="project", mode="related", environ=env)
    payload = server_info_impl(environ=env)

    assert payload["usage_stats"]["totals"]["search_calls"] == 1
    assert payload["usage_stats"]["totals"]["hydrate_calls"] == 1
    assert payload["usage_stats"]["current_project"]["project_id"] == "project-alpha"
    assert payload["usage_stats"]["headline"]


def test_semantic_search_rebuilds_project_retrieval_after_manifest_mismatch(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    remember_note_impl(
        "Auth Flow",
        "Project auth flow uses JWT refresh rotation.",
        kind="lesson",
        tags=["auth"],
        environ=env,
    )

    manifest_path = Path(env["TQMEMORY_HOME"]) / "projects" / "project-alpha" / "retrieval" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format_version"] = 0
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload = semantic_search_impl("auth refresh", scope="project", environ=env)
    repaired_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["result_count"] == 1
    assert repaired_manifest["format_version"] == RETRIEVAL_FORMAT_VERSION
