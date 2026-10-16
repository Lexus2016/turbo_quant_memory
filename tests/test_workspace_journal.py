"""Version journal: content-addressed snapshots, undoable restore, retention and index rebuild."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from turbo_memory_mcp.workspace.db import CoordinationDB
from turbo_memory_mcp.workspace.journal import Journal, JournalLimits
from turbo_memory_mcp.workspace.models import Workspace
from turbo_memory_mcp.workspace.paths import blobs_dir, coord_db_path, resolve_workspace

DAY = 86400.0
T0 = 1_800_000_000.0


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    return tmp_path / "store"


@pytest.fixture
def workspace(repo: Path) -> Workspace:
    return resolve_workspace(repo, environ={})


@pytest.fixture
def db(storage_root: Path, workspace: Workspace) -> CoordinationDB:
    handle = CoordinationDB.open(coord_db_path(storage_root, workspace.project_id))
    yield handle
    handle.close()


def _journal(
    workspace: Workspace,
    db: CoordinationDB,
    storage_root: Path,
    limits: JournalLimits | None = None,
) -> Journal:
    return Journal(
        workspace,
        db,
        blobs_dir(storage_root, workspace.project_id),
        limits or JournalLimits(),
    )


def _log_path(storage_root: Path, workspace: Workspace) -> Path:
    return blobs_dir(storage_root, workspace.project_id).parent / "journal.log"


def test_record_dedupes_unchanged_content(repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path):
    journal = _journal(workspace, db, storage_root)
    (repo / "notes.txt").write_text("first\n", encoding="utf-8")

    first = journal.record("notes.txt", attribution="inferred", session_id="s1", now=T0)
    second = journal.record("notes.txt", now=T0 + 1)

    assert first is not None
    assert second is None
    assert len(journal.history("notes.txt")) == 1


def test_record_skips_large_and_binary(repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path):
    journal = _journal(workspace, db, storage_root)
    (repo / "big.bin").write_bytes(b"a" * (2 * 1024 * 1024))
    (repo / "image.dat").write_bytes(b"PNG\x00\x01binary")

    assert journal.record("big.bin", now=T0) is None
    assert journal.record("image.dat", now=T0) is None
    assert journal.history("big.bin") == []
    assert journal.history("image.dat") == []


def test_history_newest_first_and_read_version(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path
):
    journal = _journal(workspace, db, storage_root)
    contents = [b"one\n", b"two\n", b"three\n"]
    ids = []
    for index, content in enumerate(contents):
        (repo / "doc.txt").write_bytes(content)
        record = journal.record("doc.txt", now=T0 + index)
        assert record is not None
        ids.append(record.version_id)

    history = journal.history("doc.txt")

    assert [entry.version_id for entry in history] == sorted(ids, reverse=True)
    assert journal.read_version(ids[0]) == b"one\n"


def test_restore_is_undoable(repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path):
    journal = _journal(workspace, db, storage_root)
    target = repo / "story.txt"
    target.write_text("v1 text\n", encoding="utf-8")
    v1 = journal.record("story.txt", now=T0)
    target.write_text("v2 text\nsecond line\n", encoding="utf-8")
    v2 = journal.record("story.txt", now=T0 + 1)
    assert v1 is not None and v2 is not None

    result = journal.restore("story.txt", v1.version_id)

    assert target.read_text(encoding="utf-8") == "v1 text\n"
    assert result["status"] == "ok"
    assert result["path"] == "story.txt"
    assert result["restored_version"] == v1.version_id
    snapshot = result["previous_version_id"]
    assert snapshot is not None
    snapshot_row = next(entry for entry in journal.history("story.txt") if entry.version_id == snapshot)
    assert snapshot_row.attribution == "restore"
    assert journal.read_version(snapshot) == b"v2 text\nsecond line\n"
    assert result["diff"] == {"added": 1, "removed": 2}

    journal.restore("story.txt", snapshot)

    assert target.read_text(encoding="utf-8") == "v2 text\nsecond line\n"


def test_restore_deleted_file_from_last_version_and_from_head(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path
):
    journal = _journal(workspace, db, storage_root)
    dirty = repo / "dirty.txt"
    dirty.write_text("saved\n", encoding="utf-8")
    saved = journal.record("dirty.txt", now=T0)
    assert saved is not None
    dirty.write_text("unsaved edits\n", encoding="utf-8")
    dirty.unlink()

    by_id = journal.restore("dirty.txt", saved.version_id)

    assert dirty.read_text(encoding="utf-8") == "saved\n"
    assert by_id["previous_version_id"] is None

    tracked = repo / "tracked.txt"
    tracked.unlink()

    by_head = journal.restore("tracked.txt", "HEAD")

    assert tracked.read_text(encoding="utf-8") == "committed\n"
    assert by_head["restored_version"] == "HEAD"
    assert by_head["diff"] == {"added": 1, "removed": 0}


def test_restore_rejects_path_outside_workspace(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path
):
    journal = _journal(workspace, db, storage_root)
    outside = repo.parent / "outside.txt"

    with pytest.raises(ValueError, match="path outside workspace"):
        journal.restore("../outside.txt", "HEAD")

    assert not outside.exists()


def test_unicode_and_space_paths_roundtrip(repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path):
    journal = _journal(workspace, db, storage_root)
    rel = "docs/Мої нотатки/Read Me.md"
    target = repo / "docs" / "Мої нотатки" / "Read Me.md"
    target.parent.mkdir(parents=True)
    target.write_text("сім\n", encoding="utf-8")

    record = journal.record(rel, now=T0)

    assert record is not None
    assert record.path == rel
    assert [entry.path for entry in journal.history(rel)] == [rel]
    target.write_text("changed\n", encoding="utf-8")
    result = journal.restore(rel, record.version_id)
    assert result["path"] == rel
    assert target.read_text(encoding="utf-8") == "сім\n"


def _incompressible(tag: str) -> str:
    # Hex digits compress only about 2:1, so the blobs stay well above the tiny cap used below.
    return tag + "".join(f"{index:08x}" for index in range(200)) + "\n"


def test_eviction_respects_age_cap_and_protects_recent_newest(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path
):
    limits = JournalLimits(max_file_bytes=1_048_576, max_total_bytes=100, retention_days=14)
    journal = _journal(workspace, db, storage_root, limits)
    (repo / "old.txt").write_text(_incompressible("ancient "), encoding="utf-8")
    ancient = journal.record("old.txt", now=T0 - 15 * DAY)
    (repo / "recent.txt").write_text(_incompressible("recent "), encoding="utf-8")
    fresh = journal.record("recent.txt", now=T0 - 3600)
    assert ancient is not None and fresh is not None

    removed = journal.evict(now=T0)

    assert removed == 1
    assert journal.history("old.txt") == []
    assert [entry.version_id for entry in journal.history("recent.txt")] == [fresh.version_id]
    assert journal.read_version(fresh.version_id) == _incompressible("recent ").encode("utf-8")
    assert journal.over_cap is True


def test_blobs_live_outside_repository(repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path):
    journal = _journal(workspace, db, storage_root)
    (repo / "scratch.txt").write_text("draft\n", encoding="utf-8")
    before = _git(repo, "status", "--porcelain")

    record = journal.record("scratch.txt", now=T0)

    assert record is not None
    assert _git(repo, "status", "--porcelain") == before
    assert record.sha256 is not None
    blob = blobs_dir(storage_root, workspace.project_id) / record.sha256[:2] / f"{record.sha256}.z"
    assert blob.is_file()


def test_index_rebuilt_from_log_after_db_loss(repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path):
    journal = _journal(workspace, db, storage_root)
    target = repo / "log.txt"
    target.write_text("first\n", encoding="utf-8")
    older = journal.record("log.txt", now=T0)
    target.write_text("second\n", encoding="utf-8")
    journal.record("log.txt", now=T0 + 1)
    db.close()
    db_file = coord_db_path(storage_root, workspace.project_id)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_file}{suffix}").unlink(missing_ok=True)

    fresh_db = CoordinationDB.open(db_file)
    try:
        rebuilt = _journal(workspace, fresh_db, storage_root)
        assert rebuilt.rebuild_index_if_empty() == 2
        assert len(rebuilt.history("log.txt")) == 2
        assert rebuilt.rebuild_index_if_empty() == 0
        assert older is not None
        oldest_rebuilt = rebuilt.history("log.txt")[-1]
        assert oldest_rebuilt.sha256 == older.sha256
        rebuilt.restore("log.txt", oldest_rebuilt.version_id)
        assert target.read_text(encoding="utf-8") == "first\n"
    finally:
        fresh_db.close()


def test_evict_compacts_log(repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path):
    journal = _journal(workspace, db, storage_root)
    (repo / "a.txt").write_text("expired\n", encoding="utf-8")
    journal.record("a.txt", now=T0 - 20 * DAY)
    (repo / "b.txt").write_text("kept\n", encoding="utf-8")
    journal.record("b.txt", now=T0)

    journal.evict(now=T0)

    lines = _log_path(storage_root, workspace).read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    assert [entry["path"] for entry in entries] == ["b.txt"]
    assert set(entries[0]) == {
        "workspace",
        "path",
        "kind",
        "sha256",
        "size",
        "observed_at",
        "attribution",
        "session_id",
    }


def test_file_events_carry_attribution_and_feature_hint(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path
):
    journal = _journal(workspace, db, storage_root)
    (repo / "hinted.txt").write_text("hint\n", encoding="utf-8")
    saved = journal.record("hinted.txt", attribution="inferred", session_id="s9", now=T0, feature="checkout")
    (repo / "hinted.txt").unlink()
    deleted = journal.record_deletion("hinted.txt", session_id="s9", now=T0 + 1)

    events = db.events_after(workspace.root_key, 0)

    assert saved is not None and deleted is not None
    assert [event.kind for event in events] == ["file_changed", "file_deleted"]
    assert events[0].path == "hinted.txt"
    assert events[0].detail == {
        "attribution": "inferred",
        "session_id": "s9",
        "version_id": saved.version_id,
        "feature": "checkout",
    }
    assert events[1].detail == {"attribution": "unknown", "session_id": "s9", "version_id": deleted.version_id}


def test_restore_refuses_to_overwrite_oversized_current_file(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path
):
    limits = JournalLimits(max_file_bytes=64)
    journal = _journal(workspace, db, storage_root, limits)
    (repo / "grow.txt").write_text("small\n", encoding="utf-8")
    saved = journal.record("grow.txt", now=T0)
    assert saved is not None
    big = repo / "grow.txt"
    big.write_text("x" * 200, encoding="utf-8")

    with pytest.raises(ValueError, match="not overwritten"):
        journal.restore("grow.txt", saved.version_id)

    assert big.read_text(encoding="utf-8") == "x" * 200
    assert [entry.attribution for entry in journal.history("grow.txt")] == ["unknown"]


def test_restore_rejects_wrong_path_and_deletion_versions(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path
):
    journal = _journal(workspace, db, storage_root)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    (repo / "two.txt").write_text("two\n", encoding="utf-8")
    one = journal.record("one.txt", now=T0)
    two = journal.record("two.txt", now=T0)
    gone = journal.record_deletion("two.txt", now=T0 + 1)
    assert one is not None and two is not None and gone is not None

    with pytest.raises(ValueError, match="different path"):
        journal.restore("two.txt", one.version_id)
    with pytest.raises(ValueError, match="cannot be restored"):
        journal.restore("two.txt", gone.version_id)
    with pytest.raises(ValueError, match="no version"):
        journal.restore("two.txt", 99_999)
    with pytest.raises(ValueError, match="not in HEAD"):
        journal.restore("never-committed.txt", "HEAD")

    assert (repo / "two.txt").read_text(encoding="utf-8") == "two\n"
    assert (repo / "one.txt").read_text(encoding="utf-8") == "one\n"


def test_limits_from_environ_reads_and_validates():
    assert JournalLimits.from_environ({}) == JournalLimits()
    limits = JournalLimits.from_environ(
        {
            "TQMEMORY_JOURNAL_MAX_FILE_BYTES": "2048",
            "TQMEMORY_JOURNAL_MAX_BYTES": " 4096 ",
            "TQMEMORY_JOURNAL_RETENTION_DAYS": "3",
        }
    )
    assert limits == JournalLimits(max_file_bytes=2048, max_total_bytes=4096, retention_days=3)
    with pytest.raises(ValueError, match="TQMEMORY_JOURNAL_RETENTION_DAYS"):
        JournalLimits.from_environ({"TQMEMORY_JOURNAL_RETENTION_DAYS": "0"})
    with pytest.raises(ValueError, match="TQMEMORY_JOURNAL_MAX_BYTES"):
        JournalLimits.from_environ({"TQMEMORY_JOURNAL_MAX_BYTES": "lots"})


def _blob_file(storage_root: Path, workspace: Workspace, digest: str) -> Path:
    return blobs_dir(storage_root, workspace.project_id) / digest[:2] / f"{digest}.z"


def test_restore_head_refuses_committed_directory_and_writes_nothing(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path
):
    journal = _journal(workspace, db, storage_root)
    (repo / "docs").mkdir()
    (repo / "docs" / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "add docs")
    shutil.rmtree(repo / "docs")

    with pytest.raises(ValueError, match="committed file"):
        journal.restore("docs", "HEAD")

    assert not (repo / "docs").exists()


def test_evict_rolls_back_cleanly_when_log_rewrite_fails(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path, monkeypatch: pytest.MonkeyPatch
):
    limits = JournalLimits(max_file_bytes=1_048_576, max_total_bytes=268_435_456, retention_days=14)
    journal = _journal(workspace, db, storage_root, limits)
    (repo / "old.txt").write_text(_incompressible("old "), encoding="utf-8")
    expired = journal.record("old.txt", now=T0 - 20 * DAY)
    (repo / "new.txt").write_text(_incompressible("new "), encoding="utf-8")
    kept = journal.record("new.txt", now=T0)
    assert expired is not None and kept is not None
    real_replace = os.replace

    def failing_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if Path(dst).name == "journal.log":
            raise OSError("disk trouble")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk trouble"):
        journal.evict(now=T0)
    monkeypatch.undo()

    assert [entry.version_id for entry in journal.history("old.txt")] == [expired.version_id]
    assert journal.read_version(expired.version_id) == _incompressible("old ").encode("utf-8")
    assert journal.read_version(kept.version_id) == _incompressible("new ").encode("utf-8")


def test_evict_tolerates_blob_unlink_failure_and_sweeps_later(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path, monkeypatch: pytest.MonkeyPatch
):
    journal = _journal(workspace, db, storage_root)
    (repo / "old.txt").write_text("expired body\n", encoding="utf-8")
    expired = journal.record("old.txt", now=T0 - 20 * DAY)
    (repo / "new.txt").write_text("kept body\n", encoding="utf-8")
    kept = journal.record("new.txt", now=T0)
    assert expired is not None and expired.sha256 is not None and kept is not None
    real_unlink = Path.unlink

    def failing_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.suffix == ".z":
            raise PermissionError("blob is open in another process")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    assert journal.evict(now=T0) == 1
    monkeypatch.undo()

    orphan = _blob_file(storage_root, workspace, expired.sha256)
    assert orphan.is_file()
    assert journal.read_version(kept.version_id) == b"kept body\n"
    assert journal.evict(now=T0) == 0
    assert not orphan.exists()
    assert journal.read_version(kept.version_id) == b"kept body\n"


def test_evict_keeps_only_surviving_log_lines(repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path):
    journal = _journal(workspace, db, storage_root)
    (repo / "a.txt").write_text("expired\n", encoding="utf-8")
    journal.record("a.txt", now=T0 - 20 * DAY)
    (repo / "b.txt").write_text("kept\n", encoding="utf-8")
    journal.record("b.txt", now=T0)
    with _log_path(storage_root, workspace).open("a", encoding="utf-8") as handle:
        handle.write('{"torn": \n')

    journal.evict(now=T0)

    entries = [json.loads(line) for line in _log_path(storage_root, workspace).read_text(encoding="utf-8").splitlines()]
    assert [entry["path"] for entry in entries] == ["b.txt"]


def test_evict_on_empty_index_keeps_blobs_and_log_for_rebuild(
    repo: Path, workspace: Workspace, db: CoordinationDB, storage_root: Path
):
    journal = _journal(workspace, db, storage_root)
    (repo / "keep.txt").write_text("only copy\n", encoding="utf-8")
    saved = journal.record("keep.txt", now=T0)
    assert saved is not None and saved.sha256 is not None
    db.close()
    db_file = coord_db_path(storage_root, workspace.project_id)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_file}{suffix}").unlink(missing_ok=True)

    fresh_db = CoordinationDB.open(db_file)
    try:
        rebuilt = _journal(workspace, fresh_db, storage_root)
        assert rebuilt.evict(now=T0 + 365 * DAY) == 0
        assert _blob_file(storage_root, workspace, saved.sha256).is_file()
        assert rebuilt.rebuild_index_if_empty() == 1
        assert rebuilt.read_version(rebuilt.history("keep.txt")[0].version_id) == b"only copy\n"
    finally:
        fresh_db.close()
