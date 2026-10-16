"""MCP surface of workspace coordination: the status field on memory tools and the five workspace tools."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mcp.client.session import DEFAULT_CLIENT_INFO
from mcp.shared.memory import create_connected_server_and_client_session

from turbo_memory_mcp.server import build_server, make_local_dispatcher
from turbo_memory_mcp.workspace.runtime import WorkspaceRuntime

NEW_TOOLS = ("announce_work", "finish_work", "workspace_status", "file_history", "restore_file")


class _KeywordEmbedder:
    KEYWORDS = ("auth", "refresh", "login", "global", "project", "pattern", "handoff")

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vector = [0.0] * 384
            for index, keyword in enumerate(self.KEYWORDS):
                vector[index] = 1.0 if keyword in lowered else 0.0
            vectors.append(vector)
        return vectors


@pytest.fixture(autouse=True)
def _fake_embedder() -> Iterator[None]:
    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder",
        return_value=_KeywordEmbedder(),
    ):
        yield


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    return path


@pytest.fixture
def env(tmp_path: Path, repo: Path) -> dict[str, str]:
    isolated = {key: value for key, value in os.environ.items() if not key.startswith("TQMEMORY_")}
    isolated.update(
        {
            "TQMEMORY_HOME": str(tmp_path / "memory-home"),
            "TQMEMORY_PROJECT_ROOT": str(repo),
            "TQMEMORY_PROJECT_ID": "project-alpha",
            "TQMEMORY_PROJECT_NAME": "Alpha Project",
        }
    )
    return isolated


@pytest.fixture
def runtimes() -> Iterator[list[WorkspaceRuntime]]:
    started: list[WorkspaceRuntime] = []
    yield started
    for runtime in started:
        runtime.shutdown()


def _start(started: list[WorkspaceRuntime], repo: Path, env: dict[str, str]) -> WorkspaceRuntime:
    runtime = WorkspaceRuntime.start(cwd=repo, environ=env)
    assert runtime is not None
    started.append(runtime)
    return runtime


def _payload(result: Any) -> dict[str, Any]:
    if getattr(result, "structuredContent", None):
        return dict(result.structuredContent)
    return json.loads(result.content[0].text)


def _session_calls(server: Any, calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    async def _run() -> list[Any]:
        results: list[Any] = []
        async with create_connected_server_and_client_session(server) as session:
            for name, args in calls:
                results.append(await session.call_tool(name, args))
        return results

    return asyncio.run(_run())


def _lines(workspace_field: Any) -> list[str]:
    return list(workspace_field.get("new", []))


def test_new_tools_are_listed(env: dict[str, str], runtimes: list[WorkspaceRuntime], repo: Path) -> None:
    runtime = _start(runtimes, repo, env)
    server = build_server(make_local_dispatcher(default_environ=env), workspace=runtime)

    async def _list() -> list[str]:
        async with create_connected_server_and_client_session(server) as session:
            tools = await session.list_tools()
        return [tool.name for tool in tools.tools]

    names = asyncio.run(_list())

    for name in NEW_TOOLS:
        assert name in names


def test_lone_session_responses_have_no_workspace_field(
    env: dict[str, str], runtimes: list[WorkspaceRuntime], repo: Path
) -> None:
    runtime = _start(runtimes, repo, env)
    server = build_server(make_local_dispatcher(default_environ=env), workspace=runtime)

    [result] = _session_calls(
        server,
        [("remember_note", {"title": "Lone", "content": "auth refresh", "kind": "lesson"})],
    )

    assert not result.isError
    assert "workspace" not in _payload(result)


def test_other_session_appears_in_next_response_once(
    env: dict[str, str], runtimes: list[WorkspaceRuntime], repo: Path
) -> None:
    runtime = _start(runtimes, repo, env)
    peer = _start(runtimes, repo, env)
    peer.note_client("codex", "1.0")
    server = build_server(make_local_dispatcher(default_environ=env), workspace=runtime)

    first, second = _session_calls(
        server,
        [("recent_context", {}), ("recent_context", {})],
    )

    digest = _payload(first)["workspace"]
    assert any("codex" in line for line in _lines(digest))
    assert "workspace" not in _payload(second)


def test_client_name_recorded_from_initialize(
    env: dict[str, str], runtimes: list[WorkspaceRuntime], repo: Path
) -> None:
    runtime = _start(runtimes, repo, env)
    server = build_server(make_local_dispatcher(default_environ=env), workspace=runtime)

    _session_calls(server, [("health", {})])

    row = runtime.db.get_session(runtime.session_id)  # type: ignore[union-attr]
    assert row is not None
    assert row.client_name == DEFAULT_CLIENT_INFO.name


def test_workspace_tools_do_not_wait_for_memory_lock(
    env: dict[str, str], runtimes: list[WorkspaceRuntime], repo: Path
) -> None:
    runtime = _start(runtimes, repo, env)
    lock = threading.RLock()
    release = threading.Event()
    holding = threading.Event()

    def _hold() -> None:
        with lock:
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=_hold, daemon=True)
    holder.start()
    assert holding.wait(5)
    server = build_server(
        make_local_dispatcher(dispatch_lock=lock, dispatch_lock_timeout=0.1, default_environ=env),
        workspace=runtime,
    )
    try:
        started = time.monotonic()
        [result] = _session_calls(server, [("workspace_status", {})])
        elapsed = time.monotonic() - started
    finally:
        release.set()
        holder.join(5)

    assert _payload(result)["status"] == "ok"
    assert elapsed < 2.0


def test_memory_tools_survive_coordination_failure(
    env: dict[str, str], runtimes: list[WorkspaceRuntime], repo: Path
) -> None:
    runtime = _start(runtimes, repo, env)
    server = build_server(make_local_dispatcher(default_environ=env), workspace=runtime)
    with patch.object(runtime, "digest", side_effect=RuntimeError("coordination exploded")):
        [result] = _session_calls(
            server,
            [("remember_note", {"title": "Still saved", "content": "auth refresh", "kind": "lesson"})],
        )

    assert not result.isError
    payload = _payload(result)
    assert "workspace" not in payload
    assert payload.get("status") == "ok"


def test_disabled_workspace_tools_report_disabled(env: dict[str, str]) -> None:
    server = build_server(make_local_dispatcher(default_environ=env), workspace=None)

    [result] = _session_calls(server, [("workspace_status", {})])

    payload = _payload(result)
    assert payload["status"] == "disabled"
    assert payload["reason"] == "TQMEMORY_WORKSPACE_DISABLE is set"


def test_server_info_reports_workspace_block(env: dict[str, str], runtimes: list[WorkspaceRuntime], repo: Path) -> None:
    runtime = _start(runtimes, repo, env)
    server = build_server(make_local_dispatcher(default_environ=env), workspace=runtime)

    [result] = _session_calls(server, [("server_info", {})])

    assert _payload(result)["workspace"]["state"] == "ok"


# Beyond the brief's eight: the journal-backed tools and the version guard.


def test_restore_file_rejects_a_version_that_is_neither_id_nor_head(
    env: dict[str, str], runtimes: list[WorkspaceRuntime], repo: Path
) -> None:
    runtime = _start(runtimes, repo, env)
    server = build_server(make_local_dispatcher(default_environ=env), workspace=runtime)

    [result] = _session_calls(server, [("restore_file", {"path": "a.py", "version": "latest"})])

    assert _payload(result) == {"status": "error", "reason": "version must be a version id or HEAD"}


def test_file_history_and_restore_round_trip_through_the_journal(
    env: dict[str, str], runtimes: list[WorkspaceRuntime], repo: Path
) -> None:
    runtime = _start(runtimes, repo, env)
    target = repo / "notes.txt"
    target.write_text("first\n", encoding="utf-8")
    journal = runtime.journal
    assert journal is not None
    first = journal.record("notes.txt")
    assert first is not None
    target.write_text("second\n", encoding="utf-8")
    journal.record("notes.txt")
    server = build_server(make_local_dispatcher(default_environ=env), workspace=runtime)

    history, restored = _session_calls(
        server,
        [
            ("file_history", {"path": "notes.txt"}),
            ("restore_file", {"path": "notes.txt", "version": str(first.version_id)}),
        ],
    )

    versions = _payload(history)["versions"]
    assert [row["version_id"] for row in versions][-1] == first.version_id
    assert _payload(restored)["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "first\n"
