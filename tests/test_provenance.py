from __future__ import annotations

from unittest.mock import patch

import pytest

from turbo_memory_mcp.retrieval import semantic_search
from turbo_memory_mcp.server import build_runtime_context, remember_note_impl
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


def test_remember_note_defaults_to_agent(tmp_path):
    env = _env(tmp_path)
    payload = remember_note_impl(
        "T", "body", kind="lesson", cwd=tmp_path / "repo", environ=env
    )
    _, store = build_runtime_context(cwd=tmp_path / "repo", environ=env)
    note = store.read_project_note(payload["item"]["item_id"])
    assert note["provenance"] == "agent"


def test_remember_note_human_explicit(tmp_path):
    env = _env(tmp_path)
    payload = remember_note_impl(
        "T", "body", kind="decision",
        provenance="human-explicit", cwd=tmp_path / "repo", environ=env,
    )
    _, store = build_runtime_context(cwd=tmp_path / "repo", environ=env)
    note = store.read_project_note(payload["item"]["item_id"])
    assert note["provenance"] == "human-explicit"


def test_remember_payload_surfaces_provenance(tmp_path):
    env = _env(tmp_path)
    payload = remember_note_impl(
        "T", "body", kind="decision",
        provenance="human-explicit", cwd=tmp_path / "repo", environ=env,
    )
    assert payload["item"]["provenance"] == "human-explicit"


class _KeywordEmbedder:
    KEYWORDS = ("auth", "token", "rotation", "refresh", "session", "cache")

    def encode(self, texts):
        out = []
        for text in texts:
            low = text.lower()
            vec = [0.0] * 384
            for i, kw in enumerate(self.KEYWORDS):
                vec[i] = 1.0 if kw in low else 0.0
            out.append(vec)
        return out


def test_human_explicit_ranks_above_agent(tmp_path):
    env = _env(tmp_path)
    cwd = tmp_path / "repo"
    with patch(
        "turbo_memory_mcp.retrieval_index.build_default_embedder",
        return_value=_KeywordEmbedder(),
    ):
        # Two notes with identical embedding (same keywords) -> equal base
        # relevance. Only provenance differs.
        remember_note_impl(
            "Agent note", "auth token rotation refresh", kind="lesson",
            provenance="agent", cwd=cwd, environ=env,
        )
        remember_note_impl(
            "Human note", "auth token rotation refresh", kind="lesson",
            provenance="human-explicit", cwd=cwd, environ=env,
        )
        _, store = build_runtime_context(cwd=cwd, environ=env)
        result = semantic_search(
            store, "auth token rotation refresh", scope="project", limit=5
        )
    titles = [item["title"] for item in result["items"]]
    assert titles.index("Human note") < titles.index("Agent note")
    human = next(i for i in result["items"] if i["title"] == "Human note")
    assert human["provenance"] == "human-explicit"
