"""Integration tests for the four secrets MCP tools (Phase 9 Wave 2)."""

from __future__ import annotations

import json
from pathlib import Path

import keyring
import keyring.backend
import pytest
from keyring.backends import fail as _fail_backend

from turbo_memory_mcp.secrets.keyresolver import ENV_PASSPHRASE
from turbo_memory_mcp.server import (
    delete_secret_impl,
    get_secret_impl,
    list_secrets_impl,
    set_secret_impl,
)


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


def _env_with_passphrase(tmp_path: Path) -> dict[str, str]:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    return {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "proj-secret",
        "TQMEMORY_PROJECT_NAME": "Secrets Test Project",
        ENV_PASSPHRASE: "test-mcp-passphrase",
    }


def _env_without_passphrase(tmp_path: Path) -> dict[str, str]:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    return {
        "TQMEMORY_HOME": str(tmp_path / "memory-home"),
        "TQMEMORY_PROJECT_ROOT": str(project_root),
        "TQMEMORY_PROJECT_ID": "proj-secret",
        "TQMEMORY_PROJECT_NAME": "Secrets Test Project",
    }


# --- set_secret -------------------------------------------------------------


def test_set_secret_returns_ok_status(tmp_path, in_memory_keyring):
    env = _env_with_passphrase(tmp_path)
    payload = set_secret_impl("db-dsn", "postgresql://x:y@h/d", environ=env)
    assert payload["status"] == "ok"
    assert payload["name"] == "db-dsn"
    assert payload["project_id"] == "proj-secret"
    # CRITICAL: the SET response must NEVER contain the value.
    assert "secret_value" not in payload
    assert "postgresql" not in json.dumps(payload)


def test_set_secret_unavailable_key_returns_structured_error(
    tmp_path, fail_keyring, monkeypatch
):
    # Resolver priority 1 reads the real process env, so a passphrase set in
    # the developer's shell would leak in and resolve the key. Strip it so the
    # "unavailable key" path is genuinely exercised regardless of host env.
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    env = _env_without_passphrase(tmp_path)
    payload = set_secret_impl("k", "v", environ=env)
    assert payload["status"] == "error"
    assert payload["code"] == "master_key_unavailable"
    assert payload["name"] == "k"
    assert "setup_hint" in payload
    assert ENV_PASSPHRASE in payload["setup_hint"]


# --- get_secret -------------------------------------------------------------


def test_get_secret_returns_value_in_dedicated_field(tmp_path, in_memory_keyring):
    env = _env_with_passphrase(tmp_path)
    set_secret_impl("api-token", "sk-12345", environ=env)
    payload = get_secret_impl("api-token", environ=env)

    assert payload["status"] == "ok"
    assert payload["name"] == "api-token"
    assert payload["project_id"] == "proj-secret"
    assert payload["secret_value"] == "sk-12345"
    # The value lives ONLY in secret_value, not in any descriptive text.
    for key in ("summary", "message", "description"):
        assert key not in payload


def test_get_secret_missing_status(tmp_path, in_memory_keyring):
    env = _env_with_passphrase(tmp_path)
    # provision via set first so vault.tqv exists, but query a different name
    set_secret_impl("known", "value", environ=env)
    payload = get_secret_impl("unknown", environ=env)
    assert payload["status"] == "missing"
    assert payload["name"] == "unknown"
    assert "secret_value" not in payload


def test_get_secret_unavailable_key_returns_structured_error_when_vault_exists(
    tmp_path, in_memory_keyring, monkeypatch
):
    """Once a vault exists, losing the key must surface as a clear error,
    not a misleading 'missing'."""
    env_with = _env_with_passphrase(tmp_path)
    set_secret_impl("k", "v", environ=env_with)

    # Now strip the passphrase from env and break the keyring. delenv also
    # removes any passphrase inherited from the developer's real shell, which
    # the resolver reads via os.environ (priority 1) and would otherwise leak.
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    env_without = {k: v for k, v in env_with.items() if k != ENV_PASSPHRASE}
    original = keyring.get_keyring()
    keyring.set_keyring(_fail_backend.Keyring())
    try:
        payload = get_secret_impl("k", environ=env_without)
    finally:
        keyring.set_keyring(original)
    assert payload["status"] == "error"
    assert payload["code"] == "master_key_unavailable"


