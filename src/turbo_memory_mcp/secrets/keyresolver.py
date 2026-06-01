"""Master-key resolution for the secrets vault.

Resolves the per-project 32-byte AES-256-GCM master key via, in order:

1. Environment variable ``TQMEMORY_SECRETS_PASSPHRASE`` — derived through
   Argon2id with a project-specific salt. Env is the explicit user choice
   and ALWAYS wins. NOTE: the env var is a *passphrase* (Argon2id input),
   NOT the raw keyring key (DEFECT C).
2. Existing OS keyring entry ``(turbo-quant-memory,
   secrets-master-{project_id})``.
3. Keyring auto-bootstrap: ONLY when ``allow_bootstrap=True`` (a write path on
   a not-yet-initialized vault) and the keyring backend is writable — generate
   32 random bytes, store base64-encoded, and use it. First ``set_secret``
   works on macOS with zero manual setup. Read paths pass
   ``allow_bootstrap=False`` so they can never mint a key (DEFECT D).
4. Raise ``MasterKeyUnavailable`` with a multi-line setup hint. No interactive
   prompt fallback (would silently die on reboot).

A transient keyring READ failure (locked keychain, ACL change, headless
"interaction not allowed") is NEVER swallowed into a bootstrap: minting a new
key while an initialized vault exists would permanently orphan its secrets
(DEFECT D). Such a failure raises ``MasterKeyUnavailable`` instead.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets as _stdlib_secrets
from enum import Enum

import keyring
import keyring.errors
from keyring.backends import fail as _fail_backend

from .crypto import KEY_SIZE, derive_key_from_passphrase

SERVICE_NAME = "turbo-quant-memory"
ENV_PASSPHRASE = "TQMEMORY_SECRETS_PASSPHRASE"

logger = logging.getLogger(__name__)

# One-time, process-wide guard so the env-footgun warning is not spammed on
# every resolve. Reset in tests that exercise the warning path.
_warned_env_looks_like_raw_key = False


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
        f"NOTE: {ENV_PASSPHRASE} is a PASSPHRASE (Argon2id input), NOT the raw "
        "keyring key. Pasting the keyring's base64 value into the env var "
        "derives a DIFFERENT key and will fail to decrypt an existing vault.\n"
        "Env-var path is recommended for headless / CI machines because it "
        "survives reboot deterministically."
    )


def _maybe_warn_env_looks_like_raw_key(env_value: str) -> None:
    """Warn once if the env passphrase looks like a base64-encoded raw key.

    The keyring stores ``base64(32 raw bytes)``; the env var is an Argon2id
    passphrase. A user copying the keyring value into the env var silently
    derives a different key (DEFECT C). A value that base64-decodes to exactly
    ``KEY_SIZE`` bytes is the giveaway.
    """
    global _warned_env_looks_like_raw_key
    if _warned_env_looks_like_raw_key:
        return
    try:
        raw = base64.b64decode(env_value, validate=True)
    except (ValueError, base64.binascii.Error):
        return
    if len(raw) == KEY_SIZE:
        _warned_env_looks_like_raw_key = True
        logger.warning(
            "%s looks like a base64-encoded 32-byte raw key, but it is used "
            "as an Argon2id PASSPHRASE, not the raw master key. If you copied "
            "the keyring value into this env var, decryption of an existing "
            "vault will fail with a key mismatch. Unset it to use the keyring "
            "key, or set an actual passphrase.",
            ENV_PASSPHRASE,
        )


def resolve_master_key(
    project_id: str, *, allow_bootstrap: bool = False
) -> tuple[bytes, KeyResolutionMode]:
    """Resolve the per-project master key.

    Args:
        project_id: project whose vault key to resolve.
        allow_bootstrap: when True (write paths on a not-yet-initialized
            vault), a missing keyring entry may be minted. When False (read
            paths: get / list / delete), a missing key raises rather than
            minting — preventing a fresh key from orphaning an existing vault.
    """
    if not project_id:
        raise ValueError("project_id must be non-empty")

    # Path 1: env var ALWAYS wins as the explicit user choice.
    env_value = os.environ.get(ENV_PASSPHRASE)
    if env_value:
        _maybe_warn_env_looks_like_raw_key(env_value)
        key = derive_key_from_passphrase(env_value, project_id)
        return key, KeyResolutionMode.ENV

    account = _account_for_project(project_id)

    # Path 2: existing keyring entry. A backend-level READ failure must NOT be
    # swallowed into a bootstrap (DEFECT D) — that could mint a fresh key and
    # orphan an existing vault. ``NoKeyringError`` (no backend installed at
    # all) is the genuine "no keyring" case and falls through to the setup
    # hint below; any OTHER KeyringError (locked, access denied, transient)
    # raises immediately.
    try:
        stored = keyring.get_password(SERVICE_NAME, account)
    except keyring.errors.NoKeyringError:
        stored = None
    except keyring.errors.KeyringError as exc:
        raise MasterKeyUnavailable(
            f"Keyring read failed ({type(exc).__name__}: {exc}). "
            "Refusing to mint a new master key while an existing vault may "
            "depend on the current one.\n" + _setup_hint()
        ) from exc

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

    # Path 3: auto-bootstrap — only when a writable backend exists AND the
    # caller explicitly allows it (write path on a fresh vault).
    if isinstance(keyring.get_keyring(), _fail_backend.Keyring):
        raise MasterKeyUnavailable(_setup_hint())
    if not allow_bootstrap:
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
