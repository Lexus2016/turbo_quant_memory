from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from turbo_memory_mcp.cli import main as cli_main
from turbo_memory_mcp.identity import ENV_ACTIVE_PROJECT_ID
from turbo_memory_mcp.locks import GLOBAL_LOCK_KEY, ScopeLocks
from turbo_memory_mcp.migrations import create_snapshot
from turbo_memory_mcp.project_catalog import (
    bind_project,
    list_projects,
    set_project_labels,
    unbind_project,
)
from turbo_memory_mcp.server import (
    build_runtime_context,
    get_secret_impl,
    make_local_dispatcher,
    promote_note_impl,
    remember_note_impl,
    semantic_search_impl,
    set_secret_impl,
)


class _Embedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 383 for _ in texts]


@pytest.fixture(autouse=True)
def _fake_embedder():
    with patch("turbo_memory_mcp.retrieval_index.build_default_embedder", return_value=_Embedder()):
        yield


def _seed(storage_root: Path, root: Path, project_id: str, name: str) -> dict[str, str]:
    root.mkdir()
    env = {
        "TQMEMORY_HOME": str(storage_root),
        "TQMEMORY_PROJECT_ROOT": str(root),
        "TQMEMORY_PROJECT_ID": project_id,
        "TQMEMORY_PROJECT_NAME": name,
    }
    _, store = build_runtime_context(environ=env)
    store.write_project_manifest()
    return env


