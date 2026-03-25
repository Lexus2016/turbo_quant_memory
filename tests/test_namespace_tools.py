from __future__ import annotations

from pathlib import Path

import pytest

from turbo_memory_mcp.server import promote_note_impl, remember_note_impl, search_memory_impl


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
        tags=["auth", "jwt"],
        environ=env,
    )

    assert payload["status"] == "ok"
    assert payload["action"] == "stored"
    assert payload["item"]["scope"] == "project"
    assert payload["item"]["project_id"] == "project-alpha"
    assert payload["item"]["project_name"] == "Alpha Project"
    assert Path(payload["item"]["source_path"]).exists()


def test_remember_note_rejects_direct_global_writes(tmp_path: Path) -> None:
    env = _test_env(tmp_path)

    with pytest.raises(ValueError, match="Direct global writes are disabled"):
        remember_note_impl("Global Note", "Should fail.", scope="global", environ=env)


def test_promote_note_preserves_promoted_from(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Reusable Pattern",
        "Promote only explicit reusable patterns.",
        tags=["pattern"],
        environ=env,
    )

    promoted = promote_note_impl(stored["item"]["item_id"], environ=env)

    assert promoted["status"] == "ok"
    assert promoted["action"] == "promoted"
    assert promoted["item"]["scope"] == "global"
    assert promoted["item"]["promoted_from"]["scope"] == "project"
    assert promoted["item"]["promoted_from"]["note_id"] == stored["item"]["item_id"]


def test_search_memory_project_scope_returns_only_project_notes(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Auth Flow",
        "Project auth flow uses JWT refresh rotation.",
        tags=["auth"],
        environ=env,
    )
    promote_note_impl(stored["item"]["item_id"], environ=env)

    payload = search_memory_impl("auth refresh", scope="project", environ=env)

    assert payload["status"] == "ok"
    assert payload["scope"] == "project"
    assert payload["result_count"] == 1
    assert [item["scope"] for item in payload["items"]] == ["project"]


def test_search_memory_global_scope_returns_only_global_notes(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Auth Flow",
        "Project auth flow uses JWT refresh rotation.",
        tags=["auth"],
        environ=env,
    )
    promote_note_impl(stored["item"]["item_id"], environ=env)

    payload = search_memory_impl("auth refresh", scope="global", environ=env)

    assert payload["status"] == "ok"
    assert payload["scope"] == "global"
    assert payload["result_count"] == 1
    assert [item["scope"] for item in payload["items"]] == ["global"]


def test_search_memory_hybrid_prefers_project_hits_when_relevance_is_close(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    stored = remember_note_impl(
        "Auth Flow",
        "JWT refresh login flow for the current repository.",
        tags=["auth", "login"],
        environ=env,
    )
    promote_note_impl(stored["item"]["item_id"], environ=env)

    payload = search_memory_impl("auth refresh login", scope="hybrid", limit=5, environ=env)

    assert payload["status"] == "ok"
    assert payload["scope"] == "hybrid"
    assert payload["result_count"] == 2
    assert [item["scope"] for item in payload["items"][:2]] == ["project", "global"]
    assert payload["items"][1]["promoted_from"]["scope"] == "project"
