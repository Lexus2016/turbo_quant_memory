# Agent Skill (`turbo-quant-memory` + `skill install` CLI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a canonical agent skill (SKILL.md) packaged inside the wheel, plus an idempotent `turbo-memory-mcp skill install` CLI that deploys/upgrades it into all detected agent skill directories, and repoint the READMEs at the skill.

**Architecture:** One canonical `src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md` (packaged as a resource, `version` frontmatter marker synced with the package version). A small pure module `skill_install.py` (load / parse / resolve targets / install) with no CLI concerns; `cli.py` gains a thin `skill install` handler. READMEs (en/ru/uk) replace the full agent directive with a pointer to the skill.

**Tech Stack:** Python ≥3.11, stdlib only (`importlib.resources`, `argparse`, `dataclasses`, `re`), hatchling packaging, pytest.

**Spec:** `docs/superpowers/specs/2026-07-25-agent-skill-design.md`

## Global Constraints

- Skill `version` frontmatter MUST equal `turbo_memory_mcp.__version__` (currently `0.23.0`); a test enforces this.
- `~/.agents/skills/` (client key `agents`) is ALWAYS written; other clients only when their config dir exists in `$HOME`.
- Statuses are exactly: `installed`, `upgraded`, `reinstalled` (marker missing on existing file), `current`, `failed`.
- No new dependencies; no `skill uninstall`; no backups of overwritten skills.
- CLI exit codes: `0` all good, `1` load/write failure, `2` unknown `--client`.
- Existing suites (`pytest -q`) must stay green.
- Documentation policy: README changes land in all three files — `README.md`, `README.uk.md`, `README.ru.md`.

---

### Task 1: Canonical SKILL.md + `load_skill`/`parse_skill_version` + version-sync test

**Files:**
- Create: `src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md`
- Create: `src/turbo_memory_mcp/skill_install.py`
- Test: `tests/test_skill_install.py`

**Interfaces:**
- Produces: `skill_install.load_skill() -> tuple[str, str]` (text, version), `skill_install.parse_skill_version(text: str) -> str | None`, `skill_install.SKILL_NAME = "turbo-quant-memory"`. Later tasks consume these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skill_install.py`:

```python
from __future__ import annotations

