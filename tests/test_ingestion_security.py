"""Security tests for ingestion confinement (audit S1/S2/S3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.ingestion import (
    _iter_markdown_files,
    _matches_ignore,
    _resolve_roots,
)
from turbo_memory_mcp.store import MemoryStore


def _store(root: Path, storage: Path) -> MemoryStore:
    ident = ProjectIdentity(
        project_id="ingsectest00001",
        project_name="Ingestion Security",
        project_root=root,
        identity_source="local/ingsec",
        identity_kind="local_path",
        remote_url=None,
    )
    s = MemoryStore(ident, storage_root=storage)
    s.ensure_layout()
    return s


# --- S1: symlink containment ---


def test_iter_skips_md_symlink_escaping_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "real.md").write_text("# real", encoding="utf-8")
    secret = tmp_path / "outside" / "secret.md"
    secret.parent.mkdir()
    secret.write_text("TOP SECRET", encoding="utf-8")
    link = root / "leak.md"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks not supported")

    names = {p.name for p in _iter_markdown_files(root)}
    assert "real.md" in names
    assert "leak.md" not in names  # escaping symlink skipped (no exfil)


def test_iter_keeps_in_tree_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    target = root / "docs" / "a.md"
    target.write_text("# a", encoding="utf-8")
    link = root / "b.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks not supported")

    names = {p.name for p in _iter_markdown_files(root)}
    assert "a.md" in names and "b.md" in names  # in-tree symlink allowed


# --- S2: root confinement + opt-in ---


def test_resolve_roots_rejects_external_absolute(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    store = _store(root, tmp_path / "store")
    external = tmp_path / "elsewhere"
    external.mkdir()
    with pytest.raises(ValueError):
        _resolve_roots(store, [str(external)], base_dir=root)


def test_resolve_roots_allows_external_with_optin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    store = _store(root, tmp_path / "store")
    external = tmp_path / "elsewhere"
    external.mkdir()
    monkeypatch.setenv("TQMEMORY_ALLOW_EXTERNAL_ROOTS", "1")
    roots = _resolve_roots(store, [str(external)], base_dir=root)
    assert len(roots) == 1


def test_resolve_roots_allows_in_project(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    store = _store(root, tmp_path / "store")
    roots = _resolve_roots(store, [str(root / "docs")], base_dir=root)
    assert len(roots) == 1


# --- S3: basename ignore ---


def test_matches_ignore_basename_nested() -> None:
    assert _matches_ignore("sub/dir/secrets.md", ["secrets.md"]) is True
    assert _matches_ignore("sub/dir/keep.md", ["secrets.md"]) is False
    # A pattern WITH a slash still anchors to the full relative path.
    assert _matches_ignore("a/b.md", ["x/b.md"]) is False
