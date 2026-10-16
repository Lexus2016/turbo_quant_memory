"""CLI ``files log`` and ``files restore``: journal history and undoable restore from the shell."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from turbo_memory_mcp.cli import main
from turbo_memory_mcp.workspace.db import CoordinationDB
from turbo_memory_mcp.workspace.journal import Journal, JournalLimits
from turbo_memory_mcp.workspace.paths import blobs_dir, coord_db_path, resolve_workspace

T0 = 1_800_000_000.0


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


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
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "store"
    monkeypatch.setenv("TQMEMORY_HOME", str(home))
    monkeypatch.delenv("TQMEMORY_PROJECT_ID", raising=False)
    monkeypatch.delenv("TQMEMORY_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("TQMEMORY_WORKSPACE_DISABLE", raising=False)
    return home


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    return repo


def _seed(repo: Path, storage_root: Path, rel: str, contents: list[bytes]) -> list[int]:
    """Journal each content in order (the file ends holding the last one); return the version ids."""

    workspace = resolve_workspace(repo, environ={})
    db = CoordinationDB.open(coord_db_path(storage_root, workspace.project_id))
    try:
        journal = Journal(workspace, db, blobs_dir(storage_root, workspace.project_id), JournalLimits())
        ids: list[int] = []
        for index, content in enumerate(contents):
            (repo / rel).write_bytes(content)
            record = journal.record(rel, attribution="inferred", now=T0 + index)
            assert record is not None
            ids.append(record.version_id)
        return ids
    finally:
        db.close()


def test_files_log_lists_versions(in_repo: Path, storage_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first, second = _seed(in_repo, storage_root, "notes.txt", [b"one\n", b"two\n"])

    exit_code = main(["files", "log", "notes.txt"])
    lines = capsys.readouterr().out.splitlines()

    assert exit_code == 0
    assert len(lines) == 2
    pattern = r"^{vid}  \d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}\+00:00  content  4 B  inferred$"
    assert re.match(pattern.format(vid=second), lines[0])
    assert re.match(pattern.format(vid=first), lines[1])


def test_files_log_respects_limit(in_repo: Path, storage_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ids = _seed(in_repo, storage_root, "notes.txt", [b"a\n", b"b\n", b"c\n"])

    exit_code = main(["files", "log", "notes.txt", "--limit", "1"])
    lines = capsys.readouterr().out.splitlines()

    assert exit_code == 0
    assert len(lines) == 1
    assert lines[0].startswith(f"{ids[-1]}  ")


def test_files_log_reports_no_versions(in_repo: Path, storage_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["files", "log", "never.txt"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "no versions recorded for never.txt"


def test_files_log_rebuilds_index_from_journal_log(
    in_repo: Path, storage_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second = _seed(in_repo, storage_root, "notes.txt", [b"one\n", b"two\n"])
    for leftover in coord_db_path(storage_root, resolve_workspace(in_repo, environ={}).project_id).parent.glob(
        "coord.sqlite*"
    ):
        leftover.unlink()

    exit_code = main(["files", "log", "notes.txt"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert f"{second}  " in out
    assert f"{first}  " in out


def test_files_restore_roundtrip(in_repo: Path, storage_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first, second = _seed(in_repo, storage_root, "story.txt", [b"v1 text\n", b"v2 text\nsecond line\n"])
    capsys.readouterr()

    exit_code = main(["files", "restore", "story.txt", str(first)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert (in_repo / "story.txt").read_bytes() == b"v1 text\n"
    match = re.fullmatch(rf"restored story\.txt to {first} \(previous content saved as version (\d+)\)\n", out)
    assert match is not None, out
    assert int(match.group(1)) > second


def test_files_restore_head_writes_committed_content(
    in_repo: Path, storage_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (in_repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

    exit_code = main(["files", "restore", "tracked.txt", "HEAD"])

    assert exit_code == 0
    assert (in_repo / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    assert "restored tracked.txt to HEAD (previous content saved as version " in capsys.readouterr().out


def test_restore_records_undo_version_that_restores_back(
    in_repo: Path, storage_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second = _seed(in_repo, storage_root, "story.txt", [b"v1 text\n", b"v2 text\nsecond line\n"])
    assert main(["files", "restore", "story.txt", str(first)]) == 0
    capsys.readouterr()

    assert main(["files", "log", "story.txt", "--limit", "1"]) == 0
    undo_line = capsys.readouterr().out.split()
    undo_id = int(undo_line[0])
    assert undo_id > second
    assert undo_line[-1] == "restore"

    assert main(["files", "restore", "story.txt", str(undo_id)]) == 0
    assert (in_repo / "story.txt").read_bytes() == b"v2 text\nsecond line\n"


def test_files_restore_rejects_outside_path_exit_2(
    in_repo: Path, storage_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["files", "restore", "../x", "HEAD"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "outside workspace" in captured.err
    assert "Traceback" not in captured.err


def test_files_restore_rejects_malformed_version_exit_2(
    in_repo: Path, storage_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(in_repo, storage_root, "story.txt", [b"v1\n"])

    with pytest.raises(SystemExit) as raised:
        main(["files", "restore", "story.txt", "banana"])
    captured = capsys.readouterr()

    assert raised.value.code == 2
    assert "must be HEAD or a version id" in captured.err
    assert "Traceback" not in captured.err


def test_refused_workspace_exits_2_without_traceback(
    tmp_path: Path, storage_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("hello\n", encoding="utf-8")
    monkeypatch.chdir(plain)

    log_exit = main(["files", "log", "a.txt"])
    log_err = capsys.readouterr().err
    restore_exit = main(["files", "restore", "a.txt", "HEAD"])
    restore_err = capsys.readouterr().err

    assert log_exit == 2
    assert restore_exit == 2
    assert "not a git repository" in log_err
    assert "not a git repository" in restore_err
    assert "Traceback" not in log_err + restore_err


def test_disabled_feature_exits_2_without_traceback(
    in_repo: Path, storage_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TQMEMORY_WORKSPACE_DISABLE", "TRUE")

    exit_code = main(["files", "log", "tracked.txt"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "TQMEMORY_WORKSPACE_DISABLE" in captured.err
    assert "Traceback" not in captured.err


def test_unresolvable_project_root_exits_2_without_traceback(
    in_repo: Path, storage_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TQMEMORY_PROJECT_ROOT", "~no_such_user_for_tqmemory")

    exit_code = main(["files", "log", "tracked.txt"])
    err = capsys.readouterr().err

    assert exit_code == 2
    assert "Traceback" not in err
    assert err.startswith("error: ")
    assert len(err.strip().splitlines()) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["files", "restore", "tracked.txt", "9223372036854775808"],
        ["files", "restore", "tracked.txt", "0"],
        ["files", "log", "tracked.txt", "--limit", "9223372036854775808"],
        ["files", "log", "tracked.txt", "--limit", "0"],
    ],
)
def test_out_of_range_integers_exit_2_at_parse_time(
    in_repo: Path, storage_root: Path, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(argv)
    err = capsys.readouterr().err

    assert raised.value.code == 2
    assert "9223372036854775807" in err
    assert "Traceback" not in err
