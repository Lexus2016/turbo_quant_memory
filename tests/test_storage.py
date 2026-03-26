from __future__ import annotations

import json
from pathlib import Path

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.store import GLOBAL_SCOPE, MemoryStore, PROJECT_SCOPE, resolve_storage_root


def _project_identity(project_root: Path) -> ProjectIdentity:
    return ProjectIdentity(
        project_id="proj1234567890abc",
        project_name="Turbo Quant Memory",
        project_root=project_root,
        identity_source="github.com/example/turbo-quant-memory",
        identity_kind="git_remote",
        remote_url="git@github.com:example/turbo-quant-memory.git",
    )


def test_storage_root_honours_override_and_exact_layout(tmp_path: Path) -> None:
    storage_root = resolve_storage_root({"TQMEMORY_HOME": str(tmp_path / "memory-home")})
    store = MemoryStore(_project_identity(tmp_path / "repo"), storage_root=storage_root)

    assert store.storage_root == (tmp_path / "memory-home").resolve()
    assert store.project_manifest_path() == store.storage_root / "projects" / "proj1234567890abc" / "manifest.json"
    assert store.project_note_path("note-1") == store.storage_root / "projects" / "proj1234567890abc" / "notes" / "note-1.json"
    assert store.global_manifest_path() == store.storage_root / "global" / "manifest.json"
    assert store.global_note_path("note-1") == store.storage_root / "global" / "notes" / "note-1.json"
    assert store.global_note_path("note-1").as_posix().endswith("global/notes/note-1.json")


def test_project_manifest_and_note_persist_with_project_metadata(tmp_path: Path) -> None:
    store = MemoryStore(_project_identity(tmp_path / "repo"), storage_root=tmp_path / "central-store")

    manifest = store.write_project_manifest()
    note = store.write_project_note(
        "Auth Summary",
        "Use project scope by default.",
        note_kind="decision",
        tags=["auth", "memory"],
        source_refs=["README.md"],
        note_id="note-auth",
    )

    manifest_payload = json.loads(store.project_manifest_path().read_text(encoding="utf-8"))
    note_payload = json.loads(store.project_note_path("note-auth").read_text(encoding="utf-8"))

    assert manifest["project_id"] == "proj1234567890abc"
    assert manifest_payload["project_name"] == "Turbo Quant Memory"
    assert note["scope"] == PROJECT_SCOPE
    assert note_payload["scope"] == PROJECT_SCOPE
    assert note_payload["project_id"] == "proj1234567890abc"
    assert note_payload["project_name"] == "Turbo Quant Memory"
    assert note_payload["source_kind"] == "memory_note"
    assert note_payload["note_kind"] == "decision"
    assert store.read_note("note-auth", PROJECT_SCOPE)["title"] == "Auth Summary"
    assert [note["note_id"] for note in store.list_notes(PROJECT_SCOPE)] == ["note-auth"]


def test_promotion_preserves_provenance_and_global_partition(tmp_path: Path) -> None:
    store = MemoryStore(_project_identity(tmp_path / "repo"), storage_root=tmp_path / "central-store")
    project_note = store.write_project_note(
        "Reusable Pattern",
        "Promote only explicit reusable knowledge.",
        note_kind="pattern",
        tags=["pattern"],
        note_id="note-promote",
    )

    global_note = store.promote_note("note-promote")
    global_payload = json.loads(store.global_note_path("note-promote").read_text(encoding="utf-8"))

    assert project_note["scope"] == PROJECT_SCOPE
    assert global_note["scope"] == GLOBAL_SCOPE
    assert global_payload["scope"] == GLOBAL_SCOPE
    assert global_payload["project_id"] == "proj1234567890abc"
    assert global_payload["project_name"] == "Turbo Quant Memory"
    assert global_payload["note_kind"] == "pattern"
    assert global_payload["promoted_from"]["scope"] == PROJECT_SCOPE
    assert global_payload["promoted_from"]["note_id"] == "note-promote"
    assert global_payload["promoted_from"]["source_path"].endswith(
        "projects/proj1234567890abc/notes/note-promote.json"
    )
    assert [note["note_id"] for note in store.list_notes(GLOBAL_SCOPE)] == ["note-promote"]


def test_markdown_neighborhood_returns_bounded_before_and_after_slices(tmp_path: Path) -> None:
    store = MemoryStore(_project_identity(tmp_path / "repo"), storage_root=tmp_path / "central-store")
    store.write_markdown_root(
        {
            "root_id": "docs-root",
            "path": str((tmp_path / "repo" / "docs").resolve()),
            "path_hash": "path-hash-001",
        }
    )

    for chunk_index, block_id in enumerate(("mdblk-auth-0", "mdblk-auth-1", "mdblk-auth-2", "mdblk-auth-3")):
        content = f"auth neighborhood block {chunk_index}"
        store.write_markdown_block(
            {
                "block_id": block_id,
                "root_id": "docs-root",
                "source_path": "docs/auth.md",
                "heading_path": ["Architecture", f"Chunk {chunk_index}"],
                "chunk_index": chunk_index,
                "content_raw": content,
                "block_checksum": f"checksum-{chunk_index}",
                "source_checksum": "source-checksum-auth",
            }
        )

    neighborhood = store.read_markdown_neighborhood("mdblk-auth-2", before=1, after=2)

    assert neighborhood["item"]["block_id"] == "mdblk-auth-2"
    assert [block["block_id"] for block in neighborhood["neighbors_before"]] == ["mdblk-auth-1"]
    assert [block["block_id"] for block in neighborhood["neighbors_after"]] == ["mdblk-auth-3"]
    assert neighborhood["neighbor_window"] == {"before": 1, "after": 2}
