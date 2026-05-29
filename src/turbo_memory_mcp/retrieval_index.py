"""Embedded retrieval-index primitives for Phase 4.

Heavy runtime dependencies (sentence_transformers -> PyTorch, lancedb, pyarrow)
are imported lazily inside the functions that actually need them. This keeps
daemon-proxy processes lightweight: if a proxy never builds or queries a
vector table, it never pays the ~470 MB import cost of these libraries.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence

from .store import ACTIVE_NOTE_STATUS, GLOBAL_SCOPE, MARKDOWN_SOURCE_KIND, MemoryStore, NOTE_SOURCE_KIND, PROJECT_SCOPE

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    import pyarrow as pa
    from sentence_transformers import SentenceTransformer

DEFAULT_EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Multilingual default (EN/UK/RU/PL/ES/ZH); 384-dim like the previous
# all-MiniLM-L6-v2, so VECTOR_DIMENSIONS is unchanged and no schema change is
# needed. Override per-install via the TQMEMORY_EMBEDDING_MODEL env var without
# touching code. Changing the model requires a retrieval re-embed migration
# (RETRIEVAL v3 -> v4); a different-dimension model would also need a schema bump.
EMBEDDING_MODEL_NAME = os.environ.get("TQMEMORY_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL_NAME)
RETRIEVAL_TABLE_NAME = "items"
VECTOR_DIMENSIONS = 384
PROJECT_RETRIEVAL_LAYOUT = "projects/<project_id>/retrieval/"
GLOBAL_RETRIEVAL_LAYOUT = "global/retrieval/"
ITEM_ID_FIELD = "item_id"


class TextEmbedder(Protocol):
    def encode(self, texts: Sequence[str]) -> Any:
        """Return one embedding vector per text item."""


def build_default_embedder() -> "SentenceTransformer":
    return _load_default_embedder()


@lru_cache(maxsize=1)
def _load_default_embedder() -> "SentenceTransformer":
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL_NAME)


class RetrievalIndex:
    """File-backed retrieval index mirrored from the canonical JSON store."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        embedder: TextEmbedder | None = None,
        embedding_model_name: str = EMBEDDING_MODEL_NAME,
    ) -> None:
        self.store = store
        self.embedding_model_name = embedding_model_name
        self._embedder = embedder

    def project_db_path(self, project_id: str | None = None) -> Path:
        return self.store.project_retrieval_dir(project_id)

    def global_db_path(self) -> Path:
        return self.store.global_retrieval_dir()

    def sync_project(self, project_id: str | None = None) -> list[dict[str, Any]]:
        resolved_project_id = project_id or self.store.project.project_id
        rows = [
            *self._build_markdown_rows(project_id=resolved_project_id),
            *self._build_note_rows(PROJECT_SCOPE),
        ]
        return self._merge_scope_rows(PROJECT_SCOPE, rows, project_id=resolved_project_id, delete_missing=True)

    def sync_global(self) -> list[dict[str, Any]]:
        rows = self._build_note_rows(GLOBAL_SCOPE)
        return self._merge_scope_rows(GLOBAL_SCOPE, rows, delete_missing=True)

    def sync_project_notes(
        self,
        note_ids: Sequence[str],
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        resolved_project_id = project_id or self.store.project.project_id
        rows: list[dict[str, Any]] = []
        for note_id in _dedupe_ids(note_ids):
            try:
                note = self.store.read_project_note(note_id, resolved_project_id)
            except FileNotFoundError:
                continue
            if note["note_status"] != ACTIVE_NOTE_STATUS:
                continue
            rows.append(mirror_note_record(self.store, note))
        return self.upsert_rows(PROJECT_SCOPE, rows, project_id=resolved_project_id)

    def sync_global_notes(self, note_ids: Sequence[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for note_id in _dedupe_ids(note_ids):
            try:
                note = self.store.read_global_note(note_id)
            except FileNotFoundError:
                continue
            if note["note_status"] != ACTIVE_NOTE_STATUS:
                continue
            rows.append(mirror_note_record(self.store, note))
        return self.upsert_rows(GLOBAL_SCOPE, rows)

    def sync_project_blocks(
        self,
        block_ids: Sequence[str],
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        resolved_project_id = project_id or self.store.project.project_id
        rows: list[dict[str, Any]] = []
        for block_id in _dedupe_ids(block_ids):
            try:
                block = self.store.read_markdown_block(block_id, resolved_project_id)
            except FileNotFoundError:
                continue
            rows.append(mirror_markdown_block(self.store, block, project_id=resolved_project_id))
        return self.upsert_rows(PROJECT_SCOPE, rows, project_id=resolved_project_id)

    def upsert_rows(
        self,
        scope: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_rows = [dict(row) for row in rows]
        if not normalized_rows:
            return []
        return self._merge_scope_rows(scope, normalized_rows, project_id=project_id, delete_missing=False)

    def delete_items(
        self,
        scope: str,
        item_ids: Sequence[str],
        *,
        project_id: str | None = None,
    ) -> int:
        normalized_ids = _dedupe_ids(item_ids)
        if not normalized_ids:
            return 0

        table = self._open_scope_table(scope, project_id=project_id)
        if table is None:
            return 0

        if len(normalized_ids) == 1:
            where = f"{ITEM_ID_FIELD} = {_quote_sql_string(normalized_ids[0])}"
        else:
            values = ", ".join(_quote_sql_string(item_id) for item_id in normalized_ids)
            where = f"{ITEM_ID_FIELD} IN ({values})"
        table.delete(where)
        self._write_scope_manifest(scope, project_id=project_id)
        return len(normalized_ids)

    def search(
        self,
        query: str,
        scope: str,
        *,
        limit: int,
        project_id: str | None = None,
        tier_filter: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid retrieval: dense vector search + BM25 (FTS), merged via RRF.

        Phase 3 brings the BM25 lane on top of the existing dense-vector
        recall. Each backend returns its top candidates independently;
        Reciprocal Rank Fusion combines them so an item that ranks well in
        either lane bubbles to the top.

        Backwards compatibility: if the on-disk LanceDB table has no FTS
        index yet (legacy installs that have not run the Phase 3
        migration), the FTS lane silently returns no rows and search
        falls back to vector-only — same behavior as before.
        """
        table = self._open_scope_table(scope, project_id=project_id)
        if table is None or self.count_rows(scope, project_id=project_id) == 0:
            return []

        where_clause: str | None = None
        if tier_filter and _table_has_tier_column(table):
            tiers_quoted = ", ".join(_quote_sql_string(str(t)) for t in tier_filter)
            where_clause = f"tier IN ({tiers_quoted})"

        # Take a few extra candidates per lane so RRF has signal to merge.
        fetch_limit = max(limit * 3, limit)
        query_vector = self._embed_texts([query])[0]
        vector_rows = _safe_vector_search(table, query_vector, fetch_limit, where_clause)
        fts_rows = _safe_fts_search(table, query, fetch_limit, where_clause)

        merged = _rrf_merge([vector_rows, fts_rows], k=60, limit=limit)
        return merged

    def list_rows(self, scope: str, *, project_id: str | None = None) -> list[dict[str, Any]]:
        table = self._open_scope_table(scope, project_id=project_id)
        if table is None or self.count_rows(scope, project_id=project_id) == 0:
            return []
        return [dict(row) for row in table.to_arrow().to_pylist()]

    def count_rows(self, scope: str, project_id: str | None = None) -> int:
        table = self._open_scope_table(scope, project_id=project_id)
        if table is None:
            return 0

        count_method = getattr(table, "count_rows", None)
        if callable(count_method):
            return int(count_method())

        legacy_count_method = getattr(table, "countRows", None)
        if callable(legacy_count_method):
            return int(legacy_count_method())

        raise AttributeError("LanceDB table does not expose a supported count_rows method.")

    def _embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        embedder = self._embedder or build_default_embedder()
        self._embedder = embedder
        vectors = embedder.encode(list(texts))
        return [[float(value) for value in vector] for vector in vectors]

    def _build_markdown_rows(self, *, project_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for block in self.store.list_markdown_blocks(project_id=project_id):
            rows.append(mirror_markdown_block(self.store, block, project_id=project_id))
        return rows

    def _build_note_rows(self, scope: str) -> list[dict[str, Any]]:
        return [mirror_note_record(self.store, note) for note in self.store.list_notes(scope)]

    def _merge_scope_rows(
        self,
        scope: str,
        rows: list[dict[str, Any]],
        *,
        project_id: str | None = None,
        delete_missing: bool,
    ) -> list[dict[str, Any]]:
        db_path = self.project_db_path(project_id) if scope == PROJECT_SCOPE else self.global_db_path()
        db_path.mkdir(parents=True, exist_ok=True)
        vectors = self._embed_texts([row["content_search"] for row in rows])
        indexed_rows: list[dict[str, Any]] = []
        for row, vector in zip(rows, vectors):
            indexed_row = dict(row)
            indexed_row["vector"] = vector
            indexed_rows.append(indexed_row)

        import lancedb

        database = lancedb.connect(str(db_path))
        table = self._open_scope_table(scope, project_id=project_id)
        if table is None:
            if indexed_rows:
                database.create_table(RETRIEVAL_TABLE_NAME, indexed_rows, mode="overwrite")
            else:
                database.create_table(RETRIEVAL_TABLE_NAME, schema=_table_schema(), mode="overwrite")
            self._write_scope_manifest(scope, project_id=project_id)
            return indexed_rows

        if not indexed_rows:
            if delete_missing:
                database.create_table(RETRIEVAL_TABLE_NAME, schema=_table_schema(), mode="overwrite")
                self._write_scope_manifest(scope, project_id=project_id)
            return []

        builder = table.merge_insert(ITEM_ID_FIELD).when_matched_update_all().when_not_matched_insert_all()
        if delete_missing:
            builder = builder.when_not_matched_by_source_delete()
        try:
            builder.execute(indexed_rows)
        except Exception:
            if not delete_missing:
                raise
            database.create_table(RETRIEVAL_TABLE_NAME, indexed_rows, mode="overwrite")
        self._write_scope_manifest(scope, project_id=project_id)
        return indexed_rows

    def reset_scope(self, scope: str, *, project_id: str | None = None) -> None:
        db_path = self.project_db_path(project_id) if scope == PROJECT_SCOPE else self.global_db_path()
        db_path.mkdir(parents=True, exist_ok=True)
        import lancedb

        database = lancedb.connect(str(db_path))
        try:
            database.create_table(RETRIEVAL_TABLE_NAME, schema=_table_schema(), mode="overwrite")
        except Exception:
            database.create_table(RETRIEVAL_TABLE_NAME, schema=_table_schema(), mode="overwrite")
        self._write_scope_manifest(scope, project_id=project_id)

    def _open_scope_table(self, scope: str, project_id: str | None = None) -> Any | None:
        if scope == PROJECT_SCOPE:
            db_path = self.project_db_path(project_id)
        elif scope == GLOBAL_SCOPE:
            db_path = self.global_db_path()
        else:
            raise ValueError(f"Unsupported retrieval scope: {scope}")

        if not db_path.exists():
            return None

        import lancedb

        database = lancedb.connect(str(db_path))
        try:
            return database.open_table(RETRIEVAL_TABLE_NAME)
        except Exception:
            return None

    def _write_scope_manifest(self, scope: str, *, project_id: str | None = None) -> None:
        if scope == PROJECT_SCOPE:
            self.store.write_project_retrieval_manifest(project_id)
            return
        if scope == GLOBAL_SCOPE:
            self.store.write_global_retrieval_manifest()
            return
        raise ValueError(f"Unsupported retrieval scope: {scope}")


def _dedupe_ids(item_ids: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(item_id) for item_id in item_ids if str(item_id).strip()))


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _safe_vector_search(
    table: Any,
    query_vector: list[float],
    limit: int,
    where_clause: str | None,
) -> list[dict[str, Any]]:
    """Dense-vector lane. Returns rows with `_distance`. Defensive on any
    LanceDB error so search keeps working even if one lane breaks."""
    try:
        builder = table.search(query_vector).metric("cosine").limit(limit)
        if where_clause:
            builder = builder.where(where_clause)
        return [dict(row) for row in builder.to_list()]
    except Exception:  # noqa: BLE001
        return []


def _safe_fts_search(
    table: Any,
    query_text: str,
    limit: int,
    where_clause: str | None,
) -> list[dict[str, Any]]:
    """BM25/FTS lane. Returns rows with `_score`. Idempotently ensures the
    FTS index exists on first use; degrades to an empty list on legacy
    installs whose table predates the Phase 3 index."""
    if not query_text.strip():
        return []
    try:
        _ensure_fts_index(table)
        builder = table.search(query_text, query_type="fts").limit(limit)
        if where_clause:
            builder = builder.where(where_clause)
        return [dict(row) for row in builder.to_list()]
    except Exception:  # noqa: BLE001
        return []


def _ensure_fts_index(table: Any) -> None:
    """Create the BM25 index on `content_search` if missing. Idempotent.

    LanceDB raises when the index already exists and `replace=False`; that
    is exactly the no-op path we want.
    """
    try:
        table.create_fts_index("content_search", replace=False)
    except Exception:  # noqa: BLE001 — already exists or not supported
        pass


def _rrf_merge(
    result_lists: list[list[dict[str, Any]]],
    *,
    k: int = 60,
    limit: int,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion.

    For each row, score = sum over lanes of 1 / (k + rank_in_lane). Higher
    is better. `k=60` is the standard from the original RRF paper and
    keeps the formula robust to outliers.

    By convention ``result_lists`` is ``[vector_rows, fts_rows]``. Items
    that appeared only in the FTS lane lack a real `_distance`. Rather than
    a flat neutral value (which pinned every BM25-only hit to the same
    base score regardless of how strongly BM25 matched), we synthesize a
    `_distance` from the item's BM25 rank: rank 1 -> ~0.15 (a strong
    exact-term match can then reach high confidence), decaying with rank.
    This is what lets the BM25 lane actually influence the downstream
    additive score instead of being silently capped.
    """
    scores: dict[str, float] = {}
    rows: dict[str, dict[str, Any]] = {}
    fts_rank: dict[str, int] = {}
    for lane_index, hits in enumerate(result_lists):
        for rank, row in enumerate(hits, start=1):
            iid = str(row.get("item_id") or "")
            if not iid:
                continue
            scores[iid] = scores.get(iid, 0.0) + 1.0 / (k + rank)
            # Lane 1 is the FTS lane by convention; remember each item's
            # best BM25 rank so FTS-only hits can be scored by it below.
            if lane_index == 1:
                fts_rank.setdefault(iid, rank)
            if iid not in rows:
                rows[iid] = dict(row)
            elif "_distance" not in rows[iid] and "_distance" in row:
                # Prefer the vector row when an item appeared in both lanes:
                # _distance carries more downstream signal than _score alone.
                rows[iid] = dict(row)

    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    output: list[dict[str, Any]] = []
    for iid, rrf_score in ordered[:limit]:
        record = rows[iid]
        if "_distance" not in record:
            # FTS-only hit: map its BM25 rank to a synthetic distance so the
            # additive scorer reflects how well BM25 matched (rank 1 -> 0.15,
            # +0.08 per rank, capped at 0.6) instead of a flat neutral that
            # capped every BM25 hit at the same mid-range base.
            rank = fts_rank.get(iid, limit)
            record["_distance"] = round(min(0.15 + 0.08 * (rank - 1), 0.6), 4)
        record["_rrf_score"] = round(rrf_score, 6)
        output.append(record)
    return output


def _table_has_tier_column(table: Any) -> bool:
    """True when the LanceDB table carries the Phase-2 `tier` column.

    Returns False for v1 tables that pre-date Phase 2 (and for any
    unexpected schema-introspection failure). Falling back to False
    means tier_filter is skipped silently — degraded but not broken.
    `schema` can be a property whose getter raises (e.g. old or
    proxied LanceDB versions), so we wrap the read itself, not just
    the `.names` access.
    """
    try:
        schema_attr = getattr(table, "schema", None)
    except Exception:  # noqa: BLE001 — defensive: property may raise
        return False
    if schema_attr is None:
        return False
    try:
        field_names = list(schema_attr.names)
    except Exception:  # noqa: BLE001 — defensive: unknown LanceDB versions
        return False
    return "tier" in field_names


def mirror_markdown_block(
    store: MemoryStore,
    block: Mapping[str, Any],
    *,
    project_id: str,
) -> dict[str, Any]:
    from .store import NOTE_TIER_REFERENCE  # local import avoids cycle

    heading_path = [str(heading) for heading in block.get("heading_path", [])]
    title = heading_path[-1] if heading_path else str(block["source_path"])
    content_raw = str(block["content_raw"])
    content_search = "\n".join(part for part in [title, content_raw] if part)
    return {
        "scope": PROJECT_SCOPE,
        "project_id": project_id,
        "project_name": store.project.project_name,
        "source_kind": MARKDOWN_SOURCE_KIND,
        "note_kind": None,
        "tier": NOTE_TIER_REFERENCE,
        "item_id": str(block["block_id"]),
        "block_id": str(block["block_id"]),
        "note_id": None,
        "source_path": str(block["source_path"]),
        "heading_path": heading_path,
        "title": title,
        "tags": [],
        "content_search": content_search,
        "content_summary_seed": content_raw,
        "updated_at": str(block["updated_at"]),
    }


def mirror_note_record(store: MemoryStore, note: Mapping[str, Any]) -> dict[str, Any]:
    from .store import NOTE_TIER_DURABLE, tier_for_kind  # local import avoids cycle

    title = str(note["title"])
    content = str(note["content"])
    note_kind = str(note["note_kind"])
    tags = [str(tag) for tag in note.get("tags", [])]
    content_search = "\n".join(part for part in [title, note_kind, " ".join(tags), content] if part)
    tier = note.get("tier") or tier_for_kind(note_kind) or NOTE_TIER_DURABLE
    return {
        "scope": str(note["scope"]),
        "project_id": str(note["project_id"]),
        "project_name": str(note["project_name"]),
        "source_kind": NOTE_SOURCE_KIND,
        "note_kind": note_kind,
        "tier": str(tier),
        "item_id": str(note["note_id"]),
        "block_id": None,
        "note_id": str(note["note_id"]),
        "source_path": str(store.note_source_path(note)),
        "heading_path": [],
        "title": title,
        "tags": tags,
        "content_search": content_search,
        "content_summary_seed": content,
        "updated_at": str(note["updated_at"]),
    }


def _table_schema() -> "pa.Schema":
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("scope", pa.string()),
            pa.field("project_id", pa.string()),
            pa.field("project_name", pa.string()),
            pa.field("source_kind", pa.string()),
            pa.field("note_kind", pa.string()),
            pa.field("tier", pa.string()),
            pa.field("item_id", pa.string()),
            pa.field("block_id", pa.string()),
            pa.field("note_id", pa.string()),
            pa.field("source_path", pa.string()),
            pa.field("heading_path", pa.list_(pa.string())),
            pa.field("title", pa.string()),
            pa.field("tags", pa.list_(pa.string())),
            pa.field("content_search", pa.string()),
            pa.field("content_summary_seed", pa.string()),
            pa.field("updated_at", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), VECTOR_DIMENSIONS)),
        ]
    )


__all__ = [
    "DEFAULT_EMBEDDING_MODEL_NAME",
    "EMBEDDING_MODEL_NAME",
    "GLOBAL_RETRIEVAL_LAYOUT",
    "ITEM_ID_FIELD",
    "PROJECT_RETRIEVAL_LAYOUT",
    "RETRIEVAL_TABLE_NAME",
    "RetrievalIndex",
    "VECTOR_DIMENSIONS",
    "build_default_embedder",
    "mirror_markdown_block",
    "mirror_note_record",
]
