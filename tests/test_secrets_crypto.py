"""Unit tests for ``turbo_memory_mcp.secrets.crypto``."""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from turbo_memory_mcp.secrets.crypto import (
    KEY_SIZE,
    NONCE_SIZE,
    TAG_SIZE,
    decrypt,
    derive_key_from_passphrase,
    encrypt,
    key_fingerprint,
)

KEY_A = b"\x00" * KEY_SIZE
KEY_B = b"\x01" * KEY_SIZE


@pytest.mark.parametrize(
    "plaintext",
    [
        b"",
        b"a",
        b"hello world",
        b"\x00\xff" * 100,
        "пароль до бази даних".encode("utf-8") * 5,
    ],
)
def test_encrypt_decrypt_roundtrip(plaintext: bytes) -> None:
    blob = encrypt(plaintext, KEY_A)
    assert decrypt(blob, KEY_A) == plaintext


def test_encrypt_uses_unique_nonce_per_call() -> None:
    first = encrypt(b"same plaintext", KEY_A)
    second = encrypt(b"same plaintext", KEY_A)
    # Distinct nonces => distinct blobs even for identical inputs.
    assert first != second
    assert first[:NONCE_SIZE] != second[:NONCE_SIZE]


def test_decrypt_with_wrong_key_raises_invalid_tag() -> None:
    blob = encrypt(b"sensitive", KEY_A)
    with pytest.raises(InvalidTag):
        decrypt(blob, KEY_B)


def test_decrypt_tampered_ciphertext_raises_invalid_tag() -> None:
    blob = bytearray(encrypt(b"sensitive", KEY_A))
    blob[NONCE_SIZE + 2] ^= 0x01
    with pytest.raises(InvalidTag):
        decrypt(bytes(blob), KEY_A)


def test_decrypt_tampered_nonce_raises_invalid_tag() -> None:
    blob = bytearray(encrypt(b"sensitive", KEY_A))
    blob[0] ^= 0x01
    with pytest.raises(InvalidTag):
        decrypt(bytes(blob), KEY_A)


def test_decrypt_truncated_blob_raises_value_error() -> None:
    short = b"\x00" * (NONCE_SIZE + TAG_SIZE - 1)
    with pytest.raises(ValueError):
        decrypt(short, KEY_A)


@pytest.mark.parametrize("bad_key_len", [0, 1, 16, 31, 33, 64])
def test_encrypt_rejects_wrong_key_size(bad_key_len: int) -> None:
    with pytest.raises(ValueError):
        encrypt(b"x", b"\x00" * bad_key_len)


@pytest.mark.parametrize("bad_key_len", [0, 1, 16, 31, 33, 64])
def test_decrypt_rejects_wrong_key_size(bad_key_len: int) -> None:
    valid_blob = encrypt(b"x", KEY_A)
    with pytest.raises(ValueError):
        decrypt(valid_blob, b"\x00" * bad_key_len)


def test_kdf_deterministic_for_same_inputs() -> None:
    first = derive_key_from_passphrase("secret-pass", "project-abc")
    second = derive_key_from_passphrase("secret-pass", "project-abc")
    assert first == second
    assert len(first) == KEY_SIZE


def test_kdf_separates_projects() -> None:
    a = derive_key_from_passphrase("same-pass", "project-A")
    b = derive_key_from_passphrase("same-pass", "project-B")
    assert a != b


def test_kdf_separates_passphrases() -> None:
    a = derive_key_from_passphrase("pass-A", "project-1")
    b = derive_key_from_passphrase("pass-B", "project-1")
    assert a != b


def test_kdf_rejects_empty_passphrase() -> None:
    with pytest.raises(ValueError):
        derive_key_from_passphrase("", "project-1")


def test_kdf_rejects_empty_project_id() -> None:
    with pytest.raises(ValueError):
        derive_key_from_passphrase("pass", "")


def test_kdf_output_drives_aes_roundtrip() -> None:
    """End-to-end: KDF output is a valid AES-256-GCM key."""
    key = derive_key_from_passphrase("strong passphrase 42", "project-xyz")
    blob = encrypt(b"hello", key)
    assert decrypt(blob, key) == b"hello"


# --- key fingerprint (DEFECT B: provenance check) --------------------------


def test_key_fingerprint_is_deterministic() -> None:
    assert key_fingerprint(KEY_A) == key_fingerprint(KEY_A)


def test_key_fingerprint_differs_per_key() -> None:
    assert key_fingerprint(KEY_A) != key_fingerprint(KEY_B)


def test_key_fingerprint_is_short_lowercase_hex() -> None:
    fp = key_fingerprint(KEY_A)
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)


def test_key_fingerprint_does_not_leak_raw_key() -> None:
    """One-way: the fingerprint must not be a reversible encoding of the key."""
    import base64

    fp = key_fingerprint(KEY_B)
    assert KEY_B.hex() not in fp
    assert base64.b64encode(KEY_B).decode("ascii") not in fp
