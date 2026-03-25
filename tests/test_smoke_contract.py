from __future__ import annotations

from turbo_memory_mcp.contracts import (
    PHASE_1_TOOL_NAMES,
    SERVER_ID,
    build_self_test_payload,
    build_server_info_payload,
)


def test_server_info_matches_documented_runtime_contract() -> None:
    payload = build_server_info_payload()

    assert payload["runtime_command"] == "turbo-memory-mcp serve"
    assert payload["server_id"] == SERVER_ID
    assert payload["client_tiers"]["tier_1"] == [
        "Claude Code",
        "Codex",
        "Cursor",
        "OpenCode",
    ]
    assert payload["client_tiers"]["tier_2"] == ["Antigravity"]


def test_self_test_matches_exported_phase_1_tools() -> None:
    payload = build_self_test_payload()

    assert payload["server_id"] == "tqmemory"
    assert payload["tool_names"] == list(PHASE_1_TOOL_NAMES)
    assert payload["runtime_command"] == "turbo-memory-mcp serve"
    assert payload["client_tiers"]["tier_1"] == [
        "Claude Code",
        "Codex",
        "Cursor",
        "OpenCode",
    ]
    assert payload["client_tiers"]["tier_2"] == ["Antigravity"]
