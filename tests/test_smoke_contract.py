from __future__ import annotations

from turbo_memory_mcp.contracts import (
    PHASE_2_TOOL_NAMES,
    SERVER_ID,
    build_note_item_payload,
    build_self_test_payload,
    build_server_info_payload,
)

SAMPLE_PROJECT = {
    "project_id": "project-alpha",
    "project_name": "Alpha Project",
    "project_root": "/repo/alpha",
    "identity_kind": "git_remote",
}


def test_server_info_matches_documented_namespace_contract() -> None:
    payload = build_server_info_payload(
        storage_root="/tmp/tqmemory",
        current_project=SAMPLE_PROJECT,
    )

    assert payload["runtime_command"] == "turbo-memory-mcp serve"
    assert payload["server_id"] == SERVER_ID
    assert payload["current_project"] == SAMPLE_PROJECT
    assert payload["storage_root"] == "/tmp/tqmemory"
    assert payload["query_modes"] == ["project", "global", "hybrid"]
    assert payload["default_query_mode"] == "hybrid"


def test_self_test_matches_exported_phase_2_tools() -> None:
    payload = build_self_test_payload(
        storage_root="/tmp/tqmemory",
        current_project=SAMPLE_PROJECT,
    )

    assert payload["server_id"] == "tqmemory"
    assert payload["tool_names"] == list(PHASE_2_TOOL_NAMES)
    assert payload["runtime_command"] == "turbo-memory-mcp serve"
    assert payload["current_project"] == SAMPLE_PROJECT
    assert payload["storage_root"] == "/tmp/tqmemory"
    assert payload["namespace_contract"]["default_write_scope"] == "project"
    assert payload["namespace_contract"]["query_modes"] == ["project", "global", "hybrid"]


def test_note_item_payload_uses_compact_envelope_by_default() -> None:
    payload = build_note_item_payload(
        {
            "note_id": "note-1",
            "title": "Auth Flow",
            "content": "JWT refresh flow",
            "tags": ["auth"],
            "scope": "project",
            "project_id": "project-alpha",
            "project_name": "Alpha Project",
            "source_kind": "memory_note",
            "updated_at": "2026-03-25T21:00:00Z",
        },
        source_path="/tmp/tqmemory/projects/project-alpha/notes/note-1.json",
        confidence=0.91,
        can_hydrate=True,
        content_preview="JWT refresh flow",
    )

    assert payload["scope"] == "project"
    assert payload["project_id"] == "project-alpha"
    assert payload["project_name"] == "Alpha Project"
    assert payload["item_id"] == "note-1"
    assert payload["confidence"] == 0.91
    assert payload["can_hydrate"] is True
    assert "promoted_from" not in payload


def test_note_item_payload_includes_promoted_from_when_present() -> None:
    payload = build_note_item_payload(
        {
            "note_id": "note-1",
            "title": "Reusable Pattern",
            "content": "Promoted note",
            "tags": ["pattern"],
            "scope": "global",
            "project_id": "project-alpha",
            "project_name": "Alpha Project",
            "source_kind": "memory_note",
            "updated_at": "2026-03-25T21:00:00Z",
            "promoted_from": {
                "scope": "project",
                "project_id": "project-alpha",
                "project_name": "Alpha Project",
                "note_id": "note-1",
                "source_path": "/tmp/tqmemory/projects/project-alpha/notes/note-1.json",
            },
        },
        source_path="/tmp/tqmemory/global/notes/note-1.json",
        confidence=1.0,
        can_hydrate=True,
        content_preview="Promoted note",
    )

    assert payload["scope"] == "global"
    assert payload["promoted_from"]["scope"] == "project"
    assert payload["promoted_from"]["note_id"] == "note-1"
