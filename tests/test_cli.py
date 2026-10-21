from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from turbo_memory_mcp import __version__
from turbo_memory_mcp.cli import build_parser, main


def _seed_bucket(home: Path, project_id: str, *, project_root: Path, note_ids: list[str]) -> Path:
    bucket = home / "projects" / project_id
    (bucket / "notes").mkdir(parents=True)
    (bucket / "manifest.json").write_text(
        json.dumps(
            {
                "scope": "project",
                "project_id": project_id,
                "project_name": project_root.name,
                "project_root": str(project_root),
                "identity_source": str(project_root),
                "identity_kind": "repo_path",
                "format_version": 2,
                "updated_at": "2026-06-06T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    for note_id in note_ids:
        (bucket / "notes" / f"{note_id}.json").write_text("{}", encoding="utf-8")
    return bucket


def test_parser_help_mentions_blessed_runtime() -> None:
    help_text = build_parser().format_help()

    assert "turbo-memory-mcp serve" in help_text
    assert "serve" in help_text


def test_main_without_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "turbo-memory-mcp serve" in captured.out


def test_version_flag_prints_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    captured = capsys.readouterr()

    assert excinfo.value.code == 0
    assert __version__ in captured.out


def test_serve_routes_to_stdio_server(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"value": False}

    def fake_run_stdio_server() -> None:
        called["value"] = True

    import turbo_memory_mcp.server as server_module

    monkeypatch.setattr(server_module, "run_stdio_server", fake_run_stdio_server)

    exit_code = main(["serve"])

    assert exit_code == 0
    assert called["value"] is True


def test_prune_orphans_dry_run_lists_without_moving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    live_root = tmp_path / "live"
    live_root.mkdir()
    live = _seed_bucket(home, "livebucket000000", project_root=live_root, note_ids=["a"])
    orphan = _seed_bucket(
        home, "deadbucket000000", project_root=tmp_path / "gone", note_ids=["x", "y"]
    )
    monkeypatch.setenv("TQMEMORY_HOME", str(home))

    exit_code = main(["prune-orphans"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "deadbucket000000" in out
    assert "livebucket000000" not in out  # live project is not an orphan
    assert "dry run" in out.lower()
    assert orphan.is_dir() and live.is_dir()  # nothing moved


def test_prune_orphans_apply_moves_to_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    live_root = tmp_path / "live"
    live_root.mkdir()
    live = _seed_bucket(home, "livebucket000000", project_root=live_root, note_ids=["a"])
    orphan = _seed_bucket(
        home, "deadbucket000000", project_root=tmp_path / "gone", note_ids=["x", "y"]
    )
    monkeypatch.setenv("TQMEMORY_HOME", str(home))

    exit_code = main(["prune-orphans", "--apply"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Moved" in out  # CLI reports the reversible move to the user
    assert not orphan.exists()  # moved out of projects/
    assert live.is_dir()  # live project untouched
    staged = list((home / "staging").glob("orphan-prune-*/deadbucket000000"))
    assert len(staged) == 1  # reversible copy preserved
    assert (staged[0] / "manifest.json").exists()


def test_prune_orphans_reports_none_when_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    live_root = tmp_path / "live"
    live_root.mkdir()
    _seed_bucket(home, "livebucket000000", project_root=live_root, note_ids=["a"])
    monkeypatch.setenv("TQMEMORY_HOME", str(home))

    exit_code = main(["prune-orphans"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "No orphaned buckets" in out


def test_doctor_clean_storage_reports_all_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TQMEMORY_HOME", str(home))

    exit_code = main(["doctor"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "[PASS] storage_root" in out
    assert "no lock present" in out
    assert "All checks passed." in out


def test_doctor_flags_stale_lock_with_dead_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TQMEMORY_HOME", str(home))
    # A lockfile naming a PID that cannot be alive — the classic stale-lock
    # state behind silent MCP timeouts after an unclean shutdown.
    lock = home / ".daemon.lock"
    lock.write_text(
        json.dumps(
            {
                "pid": 2**31 - 1,
                "protocol_version": "1.0",
                "server_version": "0.0.0",
                "address": str(home / ".phantom.sock"),
                "family": "AF_UNIX",
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["doctor"])
    out = capsys.readouterr().out

    assert exit_code >= 1  # exit code == number of issues found
    assert "lockfile stale" in out
    assert "is dead" in out
    assert str(lock) in out  # exact `rm` target is surfaced


def test_doctor_flags_pending_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    # A storage root with project buckets but no secrets marker is exactly the
    # secrets v1->v2 "pending migration" state from the bug report.
    (home / "projects" / "somebucket000000").mkdir(parents=True)
    monkeypatch.setenv("TQMEMORY_HOME", str(home))

    exit_code = main(["doctor"])
    out = capsys.readouterr().out

    assert exit_code >= 1
    assert "migrations:" in out
    assert "pending" in out
    assert "secrets" in out


def test_doctor_skips_socket_probe_for_named_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TQMEMORY_HOME", str(home))
    # A live primary on Windows advertises a named pipe (AF_PIPE), which is not
    # a connectable filesystem socket — doctor must skip the probe cleanly
    # instead of emitting a false [FAIL].
    (home / ".daemon.lock").write_text(
        json.dumps(
            {
                "pid": os.getpid(),  # this process — guaranteed alive
                "protocol_version": "1.0",
                "server_version": "0.0.0",
                "address": r"\\.\pipe\tqmemory-test",
                "family": "AF_PIPE",
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["doctor"])
    out = capsys.readouterr().out

    assert "live primary" in out  # live PID is not treated as stale
    assert "probe skipped for family=AF_PIPE" in out
    assert exit_code == 0  # a skipped probe is not an issue


# --- doctor read path (GitHub issue #7) ---
# doctor checked the lock, the socket, migrations and the project, but never opened
# the index, so it said "All checks passed" for a table search could not read.


class _FlatEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]


def _index_a_note(home: Path, monkeypatch: pytest.MonkeyPatch):
    """One project note in a synced index under ``home``; returns the store."""
    from turbo_memory_mcp.retrieval_index import RetrievalIndex
    from turbo_memory_mcp.server import build_runtime_context

    monkeypatch.setenv("TQMEMORY_HOME", str(home))
    _, store = build_runtime_context()
    store.write_project_note("Auth", "Auth body", note_kind="decision", note_id="auth-note")
    RetrievalIndex(store, embedder=_FlatEmbedder()).sync_project_notes(["auth-note"])
    return store


def _null_the_list_columns(store) -> None:
    """Leave the project table as 0.32.0 repaired it: declared types, NULL list columns."""
    import lancedb
    import pyarrow as pa

    from turbo_memory_mcp.retrieval_index import RetrievalIndex, _table_schema

    index = RetrievalIndex(store, embedder=_FlatEmbedder())
    rows = [{**row, "heading_path": None, "tags": None} for row in index.list_rows("project")]
    schema = _table_schema(384)
    table = lancedb.connect(str(index.project_db_path())).create_table("items", schema=schema, mode="overwrite")
    table.add(pa.Table.from_pylist(rows, schema=schema))


def test_doctor_reports_the_read_path_of_a_healthy_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _index_a_note(home, monkeypatch)

    main(["doctor"])
    out = capsys.readouterr().out

    assert "[PASS] read path (project): 1 rows checked" in out
    assert "[PASS] read path (global): 0 rows checked" in out


def test_doctor_fails_when_the_index_cannot_be_read_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from unittest.mock import patch

    from turbo_memory_mcp import retrieval_index
    from turbo_memory_mcp.retrieval_index import RetrievalIndex

    home = tmp_path / "home"
    home.mkdir()
    store = _index_a_note(home, monkeypatch)
    healthy_code = main(["doctor"])  # other checks may already count issues (pending migrations)
    capsys.readouterr()
    _null_the_list_columns(store)
    version = RetrievalIndex(store)._open_scope_table("project").version

    # Search reads NULL as [] at its boundary; take that away so the row is one search would choke on.
    with patch.object(retrieval_index, "_normalize_row", new=dict):
        code = main(["doctor"])
    out = capsys.readouterr().out

    assert "[FAIL] read path (project): auth-note: TypeError" in out
    assert code == healthy_code + 1  # exactly one more issue, so a non-zero exit code
    assert RetrievalIndex(store)._open_scope_table("project").version == version


def test_doctor_does_not_flag_null_list_columns_that_search_reads_fine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    store = _index_a_note(home, monkeypatch)
    _null_the_list_columns(store)

    main(["doctor"])
    out = capsys.readouterr().out

    assert "[PASS] read path (project): 1 rows checked" in out


def test_doctor_reports_a_missing_index_as_passing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TQMEMORY_HOME", str(home))

    exit_code = main(["doctor"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "[PASS] read path (project): 0 rows checked" in out
    assert "All checks passed." in out
