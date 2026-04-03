from __future__ import annotations

import hashlib
from pathlib import Path

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.retrieval_index import EMBEDDING_MODEL_NAME, RetrievalIndex
from turbo_memory_mcp.store import MemoryStore, sha256_text


class StaticEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vector = [float(digest[index % len(digest)]) / 255.0 for index in range(384)]
            vectors.append(vector)
        return vectors


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


def test_retrieval_layout_and_model_match_phase_4_contract(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())

    assert index.embedding_model_name == EMBEDDING_MODEL_NAME
    assert index.project_db_path() == store.storage_root / "projects" / "proj1234567890abc" / "retrieval"
    assert index.global_db_path() == store.storage_root / "global" / "retrieval"


def test_sync_project_mirrors_auth_architecture_markdown_and_note_rows(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())

    store.write_project_note(
        "Auth Architecture",
        "Project auth summary for auth-architecture and docs/auth.md.",
        note_kind="decision",
        tags=["auth", "architecture"],
        note_id="auth-architecture",
    )
    store.write_markdown_root(
        {
            "root_id": "docs-root",
            "path": str((tmp_path / "repo" / "docs").resolve()),
            "path_hash": "docs-root-hash",
        }
    )
    store.write_markdown_block(
        {
            "block_id": "block-auth-source",
            "root_id": "docs-root",
            "source_path": "docs/auth.md",
            "heading_path": ["Architecture", "Auth"],
            "chunk_index": 0,
            "content_raw": "JWT refresh logic for docs/auth.md.",
            "block_checksum": sha256_text("JWT refresh logic for docs/auth.md."),
            "source_checksum": "source-checksum-auth",
        }
    )

    rows = index.sync_project()

    assert index.project_db_path().exists()
    assert index.count_rows("project") == 2
    assert {row["source_kind"] for row in rows} == {"markdown", "memory_note"}
    assert {row["note_kind"] for row in rows} == {None, "decision"}
    assert {row["source_path"] for row in rows} == {
        "docs/auth.md",
        str(store.project_note_path("auth-architecture")),
    }
    assert {row["item_id"] for row in rows} == {"auth-architecture", "block-auth-source"}


def test_sync_global_prunes_stale_global_pattern_note_rows(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())

    stored = store.write_project_note(
        "Reusable Pattern",
        "Reusable cross-project pattern for global-pattern-note.",
        note_kind="pattern",
        tags=["pattern"],
        note_id="global-pattern-note",
    )
    store.promote_note(stored["note_id"])

    first_rows = index.sync_global()
    store.global_note_path("global-pattern-note").unlink()
    second_rows = index.sync_global()

    assert index.global_db_path().exists()
    assert len(first_rows) == 1
    assert first_rows[0]["note_id"] == "global-pattern-note"
    assert first_rows[0]["source_kind"] == "memory_note"
    assert first_rows[0]["note_kind"] == "pattern"
    assert index.count_rows("global") == 0
    assert second_rows == []


def test_incremental_project_note_sync_and_delete_keep_other_rows_intact(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())

    store.write_project_note(
        "Auth Note",
        "Original auth note content.",
        note_kind="lesson",
        note_id="auth-note",
    )
    store.write_markdown_root(
        {
            "root_id": "docs-root",
            "path": str((tmp_path / "repo" / "docs").resolve()),
            "path_hash": "docs-root-hash",
        }
    )
    store.write_markdown_block(
        {
            "block_id": "block-auth",
            "root_id": "docs-root",
            "source_path": "docs/auth.md",
            "heading_path": ["Architecture", "Auth"],
            "chunk_index": 0,
            "content_raw": "Block content.",
            "block_checksum": sha256_text("Block content."),
            "source_checksum": "source-checksum-auth",
        }
    )
    index.sync_project()

    store.write_project_note(
        "Auth Note",
        "Updated auth note content.",
        note_kind="lesson",
        note_id="auth-note",
    )

    updated_rows = index.sync_project_notes(["auth-note"])

    assert index.count_rows("project") == 2
    assert len(updated_rows) == 1
    assert updated_rows[0]["item_id"] == "auth-note"
    assert updated_rows[0]["content_summary_seed"] == "Updated auth note content."

    index.delete_items("project", ["auth-note"])

    assert index.count_rows("project") == 1
