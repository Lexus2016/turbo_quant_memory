from __future__ import annotations

import json
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
