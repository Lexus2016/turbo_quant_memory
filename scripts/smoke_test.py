from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from turbo_memory_mcp.identity import resolve_project_identity
from turbo_memory_mcp.server import build_current_project_payload

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOL_NAMES = [
    "health",
    "server_info",
    "list_scopes",
    "self_test",
    "remember_note",
    "promote_note",
    "deprecate_note",
    "semantic_search",
    "hydrate",
    "index_paths",
    "lint_knowledge_base",
]
EXPECTED_SCOPES = ["project", "global", "hybrid"]


def result_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if structured:
        return dict(structured)

    if not getattr(result, "content", None):
        raise AssertionError("Tool call returned no content.")

    text = getattr(result.content[0], "text", None)
    if text is None:
        raise AssertionError("Tool call returned no text payload.")

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Tool call returned non-JSON payload: {text}") from exc


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def run_smoke() -> list[str]:
    with TemporaryDirectory(prefix="tqmemory-smoke-") as temp_dir:
        storage_root = Path(temp_dir) / "memory-home"
        markdown_root = Path(temp_dir) / "markdown-docs"
        architecture_file = markdown_root / "architecture.md"
        overview_file = markdown_root / "overview.md"
        markdown_root.mkdir(parents=True, exist_ok=True)
        architecture_file.write_text(
            "# Architecture\n\n"
            "## Auth\n\n"
            "- Auth refresh rotation keeps session cache stable for project login flows.\n"
            "- Markdown-first retrieval should outrank notes when both hits are close.\n",
            encoding="utf-8",
        )
        overview_file.write_text("# Overview\n\nGeneral project overview notes.\n", encoding="utf-8")
        resolved_storage_root = storage_root.resolve()
        server_env = {
            **os.environ,
            "TQMEMORY_HOME": str(storage_root),
            "TQMEMORY_PROJECT_ROOT": str(PROJECT_ROOT),
        }
        expected_project = build_current_project_payload(
            resolve_project_identity(cwd=PROJECT_ROOT, environ=server_env)
        )
        params = StdioServerParameters(
            command="uv",
            args=["run", "turbo-memory-mcp", "serve"],
            cwd=PROJECT_ROOT,
            env=server_env,
        )

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tool_names = [tool.name for tool in tools_result.tools]
                expect(tool_names == EXPECTED_TOOL_NAMES, f"Unexpected tool catalog: {tool_names}")

                health = result_payload(await session.call_tool("health"))
                server_info = result_payload(await session.call_tool("server_info"))
                list_scopes = result_payload(await session.call_tool("list_scopes"))
                self_test = result_payload(await session.call_tool("self_test"))
                full_index = result_payload(
                    await session.call_tool(
                        "index_paths",
                        {
                            "paths": [str(markdown_root)],
                            "mode": "full",
                        },
                    )
                )
                knowledge_lint = result_payload(
                    await session.call_tool(
                        "lint_knowledge_base",
                        {
                            "paths": [str(markdown_root)],
                            "max_issues": 50,
                        },
                    )
                )
                remembered = result_payload(
                    await session.call_tool(
                        "remember_note",
                        {
                            "title": "Smoke Note",
                            "content": "Phase 5 namespace smoke checks hydration and project/global memory ordering.",
                            "kind": "pattern",
                            "tags": ["smoke", "phase5", "namespace"],
                        },
                    )
                )
                project_search = result_payload(
                    await session.call_tool(
                        "semantic_search",
                        {
                            "query": "auth refresh rotation session cache",
                            "scope": "project",
                            "limit": 5,
                        },
                    )
                )
                markdown_hydrate = result_payload(
                    await session.call_tool(
                        "hydrate",
                        {
                            "item_id": project_search["items"][0]["item_id"],
                            "scope": project_search["items"][0]["scope"],
                            "mode": "default",
                        },
                    )
                )
                promoted = result_payload(
                    await session.call_tool("promote_note", {"note_id": remembered["item"]["item_id"]})
                )
                hybrid_search = result_payload(
                    await session.call_tool(
                        "semantic_search",
                        {
                            "query": "namespace smoke",
                            "scope": "hybrid",
                            "limit": 5,
                        },
                    )
                )
                note_hydrate = result_payload(
                    await session.call_tool(
                        "hydrate",
                        {
                            "item_id": remembered["item"]["item_id"],
                            "scope": remembered["item"]["scope"],
                            "mode": "related",
                        },
                    )
                )
                replacement_note = result_payload(
                    await session.call_tool(
                        "remember_note",
                        {
                            "title": "Updated Smoke Note",
                            "content": "Packaged install flow uses turbo-memory-mcp serve after installation.",
                            "kind": "lesson",
                            "tags": ["smoke", "install", "runtime"],
                        },
                    )
                )
                deprecated = result_payload(
                    await session.call_tool(
                        "deprecate_note",
                        {
                            "note_id": remembered["item"]["item_id"],
                            "scope": "project",
                            "replacement_note_id": replacement_note["item"]["item_id"],
                            "reason": "Superseded by packaged install guidance.",
                        },
                    )
                )
                lifecycle_search = result_payload(
                    await session.call_tool(
                        "semantic_search",
                        {
                            "query": "packaged install runtime",
                            "scope": "project",
                            "limit": 5,
                        },
                    )
                )
                idle_incremental = result_payload(
                    await session.call_tool(
                        "index_paths",
                        {
                            "mode": "incremental",
                        },
                    )
                )
                architecture_file.write_text(
                    "# Architecture\n\nUpdated architecture notes after edit.\n",
                    encoding="utf-8",
                )
                overview_file.unlink()
                changed_incremental = result_payload(
                    await session.call_tool(
                        "index_paths",
                        {
                            "mode": "incremental",
                        },
                    )
                )
                server_info_after = result_payload(await session.call_tool("server_info"))

    expect(health["status"] == "ok", f"health.status mismatch: {health}")
    expect(health["transport"] == "stdio", f"health.transport mismatch: {health}")
    expect(health["server_id"] == "tqmemory", f"health.server_id mismatch: {health}")

    expect(
        server_info["runtime_command"] == "turbo-memory-mcp serve",
        f"server_info.runtime_command mismatch: {server_info}",
    )
    expect(
        server_info["storage_root"] == str(resolved_storage_root),
        f"server_info.storage_root mismatch: {server_info}",
    )
    expect(server_info["current_project"] == expected_project, f"server_info.current_project mismatch: {server_info}")
    expect(server_info["query_modes"] == EXPECTED_SCOPES, f"server_info.query_modes mismatch: {server_info}")
    expect(server_info["index_status"]["project"]["freshness"] == "empty", f"server_info initial status mismatch: {server_info}")

    scopes = [scope["name"] for scope in list_scopes["scopes"]]
    expect(scopes == EXPECTED_SCOPES, f"list_scopes mismatch: {list_scopes}")
    expect(list_scopes["default_write_scope"] == "project", f"list_scopes.default_write_scope mismatch: {list_scopes}")
    expect(list_scopes["default_query_mode"] == "project", f"list_scopes.default_query_mode mismatch: {list_scopes}")

    expect(self_test["status"] == "ok", f"self_test.status mismatch: {self_test}")
    expect(self_test["tool_count"] == 11, f"self_test.tool_count mismatch: {self_test}")
    expect(self_test["tool_names"] == EXPECTED_TOOL_NAMES, f"self_test.tool_names mismatch: {self_test}")
    expect(
        self_test["namespace_contract"]["query_modes"] == EXPECTED_SCOPES,
        f"self_test.namespace_contract mismatch: {self_test}",
    )
    expect(
        self_test["namespace_contract"]["index_modes"] == ["full", "incremental"],
        f"self_test.index_modes mismatch: {self_test}",
    )
    expect(
        self_test["namespace_contract"]["hydrate_modes"] == ["default", "related"],
        f"self_test.hydrate_modes mismatch: {self_test}",
    )

    expect(remembered["item"]["scope"] == "project", f"remember_note scope mismatch: {remembered}")
    expect(remembered["item"]["project_id"] == expected_project["project_id"], f"remember_note project mismatch: {remembered}")
    expect(remembered["item"]["note_kind"] == "pattern", f"remember_note note_kind mismatch: {remembered}")
    expect(project_search["scope"] == "project", f"semantic_search project scope mismatch: {project_search}")
    expect(project_search["result_count"] >= 1, f"semantic_search project result_count mismatch: {project_search}")
    expect(
        project_search["items"][0]["source_kind"] == "markdown",
        f"semantic_search markdown-first mismatch: {project_search}",
    )
    expect(project_search["items"][0]["block_id"], f"semantic_search block_id missing: {project_search}")
    expect(
        bool(project_search["items"][0]["compressed_summary"]),
        f"semantic_search compressed_summary missing: {project_search}",
    )
    expect(
        1 <= len(project_search["items"][0]["key_points"]) <= 3,
        f"semantic_search key_points mismatch: {project_search}",
    )
    expect(
        project_search["items"][0]["confidence_state"] in {"high", "medium", "low", "ambiguous"},
        f"semantic_search confidence_state mismatch: {project_search}",
    )
    expect("content_raw" not in project_search["items"][0], f"semantic_search raw excerpt leak: {project_search}")
    expect(
        "excerpt_preview" not in project_search["items"][0],
        f"semantic_search raw excerpt preview leak: {project_search}",
    )
    expect(markdown_hydrate["status"] == "ok", f"hydrate markdown status mismatch: {markdown_hydrate}")
    expect(markdown_hydrate["mode"] == "default", f"hydrate markdown mode mismatch: {markdown_hydrate}")
    expect(markdown_hydrate["source_kind"] == "markdown", f"hydrate markdown source mismatch: {markdown_hydrate}")
    expect(
        markdown_hydrate["item"]["block_id"] == project_search["items"][0]["block_id"],
        f"hydrate markdown block mismatch: {markdown_hydrate}",
    )
    expect(
        "Auth refresh rotation" in markdown_hydrate["item"]["content"],
        f"hydrate markdown content mismatch: {markdown_hydrate}",
    )
    expect(
        markdown_hydrate["neighbor_window"] == {"before": 1, "after": 1},
        f"hydrate markdown window mismatch: {markdown_hydrate}",
    )
    expect(promoted["item"]["scope"] == "global", f"promote_note scope mismatch: {promoted}")
    expect(promoted["item"]["promoted_from"]["scope"] == "project", f"promote_note provenance mismatch: {promoted}")
    expect(hybrid_search["scope"] == "hybrid", f"semantic_search hybrid scope mismatch: {hybrid_search}")
    expect(hybrid_search["result_count"] >= 2, f"semantic_search hybrid result_count mismatch: {hybrid_search}")
    expect(
        [item["scope"] for item in hybrid_search["items"][:2]] == ["project", "global"],
        f"semantic_search project/global ordering mismatch: {hybrid_search}",
    )
    expect(
        hybrid_search["items"][0]["source_kind"] == "memory_note",
        f"semantic_search hybrid note mismatch: {hybrid_search}",
    )
    expect(
        hybrid_search["items"][0]["note_kind"] == "pattern",
        f"semantic_search hybrid note_kind mismatch: {hybrid_search}",
    )
    expect(
        hybrid_search["items"][1]["promoted_from"]["scope"] == "project",
        f"semantic_search hybrid provenance mismatch: {hybrid_search}",
    )
    expect(
        "content_raw" not in hybrid_search["items"][0],
        f"semantic_search hybrid raw excerpt leak: {hybrid_search}",
    )
    expect(note_hydrate["status"] == "ok", f"hydrate note status mismatch: {note_hydrate}")
    expect(note_hydrate["source_kind"] == "memory_note", f"hydrate note source mismatch: {note_hydrate}")
    expect(note_hydrate["item"]["note_kind"] == "pattern", f"hydrate note kind mismatch: {note_hydrate}")
    expect(note_hydrate["item"]["note_status"] == "active", f"hydrate note status mismatch: {note_hydrate}")
    expect(
        note_hydrate["item"]["content"] == "Phase 5 namespace smoke checks hydration and project/global memory ordering.",
        f"hydrate note content mismatch: {note_hydrate}",
    )
    expect(note_hydrate["neighbors_before"] == [], f"hydrate note neighbors_before mismatch: {note_hydrate}")
    expect(note_hydrate["neighbors_after"] == [], f"hydrate note neighbors_after mismatch: {note_hydrate}")
    expect(replacement_note["item"]["note_status"] == "active", f"replacement note status mismatch: {replacement_note}")
    expect(deprecated["action"] == "superseded", f"deprecate_note action mismatch: {deprecated}")
    expect(deprecated["item"]["note_status"] == "superseded", f"deprecate_note status mismatch: {deprecated}")
    expect(
        deprecated["item"]["superseded_by"]["note_id"] == replacement_note["item"]["item_id"],
        f"deprecate_note superseded_by mismatch: {deprecated}",
    )
    expect(lifecycle_search["result_count"] >= 1, f"lifecycle semantic_search mismatch: {lifecycle_search}")
    expect(
        lifecycle_search["items"][0]["item_id"] == replacement_note["item"]["item_id"],
        f"lifecycle replacement ordering mismatch: {lifecycle_search}",
    )
    expect(
        lifecycle_search["items"][0]["note_status"] == "active",
        f"lifecycle note_status mismatch: {lifecycle_search}",
    )
    expect(
        server_info_after["storage_stats"]["project"]["note_count"] == 1,
        f"server_info project note_count mismatch: {server_info_after}",
    )
    expect(
        server_info_after["storage_stats"]["project"]["superseded_note_count"] == 1,
        f"server_info project superseded_note_count mismatch: {server_info_after}",
    )
    expect(
        server_info_after["storage_stats"]["global"]["note_count"] == 1,
        f"server_info global note_count mismatch: {server_info_after}",
    )
    expect(
        server_info_after["storage_stats"]["project"]["markdown_root_count"] == 1,
        f"server_info markdown_root_count mismatch: {server_info_after}",
    )
    expect(
        server_info_after["storage_stats"]["project"]["markdown_file_count"] == 1,
        f"server_info markdown_file_count mismatch: {server_info_after}",
    )
    expect(
        server_info_after["storage_stats"]["project"]["markdown_block_count"] >= 1,
        f"server_info markdown_block_count mismatch: {server_info_after}",
    )
    expect(
        server_info_after["storage_stats"]["project"]["retrieval_row_count"]
        == server_info_after["storage_stats"]["project"]["markdown_block_count"] + 1,
        f"server_info retrieval_row_count mismatch: {server_info_after}",
    )
    expect(
        server_info_after["index_status"]["project"]["freshness"] == "fresh",
        f"server_info project freshness mismatch: {server_info_after}",
    )
    expect(
        server_info_after["index_status"]["global"]["freshness"] == "fresh",
        f"server_info global freshness mismatch: {server_info_after}",
    )
    expect(
        bool(server_info_after["index_status"]["project"]["last_indexed_at"]),
        f"server_info last_indexed_at mismatch: {server_info_after}",
    )
    expect(full_index["mode"] == "full", f"index_paths full.mode mismatch: {full_index}")
    expect(len(full_index["registered_roots"]) == 1, f"index_paths roots mismatch: {full_index}")
    expect(full_index["indexed_files"] == 2, f"index_paths indexed_files mismatch: {full_index}")
    expect(full_index["changed_files"] == 2, f"index_paths changed_files mismatch: {full_index}")
    expect(full_index["deleted_files"] == 0, f"index_paths deleted_files mismatch: {full_index}")
    expect(full_index["block_count"] >= 2, f"index_paths block_count mismatch: {full_index}")
    expect(knowledge_lint["status"] == "ok", f"knowledge_lint status mismatch: {knowledge_lint}")
    expect(knowledge_lint["summary"]["root_count"] == 1, f"knowledge_lint root_count mismatch: {knowledge_lint}")
    expect(knowledge_lint["summary"]["file_count"] == 2, f"knowledge_lint file_count mismatch: {knowledge_lint}")
    expect(knowledge_lint["summary"]["broken_link_count"] == 0, f"knowledge_lint broken_link mismatch: {knowledge_lint}")
    expect(
        knowledge_lint["summary"]["duplicate_title_count"] == 0,
        f"knowledge_lint duplicate_title mismatch: {knowledge_lint}",
    )
    expect(
        knowledge_lint["summary"]["orphan_candidate_count"] >= 1,
        f"knowledge_lint orphan_count mismatch: {knowledge_lint}",
    )

    expect(idle_incremental["mode"] == "incremental", f"idle incremental mode mismatch: {idle_incremental}")
    expect(idle_incremental["indexed_files"] == 2, f"idle incremental indexed_files mismatch: {idle_incremental}")
    expect(idle_incremental["changed_files"] == 0, f"idle incremental changed mismatch: {idle_incremental}")
    expect(idle_incremental["skipped_files"] == 2, f"idle incremental skipped mismatch: {idle_incremental}")
    expect(idle_incremental["deleted_files"] == 0, f"idle incremental deleted mismatch: {idle_incremental}")

    expect(
        changed_incremental["mode"] == "incremental",
        f"changed incremental mode mismatch: {changed_incremental}",
    )
    expect(
        changed_incremental["indexed_files"] == 1,
        f"changed incremental indexed_files mismatch: {changed_incremental}",
    )
    expect(
        changed_incremental["changed_files"] == 1,
        f"changed incremental changed_files mismatch: {changed_incremental}",
    )
    expect(
        changed_incremental["deleted_files"] == 1,
        f"changed incremental deleted_files mismatch: {changed_incremental}",
    )
    expect(
        changed_incremental["block_count"] >= 1,
        f"changed incremental block_count mismatch: {changed_incremental}",
    )

    return [
        f"PASS tool catalog: {', '.join(tool_names)}",
        f"PASS server_info: {server_info['current_project']['project_id']} @ {server_info['storage_root']}",
        f"PASS semantic_search project: {project_search['items'][0]['source_kind']} {project_search['items'][0]['block_id']}",
        f"PASS remember_note: {remembered['item']['item_id']} in {remembered['item']['scope']} ({remembered['item']['note_kind']})",
        f"PASS hydrate markdown: {markdown_hydrate['item']['block_id']} mode={markdown_hydrate['mode']}",
        f"PASS promote_note: {promoted['item']['item_id']} in {promoted['item']['scope']}",
        f"PASS semantic_search hybrid: {hybrid_search['items'][0]['scope']} before {hybrid_search['items'][1]['scope']}",
        f"PASS hydrate note: {note_hydrate['item']['item_id']} mode={note_hydrate['mode']}",
        f"PASS deprecate_note: {deprecated['item']['item_id']} -> {replacement_note['item']['item_id']}",
        f"PASS server_info stats: project_rows={server_info_after['storage_stats']['project']['retrieval_row_count']}",
        f"PASS index_paths full: {full_index['indexed_files']} files / {full_index['block_count']} blocks",
        f"PASS lint_knowledge_base: issues={knowledge_lint['summary']['issue_count']}",
        f"PASS index_paths incremental idle: skipped={idle_incremental['skipped_files']}",
        f"PASS index_paths incremental changed: changed={changed_incremental['changed_files']} deleted={changed_incremental['deleted_files']}",
    ]


def main() -> int:
    try:
        messages = asyncio.run(run_smoke())
    except Exception as exc:  # pragma: no cover - script-level failure path
        print(f"FAIL smoke test: {exc}", file=sys.stderr)
        return 1

    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
