from __future__ import annotations

import pytest

from turbo_memory_mcp.server import build_runtime_context
from turbo_memory_mcp.store import (
    DEFAULT_PROVENANCE,
    NOTE_PROVENANCE_AGENT,
    NOTE_PROVENANCE_HUMAN,
    NOTE_PROVENANCES,
    normalize_provenance,
)


def _env(tmp_path) -> dict[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return {
        "TQMEMORY_HOME": str(tmp_path / "home"),
        "TQMEMORY_PROJECT_ROOT": str(repo),
        "TQMEMORY_PROJECT_ID": "proj-test",
        "TQMEMORY_PROJECT_NAME": "Test Project",
    }


def _store(tmp_path):
    env = _env(tmp_path)
    _, store = build_runtime_context(cwd=tmp_path / "repo", environ=env)
    return store


def test_default_provenance_is_agent():
    assert DEFAULT_PROVENANCE == NOTE_PROVENANCE_AGENT == "agent"
    assert NOTE_PROVENANCE_HUMAN == "human-explicit"
    assert set(NOTE_PROVENANCES) == {"human-explicit", "agent"}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("human-explicit", "human-explicit"),
        ("HUMAN-EXPLICIT", "human-explicit"),
        ("  agent  ", "agent"),
        ("agent", "agent"),
        (None, "agent"),
        ("", "agent"),
        ("nonsense", "agent"),  # graceful fallback (unlike note_kind which raises)
    ],
)
def test_normalize_provenance(value, expected):
    assert normalize_provenance(value) == expected


def test_write_note_with_provenance_roundtrips(tmp_path):
    store = _store(tmp_path)
    note = store.write_project_note(
        "T", "body", note_kind="decision", provenance="human-explicit"
    )
    assert note["provenance"] == "human-explicit"
    reread = store.read_project_note(note["note_id"])
    assert reread["provenance"] == "human-explicit"


def test_write_note_defaults_to_agent(tmp_path):
    store = _store(tmp_path)
    note = store.write_project_note("T", "body", note_kind="lesson")
    assert note["provenance"] == "agent"


def test_legacy_note_without_field_reads_as_agent(tmp_path):
    import json

    store = _store(tmp_path)
    note = store.write_project_note("T", "body", note_kind="lesson")
    path = store.project_note_path(note["note_id"])
    raw = json.loads(path.read_text())
    raw.pop("provenance", None)
    path.write_text(json.dumps(raw))
    reread = store.read_project_note(note["note_id"])
    assert reread["provenance"] == "agent"
