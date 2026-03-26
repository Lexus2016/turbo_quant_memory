from __future__ import annotations

from pathlib import Path

from turbo_memory_mcp.hydration import hydrate
from turbo_memory_mcp.server import build_runtime_context
from turbo_memory_mcp.store import sha256_text


def _test_env(tmp_path: Path) -> dict[str, str]:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    return {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "project-alpha",
        "TQMEMORY_PROJECT_NAME": "Alpha Project",
    }


def _seed_markdown_file(tmp_path: Path, env: dict[str, str]) -> None:
    _, store = build_runtime_context(cwd=tmp_path / "repo", environ=env)
    store.write_markdown_root(
        {
            "root_id": "docs-root",
            "path": str((tmp_path / "repo" / "docs").resolve()),
            "path_hash": "docs-root-hash",
        }
    )
    for chunk_index, block_id in enumerate(("mdblk-auth-0", "mdblk-auth-1", "mdblk-auth-2", "mdblk-auth-3")):
        content = f"Hydration auth block {chunk_index} with refresh rotation context."
        store.write_markdown_block(
            {
                "block_id": block_id,
                "root_id": "docs-root",
                "source_path": "docs/auth.md",
                "heading_path": ["Architecture", f"Chunk {chunk_index}"],
                "chunk_index": chunk_index,
                "content_raw": content,
                "block_checksum": sha256_text(content),
                "source_checksum": "source-checksum-auth",
            }
        )


def test_markdown_hydration_default_mode_returns_bounded_neighbors(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    _seed_markdown_file(tmp_path, env)
    _, store = build_runtime_context(cwd=tmp_path / "repo", environ=env)

    payload = hydrate(store, "mdblk-auth-2", scope="project", mode="default")

    assert payload["status"] == "ok"
    assert payload["mode"] == "default"
    assert payload["source_kind"] == "markdown"
    assert payload["item"]["block_id"] == "mdblk-auth-2"
    assert payload["item"]["content"].startswith("Hydration auth block 2")
    assert [item["block_id"] for item in payload["neighbors_before"]] == ["mdblk-auth-1"]
    assert [item["block_id"] for item in payload["neighbors_after"]] == ["mdblk-auth-3"]
    assert payload["neighbor_window"] == {"before": 1, "after": 1}


def test_markdown_hydration_related_mode_returns_wider_window(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    _seed_markdown_file(tmp_path, env)
    _, store = build_runtime_context(cwd=tmp_path / "repo", environ=env)

    payload = hydrate(store, "mdblk-auth-1", scope="project", mode="related")

    assert payload["status"] == "ok"
    assert payload["mode"] == "related"
    assert [item["block_id"] for item in payload["neighbors_before"]] == ["mdblk-auth-0"]
    assert [item["block_id"] for item in payload["neighbors_after"]] == ["mdblk-auth-2", "mdblk-auth-3"]
    assert payload["neighbor_window"] == {"before": 2, "after": 2}


def test_note_hydration_returns_full_note_without_artificial_neighbors(tmp_path: Path) -> None:
    env = _test_env(tmp_path)
    _, store = build_runtime_context(cwd=tmp_path / "repo", environ=env)
    note = store.write_project_note(
        "Deployment Handoff",
        "Validate auth refresh rotation before production rollout.",
        note_kind="handoff",
        tags=["deploy", "auth"],
        source_refs=["README.md"],
        note_id="handoff-note",
    )

    payload = hydrate(store, note["note_id"], scope="project", mode="related")

    assert payload["status"] == "ok"
    assert payload["source_kind"] == "memory_note"
    assert payload["item"]["item_id"] == "handoff-note"
    assert payload["item"]["note_kind"] == "handoff"
    assert payload["item"]["content"] == "Validate auth refresh rotation before production rollout."
    assert payload["neighbors_before"] == []
    assert payload["neighbors_after"] == []
    assert payload["neighbor_window"] == {"before": 0, "after": 0}
