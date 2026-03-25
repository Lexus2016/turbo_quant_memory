---
phase: 1
slug: client-integration-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-25
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest 8.x` |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest -q` |
| **Full suite command** | `uv run pytest -q && uv run python scripts/smoke_test.py` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest -q`
- **After every plan wave:** Run `uv run pytest -q && uv run python scripts/smoke_test.py`
- **Before `$gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | INT-01, INT-02 | packaging | `uv run python -m turbo_memory_mcp --help` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | INT-01 | unit | `uv run pytest -q tests/test_tools.py` | ❌ W0 | ⬜ pending |
| 01-01-03 | 01 | 1 | INT-02 | unit | `uv run pytest -q tests/test_cli.py` | ❌ W0 | ⬜ pending |
| 01-02-01 | 02 | 2 | INT-03, INT-04 | static docs | `rg -n "## Quickstart|turbo-memory-mcp serve|tqmemory|Tier 1|Tier 2" README.md` | ❌ W0 | ⬜ pending |
| 01-02-02 | 02 | 2 | INT-03, INT-04 | config parse | `python -m json.tool examples/clients/claude.project.mcp.json >/dev/null && python -m json.tool examples/clients/cursor.project.mcp.json >/dev/null && python -m json.tool examples/clients/opencode.config.json >/dev/null && python -m json.tool examples/clients/antigravity.mcp.json >/dev/null && python -c "import pathlib, tomllib; tomllib.loads(pathlib.Path('examples/clients/codex.config.toml').read_text())"` | ❌ W0 | ⬜ pending |
| 01-02-03 | 02 | 2 | INT-03, INT-04 | manual checklist | `rg -n "Claude Code|Codex|Cursor|OpenCode|Antigravity|self_test|Tier 2" examples/clients/SMOKE_CHECKLIST.md` | ❌ W0 | ⬜ pending |
| 01-03-01 | 03 | 2 | INT-01, INT-02 | e2e stdio | `uv run python scripts/smoke_test.py` | ❌ W0 | ⬜ pending |
| 01-03-02 | 03 | 2 | INT-01, INT-02, INT-03 | contract | `uv run pytest -q tests/test_smoke_contract.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `pyproject.toml` — pytest configuration and package entrypoint contract
- [ ] `tests/test_cli.py` — CLI and console-script coverage
- [ ] `tests/test_tools.py` — MCP tool contract coverage
- [ ] `pytest` installed through project dependencies

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Claude Code can connect and run `self_test` | INT-03, INT-04 | Requires a real Claude Code host and MCP approval flow | Load project config or run `claude mcp add --transport stdio --scope project tqmemory -- uv run turbo-memory-mcp serve`, confirm server appears, ask the client to run `self_test` |
| Codex can connect and run `self_test` | INT-04 | Requires a real Codex CLI/IDE environment | Add the server with `codex mcp add tqmemory -- uv run turbo-memory-mcp serve`, verify it appears in MCP management, ask Codex to run `self_test` |
| Cursor can connect and run `self_test` | INT-04 | Requires a real Cursor editor/CLI environment | Load `.cursor/mcp.json` example, confirm the server is enabled in Cursor MCP settings or `agent mcp list`, then run `self_test` from Agent |
| OpenCode can connect and run `self_test` | INT-04 | Requires a real OpenCode host | Add the local MCP config under `mcp`, confirm the tool is available, then prompt the agent to run `self_test` |
| Antigravity recognizes the raw custom MCP config | INT-04 | Phase 1 only claims documented compatibility, not hard parity automation | Open Antigravity MCP management, import the raw config example, and confirm the custom server is recognized |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
