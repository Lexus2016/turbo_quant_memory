"""AES-256-GCM encryption and Argon2id key derivation for the secrets vault.

Thin wrappers around ``cryptography`` and ``argon2-cffi``. No custom crypto.

Public surface:
    encrypt(plaintext, key) -> bytes
        AES-256-GCM. Returns ``nonce || ciphertext || tag`` where the GCM tag
        is appended to the ciphertext by ``cryptography``'s AESGCM helper.
        Each call uses a fresh 12-byte random nonce.

    decrypt(blob, key) -> bytes
        Inverse of ``encrypt``. Raises
        ``cryptography.exceptions.InvalidTag`` on MAC failure (wrong key
        or tampered ciphertext / nonce).

    derive_key_from_passphrase(passphrase, project_id) -> bytes
        32-byte key via Argon2id. Salt is deterministic per project
        (``sha256("tqv-salt-v1:" + project_id)``) so the same passphrase
        produces a different key for each project — preserves project
        isolation even if a project_id leaks.

    key_fingerprint(key) -> str
        One-way, non-reversible 16-hex-char fingerprint of a master key.
        Stored in ``meta.json`` and verified on resolve so a wrong key fails
        fast (DEFECT B) before any ciphertext is touched.
"""

from __future__ import annotations

import hashlib
import secrets as _stdlib_secrets

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = 12
TAG_SIZE = 16
KEY_SIZE = 32

_KDF_TIME_COST = 3
_KDF_MEMORY_COST_KIB = 64 * 1024  # 64 MiB
_KDF_PARALLELISM = 4
_SALT_PREFIX = b"tqv-salt-v1:"
_KEYFP_PREFIX = b"tqv-keyfp-v1:"


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes (got {len(key)})")
    nonce = _stdlib_secrets.token_bytes(NONCE_SIZE)
    ciphertext_with_tag = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ciphertext_with_tag


def decrypt(blob: bytes, key: bytes) -> bytes:
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes (got {len(key)})")
    if len(blob) < NONCE_SIZE + TAG_SIZE:
        raise ValueError("blob too short to contain nonce + GCM tag")
    nonce = blob[:NONCE_SIZE]
    ciphertext_with_tag = blob[NONCE_SIZE:]
    return AESGCM(key).decrypt(nonce, ciphertext_with_tag, None)


def derive_key_from_passphrase(passphrase: str, project_id: str) -> bytes:
    if not passphrase:
        raise ValueError("passphrase must be non-empty")
    if not project_id:
        raise ValueError("project_id must be non-empty")
    salt = hashlib.sha256(_SALT_PREFIX + project_id.encode("utf-8")).digest()
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=_KDF_TIME_COST,
        memory_cost=_KDF_MEMORY_COST_KIB,
        parallelism=_KDF_PARALLELISM,
        hash_len=KEY_SIZE,
        type=Type.ID,
    )


def key_fingerprint(key: bytes) -> str:
    """One-way, non-reversible fingerprint of a master key.

    Recorded in ``meta.json`` at vault creation / first write and verified on
    resolve, so a wrong key (e.g. an env passphrase shadowing the keyring key,
    DEFECT B) fails fast with a precise error *before* any ciphertext is
    touched. Domain-separated by a version prefix and truncated; safe to store
    in plaintext metadata (does not reveal the key).
    """
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes (got {len(key)})")
    return hashlib.sha256(_KEYFP_PREFIX + key).hexdigest()[:16]
