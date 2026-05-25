"""Unit tests for ``turbo_memory_mcp.secrets.keyresolver``.

Uses in-memory keyring backends so no real OS keychain is touched.
"""

from __future__ import annotations

import base64

import keyring
import keyring.backend
import keyring.errors
import pytest
from keyring.backends import fail as _fail_backend

from turbo_memory_mcp.secrets.crypto import KEY_SIZE, derive_key_from_passphrase
from turbo_memory_mcp.secrets.keyresolver import (
    ENV_PASSPHRASE,
    SERVICE_NAME,
    KeyResolutionMode,
    MasterKeyUnavailable,
    resolve_master_key,
)

PROJECT = "test-project-1"


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


class _ReadOnlyKeyring(_InMemoryKeyring):
    """Pretends to be present but rejects writes — e.g. a locked keychain."""

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.PasswordSetError("backend is read-only for the test")


@pytest.fixture
def in_memory_keyring():
    backend = _InMemoryKeyring()
    original = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(original)


@pytest.fixture
def fail_keyring():
    backend = _fail_backend.Keyring()
    original = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(original)


@pytest.fixture
def readonly_keyring():
    backend = _ReadOnlyKeyring()
    original = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(original)


def test_env_always_wins_even_when_keyring_has_entry(in_memory_keyring, monkeypatch):
    in_memory_keyring._store[(SERVICE_NAME, f"secrets-master-{PROJECT}")] = (
        base64.b64encode(b"\xff" * KEY_SIZE).decode("ascii")
    )
    monkeypatch.setenv(ENV_PASSPHRASE, "explicit-user-passphrase")

    key, mode = resolve_master_key(PROJECT)
    assert mode is KeyResolutionMode.ENV
    assert key == derive_key_from_passphrase("explicit-user-passphrase", PROJECT)
    assert key != b"\xff" * KEY_SIZE


def test_env_separates_projects(in_memory_keyring, monkeypatch):
    monkeypatch.setenv(ENV_PASSPHRASE, "same-passphrase")
    k_a, _ = resolve_master_key("project-A")
    k_b, _ = resolve_master_key("project-B")
    assert k_a != k_b


def test_existing_keyring_entry_used_when_no_env(in_memory_keyring, monkeypatch):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    stored_key = b"\xab" * KEY_SIZE
    in_memory_keyring._store[(SERVICE_NAME, f"secrets-master-{PROJECT}")] = (
        base64.b64encode(stored_key).decode("ascii")
    )

    key, mode = resolve_master_key(PROJECT)
    assert mode is KeyResolutionMode.KEYRING_EXISTING
    assert key == stored_key


def test_keyring_bootstrap_when_writable_and_empty(in_memory_keyring, monkeypatch):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    assert in_memory_keyring._store == {}

    key, mode = resolve_master_key(PROJECT)
    assert mode is KeyResolutionMode.KEYRING_BOOTSTRAPPED
    assert len(key) == KEY_SIZE
    stored = in_memory_keyring._store[(SERVICE_NAME, f"secrets-master-{PROJECT}")]
    assert base64.b64decode(stored) == key


def test_second_call_after_bootstrap_uses_existing(in_memory_keyring, monkeypatch):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    first_key, first_mode = resolve_master_key(PROJECT)
    second_key, second_mode = resolve_master_key(PROJECT)

    assert first_mode is KeyResolutionMode.KEYRING_BOOTSTRAPPED
    assert second_mode is KeyResolutionMode.KEYRING_EXISTING
    assert first_key == second_key


def test_fail_keyring_raises_master_key_unavailable(fail_keyring, monkeypatch):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    with pytest.raises(MasterKeyUnavailable) as excinfo:
        resolve_master_key(PROJECT)
    msg = str(excinfo.value)
    assert ENV_PASSPHRASE in msg
    assert "keyring set" in msg


def test_readonly_keyring_raises_master_key_unavailable(readonly_keyring, monkeypatch):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    with pytest.raises(MasterKeyUnavailable) as excinfo:
        resolve_master_key(PROJECT)
    assert "bootstrap" in str(excinfo.value).lower()


def test_corrupted_keyring_entry_raises_master_key_unavailable(
    in_memory_keyring, monkeypatch
):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    in_memory_keyring._store[(SERVICE_NAME, f"secrets-master-{PROJECT}")] = (
        base64.b64encode(b"too short").decode("ascii")
    )
    with pytest.raises(MasterKeyUnavailable) as excinfo:
        resolve_master_key(PROJECT)
    assert "expected 32" in str(excinfo.value)


def test_invalid_base64_keyring_entry_raises_master_key_unavailable(
    in_memory_keyring, monkeypatch
):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    in_memory_keyring._store[(SERVICE_NAME, f"secrets-master-{PROJECT}")] = "!!!not-base64!!!"
    with pytest.raises(MasterKeyUnavailable) as excinfo:
        resolve_master_key(PROJECT)
    assert "base64" in str(excinfo.value).lower()


def test_empty_project_id_rejected():
    with pytest.raises(ValueError):
        resolve_master_key("")
