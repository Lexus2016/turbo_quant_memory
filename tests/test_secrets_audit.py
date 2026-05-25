"""Unit tests for ``turbo_memory_mcp.secrets.audit``."""

from __future__ import annotations

import json
import os
import re

import pytest

from turbo_memory_mcp.secrets.audit import AUDIT_FILENAME, AuditLog

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")


def _make_dir(tmp_path):
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(secrets_dir, 0o700)
    return secrets_dir


def test_record_creates_file(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    assert not log.path.exists()
    log.record("set", "db-dsn")
    assert log.path.exists()


def test_record_schema_has_only_ts_action_name(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    log.record("set", "alpha")
    line = log.path.read_text().rstrip("\n")
    obj = json.loads(line)
    assert set(obj.keys()) == {"ts", "action", "name"}
    assert _ISO_RE.match(obj["ts"])
    assert obj["action"] == "set"
    assert obj["name"] == "alpha"


def test_record_never_contains_value_field(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    log.record("set", "db-dsn")
    log.record("get", "db-dsn")
    log.record("delete", "db-dsn")
    content = log.path.read_text()
    assert "value" not in content
    assert "secret_value" not in content


def test_record_appends_does_not_overwrite(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    log.record("set", "a")
    log.record("set", "b")
    log.record("get", "a")
    log.record("delete", "b")
    lines = log.path.read_text().splitlines()
    assert len(lines) == 4
    objs = [json.loads(line) for line in lines]
    assert [(o["action"], o["name"]) for o in objs] == [
        ("set", "a"),
        ("set", "b"),
        ("get", "a"),
        ("delete", "b"),
    ]


def test_record_list_action_uses_star_name(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    log.record("list", "*")
    obj = json.loads(log.path.read_text().rstrip("\n"))
    assert obj["action"] == "list"
    assert obj["name"] == "*"


def test_record_rejects_unknown_action(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    with pytest.raises(ValueError):
        log.record("rotate", "x")


def test_record_rejects_non_string_name(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    with pytest.raises(TypeError):
        log.record("set", 42)  # type: ignore[arg-type]


def test_file_permissions_0600(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    log.record("set", "x")
    assert (os.stat(log.path).st_mode & 0o777) == 0o600


def test_count_zero_when_no_file(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    assert log.count() == 0


def test_count_after_records(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    for action in ["set", "get", "list", "delete"]:
        log.record(action, "x" if action != "list" else "*")
    assert log.count() == 4


def test_audit_filename_is_audit_jsonl(tmp_path):
    log = AuditLog(_make_dir(tmp_path))
    assert log.path.name == AUDIT_FILENAME == "audit.jsonl"