from turbo_memory_mcp import __version__
from turbo_memory_mcp.skill_install import (
    SKILL_NAME,
    load_skill,
    parse_skill_version,
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_skill_install.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'turbo_memory_mcp.skill_install'`.

- [ ] **Step 3: Write the canonical SKILL.md**

Create `src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md` with exactly this content:

````markdown
---
name: turbo-quant-memory
description: Install, configure, and operate the Turbo Quant Memory (tqmemory) MCP server. Use when setting up persistent agent memory in a workspace, registering the tqmemory MCP server in a client, or working with tqmemory tools (remember_note, semantic_search, recent_context, link_entities, secrets vault).
version: 0.23.0
---

# Turbo Quant Memory (tqmemory)

Local-first MCP memory server for AI coding agents: compact retrieval, knowledge graph, secrets vault. You are the agent — this skill tells you how to detect, install, and operate it.

## 1. Detect

Check whether tqmemory is already available:

1. **MCP tools present?** If you can call `health` / `server_info`, tqmemory is installed and registered. Call `health()`:
   - `migrations_pending: true` → surface the `migrations_hint` field verbatim to the user. Do NOT run `migrate --apply` yourself — active MCP clients must close first.
2. **CLI present but not registered?** Run `turbo-memory-mcp --version` in a shell. If it answers, the tool is installed and only MCP registration in the current client may be missing (see §2.3).
3. **Neither?** Go to §2.

## 2. Install & Deploy

### 2.1 Install the CLI

```bash
uv tool install git+https://github.com/Lexus2016/turbo_quant_memory
# or pinned: uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.23.0
```

### 2.2 Install this skill

```bash
turbo-memory-mcp skill install
```

Copies the canonical SKILL.md into all detected agent skill directories (`~/.agents/skills/` always, plus detected client dirs). Idempotent — re-run after every `uv tool upgrade` to upgrade older copies.

### 2.3 Register the MCP server in the client

```bash
# Codex
codex mcp add tqmemory -- turbo-memory-mcp serve

# Gemini CLI
gemini mcp add tqmemory turbo-memory-mcp serve

# Claude Code (project scope)
claude mcp add --scope project tqmemory -- turbo-memory-mcp serve
```

For other clients (Cursor, OpenCode, Antigravity, Hermes), see `CLIENT_INTEGRATIONS.md` in the repository. Restart the client afterwards.

### 2.4 Verify and index

1. After the client restarts, call `health()` — expect `status: "ok"`.
2. Index the project's Markdown docs: `index_paths()` (defaults to the project root).
3. Call `server_info()` and note the `project_id` — memory is scoped to it.

## 3. Operate

### 3.1 Session-start ritual

1. Call `health()` + `server_info()` (migrations check — see §1).
2. Call `recent_context()` FIRST — a query-free bootstrap returning your most recently updated notes (newest first), **including session `handoff` notes** that a plain `semantic_search` hides by default.
3. For a specific task: `semantic_search(query="<task topic>", scope="hybrid")`. Asking "what did we decide/learn about X"? Pass `source_filter="notes"` so indexed doc blocks don't crowd decision/lesson notes out of the top ranks. Recovering a handoff by query? Pass `tier_filter=["episodic"]`.

### 3.2 Memory writing discipline

When you learn something important, fix a complex bug, or make an architectural decision — save it IMMEDIATELY with `remember_note()`; do not wait for session end.

- `kind="lesson"` — hard-won bug fixes and gotchas.
- `kind="decision"` — structural or tooling choices.
- `kind="pattern"` — reusable templates and conventions.
- `kind="handoff"` — episodic progress before pausing or ending a session (lands in the `episodic` tier).
- The USER explicitly asked to remember something → `remember_note(..., provenance="human-explicit")` (ranks above agent-written notes). Your own notes keep the default `provenance="agent"`.
- Write notes in English: concise, technical, actionable, with 2–3 lowercase semantic tags. Never write smoke/temporary notes.

### 3.3 Knowledge graph linking

- Note about a bug fix: `link_entities(source_uri="note://<note_id>", target_uri="file://src/auth.py", relation_type="fixes")` — file URIs are **project-root-relative**, never absolute.
- Note supersedes a note: `link_entities(source_uri="note://<new>", target_uri="note://<old>", relation_type="supersedes")`.
- File implements a task: `link_entities(source_uri="file://src/auth.py", target_uri="task://TASK-101", relation_type="implements")`.
- Browse associations with `get_related_entities(uri)`; remove wrong links with `unlink_entities(...)`.

### 3.4 Updating memory

- Knowledge replaced? Write the NEW note first, then `deprecate_note(old_id)` on the old one — never leave both active (search pollution).
- Global scope is for reusable cross-project knowledge only, by explicit promotion (`promote_note`).
- Preserve provenance: keep file paths and line numbers in note payloads.

### 3.5 Secrets vault

- **Discover, don't guess:** find the right `get_secret(name)` call via `semantic_search` for a `pattern`-kind recipe note that documents the credential. Never fish names from chat history.
- Read the value from the dedicated `secret_value` field; pass it programmatically (env var, subprocess argument, in-memory). NEVER echo it into summaries, logs, or `remember_note`.
- The user pasted a credential into chat (or you generated one in-conversation) → call `set_secret(name, value)` directly; the exposure already happened, the CLI adds no secrecy now.
- The user is ABOUT to share a credential but hasn't → suggest `turbo-memory-mcp secret-set NAME` from a terminal (getpass keeps it out of the chat entirely).
- `master_key_unavailable` → the response carries a `setup_hint` field with the exact commands the user needs. Print it verbatim and stop.

## 4. Troubleshoot

| Symptom | Action |
| --- | --- |
| `migrations_pending` in `health()` | Surface `migrations_hint` verbatim; the user runs `turbo-memory-mcp migrate --apply` with MCP clients closed. Never self-apply. |
| MCP tool timeouts | Stale daemon lock: `pkill -f turbo-memory-mcp && rm -f ~/.turbo-quant-memory/.daemon.lock`, then `turbo-memory-mcp doctor`. |
| `master_key_unavailable` | Print the `setup_hint` field verbatim; stop. |
| Anything else | `turbo-memory-mcp doctor` runs lock / migration / storage / socket diagnostics and prints PASS/FAIL per check. |
````

- [ ] **Step 4: Write `skill_install.py` (load + parse only for now)**

Create `src/turbo_memory_mcp/skill_install.py`:

```python
"""Deploy the canonical agent skill (SKILL.md) into agent skill directories."""

from __future__ import annotations

import re
from importlib import resources

SKILL_NAME = "turbo-quant-memory"
SKILL_RESOURCE = "skills/turbo-quant-memory/SKILL.md"

_VERSION_RE = re.compile(r"^version:\s*([0-9][0-9A-Za-z.\-]*)\s*$", re.MULTILINE)


def load_skill() -> tuple[str, str]:
    """Return (text, version) of the packaged canonical SKILL.md."""
    text = (
        resources.files("turbo_memory_mcp")
        .joinpath(SKILL_RESOURCE)
        .read_text(encoding="utf-8")
    )
    version = parse_skill_version(text)
    if version is None:
        raise ValueError(f"packaged {SKILL_RESOURCE} has no version marker")
    return text, version


def parse_skill_version(text: str) -> str | None:
    """Extract the `version:` frontmatter marker, or None when absent."""
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_skill_install.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Verify the wheel packages SKILL.md**

Run: `rm -f dist/*.whl && .venv/bin/uv build --wheel 2>&1 | tail -2 && unzip -l dist/*.whl | grep SKILL.md`
Expected: a line like `src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md` (hatchling includes non-Python package files by default). If MISSING, add to `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/turbo_memory_mcp/skills" = "turbo_memory_mcp/skills"
```

and re-run this step.

- [ ] **Step 7: Commit**

```bash
git add src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md src/turbo_memory_mcp/skill_install.py tests/test_skill_install.py
git commit -m "feat: canonical turbo-quant-memory agent skill (SKILL.md) + loader"
```

---

### Task 2: `resolve_targets` + `install_skill` (target detection, idempotent upgrade)

**Files:**
- Modify: `src/turbo_memory_mcp/skill_install.py`
- Test: `tests/test_skill_install.py`

**Interfaces:**
- Consumes: `load_skill`, `parse_skill_version`, `SKILL_NAME` (Task 1).
- Produces: `CLIENT_SKILL_DIRS: dict[str, tuple[str, bool]]`, `InstallResult` dataclass (`client: str`, `target: Path`, `status: str`, `old_version: str | None`, `new_version: str`, `error: str | None = None`), `resolve_targets(home: Path, client: str | None = None) -> list[tuple[str, Path]]`, `install_skill(home: Path | None = None, *, client: str | None = None, dry_run: bool = False) -> list[InstallResult]`. Task 3 consumes these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skill_install.py`:

```python
from pathlib import Path

from turbo_memory_mcp.skill_install import (
    CLIENT_SKILL_DIRS,
    install_skill,
    resolve_targets,
)


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
    target.write_text("---\nname: turbo-quant-memory\nversion: 0.1.0\n---\nold\n", encoding="utf-8")

    results = install_skill(tmp_path)

    assert results[0].status == "upgraded"
    assert results[0].old_version == "0.1.0"
    assert "old" not in target.read_text(encoding="utf-8")


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_skill_install.py -v`
Expected: FAIL — `ImportError: cannot import name 'CLIENT_SKILL_DIRS'`.

- [ ] **Step 3: Implement**

Append to `src/turbo_memory_mcp/skill_install.py`:

```python
from dataclasses import dataclass
from pathlib import Path

# Client key -> (skills dir relative to $HOME, always-install flag).
# Non-flagged clients receive the skill only when their config dir (the first
# path segment) exists in $HOME.
CLIENT_SKILL_DIRS: dict[str, tuple[str, bool]] = {
    "agents": (".agents/skills", True),  # universal skills dir — always
    "claude": (".claude/skills", False),
    "gemini": (".gemini/skills", False),
    "kimi": (".kimi-code/skills", False),
    "codex": (".codex/skills", False),
}


@dataclass
class InstallResult:
    client: str
    target: Path
    status: str  # "installed" | "upgraded" | "reinstalled" | "current" | "failed"
    old_version: str | None
    new_version: str
    error: str | None = None


def resolve_targets(home: Path, client: str | None = None) -> list[tuple[str, Path]]:
    """Skill-file target paths for every client that should receive the skill."""
    targets: list[tuple[str, Path]] = []
    for name, (rel, always) in CLIENT_SKILL_DIRS.items():
        if client is not None and name != client:
            continue
        config_dir = home / rel.split("/")[0]
        if always or client is not None or config_dir.is_dir():
            targets.append((name, home / rel / SKILL_NAME / "SKILL.md"))
    return targets


def install_skill(
    home: Path | None = None,
    *,
    client: str | None = None,
    dry_run: bool = False,
) -> list[InstallResult]:
    """Install or upgrade the skill into every resolved target. Idempotent."""
    home = home or Path.home()
    text, new_version = load_skill()
    results: list[InstallResult] = []
    for name, target in resolve_targets(home, client):
        existed = target.is_file()
        old_version = (
            parse_skill_version(target.read_text(encoding="utf-8"))
            if existed
            else None
        )
        if old_version == new_version:
            results.append(
                InstallResult(name, target, "current", old_version, new_version)
            )
            continue
        if not existed:
            status = "installed"
        elif old_version is not None:
            status = "upgraded"
        else:
            status = "reinstalled"
        if not dry_run:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            except OSError as exc:
                results.append(
                    InstallResult(name, target, "failed", old_version, new_version, str(exc))
                )
                continue
        results.append(InstallResult(name, target, status, old_version, new_version))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_skill_install.py -v`
Expected: all PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add src/turbo_memory_mcp/skill_install.py tests/test_skill_install.py
git commit -m "feat: skill_install resolve_targets + idempotent install/upgrade"
```

---

### Task 3: CLI wiring — `turbo-memory-mcp skill install`

**Files:**
- Modify: `src/turbo_memory_mcp/cli.py`
- Test: `tests/test_skill_install.py`

**Interfaces:**
- Consumes: `install_skill`, `CLIENT_SKILL_DIRS`, `InstallResult` (Task 2).
- Produces: CLI surface `turbo-memory-mcp skill install [--client NAME] [--dry-run]`; handler `_handle_skill_install(args) -> int` in `cli.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skill_install.py`:

```python
import turbo_memory_mcp.skill_install as skill_install_module
from turbo_memory_mcp.cli import main
from turbo_memory_mcp.skill_install import InstallResult


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_skill_install.py -v -k cli_skill`
Expected: FAIL — `main(["skill", ...])` prints help / SystemExit (no such subcommand).

- [ ] **Step 3: Implement**

In `src/turbo_memory_mcp/cli.py`, add the subparser inside `build_parser()` right after the `doctor_parser.set_defaults(...)` line (before `return parser`):

```python
    skill_parser = subparsers.add_parser(
        "skill",
        help="Manage the bundled agent skill (SKILL.md).",
        description="Manage the bundled agent skill (SKILL.md).",
    )
    skill_subparsers = skill_parser.add_subparsers(
        dest="skill_command", metavar="action"
    )
    skill_install_parser = skill_subparsers.add_parser(
        "install",
        help="Install or upgrade the agent skill into detected skill dirs.",
        description=(
            "Copy the canonical SKILL.md into ~/.agents/skills/ (always) and "
            "every detected client skill dir. Idempotent: an older installed "
            "copy is upgraded, an equal one is left untouched. Re-run after "
            "'uv tool upgrade'."
        ),
    )
    skill_install_parser.add_argument(
        "--client",
        metavar="NAME",
        help="Install only for this client (agents, claude, gemini, kimi, codex).",
    )
    skill_install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned writes without touching disk.",
    )
    skill_install_parser.set_defaults(handler=_handle_skill_install)
```

Add the handler after `_handle_doctor` (before `main`):

```python
def _handle_skill_install(args: argparse.Namespace) -> int:
    """Install/upgrade the bundled agent skill into detected skill dirs."""
    from .skill_install import CLIENT_SKILL_DIRS, install_skill

    client = args.client
    if client is not None and client not in CLIENT_SKILL_DIRS:
        known = ", ".join(sorted(CLIENT_SKILL_DIRS))
        print(f"error: unknown client {client!r} (known: {known})", file=sys.stderr)
        return 2

    try:
        results = install_skill(client=client, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - packaging bug must fail loudly
        print(f"error: cannot load packaged skill: {exc}", file=sys.stderr)
        return 1

    failures = 0
    for r in results:
        would = "would " if args.dry_run and r.status != "current" else ""
        if r.status == "failed":
            failures += 1
            print(f"  [FAIL] {r.client}: {r.target} — {r.error}")
        elif r.status == "current":
            print(f"  [OK] {r.client}: already current (v{r.new_version}) — {r.target}")
        elif r.status == "installed":
            print(f"  [OK] {r.client}: {would}installed v{r.new_version} — {r.target}")
        else:  # upgraded / reinstalled
            old = r.old_version or "unknown"
            print(
                f"  [OK] {r.client}: {would}{r.status} "
                f"v{old} -> v{r.new_version} — {r.target}"
            )
    if failures:
        print(f"\n{failures} target(s) failed.", file=sys.stderr)
        return 1
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_skill_install.py tests/test_cli.py -v`
Expected: all PASS (15 new + existing CLI tests green).

- [ ] **Step 5: Smoke-test the real CLI**

Run: `.venv/bin/turbo-memory-mcp skill install --dry-run`
Expected: `[OK] agents: would installed ...` lines (or `already current` / `upgraded` depending on this machine's state), exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/turbo_memory_mcp/cli.py tests/test_skill_install.py
git commit -m "feat: turbo-memory-mcp skill install CLI"
```

---

### Task 4: Docs — READMEs (en/ru/uk), AGENTS.md, CHANGELOG

**Files:**
- Modify: `README.md` (lines 57 and 230–268)
- Modify: `README.uk.md` (lines 57 and 223 through the end of that section)
- Modify: `README.ru.md` (lines 57 and 223 through the end of that section)
- Modify: `AGENTS.md` (Documentation Policy section)
- Modify: `CHANGELOG.md` (top)

**Interfaces:**
- Consumes: the shipped `skill install` CLI (Task 3).

- [ ] **Step 1: README.md — replace the directive section**

Replace the whole section `## 🤖 Instructions for AI Agents (System Directive)` (`README.md:230` through line 268, i.e. up to but not including the `---` before `## 🛰️ Platform-Specific: Hermes Agent`) with:

````markdown
## 🤖 Instructions for AI Agents (System Directive)

> [!IMPORTANT]
> **ATTENTION AI AGENT (Claude, Gemini, Codex, or Antigravity):**
> Your complete operating manual ships as the **`turbo-quant-memory` agent skill**, bundled with this package. Install it once and every agent on this machine knows how to detect, install, and operate the `tqmemory` memory server:
>
> ```bash
> turbo-memory-mcp skill install
> ```
>
> The command copies the canonical `SKILL.md` into all detected agent skill directories (`~/.agents/skills/` always, plus detected client dirs) and upgrades older copies. Re-run it after every `uv tool upgrade`. The raw skill file also ships inside the installed package at `turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md` and lives in this repository under `src/turbo_memory_mcp/skills/`.
````

- [ ] **Step 2: README.md — add the skill step to the self-install prompt**

In `README.md:57`, replace the sentence fragment:

`register the \`tqmemory\` MCP server, run health checks, index this project, and set up our persistent memory`

with:

`register the \`tqmemory\` MCP server, run \`turbo-memory-mcp skill install\`, run health checks, index this project, and set up our persistent memory`

- [ ] **Step 3: README.uk.md — same two changes**

Replace the section `## 🤖 Інструкції для ШІ-агентів (Системна директива)` (from `README.uk.md:223` up to but not including the `---` before the Hermes section) with:

````markdown
## 🤖 Інструкції для ШІ-агентів (Системна директива)

> [!IMPORTANT]
> **УВАГА ШІ-АГЕНТУ (Claude, Gemini, Codex або Antigravity):**
> Твій повний посібник з експлуатації постачається як **агентний скіл `turbo-quant-memory`**, вбудований у цей пакет. Встанови його один раз — і кожен агент на цій машині знатиме, як виявити, встановити та використовувати сервер пам'яті `tqmemory`:
>
> ```bash
> turbo-memory-mcp skill install
> ```
>
> Команда копіює канонічний `SKILL.md` до всіх виявлених директорій скілів агентів (`~/.agents/skills/` завжди, плюс виявлені директорії клієнтів) та оновлює старіші копії. Перезапускай її після кожного `uv tool upgrade`. Сирий файл скіла також постачається у встановленому пакеті за шляхом `turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md` і живе у цьому репозиторії під `src/turbo_memory_mcp/skills/`.
````

In `README.uk.md:57`, replace the fragment:

`зареєструвати MCP-сервер \`tqmemory\`, запустити перевірку здоров'я`

with:

`зареєструвати MCP-сервер \`tqmemory\`, виконати \`turbo-memory-mcp skill install\`, запустити перевірку здоров'я`

- [ ] **Step 4: README.ru.md — same two changes**

Replace the section `## 🤖 Инструкции для ИИ-агентов (Системная директива)` (from `README.ru.md:223` up to but not including the `---` before the Hermes section) with:

````markdown
## 🤖 Инструкции для ИИ-агентов (Системная директива)

> [!IMPORTANT]
> **ВНИМАНИЕ ИИ-АГЕНТУ (Claude, Gemini, Codex или Antigravity):**
> Твоё полное руководство по эксплуатации поставляется как **агентный скилл `turbo-quant-memory`**, встроенный в этот пакет. Установи его один раз — и каждый агент на этой машине будет знать, как обнаружить, установить и использовать сервер памяти `tqmemory`:
>
> ```bash
> turbo-memory-mcp skill install
> ```
>
> Команда копирует канонический `SKILL.md` во все обнаруженные директории скиллов агентов (`~/.agents/skills/` всегда, плюс обнаруженные директории клиентов) и обновляет более старые копии. Перезапускай её после каждого `uv tool upgrade`. Исходный файл скилла также поставляется в установленном пакете по пути `turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md` и живёт в этом репозитории в `src/turbo_memory_mcp/skills/`.
````

In `README.ru.md:57`, replace the fragment:

`зарегистрировать MCP-сервер \`tqmemory\`, запустить проверку здоровья`

with:

`зарегистрировать MCP-сервер \`tqmemory\`, выполнить \`turbo-memory-mcp skill install\`, запустить проверку здоровья`

- [ ] **Step 5: AGENTS.md — canonical-manual note**

In `AGENTS.md`, append to the `## Documentation Policy` section:

```markdown
- The `turbo-quant-memory` agent skill (`src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md`) is the canonical agent operating manual: update it whenever memory policy, tool recipes, or install flows change, and bump its `version` frontmatter field together with the package version on every release (a test enforces the sync).
```

- [ ] **Step 6: CHANGELOG.md entry**

At the top of `CHANGELOG.md`, add (following the existing `## [x.y.z] - date` heading format):

```markdown
## [Unreleased]

### Added
- `turbo-quant-memory` agent skill: the canonical agent operating manual (`SKILL.md`) now ships as a package resource; `turbo-memory-mcp skill install` deploys it into `~/.agents/skills/` and all detected client skill dirs — idempotent, upgrades older copies (`--client`, `--dry-run`).

### Changed
- README "Instructions for AI Agents" sections (en/ru/uk) now point to the bundled skill instead of duplicating the full directive.
```

- [ ] **Step 7: Verify docs**

Run: `grep -c "skill install" README.md README.uk.md README.ru.md AGENTS.md CHANGELOG.md`
Expected: every file reports at least 1 (READMEs at least 2 each).
Run: `.venv/bin/pytest -q`
Expected: whole suite green.

- [ ] **Step 8: Commit**

```bash
git add README.md README.uk.md README.ru.md AGENTS.md CHANGELOG.md
git commit -m "docs: point agent instructions at the bundled turbo-quant-memory skill"
```

---

## Self-Review Notes

- **Spec coverage:** canonical skill file (Task 1), packaged resource + wheel check (Task 1 Step 6), `skill install` CLI with idempotent upgrade / `--client` / `--dry-run` / exit codes (Tasks 2–3), README×3 pointer + prompt step (Task 4 Steps 1–4), AGENTS.md note (Task 4 Step 5), version-sync release guard (Task 1 test), troubleshooting/migrations/secrets content (SKILL.md §4, §3.5). Non-goals respected: no uninstall, no backups, no marketplace, no `skill status`.
- **Type consistency:** `InstallResult` field order (`client, target, status, old_version, new_version, error=None`) is identical in Tasks 2 and 3; statuses match Global Constraints; `resolve_targets(home, client)` signature matches Task 2 tests; CLI handler calls `install_skill(client=..., dry_run=...)` with `home` defaulting to `Path.home()` (tests monkeypatch `Path.home`).
- **Known soft spot:** per-client skills-dir paths (`.claude/skills`, `.gemini/skills`, `.kimi-code/skills`, `.codex/skills`) are best-known conventions isolated in the single `CLIENT_SKILL_DIRS` dict — wrong paths are a one-line fix, not a redesign.