def test_get_secret_on_fresh_project_returns_missing(tmp_path, fail_keyring):
    """Fresh install (no vault, no key): honest answer is 'missing', not 'error'.

    The error surfaces only when the user tries to WRITE (set_secret). This
    keeps the read-path UX clean: 'no such secret yet' instead of 'setup
    error' on first-ever get_secret call.
    """
    env = _env_without_passphrase(tmp_path)
    payload = get_secret_impl("never-stored", environ=env)
    assert payload["status"] == "missing"
    assert payload["name"] == "never-stored"
    assert "secret_value" not in payload


# --- list_secrets ----------------------------------------------------------


def test_list_secrets_returns_names_sorted_no_values(tmp_path, in_memory_keyring):
    env = _env_with_passphrase(tmp_path)
    set_secret_impl("zeta", "Z", environ=env)
    set_secret_impl("alpha", "A", environ=env)
    set_secret_impl("mu", "M", environ=env)

    payload = list_secrets_impl(environ=env)
    assert payload["status"] == "ok"
    assert payload["project_id"] == "proj-secret"
    assert payload["names"] == ["alpha", "mu", "zeta"]
    # The response shape MUST NOT expose any value-bearing field.
    assert "secret_value" not in payload
    assert "values" not in payload


def test_list_secrets_empty_vault_returns_empty_names(
    tmp_path, in_memory_keyring
):
    env = _env_with_passphrase(tmp_path)
    payload = list_secrets_impl(environ=env)
    assert payload["status"] == "ok"
    assert payload["names"] == []


# --- delete_secret ---------------------------------------------------------


def test_delete_secret_existing_returns_deleted_true(tmp_path, in_memory_keyring):
    env = _env_with_passphrase(tmp_path)
    set_secret_impl("ephemeral", "value", environ=env)
    payload = delete_secret_impl("ephemeral", environ=env)
    assert payload["status"] == "ok"
    assert payload["deleted"] is True
    assert payload["name"] == "ephemeral"

    # Followup: name is gone.
    assert get_secret_impl("ephemeral", environ=env)["status"] == "missing"


def test_delete_secret_missing_returns_deleted_false(
    tmp_path, in_memory_keyring
):
    env = _env_with_passphrase(tmp_path)
    set_secret_impl("real", "v", environ=env)  # provision the vault first
    payload = delete_secret_impl("nonexistent", environ=env)
    assert payload["status"] == "ok"
    assert payload["deleted"] is False


# --- audit trail ----------------------------------------------------------


def test_each_tool_records_an_audit_entry(tmp_path, in_memory_keyring):
    env = _env_with_passphrase(tmp_path)
    set_secret_impl("audited", "value", environ=env)
    get_secret_impl("audited", environ=env)
    list_secrets_impl(environ=env)
    delete_secret_impl("audited", environ=env)

    audit_path = (
        Path(env["TQMEMORY_HOME"])
        / "projects"
        / "proj-secret"
        / "secrets"
        / "audit.jsonl"
    )
    assert audit_path.exists()
    lines = audit_path.read_text().splitlines()
    actions = [json.loads(line)["action"] for line in lines]
    assert "set" in actions
    assert "get" in actions
    assert "list" in actions
    assert "delete" in actions

    # CRITICAL: audit log must never contain the secret value.
    content = audit_path.read_text()
    assert "value" not in content.lower().replace("\"name\"", "") or '"value":' not in content


