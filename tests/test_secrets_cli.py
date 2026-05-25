"""Tests for the ``turbo-memory-mcp secret-set`` CLI subcommand (Phase 9 follow-up).

The CLI exists so users can provision the first value of a secret without
ever pasting it into a chat transcript — getpass on a TTY, raw stdin on a
pipe. These tests drive the pipe path (testable without a real PTY) and
verify the contract: success exit 0, empty-value exit 2, invalid name
exit 2, ``MasterKeyUnavailable`` exit 3 with the setup hint on stderr,
and that the value never reaches stdout.
"""

from __future__ import annotations

import io
import sys

import keyring
import keyring.backend
import pytest
from keyring.backends import fail as _fail_backend

from turbo_memory_mcp.cli import _handle_secret_set, build_parser
from turbo_memory_mcp.secrets import SecretsStore
from turbo_memory_mcp.secrets.keyresolver import ENV_PASSPHRASE


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
def cli_env(tmp_path, monkeypatch):
    """Wire ENV so build_runtime_context resolves to a temp project + storage."""
    project_root = tmp_path / "repo"
    project_root.mkdir()
    monkeypatch.setenv("TQMEMORY_HOME", str(tmp_path / "memory-home"))
    monkeypatch.setenv("TQMEMORY_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("TQMEMORY_PROJECT_ID", "cli-secret-project")
    monkeypatch.setenv("TQMEMORY_PROJECT_NAME", "CLI Secret Project")
    monkeypatch.setenv(ENV_PASSPHRASE, "cli-test-passphrase")
    return tmp_path


def _invoke(monkeypatch, capsys, name: str, stdin_text: str) -> tuple[int, str, str]:
    """Run secret-set with a piped stdin (sys.stdin.isatty() returns False)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    args = build_parser().parse_args(["secret-set", name])
    code = _handle_secret_set(args)
    out, err = capsys.readouterr()
    return code, out, err


def test_subparser_registered_and_help_renders():
    parser = build_parser()
    namespace = parser.parse_args(["secret-set", "some-name"])
    assert namespace.command == "secret-set"
    assert namespace.name == "some-name"
    assert callable(namespace.handler)


def test_secret_set_pipe_input_success(
    cli_env, in_memory_keyring, monkeypatch, capsys
):
    code, out, err = _invoke(monkeypatch, capsys, "db-dsn", "postgresql://x:y@h/d\n")
    assert code == 0
    assert "Stored secret 'db-dsn'" in out
    assert "cli-secret-project" in out
    # Value MUST NOT appear in either stdout or stderr.
    assert "postgresql" not in out
    assert "postgresql" not in err

    # Verify via SecretsStore that the value actually landed.
    vault = SecretsStore(cli_env / "memory-home", "cli-secret-project")
    assert vault.get("db-dsn") == "postgresql://x:y@h/d"


def test_secret_set_strips_single_trailing_newline_only(
    cli_env, in_memory_keyring, monkeypatch, capsys
):
    code, _, _ = _invoke(monkeypatch, capsys, "k", "value with spaces\n")
    assert code == 0
    vault = SecretsStore(cli_env / "memory-home", "cli-secret-project")
    assert vault.get("k") == "value with spaces"


def test_secret_set_rejects_empty_value(
    cli_env, in_memory_keyring, monkeypatch, capsys
):
    code, out, err = _invoke(monkeypatch, capsys, "k", "")
    assert code == 2
    assert "empty value" in err
    assert out == ""


def test_secret_set_rejects_empty_value_after_strip(
    cli_env, in_memory_keyring, monkeypatch, capsys
):
    code, out, err = _invoke(monkeypatch, capsys, "k", "\n")
    assert code == 2
    assert "empty value" in err
    assert out == ""


def test_secret_set_rejects_invalid_name(
    cli_env, in_memory_keyring, monkeypatch, capsys
):
    code, out, err = _invoke(monkeypatch, capsys, "has space", "value")
    assert code == 2
    assert "must match" in err
    assert out == ""


def test_secret_set_master_key_unavailable_returns_3_with_hint(
    cli_env, fail_keyring, monkeypatch, capsys
):
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    code, out, err = _invoke(monkeypatch, capsys, "k", "value")
    assert code == 3
    assert ENV_PASSPHRASE in err  # setup hint references the env var
    assert "keyring set" in err  # and the keyring path
    assert out == ""


def test_secret_set_never_echoes_sentinel_value(
    cli_env, in_memory_keyring, monkeypatch, capsys
):
    sentinel = "sentinel_cli_value_98765"
    code, out, err = _invoke(monkeypatch, capsys, "secret-key", sentinel + "\n")
    assert code == 0
    # The sentinel value must not appear anywhere in CLI output.
    assert sentinel not in out
    assert sentinel not in err


def test_module_invocation_path_exposed_via_main(tmp_path, monkeypatch, capsys):
    """`turbo-memory-mcp secret-set --help` works through main()."""
    from turbo_memory_mcp.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["secret-set", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "Store a secret" in out
    assert "[A-Za-z0-9_.-]" in out


def test_secret_set_writes_audit_entry(
    cli_env, in_memory_keyring, monkeypatch, capsys
):
    """CLI is the canonical setup path — every set MUST be audited at parity
    with the MCP set_secret tool. Verified by inspecting audit.jsonl."""
    import json

    code, _, _ = _invoke(monkeypatch, capsys, "audited-secret", "audited-value\n")
    assert code == 0

    audit_path = (
        cli_env
        / "memory-home"
        / "projects"
        / "cli-secret-project"
        / "secrets"
        / "audit.jsonl"
    )
    assert audit_path.exists()
    lines = audit_path.read_text().splitlines()
    assert any(
        json.loads(line) == {**json.loads(line), "action": "set", "name": "audited-secret"}
        for line in lines
    ), f"expected a 'set' audit entry for 'audited-secret', got: {lines}"
    # Audit log never contains the value.
    assert "audited-value" not in audit_path.read_text()


def test_secret_set_does_not_audit_on_master_key_unavailable(
    cli_env, fail_keyring, monkeypatch, capsys
):
    """Failed set (no key) must NOT leave an audit line — audit reflects
    accepted operations only, mirroring the MCP impl's behavior."""
    monkeypatch.delenv(ENV_PASSPHRASE, raising=False)
    code, _, _ = _invoke(monkeypatch, capsys, "wont-stick", "value\n")
    assert code == 3

    audit_path = (
        cli_env
        / "memory-home"
        / "projects"
        / "cli-secret-project"
        / "secrets"
        / "audit.jsonl"
    )
    # Either no file or no entry for the failed name.
    if audit_path.exists():
        assert "wont-stick" not in audit_path.read_text()
