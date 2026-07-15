"""Agent-experience features: URI validation, KB lint without markdown, stale
episodic reporting, and remember_note auto-link + hints."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.knowledge_lint import _scan_stale_episodic_notes
from turbo_memory_mcp.server import (
    lint_knowledge_base_impl,
    link_entities_impl,
    remember_note_impl,
)
from turbo_memory_mcp.store import MemoryStore


def _env(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    return {
        "TQMEMORY_HOME": str(tmp_path / "home"),
        "TQMEMORY_PROJECT_ROOT": str(root),
        "TQMEMORY_PROJECT_ID": "agentux",
        "TQMEMORY_PROJECT_NAME": "Agent UX",
        "TQMEMORY_DAEMON_DISABLE": "1",
    }


# --------------------------------------------------------------------------- #
# ISSUE #6 — link_entities URI validation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad", ["note:abc", "file:/path", "just-text", "issue:BUG-1", "note:/x", ""])
def test_link_entities_rejects_malformed_uri(tmp_path: Path, bad: str) -> None:
    env = _env(tmp_path)
    with pytest.raises(ValueError):
        link_entities_impl(bad, "note://target", "references", environ=env, cwd=env["TQMEMORY_PROJECT_ROOT"])
    with pytest.raises(ValueError):
        link_entities_impl("note://src", bad, "references", environ=env, cwd=env["TQMEMORY_PROJECT_ROOT"])


def test_link_entities_accepts_valid_uris(tmp_path: Path) -> None:
    env = _env(tmp_path)
    result = link_entities_impl(
        "note://a1b2", "file://src/x.py", "implements", environ=env, cwd=env["TQMEMORY_PROJECT_ROOT"]
    )
    assert result["action"] == "linked"
    assert result["relation"]["target"] == "file://src/x.py"


@pytest.mark.parametrize("good", ["mailto:me@example.com", "urn:uuid:1234-5678", "tel:+15551234"])
def test_link_entities_accepts_nonhierarchical_uris(tmp_path: Path, good: str) -> None:
    # RFC 3986 non-hierarchical URIs (no '//') are valid entity targets; only
    # note/file/issue/task/http(s) require the '//' form.
    env = _env(tmp_path)
    result = link_entities_impl(
        "note://n", good, "references", environ=env, cwd=env["TQMEMORY_PROJECT_ROOT"]
    )
    assert result["action"] == "linked"
    assert result["relation"]["target"] == good


# --------------------------------------------------------------------------- #
# ISSUE #4 — lint works without markdown roots
# --------------------------------------------------------------------------- #

def test_lint_ok_without_markdown_roots(tmp_path: Path) -> None:
    env = _env(tmp_path)
    payload = lint_knowledge_base_impl(
        paths=None, max_issues=50, cwd=env["TQMEMORY_PROJECT_ROOT"], environ=env
    )
    # An MCP-only store (no indexed markdown) must not be a failure.
    assert payload["status"] == "ok"
    assert payload["summary"]["markdown_configured"] is False
    assert payload["summary"]["file_count"] == 0


# --------------------------------------------------------------------------- #
# ISSUE #3 — stale episodic note reporting
# --------------------------------------------------------------------------- #

def _store(tmp_path: Path) -> MemoryStore:
    identity = ProjectIdentity(
        project_id="eptest0000000001",
        project_name="Ep",
        project_root=tmp_path / "repo",
        identity_source="local/ep",
        identity_kind="local_path",
    )
    return MemoryStore(identity, storage_root=tmp_path / "store")


def _backdate(store: MemoryStore, note_id: str, days: int) -> None:
    path = store.project_note_path(note_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["updated_at"] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    path.write_text(json.dumps(record), encoding="utf-8")


def test_scan_stale_episodic_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TQMEMORY_EPISODIC_STALE_DAYS", "14")
    store = _store(tmp_path)

    handoff = store.write_project_note("Handoff", "session summary", note_kind="handoff", tags=["h"])
    # A fresh episodic note is not stale.
    assert _scan_stale_episodic_notes(store) == []

    _backdate(store, handoff["note_id"], days=30)
    stale = _scan_stale_episodic_notes(store)
    assert [e["note_id"] for e in stale] == [handoff["note_id"]]
    assert int(stale[0]["age_days"]) >= 14

    # A durable note of the same age is NOT reported (only episodic churns).
    durable = store.write_project_note("Lesson", "keep me", note_kind="lesson", tags=["d"])
    _backdate(store, durable["note_id"], days=60)
    assert [e["note_id"] for e in _scan_stale_episodic_notes(store)] == [handoff["note_id"]]


def test_stale_episodic_check_disabled_at_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TQMEMORY_EPISODIC_STALE_DAYS", "0")
    store = _store(tmp_path)
    handoff = store.write_project_note("Old", "x", note_kind="handoff", tags=["h"])
    _backdate(store, handoff["note_id"], days=999)
    assert _scan_stale_episodic_notes(store) == []


# --------------------------------------------------------------------------- #
# ISSUE #1 — remember_note auto-links URI source_refs + emits hints
# --------------------------------------------------------------------------- #

def test_remember_note_auto_links_uri_source_refs(tmp_path: Path) -> None:
    env = _env(tmp_path)
    stored = remember_note_impl(
        "Fix",
        "fixed the null deref",
        kind="lesson",
        tags=["bug"],
        source_refs=["file://src/x.py", "not-a-uri", "issue://BUG-1"],
        environ=env,
        cwd=env["TQMEMORY_PROJECT_ROOT"],
    )
    linked = stored.get("linked_refs", [])
    targets = {r["target"] for r in linked}
    # Only the well-formed URIs are linked; the bare string is left as metadata.
    assert targets == {"file://src/x.py", "issue://BUG-1"}
    assert all(r["type"] == "references" for r in linked)
    # With relations created, no "link this note" nudge is emitted.
    assert not any("link_entities" in h for h in stored.get("hints", []))


def test_remember_note_dedupes_duplicate_source_refs(tmp_path: Path) -> None:
    env = _env(tmp_path)
    stored = remember_note_impl(
        "Fix",
        "x",
        kind="lesson",
        tags=["t"],
        source_refs=["file://src/a.py", "file://src/a.py", " file://src/a.py "],
        environ=env,
        cwd=env["TQMEMORY_PROJECT_ROOT"],
    )
    linked = stored.get("linked_refs", [])
    # The same target linked once, not three times (graph dedupes; payload must too).
    assert [r["target"] for r in linked] == ["file://src/a.py"]


def test_remember_note_hints_when_no_tags_and_no_links(tmp_path: Path) -> None:
    env = _env(tmp_path)
    stored = remember_note_impl(
        "Quick", "handoff note", kind="handoff", environ=env, cwd=env["TQMEMORY_PROJECT_ROOT"]
    )
    hints = stored.get("hints", [])
    assert any("tags" in h.lower() for h in hints)
    assert any("link_entities" in h for h in hints)