def test_catalog_labels_and_exact_binding_preserve_canonical_manifest(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    alias = tmp_path / "alias"
    _seed(storage, original, "alpha", "Alpha")
    alias.mkdir()
    before = json.loads((storage / "projects" / "alpha" / "manifest.json").read_text())

    assert set_project_labels(storage, "alpha", ["Backend", "backend", " важливо "]) == ["Backend", "важливо"]
    bind_project(storage, "alpha", alias)
    project, store = build_runtime_context(cwd=alias, environ={"TQMEMORY_HOME": str(storage)})
    store.write_project_manifest()

    after = json.loads((storage / "projects" / "alpha" / "manifest.json").read_text())
    assert project.project_id == "alpha"
    assert project.identity_kind == "folder_binding"
    assert project.project_root == alias
    assert after["project_root"] == before["project_root"]
    assert after["identity_sources"] == before["identity_sources"]
    assert list_projects(storage) == [{
        "project_id": "alpha",
        "project_name": "Alpha",
        "labels": ["Backend", "важливо"],
        "project_root": str(original),
        "remote_url": None,
        "bound_folders": [str(alias)],
    }]


def test_binding_routes_note_writes_without_indexing_folder(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    alias = tmp_path / "alias"
    _seed(storage, original, "alpha", "Alpha")
    alias.mkdir()
    (alias / "private.md").write_text("not indexed", encoding="utf-8")
    bind_project(storage, "alpha", alias)

    payload = remember_note_impl(
        "Shared project", "Written from an alias folder.", kind="lesson",
        cwd=alias, environ={"TQMEMORY_HOME": str(storage)},
    )

    assert payload["item"]["project_id"] == "alpha"
    assert build_runtime_context(environ=_seed_existing_env(storage, original))[1].list_markdown_roots() == []
    assert not (storage / "projects" / "private.md").exists()


def test_binding_reads_existing_project_notes(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    alias = tmp_path / "alias"
    env = _seed(storage, original, "alpha", "Alpha")
    alias.mkdir()
    remember_note_impl("Existing decision", "Keep one project memory across folders.", kind="decision", environ=env)
    bind_project(storage, "alpha", alias)

    result = semantic_search_impl(
        "Existing decision", scope="project", cwd=alias,
        environ={"TQMEMORY_HOME": str(storage)},
    )
    assert result["result_count"] == 1
    assert result["items"][0]["project_id"] == "alpha"


def _seed_existing_env(storage: Path, root: Path) -> dict[str, str]:
    return {"TQMEMORY_HOME": str(storage), "TQMEMORY_PROJECT_ROOT": str(root), "TQMEMORY_PROJECT_ID": "alpha"}


def test_binding_does_not_capture_child_directory_or_nested_repo(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    parent = tmp_path / "parent"
    child = parent / "child"
    _seed(storage, original, "alpha", "Alpha")
    child.mkdir(parents=True)
    bind_project(storage, "alpha", parent)

    parent_project, _ = build_runtime_context(cwd=parent, environ={"TQMEMORY_HOME": str(storage)})
    child_project, _ = build_runtime_context(cwd=child, environ={"TQMEMORY_HOME": str(storage)})
    assert parent_project.project_id == "alpha"
    assert child_project.project_id != "alpha"


def test_stale_binding_and_corrupt_catalog_fail_closed(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    alias = tmp_path / "alias"
    _seed(storage, original, "alpha", "Alpha")
    alias.mkdir()
    bind_project(storage, "alpha", alias)
    alias.rename(tmp_path / "moved")
    alias.mkdir()

    with pytest.raises(ValueError, match="changed since registration"):
        build_runtime_context(cwd=alias, environ={"TQMEMORY_HOME": str(storage)})

    (storage / "project-catalog.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="Project catalog is unreadable"):
        build_runtime_context(cwd=original, environ={"TQMEMORY_HOME": str(storage)})
    with pytest.raises(ValueError, match="Project catalog is unreadable"):
        list_projects(storage)


def test_unbind_missing_folder_and_refuse_silent_rebinding(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    other = tmp_path / "other"
    alias = tmp_path / "alias"
    _seed(storage, original, "alpha", "Alpha")
    _seed(storage, other, "beta", "Beta")
    alias.mkdir()
    bind_project(storage, "alpha", alias)
    with pytest.raises(ValueError, match="already bound"):
        bind_project(storage, "beta", alias)
    alias.rmdir()
    assert unbind_project(storage, alias)
    assert not unbind_project(storage, alias)


def test_binding_rejects_changed_git_origin(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    alias = tmp_path / "alias"
    _seed(storage, original, "alpha", "Alpha")
    alias.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=alias, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@example.com:team/first.git"], cwd=alias, check=True)
    bind_project(storage, "alpha", alias)
    subprocess.run(["git", "remote", "set-url", "origin", "git@example.com:team/second.git"], cwd=alias, check=True)

    with pytest.raises(ValueError, match="git origin changed"):
        build_runtime_context(cwd=alias, environ={"TQMEMORY_HOME": str(storage)})


def test_symlink_alias_and_environment_override(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    alias = tmp_path / "alias"
    link = tmp_path / "link"
    _seed(storage, original, "alpha", "Alpha")
    alias.mkdir()
    link.symlink_to(alias, target_is_directory=True)
    bind_project(storage, "alpha", link)

    project, _ = build_runtime_context(cwd=link, environ={"TQMEMORY_HOME": str(storage)})
    explicit, _ = build_runtime_context(cwd=link, environ={"TQMEMORY_HOME": str(storage), "TQMEMORY_PROJECT_ID": "manual"})
    assert project.project_id == "alpha"
    assert explicit.project_id == "manual"
    assert unbind_project(storage, link)
    assert not unbind_project(storage, link)


def test_dispatcher_isolates_two_callers_by_cwd(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    alias = tmp_path / "alias"
    other = tmp_path / "other"
    _seed(storage, original, "alpha", "Alpha")
    _seed(storage, other, "beta", "Beta")
    alias.mkdir()
    dispatcher = make_local_dispatcher(default_environ={"TQMEMORY_HOME": str(storage)})

    dispatcher("bind_project", {"project_id": "alpha", "path": str(alias), "_cwd": str(alias)})
    first = dispatcher("server_info", {"_cwd": str(alias)})
    second = dispatcher("server_info", {"_cwd": str(other)})
    assert first["current_project"]["project_id"] == "alpha"
    assert second["current_project"]["project_id"] == "beta"


def test_selected_project_preserves_manifest_and_rejects_unknown_id(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    other = tmp_path / "other"
    _seed(storage, original, "alpha", "Alpha")
    other.mkdir()
    manifest_path = storage / "projects" / "alpha" / "manifest.json"
    before = json.loads(manifest_path.read_text(encoding="utf-8"))
    dispatcher = make_local_dispatcher(
        default_cwd=other, default_environ={"TQMEMORY_HOME": str(storage)}
    )

    assert dispatcher("select_project", {"project_id": "alpha"})["project_id"] == "alpha"
    selected = dispatcher("remember_note", {
        "title": "Selected project note", "content": "Written from another folder.",
        "kind": "lesson", "_selected_project_id": "alpha", "_required_project_id": "alpha",
    })
    assert selected["item"]["project_id"] == "alpha"
    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after["project_root"] == before["project_root"]
    assert after["identity_sources"] == before["identity_sources"]
    with pytest.raises(ValueError, match="Unknown project_id"):
        dispatcher("select_project", {"project_id": "missing"})
    manifest_path.unlink()
    with pytest.raises(ValueError, match="Selected project no longer resolves"):
        dispatcher("server_info", {
            "_selected_project_id": "alpha", "_required_project_id": "alpha",
        })


def test_snapshot_transfers_to_clean_home_without_live_memory_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    with TemporaryDirectory(prefix="tqm-transfer-") as temporary:
        root = Path(temporary)
        source = root / "source-memory"
        restored = root / "restored-memory"
        original = root / "original-repo"
        caller = root / "new-client-folder"
        caller.mkdir()
        env = _seed(source, original, "alpha", "Alpha")
        project_note = remember_note_impl(
            "Portable project note", "Survives transfer to another storage root.",
            kind="decision", environ=env,
        )
        global_note = remember_note_impl(
            "Portable global note", "Visible across projects after restore.",
            kind="pattern", environ=env,
        )
        promote_note_impl(global_note["item"]["item_id"], environ=env)
        set_project_labels(source, "alpha", ["portable"])
        monkeypatch.setenv("TQMEMORY_SECRETS_PASSPHRASE", "isolated-transfer-test-passphrase")
        assert set_secret_impl("transfer-check", "temporary-secret", environ=env)["status"] == "ok"

        snapshot = create_snapshot(source)
        shutil.copytree(snapshot, restored)
        restored_env = {
            "TQMEMORY_HOME": str(restored),
            ENV_ACTIVE_PROJECT_ID: "alpha",
        }
        project, _ = build_runtime_context(cwd=caller, environ=restored_env)
        assert project.project_id == "alpha"
        assert project.identity_kind == "selected_project"
        assert list_projects(restored)[0]["labels"] == ["portable"]
        project_results = semantic_search_impl(
            "Portable project note", scope="project", cwd=caller, environ=restored_env,
        )
        global_results = semantic_search_impl(
            "Portable global note", scope="global", cwd=caller, environ=restored_env,
        )
        assert project_note["item"]["item_id"] in {item["item_id"] for item in project_results["items"]}
        assert global_note["item"]["item_id"] in {item["item_id"] for item in global_results["items"]}
        assert get_secret_impl("transfer-check", cwd=caller, environ=restored_env)["secret_value"] == "temporary-secret"

    assert not Path(temporary).exists()


def test_binding_change_before_lock_fails_without_rerouting(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    alias = tmp_path / "alias"
    _seed(storage, original, "alpha", "Alpha")
    alias.mkdir()
    bind_project(storage, "alpha", alias)

    class ChangingLocks(ScopeLocks):
        @contextmanager
        def hold(self, keys, *, timeout, tool):
            assert GLOBAL_LOCK_KEY in keys
            unbind_project(storage, alias)
            with super().hold(keys, timeout=timeout, tool=tool):
                yield

    dispatcher = make_local_dispatcher(
        default_cwd=alias,
        default_environ={"TQMEMORY_HOME": str(storage)},
        scope_locks=ChangingLocks(),
    )
    with pytest.raises(ValueError, match="Project binding changed"):
        dispatcher("server_info", {})


def test_projects_cli_lists_saved_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    storage = tmp_path / "memory"
    _seed(storage, tmp_path / "original", "alpha", "Alpha")
    set_project_labels(storage, "alpha", ["work"])
    monkeypatch.setenv("TQMEMORY_HOME", str(storage))

    assert cli_main(["projects"]) == 0
    assert "alpha  Alpha  labels: work" in capsys.readouterr().out


def test_stdio_binding_selects_only_current_client_context(tmp_path: Path) -> None:
    storage = tmp_path / "memory"
    original = tmp_path / "original"
    other = tmp_path / "other"
    alias = tmp_path / "alias"
    _seed(storage, original, "alpha", "Alpha")
    _seed(storage, other, "beta", "Beta")
    alias.mkdir()
    original_env = _seed_existing_env(storage, original)
    project_note = remember_note_impl(
        "Project folder continuity", "Available from the second folder.",
        kind="decision", environ=original_env,
    )
    global_note = remember_note_impl(
        "Shared global continuity", "Available across local projects.",
        kind="pattern", environ=original_env,
    )
    promote_note_impl(global_note["item"]["item_id"], environ=original_env)

    async def probe() -> None:
        env = {
            **os.environ,
            "TQMEMORY_HOME": str(storage),
            "TQMEMORY_DAEMON_DISABLE": "1",
            "TQMEMORY_WORKSPACE_DISABLE": "1",
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "turbo_memory_mcp", "serve"],
            cwd=other,
            env=env,
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()

            def payload(result):
                return result.structuredContent or json.loads(result.content[0].text)

            projects = payload(await client.call_tool("list_projects"))
            assert {project["project_id"] for project in projects["projects"]} == {"alpha", "beta"}
            selected = payload(await client.call_tool("select_project", {"project_id": "alpha"}))
            assert selected["selection"] == "explicit"
            assert payload(await client.call_tool("server_info"))["current_project"]["project_id"] == "alpha"
            async with stdio_client(params) as (second_read, second_write), ClientSession(second_read, second_write) as second_client:
                await second_client.initialize()
                assert payload(await second_client.call_tool("server_info"))["current_project"]["project_id"] == "beta"
                assert payload(await client.call_tool("server_info"))["current_project"]["project_id"] == "alpha"
            project_results = payload(await client.call_tool("semantic_search", {
                "query": "Project folder continuity", "scope": "project",
            }))
            assert project_results["items"][0]["item_id"] == project_note["item"]["item_id"]
            assert payload(await client.call_tool("select_project"))["project_id"] == "beta"
            assert payload(await client.call_tool("server_info"))["current_project"]["project_id"] == "beta"
            missing = await client.call_tool("select_project", {"project_id": "missing"})
            assert missing.isError
            assert payload(await client.call_tool("server_info"))["current_project"]["project_id"] == "beta"
            bound = payload(await client.call_tool("bind_project", {"project_id": "alpha", "path": str(alias)}))
            assert bound["project_id"] == "alpha"
            assert payload(await client.call_tool("server_info"))["current_project"]["project_id"] == "alpha"
            project_results = payload(await client.call_tool("semantic_search", {
                "query": "Project folder continuity", "scope": "project",
            }))
            assert project_results["items"][0]["item_id"] == project_note["item"]["item_id"]
            global_results = payload(await client.call_tool("semantic_search", {
                "query": "Shared global continuity", "scope": "global",
            }))
            assert global_results["items"][0]["item_id"] == global_note["item"]["item_id"]
            assert unbind_project(storage, alias)
            stale = await client.call_tool("server_info")
            assert stale.isError
            assert "Selected folder no longer resolves" in stale.content[0].text
            assert payload(await client.call_tool("unbind_project"))["removed"] is False
            assert payload(await client.call_tool("server_info"))["current_project"]["project_id"] == "beta"

    asyncio.run(probe())
