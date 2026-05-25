"""Tests for the SECRETS subsystem migration (Phase 9 Wave 2)."""

from __future__ import annotations

import json
from pathlib import Path

import keyring
import keyring.backend
import pytest
from keyring.backends import fail as _fail_backend

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.migrations import (
    Subsystem,
    apply_pending,
    clear_registry,
    detect_status,
    migration,
)
from turbo_memory_mcp.migrations.upgrades import upgrade_secrets_v1_to_v2
from turbo_memory_mcp.secrets.keyresolver import ENV_PASSPHRASE
from turbo_memory_mcp.store import (
    SECRETS_FORMAT_VERSION,
    MemoryStore,
)


def _project_identity(project_root: Path) -> ProjectIdentity:
    return ProjectIdentity(
        project_id="sec-mig-test-id",
        project_name="Secrets Migration Test",
        project_root=project_root,
        identity_source="local/secmigtest",
        identity_kind="local_path",
        remote_url=None,
    )


@pytest.fixture
def store(tmp_path):
    storage_root = tmp_path / "store"
    s = MemoryStore(
        _project_identity(tmp_path / "repo"), storage_root=storage_root
    )
    s.ensure_layout()
    return s


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


@pytest.fixture
def env_passphrase(monkeypatch):
    monkeypatch.setenv(ENV_PASSPHRASE, "test-migration-passphrase")


@pytest.fixture
def isolated_registry(monkeypatch, tmp_path):
    clear_registry()
    monkeypatch.setenv(
        "TQMEMORY_MIGRATION_LOG_PATH", str(tmp_path / "migration.log")
    )
    yield
    clear_registry()


def _make_projects(storage_root: Path, ids: list[str]) -> None:
    for pid in ids:
        (storage_root / "projects" / pid).mkdir(parents=True, exist_ok=True)


# --- direct upgrade function ---


def test_migration_provisions_each_existing_project(
    store, env_passphrase, in_memory_keyring
):
    _make_projects(store.storage_root, ["alpha", "beta", "gamma"])
    upgrade_secrets_v1_to_v2(store)
    for pid in ["alpha", "beta", "gamma"]:
        secrets_dir = store.storage_root / "projects" / pid / "secrets"
        assert secrets_dir.is_dir()
        assert (secrets_dir / "meta.json").exists()
        assert (secrets_dir / "vault.tqv").exists()


def test_migration_idempotent(store, env_passphrase, in_memory_keyring):
    _make_projects(store.storage_root, ["alpha"])
    upgrade_secrets_v1_to_v2(store)
    secrets_dir = store.storage_root / "projects" / "alpha" / "secrets"
    first_vault = (secrets_dir / "vault.tqv").read_bytes()
    first_meta = json.loads((secrets_dir / "meta.json").read_text())

    upgrade_secrets_v1_to_v2(store)
    second_vault = (secrets_dir / "vault.tqv").read_bytes()
    second_meta = json.loads((secrets_dir / "meta.json").read_text())

    # provision() leaves an existing vault file untouched (byte-identical).
    assert first_vault == second_vault
    # created_at preserved across re-provision.
    assert first_meta["created_at"] == second_meta["created_at"]


def test_migration_with_no_projects_root_does_nothing(
    tmp_path, env_passphrase, in_memory_keyring
):
    storage_root = tmp_path / "empty-store"
    storage_root.mkdir(parents=True)
    minimal_store = MemoryStore(
        _project_identity(tmp_path / "repo"), storage_root=storage_root
    )
    # Should not raise even with no projects/ subdir.
    upgrade_secrets_v1_to_v2(minimal_store)
    assert not (storage_root / "projects").exists()


def test_migration_skips_non_directory_entries(
    store, env_passphrase, in_memory_keyring
):
    projects_root = store.storage_root / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    (projects_root / "real-project").mkdir()
    (projects_root / "stray.txt").write_text("not a project dir", encoding="utf-8")

    upgrade_secrets_v1_to_v2(store)
    assert (projects_root / "real-project" / "secrets" / "vault.tqv").exists()
    # Stray file is left alone.
    assert (projects_root / "stray.txt").is_file()


