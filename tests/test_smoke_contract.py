from __future__ import annotations

from turbo_memory_mcp import __version__
from turbo_memory_mcp.contracts import (
    CURRENT_TOOL_NAMES,
    SERVER_ID,
    build_install_contract,
    build_hydrated_markdown_item_payload,
    build_hydrated_note_item_payload,
    build_hydration_payload,
    build_indexing_payload,
    build_note_item_payload,
    build_semantic_item_payload,
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
    install_contract = build_install_contract()
    payload = build_server_info_payload(
        storage_root="/tmp/tqmemory",
        current_project=SAMPLE_PROJECT,
        storage_stats={
            "project": {
                "note_count": 1,
                "markdown_root_count": 1,
                "markdown_file_count": 2,
                "markdown_block_count": 3,
                "retrieval_row_count": 4,
            },
            "global": {
                "note_count": 1,
                "retrieval_row_count": 1,
            },
        },
        index_status={
            "project": {
                "freshness": "fresh",
                "last_indexed_at": "2026-03-26T10:10:00Z",
                "last_note_update": "2026-03-26T10:11:00Z",
            },
            "global": {
                "freshness": "fresh",
                "last_note_update": "2026-03-26T10:12:00Z",
            },
        },
    )

    assert payload["runtime_command"] == "turbo-memory-mcp serve"
    assert payload["version"] == __version__
    assert payload["install"]["primary"]["command"] == install_contract["primary"]["command"]
    assert payload["install"]["fallback"]["command"] == install_contract["fallback"]["command"]
    assert payload["server_id"] == SERVER_ID
    assert payload["current_project"] == SAMPLE_PROJECT
    assert payload["storage_root"] == "/tmp/tqmemory"
    assert payload["query_modes"] == ["project", "global", "hybrid"]
    assert payload["default_query_mode"] == "project"
    assert payload["storage_stats"]["project"]["markdown_block_count"] == 3
    assert payload["index_status"]["project"]["freshness"] == "fresh"


def test_self_test_matches_exported_phase_5_tools() -> None:
    install_contract = build_install_contract()
    payload = build_self_test_payload(
        storage_root="/tmp/tqmemory",
        current_project=SAMPLE_PROJECT,
    )

    assert payload["server_id"] == "tqmemory"
    assert payload["tool_names"] == list(CURRENT_TOOL_NAMES)
    assert payload["runtime_command"] == "turbo-memory-mcp serve"
    assert payload["version"] == __version__
    assert payload["install"]["primary"]["command"] == install_contract["primary"]["command"]
    assert payload["install"]["fallback"]["command"] == install_contract["fallback"]["command"]
    assert payload["current_project"] == SAMPLE_PROJECT
    assert payload["storage_root"] == "/tmp/tqmemory"
    assert payload["namespace_contract"]["default_write_scope"] == "project"
    assert payload["namespace_contract"]["query_modes"] == ["project", "global", "hybrid"]
    assert payload["namespace_contract"]["index_modes"] == ["full", "incremental"]
    assert payload["namespace_contract"]["hydrate_modes"] == ["default", "related"]


def test_indexing_payload_exposes_incremental_contract_fields() -> None:
    payload = build_indexing_payload(
        mode="incremental",
        registered_roots=[{"root_id": "mdroot-123", "path": "/tmp/docs"}],
        indexed_files=2,
        changed_files=1,
        skipped_files=1,
        deleted_files=0,
        block_count=3,
    )

    assert payload["status"] == "ok"
    assert payload["mode"] == "incremental"
    assert payload["registered_roots"] == [{"root_id": "mdroot-123", "path": "/tmp/docs"}]
    assert payload["indexed_files"] == 2
    assert payload["changed_files"] == 1
    assert payload["skipped_files"] == 1
    assert payload["deleted_files"] == 0
    assert payload["block_count"] == 3


def test_note_item_payload_uses_compact_envelope_by_default() -> None:
    payload = build_note_item_payload(
        {
            "note_id": "note-1",
            "title": "Auth Flow",
            "content": "JWT refresh flow",
            "note_kind": "decision",
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
    assert payload["note_kind"] == "decision"
    assert payload["note_status"] == "active"
    assert payload["confidence"] == 0.91
    assert payload["can_hydrate"] is True
    assert "promoted_from" not in payload


def test_semantic_item_payload_uses_balanced_card_contract() -> None:
    payload = build_semantic_item_payload(
        {
            "scope": "project",
            "project_id": "project-alpha",
            "project_name": "Alpha Project",
            "source_kind": "markdown",
            "item_id": "block-auth-rotation",
            "block_id": "block-auth-rotation",
            "source_path": "docs/auth.md",
            "title": "Auth",
            "heading_path": ["Architecture", "Auth"],
            "updated_at": "2026-03-26T10:10:00Z",
            "score": 0.934,
            "confidence": 0.934,
            "confidence_state": "high",
            "compressed_summary": "Auth: Refresh rotation keeps the session cache stable.",
            "key_points": [
                "Refresh rotation keeps the session cache stable.",
                "The block lives under Architecture/Auth.",
            ],
            "can_hydrate": True,
        }
    )

    assert payload["scope"] == "project"
    assert payload["project_id"] == "project-alpha"
    assert payload["source_kind"] == "markdown"
    assert payload["block_id"] == "block-auth-rotation"
    assert payload["score"] == 0.934
    assert payload["confidence_state"] == "high"
    assert payload["compressed_summary"].startswith("Auth:")
    assert len(payload["key_points"]) == 2
    assert "content_raw" not in payload
    assert "content_search" not in payload
    assert "excerpt_preview" not in payload


def test_note_item_payload_includes_promoted_from_when_present() -> None:
    payload = build_note_item_payload(
        {
            "note_id": "note-1",
            "title": "Reusable Pattern",
            "content": "Promoted note",
            "note_kind": "pattern",
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
    assert payload["note_kind"] == "pattern"
    assert payload["note_status"] == "active"
    assert payload["promoted_from"]["scope"] == "project"
    assert payload["promoted_from"]["note_id"] == "note-1"


def test_hydrated_markdown_payload_exposes_bounded_neighbors() -> None:
    item = build_hydrated_markdown_item_payload(
        {
            "scope": "project",
            "project_id": "project-alpha",
            "source_kind": "markdown",
            "block_id": "mdblk-auth-1",
            "source_path": "docs/auth.md",
            "heading_path": ["Architecture", "Auth"],
            "updated_at": "2026-03-26T10:10:00Z",
            "content_raw": "Full hydrated auth content.",
        },
        project_name="Alpha Project",
    )
    payload = build_hydration_payload(
        mode="default",
        item=item,
        neighbors_before=[
            build_hydrated_markdown_item_payload(
                {
                    "scope": "project",
                    "project_id": "project-alpha",
                    "source_kind": "markdown",
                    "block_id": "mdblk-auth-0",
                    "source_path": "docs/auth.md",
                    "heading_path": ["Architecture", "Intro"],
                    "updated_at": "2026-03-26T10:09:00Z",
                    "content_raw": "Neighbor before.",
                },
                project_name="Alpha Project",
            )
        ],
        neighbors_after=[],
        neighbor_window={"before": 1, "after": 1},
    )

    assert payload["mode"] == "default"
    assert payload["source_kind"] == "markdown"
    assert payload["item"]["block_id"] == "mdblk-auth-1"
    assert payload["neighbors_before"][0]["block_id"] == "mdblk-auth-0"
    assert payload["neighbor_window"] == {"before": 1, "after": 1}


def test_hydrated_note_payload_preserves_note_kind_and_content() -> None:
    item = build_hydrated_note_item_payload(
        {
            "scope": "project",
            "project_id": "project-alpha",
            "project_name": "Alpha Project",
            "source_kind": "memory_note",
            "note_id": "note-1",
            "title": "Release Handoff",
            "note_kind": "handoff",
            "updated_at": "2026-03-26T10:15:00Z",
            "content": "Validate the rollout carefully.",
            "tags": ["deploy"],
            "source_refs": ["README.md"],
        },
        source_path="/tmp/tqmemory/projects/project-alpha/notes/note-1.json",
    )

    assert item["item_id"] == "note-1"
    assert item["note_kind"] == "handoff"
    assert item["note_status"] == "active"
    assert item["content"] == "Validate the rollout carefully."
