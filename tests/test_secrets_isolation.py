"""Integration tests for the hard isolation between the secrets vault and
the ingestion / lint / retrieval paths (Phase 9 Wave 2)."""

from __future__ import annotations

from pathlib import Path

import keyring
import keyring.backend
import pytest

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.ingestion import _iter_markdown_files as ingest_iter
from turbo_memory_mcp.ingestion import index_paths_with_sync_plan
from turbo_memory_mcp.knowledge_lint import (
    _iter_markdown_files as lint_iter,
    lint_knowledge_base,
)
from turbo_memory_mcp.secrets.paths import is_inside_secrets_storage
from turbo_memory_mcp.store import MemoryStore


class _InMemoryKeyring(keyring.backend.KeyringBackend):
    priority = 10  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


@pytest.fixture
def in_memory_keyring():
    original = keyring.get_keyring()
    keyring.set_keyring(_InMemoryKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(original)


def _project_identity(project_root: Path) -> ProjectIdentity:
    return ProjectIdentity(
        project_id="iso-test-id",
        project_name="Isolation Test",
        project_root=project_root,
        identity_source="local/isotest",
        identity_kind="local_path",
        remote_url=None,
    )


@pytest.fixture
def store(tmp_path):
    storage_root = tmp_path / "store"
    s = MemoryStore(_project_identity(tmp_path / "repo"), storage_root=storage_root)
    s.ensure_layout()
    s.ensure_markdown_layout()
    return s


# --- helper unit -----------------------------------------------------------


def test_is_inside_secrets_storage_matches_only_secrets_subtree(tmp_path):
    storage_root = tmp_path / "store"
    secrets_dir = storage_root / "projects" / "p1" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    other_dir = storage_root / "projects" / "p1" / "notes"
    other_dir.mkdir(parents=True, exist_ok=True)

    assert is_inside_secrets_storage(secrets_dir, storage_root) is True
    assert is_inside_secrets_storage(secrets_dir / "vault.tqv", storage_root) is True
    assert is_inside_secrets_storage(secrets_dir / "audit.jsonl", storage_root) is True

    assert is_inside_secrets_storage(other_dir, storage_root) is False
    assert is_inside_secrets_storage(storage_root, storage_root) is False
    # An unrelated path that happens to contain "secrets" in its name MUST NOT
    # match (only the canonical projects/<id>/secrets/ layout does).
    unrelated = tmp_path / "my-secrets-notes"
    unrelated.mkdir()
    assert is_inside_secrets_storage(unrelated, storage_root) is False


# --- ingestion boundary ----------------------------------------------------


def test_index_paths_refuses_secrets_dir(store, tmp_path):
    secrets_dir = store.storage_root / "projects" / "iso-test-id" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "leak.md").write_text("# this should never be indexed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="secrets vault"):
        index_paths_with_sync_plan(
            store,
            paths=[str(secrets_dir)],
            mode="full",
            cwd=tmp_path / "repo",
        )


def test_index_paths_skips_secrets_subtree_under_parent(store, tmp_path):
    """If the user registers a parent path that contains secrets/, the walker
    must still skip files inside the secrets subtree."""
    secrets_dir = store.storage_root / "projects" / "iso-test-id" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "sneaky.md").write_text("# sentinel content\n", encoding="utf-8")

    # Walk the storage_root explicitly via the iter helper; it must omit
    # anything under secrets/.
    files = ingest_iter(store.storage_root, storage_root=store.storage_root)
    for f in files:
        assert "secrets" not in f.parts, f"secrets file leaked into ingest walk: {f}"


# --- lint boundary ---------------------------------------------------------


def test_lint_refuses_secrets_dir(store, tmp_path):
    secrets_dir = store.storage_root / "projects" / "iso-test-id" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "leak.md").write_text("# nope\n", encoding="utf-8")

    with pytest.raises(ValueError, match="secrets vault"):
        lint_knowledge_base(
            store,
            paths=[str(secrets_dir)],
            cwd=tmp_path / "repo",
        )


def test_lint_iter_markdown_files_skips_secrets_subtree(store):
    secrets_dir = store.storage_root / "projects" / "iso-test-id" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "shadow.md").write_text("# secret-shaped content\n", encoding="utf-8")
    files = lint_iter(store.storage_root, storage_root=store.storage_root)
    for f in files:
        assert "secrets" not in f.parts


# --- end-to-end: planted-secret leak guard ---------------------------------


def test_secret_value_never_appears_in_index_walk(
    store, in_memory_keyring, tmp_path, monkeypatch
):
    """Plant a sentinel string inside vault.tqv; verify that even a
    pathological index_paths attempt on the storage_root never picks up the
    secrets/ subtree (which contains the encrypted blob)."""
    from turbo_memory_mcp.secrets import SecretsStore
    from turbo_memory_mcp.secrets.keyresolver import ENV_PASSPHRASE

    monkeypatch.setenv(ENV_PASSPHRASE, "isolation-test")
    vault = SecretsStore(store.storage_root, store.project.project_id)
    vault.provision()
    sentinel = "sentinel_isolation_phrase_42"
    vault.set("planted", sentinel)
    assert vault.vault_path.exists()

    # Walk the storage_root through the ingest walker.
    files = ingest_iter(store.storage_root, storage_root=store.storage_root)
    for f in files:
        if f.suffix == ".md":
            assert sentinel not in f.read_text(encoding="utf-8", errors="replace")
        # vault.tqv / meta.json / audit.jsonl must not be in the walk at all.
        assert "secrets" not in f.parts
