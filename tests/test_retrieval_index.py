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


def _mirror_rows(index: RetrievalIndex, scope: str) -> list[dict[str, object]]:
    table = index._open_scope_table(scope)
    return [] if table is None else table.to_arrow().to_pylist()


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

    assert index.sync_project() == 2
    rows = _mirror_rows(index, "project")

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

    assert index.sync_global() == 1
    first_rows = _mirror_rows(index, "global")
    store.global_note_path("global-pattern-note").unlink()

    assert index.sync_global() == 0
    assert index.global_db_path().exists()
    assert len(first_rows) == 1
    assert first_rows[0]["note_id"] == "global-pattern-note"
    assert first_rows[0]["source_kind"] == "memory_note"
    assert first_rows[0]["note_kind"] == "pattern"
    assert index.count_rows("global") == 0


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

    assert index.sync_project_notes(["auth-note"]) == 1
    updated_rows = [row for row in _mirror_rows(index, "project") if row["item_id"] == "auth-note"]

    assert index.count_rows("project") == 2
    assert len(updated_rows) == 1
    assert updated_rows[0]["item_id"] == "auth-note"
    assert updated_rows[0]["content_summary_seed"] == "Updated auth note content."

    index.delete_items("project", ["auth-note"])

    assert index.count_rows("project") == 1


def test_embed_texts_encodes_in_chunks_under_lock() -> None:
    """Embedding shares one model across projects: a bulk re-embed must hand
    the model back between small chunks so another project's query waits for
    at most one chunk, and the tokenizer never runs on two threads at once."""
    from turbo_memory_mcp import retrieval_index

    batches: list[int] = []

    class _RecordingEmbedder:
        def encode(self, texts: list[str]) -> list[list[float]]:
            assert retrieval_index._EMBED_LOCK.locked()
            batches.append(len(texts))
            return [[float(len(text))] for text in texts]

    texts = [f"text-{index}" for index in range(150)]
    vectors = retrieval_index.encode_texts(texts, embedder=_RecordingEmbedder())

    assert batches == [64, 64, 22]
    assert vectors == [[float(len(text))] for text in texts]
    assert not retrieval_index._EMBED_LOCK.locked()


# --- streamed full syncs (2026-10-17) ---
# sync_project built every row, embedded every text in one call and returned
# every row with its vector: several hundred MB at 40k blocks.


def _write_blocks(store: MemoryStore, count: int) -> list[str]:
    block_ids = [f"block-{index:04d}" for index in range(count)]
    for block_id in block_ids:
        content = f"Body of {block_id}."
        store.write_markdown_block(
            {
                "block_id": block_id,
                "root_id": "docs-root",
                "source_path": f"docs/{block_id}.md",
                "heading_path": [block_id],
                "chunk_index": 0,
                "content_raw": content,
                "block_checksum": sha256_text(content),
                "source_checksum": sha256_text(content),
            }
        )
    return block_ids


def test_sync_project_merges_in_bounded_chunks(tmp_path: Path) -> None:
    from unittest.mock import patch

    from turbo_memory_mcp import retrieval_index

    store = _build_store(tmp_path)
    _write_blocks(store, 600)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    chunk_sizes: list[int] = []
    real_write = RetrievalIndex._write_chunk

    def spy(self: RetrievalIndex, scope: str, rows: list[dict[str, object]], **kwargs: object) -> object:
        chunk_sizes.append(len(rows))
        return real_write(self, scope, rows, **kwargs)  # type: ignore[arg-type]

    with patch.object(RetrievalIndex, "_write_chunk", autospec=True, side_effect=spy):
        mirrored = index.sync_project()

    assert chunk_sizes and max(chunk_sizes) <= retrieval_index.SYNC_CHUNK_ROWS
    assert sum(chunk_sizes) == 600
    assert index.count_rows("project") == 600
    assert mirrored == 600


