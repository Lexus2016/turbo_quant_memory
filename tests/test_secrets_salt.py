"""M5: random per-vault salt for env-passphrase vaults, backward-compatible.

Legacy (pre-M5) env vaults were keyed with a DETERMINISTIC salt derived from
project_id. M5 generates a RANDOM salt for new env vaults and persists it in
meta.json, while old vaults (meta without "salt") keep deriving the legacy
deterministic key — so existing secrets never lose access. Keyring-backed
vaults use a random key directly and are unaffected by salt at all.
"""

from __future__ import annotations

import json

import keyring
import keyring.backend
import pytest

from turbo_memory_mcp.secrets.crypto import (
    derive_key_from_passphrase,
    encrypt,
    key_fingerprint,
)
from turbo_memory_mcp.secrets.keyresolver import ENV_PASSPHRASE
from turbo_memory_mcp.secrets.store import SecretsStore

PROJECT = "test-salt-project"


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


# --- backward-compat guards (must stay green before AND after M5) ---


def test_default_derive_is_deterministic_and_stable() -> None:
    a = derive_key_from_passphrase("p", "proj")
    b = derive_key_from_passphrase("p", "proj")
    assert a == b
    assert len(a) == 32


def test_legacy_vault_without_salt_still_decrypts(
    tmp_path, in_memory_keyring, monkeypatch
) -> None:
    """A pre-M5 vault: deterministic-salt key, meta WITHOUT a "salt" field.
    The new code must still derive the legacy deterministic key and decrypt."""
    monkeypatch.setenv(ENV_PASSPHRASE, "legacy-pass")
    s = _make_store(tmp_path)
    s.secrets_dir.mkdir(parents=True, exist_ok=True)

    key = derive_key_from_passphrase("legacy-pass", PROJECT)  # deterministic
    blob = encrypt(
        json.dumps(
            {
                "version": 1,
                "entries": {"k": {"value": "v", "created_at": "t", "updated_at": "t"}},
            }
        ).encode("utf-8"),
        key,
    )
    s.vault_path.write_bytes(blob)
    s.meta_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kdf": "argon2id",
                "kdf_params": {"time_cost": 3, "memory_cost_kib": 65536, "parallelism": 4},
                "key_mode": "env",
                "vault_initialized": True,
                "key_fingerprint": key_fingerprint(key),
                # NOTE: no "salt" -> legacy deterministic path
            }
        )
    )
    assert s.get("k") == "v"


# --- RED drivers: new salt behavior ---


def test_derive_accepts_explicit_salt_and_differs_from_default() -> None:
    default = derive_key_from_passphrase("p", "proj")
    explicit = derive_key_from_passphrase("p", "proj", salt=b"\x11" * 32)
    assert explicit != default
    # salt=None must be byte-identical to the legacy default (no behavior change).
    assert derive_key_from_passphrase("p", "proj", salt=None) == default


def test_new_env_vault_persists_random_salt_in_meta(
    tmp_path, in_memory_keyring, monkeypatch
) -> None:
    monkeypatch.setenv(ENV_PASSPHRASE, "new-pass")
    s = _make_store(tmp_path)
    s.set("k", "v")

    meta = json.loads(s.meta_path.read_text())
    assert meta.get("salt"), "a new env vault must persist a random salt in meta.json"

    # The persisted salt must round-trip across a fresh store instance.
    s2 = SecretsStore(tmp_path, PROJECT)
    assert s2.get("k") == "v"


def test_new_env_vault_salt_is_random_not_deterministic(
    tmp_path, in_memory_keyring, monkeypatch
) -> None:
    """The persisted salt must NOT equal the legacy deterministic salt."""
    import hashlib

    from turbo_memory_mcp.secrets.crypto import _SALT_PREFIX

    monkeypatch.setenv(ENV_PASSPHRASE, "new-pass")
    s = _make_store(tmp_path)
    s.set("k", "v")

    meta = json.loads(s.meta_path.read_text())
    persisted_salt = bytes.fromhex(meta["salt"])
    legacy_salt = hashlib.sha256(_SALT_PREFIX + PROJECT.encode("utf-8")).digest()
    assert persisted_salt != legacy_salt
    assert len(persisted_salt) == 32


def test_keyring_vault_has_no_salt(tmp_path, in_memory_keyring, monkeypatch) -> None:
    """A keyring-backed vault uses a random key directly; salt is irrelevant
    and must not be written."""
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    s = _make_store(tmp_path)
    s.set("k", "v")  # bootstraps a keyring key

    meta = json.loads(s.meta_path.read_text())
    assert meta["key_mode"] == "keyring_bootstrapped"
    assert "salt" not in meta


def test_new_env_vault_persists_salt_before_writing_vault(
    tmp_path, in_memory_keyring, monkeypatch
) -> None:
    """Crash-safety (peer-review Q3): for a NEW env vault the random salt must
    be persisted in meta.json BEFORE the vault ciphertext. A crash between the
    two atomic writes then leaves a not-yet-created vault (recoverable on the
    next set) instead of a vault whose random salt was lost — which would be
    permanently undecryptable (the deterministic fallback derives a wrong key).
    """
    monkeypatch.setenv(ENV_PASSPHRASE, "order-pass")
    s = _make_store(tmp_path)

    observed = {}
    real_save = SecretsStore._save

    def spy_save(self, key, data):
        meta = (
            json.loads(self.meta_path.read_text())
            if self.meta_path.exists()
            else {}
        )
        observed["salt_present_at_vault_write"] = bool(meta.get("salt"))
        return real_save(self, key, data)

    monkeypatch.setattr(SecretsStore, "_save", spy_save)
    s.set("k", "v")  # creates a new env vault

    assert observed["salt_present_at_vault_write"] is True
    # And the vault is fully usable from a fresh instance afterwards.
    assert SecretsStore(tmp_path, PROJECT).get("k") == "v"
