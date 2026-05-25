"""Regression test: secrets vault env passphrase must be forwarded from
proxy to primary in daemon mode.

Without this forwarding, a user who only set
``TQMEMORY_SECRETS_PASSPHRASE`` in their shell rc would hit
``MasterKeyUnavailable`` on every set_secret / get_secret call routed
through a long-running primary daemon (Phase 9 round-1 audit catch).
"""

from __future__ import annotations

from turbo_memory_mcp.server import _FORWARDED_ENV_KEYS


def test_secrets_passphrase_is_in_forwarded_env_keys():
    assert "TQMEMORY_SECRETS_PASSPHRASE" in _FORWARDED_ENV_KEYS


def test_forwarded_env_keys_includes_project_identity_and_storage():
    """Sanity guard against accidental removal of pre-existing keys."""
    expected_minimal = {
        "TQMEMORY_PROJECT_ROOT",
        "TQMEMORY_PROJECT_ID",
        "TQMEMORY_PROJECT_NAME",
        "TQMEMORY_HOME",
        "TQMEMORY_SECRETS_PASSPHRASE",
    }
    assert expected_minimal.issubset(set(_FORWARDED_ENV_KEYS))
