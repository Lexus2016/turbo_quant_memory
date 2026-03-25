from __future__ import annotations

import pytest

from turbo_memory_mcp import __version__
from turbo_memory_mcp.cli import build_parser, main


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
