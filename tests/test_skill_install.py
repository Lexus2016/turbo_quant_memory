from __future__ import annotations

from pathlib import Path

from turbo_memory_mcp import __version__
from turbo_memory_mcp import skill_install as skill_install_module
from turbo_memory_mcp.cli import main
from turbo_memory_mcp.skill_install import (
    CLIENT_SKILL_DIRS,
    SKILL_NAME,
    InstallResult,
    install_skill,
    load_skill,
    parse_skill_version,
    resolve_targets,
)


def test_skill_version_matches_package_version() -> None:
    text, version = load_skill()

    assert version == __version__
    assert parse_skill_version(text) == version


def test_skill_frontmatter_and_body_are_complete() -> None:
    text, _ = load_skill()

    assert f"name: {SKILL_NAME}" in text
    assert "description:" in text
    # The four contract sections from the spec.
    assert "## 1. Detect" in text
    assert "## 2. Install & Deploy" in text
    assert "## 3. Operate" in text
    assert "## 4. Troubleshoot" in text


def test_parse_skill_version_handles_missing_marker() -> None:
    assert parse_skill_version("no frontmatter here\n") is None


def _skill_file(home: Path, client_key: str) -> Path:
    rel, _ = CLIENT_SKILL_DIRS[client_key]
    return home / rel / SKILL_NAME / "SKILL.md"


def test_resolve_targets_always_includes_universal(tmp_path: Path) -> None:
    targets = dict(resolve_targets(tmp_path))

    assert "agents" in targets  # universal dir even with an empty $HOME
    assert "claude" not in targets  # client dir not detected


def test_resolve_targets_detects_client_config_dirs(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()

    targets = dict(resolve_targets(tmp_path))

    assert "claude" in targets
    assert "gemini" not in targets


def test_resolve_targets_client_filter(tmp_path: Path) -> None:
    targets = dict(resolve_targets(tmp_path, client="codex"))

    assert list(targets) == ["codex"]  # explicit client skips detection


def test_install_writes_universal_and_detected_clients(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()

    results = install_skill(tmp_path)

    by_client = {r.client: r for r in results}
    assert by_client["agents"].status == "installed"
    assert by_client["claude"].status == "installed"
    assert _skill_file(tmp_path, "agents").is_file()
    assert _skill_file(tmp_path, "claude").is_file()


def test_install_is_idempotent(tmp_path: Path) -> None:
    install_skill(tmp_path)
    before = _skill_file(tmp_path, "agents").read_bytes()

    results = install_skill(tmp_path)

    assert {r.status for r in results} == {"current"}
    assert _skill_file(tmp_path, "agents").read_bytes() == before


def test_install_upgrades_older_copy(tmp_path: Path) -> None:
    target = _skill_file(tmp_path, "agents")
    target.parent.mkdir(parents=True)
    target.write_text("---\nname: turbo-quant-memory\nversion: 0.1.0\n---\nOLD_COPY_SENTINEL\n", encoding="utf-8")

    results = install_skill(tmp_path)

    assert results[0].status == "upgraded"
    assert results[0].old_version == "0.1.0"
    assert "OLD_COPY_SENTINEL" not in target.read_text(encoding="utf-8")


def test_install_reinstalls_copy_without_version_marker(tmp_path: Path) -> None:
    target = _skill_file(tmp_path, "agents")
    target.parent.mkdir(parents=True)
    target.write_text("garbage without frontmatter\n", encoding="utf-8")

    results = install_skill(tmp_path)

    assert results[0].status == "reinstalled"
    assert results[0].old_version is None


def test_install_dry_run_writes_nothing(tmp_path: Path) -> None:
    results = install_skill(tmp_path, dry_run=True)

    assert results[0].status == "installed"  # reports what WOULD happen
    assert not _skill_file(tmp_path, "agents").exists()


def test_install_reports_failed_target_and_continues(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    # Make the claude target unwritable: a FILE where the skill dir must go.
    blocker = tmp_path / ".claude" / "skills" / SKILL_NAME
    blocker.parent.mkdir(parents=True)
    blocker.write_text("not a dir", encoding="utf-8")

    results = install_skill(tmp_path)

    by_client = {r.client: r for r in results}
    assert by_client["claude"].status == "failed"
    assert by_client["claude"].error
    assert by_client["agents"].status == "installed"  # other targets still written


def test_install_treats_unreadable_copy_as_reinstalled(
    tmp_path: Path, monkeypatch
) -> None:
    target = _skill_file(tmp_path, "agents")
    target.parent.mkdir(parents=True)
    target.write_text("---\nversion: 0.1.0\n---\n", encoding="utf-8")

    # chmod(0) would also block the rewrite -> "failed", so simulate an
    # unreadable copy by raising OSError from read_text for this path only.
    real_read_text = Path.read_text

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if self == target:
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    results = install_skill(tmp_path)

    assert results[0].status == "reinstalled"
    assert results[0].old_version is None


def test_cli_skill_install_routes_and_reports(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()

    exit_code = main(["skill", "install"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "agents" in out and "claude" in out
    assert _skill_file(tmp_path, "agents").is_file()


def test_cli_skill_install_unknown_client(capsys) -> None:
    exit_code = main(["skill", "install", "--client", "emacs"])

    captured = capsys.readouterr()

    assert exit_code == 2
    assert "unknown client" in captured.err
    assert "agents" in captured.err  # known names are listed


def test_cli_skill_install_failure_exits_1(monkeypatch, capsys) -> None:
    def fake_install_skill(home=None, *, client=None, dry_run=False):
        return [
            InstallResult("agents", Path("/x"), "failed", None, "9.9.9", "boom")
        ]

    monkeypatch.setattr(skill_install_module, "install_skill", fake_install_skill)

    exit_code = main(["skill", "install"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "FAIL" in captured.out


def test_cli_skill_install_dry_run(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    exit_code = main(["skill", "install", "--dry-run"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "would" in out
    assert not _skill_file(tmp_path, "agents").exists()