def test_migration_with_no_key_writes_stub_only(store, monkeypatch):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    original = keyring.get_keyring()
    keyring.set_keyring(_fail_backend.Keyring())
    try:
        _make_projects(store.storage_root, ["headless-1", "headless-2"])
        upgrade_secrets_v1_to_v2(store)
        for pid in ["headless-1", "headless-2"]:
            secrets_dir = store.storage_root / "projects" / pid / "secrets"
            assert secrets_dir.is_dir()
            assert (secrets_dir / "meta.json").exists()
            assert not (secrets_dir / "vault.tqv").exists()
            meta = json.loads((secrets_dir / "meta.json").read_text())
            assert meta["vault_initialized"] is False
            assert meta["key_mode"] == "unavailable"
    finally:
        keyring.set_keyring(original)


# --- integration via apply_pending ---


def test_detect_status_fresh_install_no_projects_is_v0(
    tmp_path, isolated_registry
):
    """No project dirs at all -> v0 (fresh, no migration warning)."""
    storage_root = tmp_path / "fresh"
    storage_root.mkdir(parents=True)
    s = MemoryStore(_project_identity(tmp_path / "repo"), storage_root=storage_root)
    statuses = detect_status(s)
    assert statuses[Subsystem.SECRETS].current_version == 0
    assert not statuses[Subsystem.SECRETS].needs_upgrade


def test_detect_status_upgrade_from_pre_v07_is_v1(
    store, isolated_registry
):
    """Existing install (projects/ has subdirs) but no manifest -> v1 (pending)."""
    _make_projects(store.storage_root, ["existing-proj"])
    # Register the migration so the chain is non-empty.
    migration(
        Subsystem.SECRETS,
        from_version=1,
        to_version=2,
        description="test wire-up",
    )(upgrade_secrets_v1_to_v2)
    statuses = detect_status(store)
    assert statuses[Subsystem.SECRETS].current_version == 1
    assert statuses[Subsystem.SECRETS].latest_version == SECRETS_FORMAT_VERSION
    assert statuses[Subsystem.SECRETS].needs_upgrade


def test_apply_pending_runs_secrets_migration_and_bumps_manifest(
    store, env_passphrase, in_memory_keyring, isolated_registry
):
    _make_projects(store.storage_root, ["proj-A", "proj-B"])
    migration(
        Subsystem.SECRETS,
        from_version=1,
        to_version=2,
        description="test wire-up",
    )(upgrade_secrets_v1_to_v2)

    before = detect_status(store)
    assert before[Subsystem.SECRETS].current_version == 1
    assert before[Subsystem.SECRETS].latest_version == SECRETS_FORMAT_VERSION
    assert before[Subsystem.SECRETS].needs_upgrade

    outcomes = apply_pending(
        store, subsystems=[Subsystem.SECRETS], snapshot=False
    )
    assert all(o.success for o in outcomes)

    manifest = store.read_secrets_manifest()
    assert manifest is not None
    assert manifest["format_version"] == SECRETS_FORMAT_VERSION
    assert "updated_at" in manifest

    for pid in ["proj-A", "proj-B"]:
        assert (
            store.storage_root / "projects" / pid / "secrets" / "vault.tqv"
        ).exists()

    after = detect_status(store)
    assert after[Subsystem.SECRETS].current_version == SECRETS_FORMAT_VERSION
    assert not after[Subsystem.SECRETS].needs_upgrade


# --- store helpers ---


def test_secrets_manifest_path_at_storage_root(store):
    assert store.secrets_manifest_path() == store.storage_root / "secrets-manifest.json"


def test_read_secrets_manifest_none_when_missing(store):
    assert store.read_secrets_manifest() is None