def test_audit_never_contains_secret_value(tmp_path, in_memory_keyring):
    env = _env_with_passphrase(tmp_path)
    sentinel = "sentinel_super_secret_phrase_98765"
    set_secret_impl("k", sentinel, environ=env)
    get_secret_impl("k", environ=env)
    list_secrets_impl(environ=env)

    audit_path = (
        Path(env["TQMEMORY_HOME"])
        / "projects"
        / "proj-secret"
        / "secrets"
        / "audit.jsonl"
    )
    assert sentinel not in audit_path.read_text()


# --- DEFECT A: a wrong key surfaces as a structured error, never a bare,
#     message-less exception escaping the MCP boundary (the reported bug) ----


def test_get_secret_wrong_key_returns_master_key_mismatch(
    tmp_path, in_memory_keyring, monkeypatch
):
    # The resolver reads the REAL process env for the passphrase; switch it
    # between write and read to simulate a key that no longer matches.
    env = _env_without_passphrase(tmp_path)
    monkeypatch.setenv(ENV_PASSPHRASE, "passphrase-one")
    set_secret_impl("k", "v", environ=env)

    monkeypatch.setenv(ENV_PASSPHRASE, "passphrase-two")  # derives a different key
    payload = get_secret_impl("k", environ=env)
    assert payload["status"] == "error"
    assert payload["code"] == "master_key_mismatch"
    assert payload["setup_hint"]  # actionable, non-empty
    assert "secret_value" not in payload


def test_list_secrets_wrong_key_returns_master_key_mismatch(
    tmp_path, in_memory_keyring, monkeypatch
):
    env = _env_without_passphrase(tmp_path)
    monkeypatch.setenv(ENV_PASSPHRASE, "passphrase-one")
    set_secret_impl("k", "v", environ=env)

    monkeypatch.setenv(ENV_PASSPHRASE, "passphrase-two")
    payload = list_secrets_impl(environ=env)
    assert payload["status"] == "error"
    assert payload["code"] == "master_key_mismatch"
    assert "names" not in payload


def test_delete_secret_wrong_key_returns_master_key_mismatch(
    tmp_path, in_memory_keyring, monkeypatch
):
    env = _env_without_passphrase(tmp_path)
    monkeypatch.setenv(ENV_PASSPHRASE, "passphrase-one")
    set_secret_impl("k", "v", environ=env)

    monkeypatch.setenv(ENV_PASSPHRASE, "passphrase-two")
    payload = delete_secret_impl("k", environ=env)
    assert payload["status"] == "error"
    assert payload["code"] == "master_key_mismatch"


def test_set_secret_wrong_key_returns_master_key_mismatch(
    tmp_path, in_memory_keyring, monkeypatch
):
    env = _env_without_passphrase(tmp_path)
    monkeypatch.setenv(ENV_PASSPHRASE, "passphrase-one")
    set_secret_impl("k", "v", environ=env)

    monkeypatch.setenv(ENV_PASSPHRASE, "passphrase-two")
    payload = set_secret_impl("k2", "v2", environ=env)
    assert payload["status"] == "error"
    assert payload["code"] == "master_key_mismatch"


def test_get_secret_legacy_vault_wrong_key_returns_structured_error(
    tmp_path, in_memory_keyring, monkeypatch
):
    """Even a pre-fingerprint (legacy) vault must never leak a bare InvalidTag:
    the decrypt-time failure becomes a structured master_key_mismatch."""
    env = _env_without_passphrase(tmp_path)
    monkeypatch.setenv(ENV_PASSPHRASE, "passphrase-one")
    set_secret_impl("k", "v", environ=env)

    meta_path = (
        Path(env["TQMEMORY_HOME"])
        / "projects" / "proj-secret" / "secrets" / "meta.json"
    )
    meta = json.loads(meta_path.read_text())
    meta.pop("key_fingerprint", None)  # simulate a vault created before DEFECT B
    meta_path.write_text(json.dumps(meta))

    monkeypatch.setenv(ENV_PASSPHRASE, "passphrase-two")
    payload = get_secret_impl("k", environ=env)
    assert payload["status"] == "error"
    assert payload["code"] == "master_key_mismatch"
