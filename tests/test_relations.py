from __future__ import annotations

import json
from pathlib import Path

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.store import (
    GLOBAL_SCOPE,
    MemoryStore,
    PROJECT_SCOPE,
)
from turbo_memory_mcp.retrieval import _decorate_candidate
from turbo_memory_mcp.server import (
    link_entities_impl,
    unlink_entities_impl,
    get_related_entities_impl,
)


def _project_identity(project_root: Path) -> ProjectIdentity:
    return ProjectIdentity(
        project_id="proj_relations_test",
        project_name="Relations Test Project",
        project_root=project_root,
        identity_source="github.com/example/relations-test",
        identity_kind="git_remote",
        remote_url="git@github.com:example/relations-test.git",
    )


def test_relations_persistence_and_lookup(tmp_path: Path) -> None:
    store = MemoryStore(_project_identity(tmp_path / "repo"), storage_root=tmp_path / "central-store")

    # Add relations in project scope
    rel1 = store.add_relation("note://note-1", "file://src/main.py", "references")
    assert rel1["source"] == "note://note-1"
    assert rel1["target"] == "file://src/main.py"
    assert rel1["type"] == "references"
    assert "created_at" in rel1

    # Verify duplicate relations are not added twice
    rel1_dup = store.add_relation("note://note-1", "file://src/main.py", "references")
    assert rel1_dup == rel1
    assert len(store.read_relations(PROJECT_SCOPE)) == 1

    # Add relation in global scope
    rel_global = store.add_relation("note://note-1", "issue://BUG-404", "fixes", scope=GLOBAL_SCOPE)
    assert rel_global["source"] == "note://note-1"
    assert rel_global["target"] == "issue://BUG-404"
    assert rel_global["type"] == "fixes"

    # Query relations using get_relations_for_entity (hybrid scope)
    hybrid_rels = store.get_relations_for_entity("note://note-1", scope="hybrid")
    assert len(hybrid_rels) == 2
    sources = {r["source"] for r in hybrid_rels}
    targets = {r["target"] for r in hybrid_rels}
    assert "note://note-1" in sources
    assert "file://src/main.py" in targets
    assert "issue://BUG-404" in targets

    # Query with specific relation type filter
    filtered_rels = store.get_relations_for_entity("note://note-1", relation_type="fixes", scope="hybrid")
    assert len(filtered_rels) == 1
    assert filtered_rels[0]["target"] == "issue://BUG-404"

    # Remove relation
    removed = store.remove_relation("note://note-1", "file://src/main.py", "references")
    assert removed is True
    assert len(store.read_relations(PROJECT_SCOPE)) == 0

    removed_nonexistent = store.remove_relation("note://note-1", "file://src/main.py", "references")
    assert removed_nonexistent is False


def test_relation_server_impls(tmp_path: Path) -> None:
    # Set environment variables so build_runtime_context targets our temp store
    environ = {
        "TQMEMORY_HOME": str(tmp_path / "central-store"),
        "TQMEMORY_PROJECT_ROOT": str(tmp_path / "repo"),
        "TQMEMORY_PROJECT_ID": "proj_relations_test",
        "TQMEMORY_PROJECT_NAME": "Relations Test Project",
    }
    
    # Link entities
    res_link = link_entities_impl(
        source_uri="note://note-abc",
        target_uri="file://src/auth.py",
        relation_type="documents",
        scope="project",
        cwd=tmp_path / "repo",
        environ=environ,
    )
    assert res_link["action"] == "linked"
    assert res_link["relation"]["source"] == "note://note-abc"
    assert res_link["relation"]["target"] == "file://src/auth.py"
    assert res_link["relation"]["type"] == "documents"

    # Query relations
    res_query = get_related_entities_impl(
        uri="note://note-abc",
        relation_type=None,
        scope="hybrid",
        cwd=tmp_path / "repo",
        environ=environ,
    )
    assert res_query["uri"] == "note://note-abc"
    assert len(res_query["relations"]) == 1
    assert res_query["relations"][0]["target"] == "file://src/auth.py"

    # Unlink entities
    res_unlink = unlink_entities_impl(
        source_uri="note://note-abc",
        target_uri="file://src/auth.py",
        relation_type="documents",
        scope="project",
        cwd=tmp_path / "repo",
        environ=environ,
    )
    assert res_unlink["action"] == "unlinked"
    assert res_unlink["changed"] is True


def test_decorate_candidate_enrichment(tmp_path: Path) -> None:
    store = MemoryStore(_project_identity(tmp_path / "repo"), storage_root=tmp_path / "central-store")

    # Add relation
    store.add_relation("note://note-123", "file://src/db.py", "configures")

    # Mock candidate representing a note
    candidate_note = {
        "scope": PROJECT_SCOPE,
        "project_id": "proj_relations_test",
        "project_name": "Relations Test Project",
        "source_kind": "memory_note",
        "item_id": "note-123",
        "note_id": "note-123",
        "source_path": "projects/proj_relations_test/notes/note-123.json",
        "title": "Database Config",
        "content_summary_seed": "Points to localhost.",
        "updated_at": "2026-05-22T20:00:00Z",
        "score": 0.9,
        "confidence": 0.9,
    }

    # We need to write the actual note to store because _decorate_candidate reads it
    store.write_project_note(
        "Database Config",
        "Points to localhost.",
        note_kind="decision",
        note_id="note-123",
    )

    enriched = _decorate_candidate(
        candidate_note,
        store=store,
        query="Database",
        overall_state="high",
    )

    assert "relations" in enriched
    assert len(enriched["relations"]) == 1
    assert enriched["relations"][0]["source"] == "note://note-123"
    assert enriched["relations"][0]["target"] == "file://src/db.py"
    assert enriched["relations"][0]["type"] == "configures"
