"""Master-key resolution for the secrets vault.

Resolves the per-project 32-byte AES-256-GCM master key via, in order:

1. Environment variable ``TQMEMORY_SECRETS_PASSPHRASE`` — derived through
   Argon2id with a project-specific salt. Env is the explicit user choice
   and ALWAYS wins.
2. Existing OS keyring entry ``(turbo-quant-memory,
   secrets-master-{project_id})``.
3. Keyring auto-bootstrap: if neither (1) nor (2) and the keyring backend is
   not the fail backend, generate 32 random bytes, store base64-encoded under
   the same service/account, and use it. First ``set_secret`` works on macOS
   with zero manual setup.
4. Raise ``MasterKeyUnavailable`` with a multi-line setup hint. No interactive
   prompt fallback (would silently die on reboot).
"""

from __future__ import annotations

import base64
import os
import secrets as _stdlib_secrets
from enum import Enum

import keyring
import keyring.errors
from keyring.backends import fail as _fail_backend

from .crypto import KEY_SIZE, derive_key_from_passphrase

SERVICE_NAME = "turbo-quant-memory"
ENV_PASSPHRASE = "TQMEMORY_SECRETS_PASSPHRASE"


class KeyResolutionMode(Enum):
    """Internal enum — how the master key was obtained.

    Not part of the public package surface; kept here for diagnostics and
    ``server_info()`` reporting only.
    """

    ENV = "env"
    KEYRING_EXISTING = "keyring_existing"
    KEYRING_BOOTSTRAPPED = "keyring_bootstrapped"


class MasterKeyUnavailable(RuntimeError):
    """Raised when no master key can be resolved.

    The exception message contains an actionable multi-line setup hint that
    can be surfaced verbatim in MCP error responses.
    """


def _account_for_project(project_id: str) -> str:
    return f"secrets-master-{project_id}"


def _setup_hint() -> str:
    return (
        "No master key available for the secrets vault.\n"
        "Choose ONE setup path (no interactive prompt fallback exists):\n"
        f"  - export {ENV_PASSPHRASE}=<long-passphrase>  in your shell rc, OR\n"
        f"  - keyring set {SERVICE_NAME} secrets-master-<project_id>  "
        "with a 32-byte base64 value.\n"
        "Env-var path is recommended for headless / CI machines because it "
        "survives reboot deterministically."
    )


def resolve_master_key(project_id: str) -> tuple[bytes, KeyResolutionMode]:
    if not project_id:
        raise ValueError("project_id must be non-empty")

    # Path 1: env var ALWAYS wins as the explicit user choice.
    env_value = os.environ.get(ENV_PASSPHRASE)
    if env_value:
        key = derive_key_from_passphrase(env_value, project_id)
        return key, KeyResolutionMode.ENV

    account = _account_for_project(project_id)

    # Path 2: existing keyring entry.
    try:
        stored = keyring.get_password(SERVICE_NAME, account)
    except keyring.errors.KeyringError:
        stored = None
    if stored:
        try:
            key = base64.b64decode(stored, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise MasterKeyUnavailable(
                f"Existing keyring entry {SERVICE_NAME}/{account} is not valid "
                f"base64 ({exc}). Delete it and rerun.\n" + _setup_hint()
            ) from exc
        if len(key) != KEY_SIZE:
            raise MasterKeyUnavailable(
                f"Existing keyring entry {SERVICE_NAME}/{account} decodes to "
                f"{len(key)} bytes; expected {KEY_SIZE}. Delete it and rerun.\n"
                + _setup_hint()
            )
        return key, KeyResolutionMode.KEYRING_EXISTING

    # Path 3: auto-bootstrap if backend is writable.
    if isinstance(keyring.get_keyring(), _fail_backend.Keyring):
        raise MasterKeyUnavailable(_setup_hint())
    key = _stdlib_secrets.token_bytes(KEY_SIZE)
    try:
        keyring.set_password(
            SERVICE_NAME, account, base64.b64encode(key).decode("ascii")
        )
    except keyring.errors.KeyringError as exc:
        raise MasterKeyUnavailable(
            f"Keyring write failed during bootstrap: {exc}\n" + _setup_hint()
        ) from exc
    return key, KeyResolutionMode.KEYRING_BOOTSTRAPPED