def test_sync_project_removes_rows_missing_from_the_store(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    block_ids = _write_blocks(store, 300)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    index.sync_project()
    removed = block_ids[:3]
    for block_id in removed:
        store.project_markdown_block_path(block_id).unlink()

    index.sync_project()

    assert index.count_rows("project") == 297
    assert not set(removed) & set(index.existing_item_updated_at("project"))


def test_delete_items_batches_large_id_lists(tmp_path: Path) -> None:
    from unittest.mock import patch

    from lancedb.table import LanceTable

    store = _build_store(tmp_path)
    _write_blocks(store, 5)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    index.sync_project()
    real_delete = LanceTable.delete
    calls: list[str] = []

    def spy(self: LanceTable, where: str) -> object:
        calls.append(where)
        return real_delete(self, where)

    with patch.object(LanceTable, "delete", autospec=True, side_effect=spy):
        deleted = index.delete_items("project", [f"missing-{index}" for index in range(1200)] + ["block-0000"])

    assert deleted == 1201
    assert len(calls) == 3
    assert index.count_rows("project") == 4


def test_sync_calls_return_counts_not_rows(tmp_path: Path) -> None:
    """Returned row lists grew with the corpus; no caller needs the rows."""
    store = _build_store(tmp_path)
    _write_blocks(store, 300)
    index = RetrievalIndex(store, embedder=StaticEmbedder())

    assert index.sync_project() == 300
    assert index.sync_project_blocks(["block-0000", "block-0001", "missing"]) == 2


# --- explicit table schema (2026-10-17) ---
# A table created from markdown rows alone got its schema inferred from the
# data: note_kind/note_id as null and tags as list<null>. Every later note
# upsert then failed ("Spill has sent an error") and fell back to a full
# re-sync of the project.


def test_table_created_from_blocks_only_accepts_notes_later(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    _write_blocks(store, 3)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    index.sync_project()
    store.write_project_note("Auth", "Auth note body.", note_kind="pattern", tags=["auth"], note_id="auth-note")

    assert index.sync_project_notes(["auth-note"]) == 1
    types = {field.name: str(field.type) for field in index._open_scope_table("project").schema}
    assert types["note_kind"] == "string"
    assert types["tags"] == "list<item: string>"


def test_null_typed_columns_of_an_existing_table_are_repaired_in_place(tmp_path: Path) -> None:
    from unittest.mock import patch

    import lancedb

    store = _build_store(tmp_path)
    _write_blocks(store, 3)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    rows = [
        {**retrieval_index_mirror_block(store, block), "vector": StaticEmbedder().encode(["x"])[0]}
        for block in store.list_markdown_blocks()
    ]
    index.project_db_path().mkdir(parents=True, exist_ok=True)
    lancedb.connect(str(index.project_db_path())).create_table("items", rows, mode="overwrite")
    assert str(index._open_scope_table("project").schema.field("note_kind").type) == "null"
    store.write_project_note("Auth", "Auth note body.", note_kind="pattern", tags=["auth"], note_id="auth-note")

    with patch.object(RetrievalIndex, "_overwrite_scope", side_effect=AssertionError("full rewrite")):
        assert index.sync_project_notes(["auth-note"]) == 1

    assert index.count_rows("project") == 4


def retrieval_index_mirror_block(store: MemoryStore, block: dict[str, object]) -> dict[str, object]:
    from turbo_memory_mcp.retrieval_index import mirror_markdown_block

    return mirror_markdown_block(store, block, project_id=store.project.project_id)


# --- NULL list columns (GitHub issue #5) ---
# _repair_null_columns retyped list<null> columns by dropping and re-adding
# them, which left NULL where every existing row held an empty list. Search
# then crashed on list(None) / iterating None. The reader must tolerate NULL
# (persisted tables already hold it) and the next write must heal it.


def _create_items_table(index: RetrievalIndex, scope: str, rows: list[dict[str, object]], *, declared: bool) -> None:
    import lancedb
    import pyarrow as pa

    from turbo_memory_mcp.retrieval_index import _table_schema

    db_path = index.project_db_path() if scope == "project" else index.global_db_path()
    db_path.mkdir(parents=True, exist_ok=True)
    with_vectors = [{**row, "vector": StaticEmbedder().encode([str(row["content_search"])])[0]} for row in rows]
    database = lancedb.connect(str(db_path))
    if not declared:
        # A pre-0.32 table: the schema is inferred from the rows (empty lists become list<null>).
        database.create_table("items", with_vectors, mode="overwrite")
        return
    # Declared schema, NULL values: lancedb cannot infer a type from an all-None column.
    schema = _table_schema(384)
    table = database.create_table("items", schema=schema, mode="overwrite")
    table.add(pa.Table.from_pylist(with_vectors, schema=schema))


def _null_list_note_rows(store: MemoryStore, scope: str) -> list[dict[str, object]]:
    from turbo_memory_mcp.retrieval_index import mirror_note_record

    return [
        {**mirror_note_record(store, note), "heading_path": None, "tags": None}
        for note in store.list_notes(scope)
    ]


def _search(store: MemoryStore, scope: str) -> dict[str, object]:
    from unittest.mock import patch

    from turbo_memory_mcp.retrieval import semantic_search

    with patch("turbo_memory_mcp.retrieval_index.build_default_embedder", return_value=StaticEmbedder()):
        return semantic_search(store, "auth", scope=scope, limit=5)


def test_notes_only_legacy_table_stays_searchable_after_repair(tmp_path: Path) -> None:
    from turbo_memory_mcp.retrieval_index import mirror_note_record

    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    for note_id in ("old-note-1", "old-note-2"):
        store.write_project_note(f"Auth {note_id}", f"Auth body {note_id}", note_kind="decision", note_id=note_id)
    _create_items_table(
        index, "project", [mirror_note_record(store, n) for n in store.list_notes("project")], declared=False
    )
    store.write_project_note("Auth new", "Auth fresh note", note_kind="lesson", tags=["auth"], note_id="new-note")
    index.sync_project_notes(["new-note"])

    for scope in ("project", "hybrid"):
        payload = _search(store, scope)
        assert payload["result_count"] == 3, scope
        assert all(item["heading_path"] == [] for item in payload["items"]), scope


def test_blocks_only_legacy_table_with_later_note_stays_searchable(tmp_path: Path) -> None:
    from turbo_memory_mcp.retrieval_index import mirror_markdown_block

    store = _build_store(tmp_path)
    _write_blocks(store, 3)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    _create_items_table(
        index,
        "project",
        [mirror_markdown_block(store, b, project_id=store.project.project_id) for b in store.list_markdown_blocks()],
        declared=False,
    )
    store.write_project_note("Auth", "Auth note body.", note_kind="pattern", tags=["auth"], note_id="auth-note")
    index.sync_project_notes(["auth-note"])

    payload = _search(store, "project")

    assert payload["result_count"] == 4
    assert {item["source_kind"] for item in payload["items"]} == {"markdown", "memory_note"}


def test_search_tolerates_null_list_columns_without_any_write(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    store.write_project_note("Auth", "Auth body", note_kind="decision", note_id="auth-note")
    _create_items_table(index, "project", _null_list_note_rows(store, "project"), declared=True)

    payload = _search(store, "project")

    assert payload["result_count"] == 1
    assert payload["items"][0]["heading_path"] == []
    assert index.list_rows("project")[0]["tags"] == []


def test_hybrid_search_with_both_scopes_damaged(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    store.write_project_note("Auth project", "Auth body", note_kind="decision", note_id="project-note")
    store.write_global_note("Auth global", "Auth body", note_kind="pattern", note_id="global-note")
    _create_items_table(index, "project", _null_list_note_rows(store, "project"), declared=True)
    _create_items_table(index, "global", _null_list_note_rows(store, "global"), declared=True)

    payload = _search(store, "hybrid")

    assert {item["scope"] for item in payload["items"]} == {"project", "global"}


def test_next_sync_backfills_null_list_columns_without_a_full_rewrite(tmp_path: Path) -> None:
    from unittest.mock import patch

    from turbo_memory_mcp.retrieval_index import mirror_markdown_block

    store = _build_store(tmp_path)
    _write_blocks(store, 2)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    store.write_project_note("Auth", "Auth body", note_kind="decision", note_id="damaged-note")
    damaged_blocks = [
        mirror_markdown_block(store, b, project_id=store.project.project_id) for b in store.list_markdown_blocks()
    ]
    # Blocks keep their real heading_path; only tags (and the note's heading_path) are NULL.
    rows = [{**row, "tags": None} for row in damaged_blocks] + _null_list_note_rows(store, "project")
    _create_items_table(index, "project", rows, declared=True)
    store.write_project_note("New", "Auth fresh", note_kind="lesson", tags=["auth"], note_id="new-note")

    with patch.object(RetrievalIndex, "_overwrite_scope", side_effect=AssertionError("full rewrite")):
        assert index.sync_project_notes(["new-note"]) == 1

    schema = index._open_scope_table("project").schema
    # The fill must keep the declared type, or the merge falls back to a full re-sync.
    assert str(schema.field("heading_path").type) == "list<item: string>"
    assert str(schema.field("tags").type) == "list<item: string>"
    by_id = {row["item_id"]: row for row in _mirror_rows(index, "project")}
    assert all(row["tags"] is not None and row["heading_path"] is not None for row in by_id.values())
    assert by_id["damaged-note"]["heading_path"] == []
    assert by_id["block-0000"]["heading_path"] == ["block-0000"]  # untouched, not blanked
    assert by_id["new-note"]["tags"] == ["auth"]


def test_backfill_leaves_a_healthy_table_alone(tmp_path: Path) -> None:
    from turbo_memory_mcp.retrieval_index import _backfill_null_lists

    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    store.write_project_note("Auth", "Auth body", note_kind="decision", note_id="auth-note")
    index.sync_project()
    table = index._open_scope_table("project")
    version = table.version

    _backfill_null_lists(table)

    assert table.version == version  # no write when nothing is NULL


def test_backfill_keeps_an_existing_fts_index_working(tmp_path: Path) -> None:
    from unittest.mock import patch

    from turbo_memory_mcp.retrieval_index import _ensure_fts_index, _safe_fts_search

    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    store.write_project_note("Auth", "Auth body zebra", note_kind="decision", note_id="damaged-note")
    _create_items_table(index, "project", _null_list_note_rows(store, "project"), declared=True)
    # A table that has ever been searched already carries the BM25 index the update must not break.
    _ensure_fts_index(index._open_scope_table("project"))
    # _ensure_fts_index swallows errors, so prove the index is really there before the update.
    indexed_columns = [column for config in index._open_scope_table("project").list_indices() for column in config.columns]
    assert "content_search" in indexed_columns
    store.write_project_note("New", "Auth fresh", note_kind="lesson", tags=["auth"], note_id="new-note")

    with patch.object(RetrievalIndex, "_overwrite_scope", side_effect=AssertionError("full rewrite")):
        assert index.sync_project_notes(["new-note"]) == 1

    table = index._open_scope_table("project")
    assert [row["item_id"] for row in _safe_fts_search(table, "zebra", 5, None)] == ["damaged-note"]


# --- heal on read (GitHub issue #5) ---


def test_heal_null_list_columns_fills_a_damaged_table_once_per_process(tmp_path: Path) -> None:
    from unittest.mock import patch

    from turbo_memory_mcp import retrieval_index

    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    store.write_project_note("Auth", "Auth body", note_kind="decision", note_id="auth-note")
    _create_items_table(index, "project", _null_list_note_rows(store, "project"), declared=True)

    with patch.object(retrieval_index, "_backfill_null_lists", wraps=retrieval_index._backfill_null_lists) as backfill:
        index.heal_null_list_columns("project")
        index.heal_null_list_columns("project")

    assert backfill.call_count == 1
    assert [(row["heading_path"], row["tags"]) for row in _mirror_rows(index, "project")] == [([], [])]


def test_heal_null_list_columns_never_raises(tmp_path: Path) -> None:
    from unittest.mock import patch

    from turbo_memory_mcp import retrieval_index

    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    store.write_project_note("Auth", "Auth body", note_kind="decision", note_id="auth-note")
    _create_items_table(index, "project", _null_list_note_rows(store, "project"), declared=True)

    index.heal_null_list_columns("global")  # no table at all
    with patch.object(retrieval_index, "_backfill_null_lists", side_effect=RuntimeError("commit conflict")):
        index.heal_null_list_columns("project")  # a failed heal must not break the read that triggered it


# --- self_test read probe (GitHub issue #6) ---
# self_test returned a static contract snapshot and never opened the index, so a
# table that search could not read still reported ok. The probe runs a sample of
# real rows through the code a search runs on them, read-only and without an
# embedder.


def _block_rows(store: MemoryStore, count: int) -> list[dict[str, object]]:
    from turbo_memory_mcp.retrieval_index import mirror_markdown_block

    _write_blocks(store, count)
    return [mirror_markdown_block(store, b, project_id=store.project.project_id) for b in store.list_markdown_blocks()]


def test_read_probe_catches_a_damaged_note_row_behind_many_healthy_blocks(tmp_path: Path) -> None:
    from unittest.mock import patch

    from turbo_memory_mcp import retrieval_index
    from turbo_memory_mcp.retrieval import probe_read_path

    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    store.write_project_note("Auth", "Auth body", note_kind="decision", note_id="damaged-note")
    # Blocks are written first, as sync_project does, so a head sample would only ever see blocks.
    rows = _block_rows(store, 6) + _null_list_note_rows(store, "project")
    _create_items_table(index, "project", rows, declared=True)

    # Search tolerates NULL at its read boundary, so take that away: the probe must find what would choke it.
    with patch.object(retrieval_index, "_normalize_row", new=dict):
        result = probe_read_path(store, per_kind=3)

    assert result["project"]["state"] == "failed"
    assert result["project"]["item_id"] == "damaged-note"
    assert result["project"]["error"].startswith("TypeError")
    assert result["global"] == {"state": "ok", "rows_checked": 0}


def test_read_probe_does_not_flag_what_search_tolerates_and_never_writes(tmp_path: Path) -> None:
    from unittest.mock import patch

    from turbo_memory_mcp import retrieval_index
    from turbo_memory_mcp.retrieval import probe_read_path

    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    store.write_project_note("Auth", "Auth body", note_kind="decision", note_id="damaged-note")
    _create_items_table(index, "project", _null_list_note_rows(store, "project"), declared=True)
    version = index._open_scope_table("project").version

    with (
        patch.object(RetrievalIndex, "_overwrite_scope", side_effect=AssertionError("write")),
        patch.object(RetrievalIndex, "sync_project", side_effect=AssertionError("write")),
        patch.object(RetrievalIndex, "heal_null_list_columns", side_effect=AssertionError("write")),
        patch.object(retrieval_index, "_backfill_null_lists", side_effect=AssertionError("write")),
    ):
        result = probe_read_path(store)

    assert result["project"] == {"state": "ok", "rows_checked": 1}  # 0.33.1 reads NULL as [], so no false alarm
    assert index._open_scope_table("project").version == version
    assert [(r["heading_path"], r["tags"]) for r in _mirror_rows(index, "project")] == [(None, None)]


def test_read_probe_reports_ok_for_missing_healthy_and_empty_tables(tmp_path: Path) -> None:
    from turbo_memory_mcp.retrieval import probe_read_path

    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    assert probe_read_path(store) == {
        "project": {"state": "ok", "rows_checked": 0},
        "global": {"state": "ok", "rows_checked": 0},
    }

    store.write_project_note("Auth", "Auth body", note_kind="decision", note_id="auth-note")
    _write_blocks(store, 2)
    index.sync_project()
    _create_items_table(index, "global", [], declared=True)

    result = probe_read_path(store)

    assert result["project"] == {"state": "ok", "rows_checked": 3}
    assert result["global"] == {"state": "ok", "rows_checked": 0}


def test_read_probe_counts_a_note_whose_json_is_gone_as_skipped(tmp_path: Path) -> None:
    from turbo_memory_mcp.retrieval import probe_read_path
    from turbo_memory_mcp.retrieval_index import mirror_note_record

    store = _build_store(tmp_path)
    index = RetrievalIndex(store, embedder=StaticEmbedder())
    store.write_project_note("Auth", "Auth body", note_kind="decision", note_id="gone-note")
    _create_items_table(index, "project", [mirror_note_record(store, n) for n in store.list_notes("project")], declared=True)
    store.project_note_path("gone-note").unlink()

    result = probe_read_path(store)

    # Search skips such a candidate rather than failing, so the probe must not call it broken.
    assert result["project"] == {"state": "ok", "rows_checked": 0, "rows_skipped": 1}
