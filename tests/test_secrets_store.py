"""Unit tests for ``turbo_memory_mcp.secrets.store``."""

from __future__ import annotations

import json
import os
from pathlib import Path

import keyring
import keyring.backend
import pytest
from keyring.backends import fail as _fail_backend

from turbo_memory_mcp.secrets.keyresolver import ENV_PASSPHRASE
from turbo_memory_mcp.secrets.store import SecretsStore

PROJECT = "test-store-project"


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
def fail_keyring():
    original = keyring.get_keyring()
    keyring.set_keyring(_fail_backend.Keyring())
    try:
        yield
    finally:
        keyring.set_keyring(original)


@pytest.fixture
def env_passphrase(monkeypatch):
    monkeypatch.setenv(ENV_PASSPHRASE, "test-store-passphrase")


def _make_store(tmp_path: Path) -> SecretsStore:
    (tmp_path / "projects" / PROJECT).mkdir(parents=True, exist_ok=True)
    return SecretsStore(tmp_path, PROJECT)


# --- provisioning ---


def test_provision_with_env_creates_dir_meta_and_vault(
    tmp_path, env_passphrase, in_memory_keyring
):
    s = _make_store(tmp_path)
    s.provision()
    assert s.secrets_dir.is_dir()
    assert s.vault_path.exists()
    assert s.meta_path.exists()
    meta = json.loads(s.meta_path.read_text())
    assert meta["vault_initialized"] is True
    assert meta["key_mode"] == "env"
    assert meta["kdf"] == "argon2id"


def test_provision_is_idempotent(tmp_path, env_passphrase, in_memory_keyring):
    s = _make_store(tmp_path)
    s.provision()
    first_meta = json.loads(s.meta_path.read_text())
    first_blob = s.vault_path.read_bytes()
    s.provision()
    second_meta = json.loads(s.meta_path.read_text())
    second_blob = s.vault_path.read_bytes()
    # Vault blob untouched on re-provision (file existed, skipped).
    assert first_blob == second_blob
    assert first_meta["created_at"] == second_meta["created_at"]


def test_provision_writes_stub_meta_when_key_unavailable(
    tmp_path, fail_keyring, monkeypatch
):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    s = _make_store(tmp_path)
    s.provision()
    assert s.secrets_dir.is_dir()
    assert not s.vault_path.exists()
    meta = json.loads(s.meta_path.read_text())
    assert meta["vault_initialized"] is False
    assert meta["key_mode"] == "unavailable"


# --- happy-path CRUD ---


def test_set_get_roundtrip(tmp_path, env_passphrase, in_memory_keyring):
    s = _make_store(tmp_path)
    s.provision()
    s.set("db-dsn", "postgresql://user:pass@host:5432/db")
    assert s.get("db-dsn") == "postgresql://user:pass@host:5432/db"


def test_set_updates_existing_entry(
    tmp_path, env_passphrase, in_memory_keyring
):
    s = _make_store(tmp_path)
    s.provision()
    s.set("api-key", "v1")
    s.set("api-key", "v2")
    assert s.get("api-key") == "v2"


def test_list_names_sorted_no_values(
    tmp_path, env_passphrase, in_memory_keyring
):
    s = _make_store(tmp_path)
    s.provision()
    s.set("zeta", "z")
    s.set("alpha", "a")
    s.set("mu", "m")
    assert s.list_names() == ["alpha", "mu", "zeta"]


def test_delete_existing_returns_true(
    tmp_path, env_passphrase, in_memory_keyring
):
    s = _make_store(tmp_path)
    s.provision()
    s.set("ephemeral", "value")
    assert s.delete("ephemeral") is True
    assert s.get("ephemeral") is None
    assert s.list_names() == []


def test_delete_missing_returns_false(
    tmp_path, env_passphrase, in_memory_keyring
):
    s = _make_store(tmp_path)
    s.provision()
    assert s.delete("nonexistent") is False


def test_persistence_across_instances(
    tmp_path, env_passphrase, in_memory_keyring
):
    s1 = _make_store(tmp_path)
    s1.provision()
    s1.set("ssh-host", "prod.example.com")
    s2 = SecretsStore(tmp_path, PROJECT)
    assert s2.get("ssh-host") == "prod.example.com"


# --- migration / lazy-init path ---


def test_set_initializes_after_stub_meta(tmp_path, monkeypatch):
    """Simulates the migration scenario: provision with no key, then env appears."""
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    original = keyring.get_keyring()
    keyring.set_keyring(_fail_backend.Keyring())
    try:
        s = _make_store(tmp_path)
        s.provision()  # stub meta only
        assert not s.vault_path.exists()
    finally:
        keyring.set_keyring(original)

    # Env passphrase appears for later session.
    keyring.set_keyring(_InMemoryKeyring())
    try:
        monkeypatch.setenv(ENV_PASSPHRASE, "later-passphrase")
        s.set("late", "value")
        assert s.vault_path.exists()
        assert s.get("late") == "value"
        meta = json.loads(s.meta_path.read_text())
        assert meta["vault_initialized"] is True
        assert meta["key_mode"] == "env"
    finally:
        keyring.set_keyring(original)


# --- empty / missing semantics ---


def test_get_missing_name_returns_none(
    tmp_path, env_passphrase, in_memory_keyring
):
    s = _make_store(tmp_path)
    s.provision()
    s.set("known", "value")
    assert s.get("unknown") is None


def test_get_on_empty_vault_returns_none(
    tmp_path, env_passphrase, in_memory_keyring
):
    s = _make_store(tmp_path)
    s.provision()
    assert s.get("anything") is None
    assert s.list_names() == []


def test_list_on_uninitialized_returns_empty(tmp_path, fail_keyring, monkeypatch):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    s = _make_store(tmp_path)
    s.provision()  # stub meta, no vault file
    assert s.list_names() == []
    assert s.get("any.name") is None
    assert s.delete("any.name") is False


# --- input validation ---


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "has space",
        "has/slash",
        "has:colon",
        "has@at",
        "x" * 129,
        "пароль",  # non-ASCII rejected by the regex on purpose
    ],
)
def test_set_rejects_invalid_name(
    tmp_path, env_passphrase, in_memory_keyring, bad_name
):
    s = _make_store(tmp_path)
    s.provision()
    with pytest.raises(ValueError):
        s.set(bad_name, "value")


def test_set_rejects_non_string_value(
    tmp_path, env_passphrase, in_memory_keyring
):
    s = _make_store(tmp_path)
    s.provision()
    with pytest.raises(TypeError):
        s.set("name", 123)  # type: ignore[arg-type]


def test_constructor_rejects_empty_project_id(tmp_path):
    with pytest.raises(ValueError):
        SecretsStore(tmp_path, "")


# --- file permissions ---


def test_file_permissions(tmp_path, env_passphrase, in_memory_keyring):
    s = _make_store(tmp_path)
    s.provision()
    s.set("k", "v")
    assert (os.stat(s.secrets_dir).st_mode & 0o777) == 0o700
    assert (os.stat(s.vault_path).st_mode & 0o777) == 0o600
    assert (os.stat(s.meta_path).st_mode & 0o777) == 0o600


# --- crypto isolation: tampering ---


def test_tampered_vault_file_raises(
    tmp_path, env_passphrase, in_memory_keyring
):
    from cryptography.exceptions import InvalidTag

    s = _make_store(tmp_path)
    s.provision()
    s.set("ok", "value")
    blob = bytearray(s.vault_path.read_bytes())
    blob[20] ^= 0x01
    s.vault_path.write_bytes(bytes(blob))
    with pytest.raises(InvalidTag):
        s.get("ok")
