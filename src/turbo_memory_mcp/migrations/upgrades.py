"""Registered schema upgrade functions.

Imported once at package load (see migrations/__init__.py) so the
@migration decorators register their steps in REGISTRY. New phases add
their upgrade functions here.

Each function MUST be idempotent: re-running on a partially-migrated
state should converge to the same result without raising. The runner
writes the new format_version into the manifest AFTER the function
returns, so a crash mid-upgrade leaves the manifest at the previous
version and the next `migrate --apply` retries cleanly.
"""
from __future__ import annotations

from typing import Any

from ..store import (
    MemoryStore,
    NOTE_TIERS,
    tier_for_kind,
)
from .io import write_json_atomic
from .registry import Subsystem, migration


@migration(
    Subsystem.NOTES,
    from_version=1,
    to_version=2,
    description="assign `tier` to every existing note based on its kind",
)
def upgrade_notes_v1_to_v2(store: MemoryStore) -> None:
    """Add `tier` field to every note JSON that lacks one.

    Classification rule:
        - `kind == 'handoff'`  -> `tier='episodic'`
        - everything else       -> `tier='durable'`
    Notes that already have a recognized `tier` field are left alone
    (idempotency on re-runs and partial recovery).
    """
    _retier_directory(store.project_notes_dir())
    _retier_directory(store.global_notes_dir())


@migration(
    Subsystem.RETRIEVAL,
    from_version=1,
    to_version=2,
    description="reset LanceDB retrieval tables to pick up the new `tier` column",
)
def upgrade_retrieval_v1_to_v2(store: MemoryStore) -> None:
    """Drop and re-sync project and global retrieval tables.

    The LanceDB schema gained a `tier` column in this version. Existing
    tables on disk lack it, so we wipe and rebuild. Source data (note
    JSONs, markdown blocks) is unchanged — only the vector index is
    regenerated.
    """
    # Local import: RetrievalIndex pulls heavy deps (PyTorch, LanceDB)
    # and we want the migrations module itself to stay lightweight.
    from ..retrieval_index import RetrievalIndex
    from ..store import GLOBAL_SCOPE, PROJECT_SCOPE

    index = RetrievalIndex(store)
    index.reset_scope(PROJECT_SCOPE, project_id=store.project.project_id)
    index.reset_scope(GLOBAL_SCOPE)
    # Re-populate from the current note + markdown corpus. sync_*
    # methods are no-ops on an empty store.
    index.sync_project()
    index.sync_global()


@migration(
    Subsystem.SECRETS,
    from_version=1,
    to_version=2,
    description="provision empty per-project secrets vaults under projects/*/secrets/",
)
def upgrade_secrets_v1_to_v2(store: MemoryStore) -> None:
    """Walk every existing project directory and ensure a ``secrets/`` slot
    is provisioned. If the master key cannot be resolved for a project
    (e.g. headless install without ``TQMEMORY_SECRETS_PASSPHRASE`` yet),
    ``SecretsStore.provision()`` writes a stub ``meta.json`` with
    ``vault_initialized: false`` and skips ``vault.tqv``; the first
    successful ``set_secret`` lazily completes initialization.

    Idempotent: re-running on a fully-provisioned tree is a no-op (all
    projects already have ``secrets/meta.json``). A fresh install with no
    project dirs yet still succeeds — the manifest bump is what actually
    moves the subsystem version forward.
    """
    # Local import keeps the migrations module lightweight at load time.
    from ..secrets.store import SecretsStore

    projects_root = store.storage_root / "projects"
    if not projects_root.exists():
        # Fresh install, no projects yet. The runner will still bump the
        # secrets-manifest.json so the next start sees version 1.
        return
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        SecretsStore(store.storage_root, project_dir.name).provision()


@migration(
    Subsystem.RETRIEVAL,
    from_version=2,
    to_version=3,
    description="create BM25 full-text-search index on `content_search` for hybrid retrieval",
)
def upgrade_retrieval_v2_to_v3(store: MemoryStore) -> None:
    """Add the FTS index on project + global retrieval tables.

    No data change — existing rows stay where they are. The FTS index is
    built over the `content_search` column already populated by
    `mirror_note_record` and `mirror_markdown_block`. After this
    migration `RetrievalIndex.search` automatically combines the BM25
    lane with the dense-vector lane via RRF.
    """
    from ..retrieval_index import RetrievalIndex, _ensure_fts_index
    from ..store import GLOBAL_SCOPE, PROJECT_SCOPE

    index = RetrievalIndex(store)
    for scope, project_id in (
        (PROJECT_SCOPE, store.project.project_id),
        (GLOBAL_SCOPE, None),
    ):
        table = index._open_scope_table(scope, project_id=project_id)
        if table is None:
            continue
        _ensure_fts_index(table)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _retier_directory(notes_dir: Any) -> None:
    if not notes_dir.exists():
        return
    for entry in notes_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            payload = _read_json(entry)
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        existing_tier = payload.get("tier")
        if isinstance(existing_tier, str) and existing_tier in NOTE_TIERS:
            continue
        kind = payload.get("note_kind") or payload.get("kind")
        payload["tier"] = tier_for_kind(str(kind) if kind else None)
        write_json_atomic(entry, payload)


def _read_json(path: Any) -> Any:
    import json

    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)
