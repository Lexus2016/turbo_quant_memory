"""M1: cross-process exclusive lock around vault read-modify-write.

The daemon (set_secret RPC) and a standalone `secret-set` CLI mutate the same
vault.tqv from different processes. Without a lock, an interleaved
read -> modify -> write loses one update. These tests assert that a write holds
an exclusive flock on a stable lock file for the whole RMW, so a competing
writer is forced to wait instead of clobbering.
"""

from __future__ import annotations

import fcntl
import os

import keyring
import keyring.backend
import pytest

from turbo_memory_mcp.secrets.keyresolver import ENV_PASSPHRASE
from turbo_memory_mcp.secrets.store import SecretsStore

PROJECT = "test-lock-project"
LOCK_NAME = ".vault.lock"


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


def _make_store(tmp_path) -> SecretsStore:
    (tmp_path / "projects" / PROJECT).mkdir(parents=True, exist_ok=True)
    return SecretsStore(tmp_path, PROJECT)


def _probe_lock_held(lock_file) -> bool:
    """True iff an exclusive flock on lock_file is currently held by someone
    (a non-blocking acquire from a separate fd fails). flock conflicts across
    distinct open descriptions even within one process, so this works in-test."""
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)


def test_set_holds_exclusive_lock_during_rmw(
    tmp_path, in_memory_keyring, monkeypatch
) -> None:
    monkeypatch.setenv(ENV_PASSPHRASE, "lock-pass")
    s = _make_store(tmp_path)
    s.provision()
    lock_file = s.secrets_dir / LOCK_NAME

    observed = {}
    real_save = SecretsStore._save

    def spy_save(self, key, data):
        # Mid-RMW: the lock must already be held, so an external acquire fails.
        observed["held"] = _probe_lock_held(lock_file)
        return real_save(self, key, data)

    monkeypatch.setattr(SecretsStore, "_save", spy_save)
    s.set("k", "v")
    assert observed["held"] is True


def test_delete_holds_exclusive_lock_during_rmw(
    tmp_path, in_memory_keyring, monkeypatch
) -> None:
    monkeypatch.setenv(ENV_PASSPHRASE, "lock-pass")
    s = _make_store(tmp_path)
    s.provision()
    s.set("k", "v")
    lock_file = s.secrets_dir / LOCK_NAME

    observed = {}
    real_save = SecretsStore._save

    def spy_save(self, key, data):
        observed["held"] = _probe_lock_held(lock_file)
        return real_save(self, key, data)

    monkeypatch.setattr(SecretsStore, "_save", spy_save)
    s.delete("k")
    assert observed["held"] is True


def test_lock_released_after_write(tmp_path, in_memory_keyring, monkeypatch) -> None:
    """After set() returns, the lock must be free again (no leak / no deadlock
    for the next writer)."""
    monkeypatch.setenv(ENV_PASSPHRASE, "lock-pass")
    s = _make_store(tmp_path)
    s.set("k", "v")
    lock_file = s.secrets_dir / LOCK_NAME
    assert _probe_lock_held(lock_file) is False
    # And a second write still succeeds (lock is re-acquirable).
    s.set("k2", "v2")
    assert s.get("k2") == "v2"
