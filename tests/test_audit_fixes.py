"""Regression tests for the v0.18.0 audit fixes.

Covers: client-id path-traversal guard, markdown derived-cache quarantine,
block-id dot-directory collision, telemetry milestone dedup, and resilient
lint on non-UTF-8 input.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import turbo_memory_mcp.identity as idmod
from turbo_memory_mcp.identity import ProjectIdentity, resolve_project_identity
from turbo_memory_mcp.markdown_parser import build_block_id
from turbo_memory_mcp.server import lint_knowledge_base_impl
from turbo_memory_mcp.store import MemoryStore, PROJECT_SCOPE
from turbo_memory_mcp.telemetry import record_semantic_search_usage


def _build_store(tmp_path: Path) -> MemoryStore:
    identity = ProjectIdentity(
        project_id="proj1234567890abc",
        project_name="Turbo Quant Memory",
        project_root=tmp_path / "repo",
        identity_source="github.com/example/turbo-quant-memory",
        identity_kind="git_remote",
    )
    return MemoryStore(identity, storage_root=tmp_path / "central-store")


# --------------------------------------------------------------------------- #
# Security: client-supplied id path-traversal guard
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "bad_id",
    ["../evil", "../../etc/passwd", "a/b", "..", ".", "sub/dir", "back\\slash", ""],
)
def test_project_note_path_rejects_traversal_ids(tmp_path: Path, bad_id: str) -> None:
    store = _build_store(tmp_path)
    with pytest.raises(ValueError):
        store.project_note_path(bad_id)


def test_read_note_with_traversal_id_raises_valueerror_not_traversal(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    # Must fail closed on the id, never resolve to a file outside the notes dir.
    with pytest.raises(ValueError):
        store.read_project_note("../../../../../../etc/hosts")
    with pytest.raises(ValueError):
        store.read_global_note("../secrets/vault")


def test_project_dir_rejects_traversal_project_id(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    with pytest.raises(ValueError):
        store.project_dir("../../escape")
    with pytest.raises(ValueError):
        store.project_markdown_block_path("../../escape")


def test_safe_ids_still_resolve(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    # A normal generated note id (uuid hex) and a hyphenated project id both pass.
    assert store.project_note_path("abcdef1234567890").name == "abcdef1234567890.json"
    assert store.project_dir("project-alpha").name == "project-alpha"
    written = store.write_project_note("Title", "content", note_kind="lesson")
    assert store.read_project_note(written["note_id"])["title"] == "Title"


# --------------------------------------------------------------------------- #
# Robustness: one corrupt markdown-cache file must not take down retrieval
# --------------------------------------------------------------------------- #

def test_list_markdown_blocks_skips_corrupt_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _build_store(tmp_path)
    store.write_markdown_block(
        {
            "block_id": "mdblk-good0000000001",
            "root_id": "root-1",
            "source_path": "a.md",
            "heading_path": [],
            "chunk_index": 0,
            "content_raw": "hello world",
            "block_checksum": "chk-a",
            "source_checksum": "src-a",
        }
    )
    corrupt = store.project_markdown_blocks_dir() / "mdblk-corrupt.json"
    corrupt.write_text("{ not valid json", encoding="utf-8")

    blocks = store.list_markdown_blocks()

    assert [b["block_id"] for b in blocks] == ["mdblk-good0000000001"]
    assert "skipping unreadable markdown block" in capsys.readouterr().err


def test_list_markdown_roots_skips_corrupt_file(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    roots_dir = store.project_markdown_roots_dir()
    roots_dir.mkdir(parents=True, exist_ok=True)
    (roots_dir / "broken.json").write_text("}{", encoding="utf-8")
    # Must not raise — the corrupt root is skipped.
    assert store.list_markdown_roots() == []


# --------------------------------------------------------------------------- #
# Correctness: build_block_id must not collapse dot-leading directories
# --------------------------------------------------------------------------- #

def test_build_block_id_does_not_collide_dot_directory() -> None:
    dot_dir = build_block_id("root", ".github/workflows/x.md", ["H"], 0)
    plain = build_block_id("root", "github/workflows/x.md", ["H"], 0)
    assert dot_dir != plain


# --------------------------------------------------------------------------- #
# Correctness: a crossed milestone is announced exactly once (dedup persists)
# --------------------------------------------------------------------------- #

def test_search_milestone_announced_once(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    payload = {"items": []}
    fired = []
    for _ in range(12):
        milestone = record_semantic_search_usage(
            store,
            project_id=store.project.project_id,
            project_name=store.project.project_name,
            response_payload=payload,
            raw_source_bytes=0,  # keep token savings at 0 to isolate the search milestone
            environ={},
        )
        if milestone is not None:
            fired.append(milestone)

    # The 10-retrievals milestone fires once; the buggy pre-fix code re-fired it
    # on every subsequent search because the dedup marker was never persisted.
    assert [m["milestone"] for m in fired] == [10]
    assert fired[0]["kind"] == "retrievals"


# --------------------------------------------------------------------------- #
# Robustness: a non-UTF-8 markdown file must not crash the linter
# --------------------------------------------------------------------------- #

def test_lint_survives_non_utf8_file(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    docs = project_root / "docs"
    docs.mkdir(parents=True)
    (docs / "ok.md").write_text("# OK\n\nclean.", encoding="utf-8")
    (docs / "bad.md").write_bytes(b"# Bad\n\n\xff\xfe not utf-8 \x80\x81")
    env = {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "project-alpha",
        "TQMEMORY_PROJECT_NAME": "Alpha Project",
    }

    payload = lint_knowledge_base_impl(paths=[str(docs)], max_issues=50, cwd=project_root, environ=env)

    assert payload["status"] == "ok"


# --------------------------------------------------------------------------- #
# Performance: project identity is cached (no git re-fork) but stays isolated
# --------------------------------------------------------------------------- #

def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    return path


def test_identity_cache_avoids_second_git_fork(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    idmod._clear_identity_cache()
    repo = _init_repo(tmp_path / "repo")

    calls = {"n": 0}
    real = idmod._run_git_command

    def _counting(cwd, *args):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return real(cwd, *args)

    monkeypatch.setattr(idmod, "_run_git_command", _counting)

    first = resolve_project_identity(cwd=repo, environ={})
    forks_after_first = calls["n"]
    second = resolve_project_identity(cwd=repo, environ={})

    assert forks_after_first >= 1  # the uncached call really forked git
    assert calls["n"] == forks_after_first  # the cached call forked git zero more times
    assert second == first


def test_identity_cache_is_isolated_per_repo(tmp_path: Path) -> None:
    idmod._clear_identity_cache()
    a = resolve_project_identity(cwd=tmp_path / "repo-a", environ={})
    b = resolve_project_identity(cwd=tmp_path / "repo-b", environ={})
    # Distinct repos never share a cached identity — the issue-#1 isolation
    # property must survive at the identity layer too.
    assert a.project_id != b.project_id
    assert a.project_root != b.project_root


def test_identity_cache_keys_on_env_override(tmp_path: Path) -> None:
    idmod._clear_identity_cache()
    repo = tmp_path / "repo"
    repo.mkdir()
    alpha = resolve_project_identity(cwd=repo, environ={"TQMEMORY_PROJECT_ID": "alpha"})
    beta = resolve_project_identity(cwd=repo, environ={"TQMEMORY_PROJECT_ID": "beta"})
    # Same cwd but different forwarded identity env must NOT collide in the cache.
    assert alpha.project_id == "alpha"
    assert beta.project_id == "beta"


def test_identity_cache_invalidates_when_git_remote_changes(tmp_path: Path) -> None:
    idmod._clear_identity_cache()
    repo = _init_repo(tmp_path / "repo")
    env: dict[str, str] = {}

    before = resolve_project_identity(cwd=repo, environ=env)
    assert before.identity_kind == "repo_path"

    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/x.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    after = resolve_project_identity(cwd=repo, environ=env)
    # The git-config mtime fingerprint must invalidate the cache immediately.
    assert after.identity_kind == "git_remote"

