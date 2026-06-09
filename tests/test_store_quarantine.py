from __future__ import annotations

from pathlib import Path

import pytest

from turbo_memory_mcp.identity import ProjectIdentity
from turbo_memory_mcp.store import MemoryStore, PROJECT_SCOPE


def _build_store(tmp_path: Path) -> MemoryStore:
    identity = ProjectIdentity(
        project_id="proj1234567890abc",
        project_name="Turbo Quant Memory",
        project_root=tmp_path / "repo",
        identity_source="github.com/example/turbo-quant-memory",
        identity_kind="git_remote",
    )
    return MemoryStore(identity, storage_root=tmp_path / "central-store")


def test_list_notes_isolates_corrupt_files_and_scan_reports_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _build_store(tmp_path)
    good = store.write_project_note("Good", "valid content", note_kind="lesson")

    note_dir = store.project_notes_dir()
    malformed = note_dir / "malformed.json"
    malformed.write_text("{ not valid json", encoding="utf-8")
    bad_status = note_dir / "bad-status.json"
    bad_status.write_text('{"note_kind": "lesson", "note_status": "bogus"}', encoding="utf-8")

    listed = store.list_notes(PROJECT_SCOPE)

    # The one good note survives; neither corrupt file raises or vanishes silently.
    assert [note["note_id"] for note in listed] == [good["note_id"]]

    quarantined = store.scan_quarantined_notes(PROJECT_SCOPE)
    assert {entry["path"] for entry in quarantined} == {str(malformed), str(bad_status)}
    assert all(entry["reason"] for entry in quarantined)

    assert "skipping unreadable note" in capsys.readouterr().err


def test_scan_quarantined_notes_empty_for_clean_scope(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    store.write_project_note("Good", "content", note_kind="lesson")
    assert store.scan_quarantined_notes(PROJECT_SCOPE) == []
