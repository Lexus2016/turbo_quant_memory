# Design: `turbo-quant-memory` Agent Skill + `skill install` CLI

- **Date:** 2026-07-25
- **Status:** Approved (brainstorming dialogue). Not yet implemented.
- **Scope decisions (locked with user):**
  - One all-in-one skill covering **detect → install/deploy → operate** (not split into install-only / usage-only).
  - Distribution: canonical `SKILL.md` shipped **as a package resource** inside the wheel; new CLI subcommand `turbo-memory-mcp skill install` copies it into client skill directories. Install is **idempotent and doubles as upgrade**.
  - Targets: **all detected client skill dirs** — `~/.agents/skills/` (universal, always) plus per-client dirs (Claude Code, Gemini CLI, Kimi Code, Codex) detected via their config dirs.
  - The skill is the **full source of truth** for agent instructions; the README "Instructions for AI Agents" section shrinks to a pointer.

## Problem

Today the project's agent-facing guidance lives in README §"Instructions for AI Agents (System Directive)", `CLIENT_INTEGRATIONS.md`, and `AGENTS.md`. That works only when the agent has already opened this repo. There is no portable artifact an agent can carry across workspaces: install the skill once and the agent knows, in *any* project, how to install TQ Memory into a new workspace, register the MCP server, and operate the memory day-to-day. Skills (SKILL.md format) also give progressive disclosure — the full text is loaded only when relevant, unlike always-on AGENTS.md blocks.

## Goals

- A single canonical `SKILL.md` that is the complete, self-sufficient agent manual: detect, install/deploy, operate, troubleshoot.
- `turbo-memory-mcp skill install` that deploys the skill to all known client skill directories, is idempotent, and upgrades older installed copies (version marker comparison).
- Upgrade flow is exactly: `uv tool upgrade … && turbo-memory-mcp skill install`.
- README (en/ru/uk) points at the skill instead of duplicating the full directive.

## Non-goals (YAGNI)

- No `skill uninstall`, no backups of overwritten skills (the canonical copy is versioned in the package), no skills-marketplace publishing, no separate `skill status` command (the install report shows status).

## Components

### 1. Canonical skill file

`src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md`, packaged into the wheel as package data.

- **Frontmatter:** `name: turbo-quant-memory`; `description` with explicit triggers ("use when installing, configuring, or using the tqmemory / Turbo Quant Memory MCP server…"); a machine-readable `version` field that MUST equal the package version.
- **Body sections:**
  1. **Detect** — `health()` / `server_info()` checks; how to tell whether `tqmemory` is already registered in the current client; how to detect a partial/broken install.
  2. **Install & Deploy** — `uv tool install`, condensed per-client MCP registration table, post-install health check, first `index_paths` of the project, and running `turbo-memory-mcp skill install` itself.
  3. **Operate** — migrated from README §"Instructions for AI Agents" and the AGENTS.md memory policy: session-start `recent_context()` ritual; `remember_note` discipline (kind / provenance / tags / English); knowledge-graph linking (`link_entities`, project-root-relative file URIs); `deprecate_note` on superseded knowledge; secrets vault recipe (discover via `semantic_search`, `secret_value` field, never echo); no smoke notes.
  4. **Troubleshoot** — `migrations_pending` (surface `migrations_hint`, never self-apply), `master_key_unavailable` (`setup_hint` verbatim), daemon/health anomalies.
- **Language:** English (agent-facing; the trilingual documentation policy covers user-facing docs, not skills).

### 2. CLI subcommand

`turbo-memory-mcp skill install` in the existing CLI (`src/turbo_memory_mcp/cli.py`):

- Reads the canonical SKILL.md via `importlib.resources`.
- Target directories: a single `CLIENT_SKILL_DIRS` mapping in one module (easy to extend). `~/.agents/skills/` is always written; per-client dirs (Claude Code, Gemini CLI, Kimi Code, Codex — exact paths verified during implementation) are written when the client's config dir exists.
- **Idempotency / upgrade:** parses the `version` marker of an already-installed copy; overwrites and reports `old → new` when older, reports `already current` when equal, installs fresh otherwise. Pure content comparison/writes — no backups.
- Flags: `--client NAME` (restrict to one target), `--dry-run` (show planned writes without touching disk).
- Output: per-target report line (`installed` / `upgraded X→Y` / `already current`), non-zero exit on any write failure.

### 3. README changes (en + ru + uk)

- §"Instructions for AI Agents (System Directive)" replaced by a short pointer: full agent instructions ship as the `turbo-quant-memory` skill; install via `turbo-memory-mcp skill install`; the raw file is also readable in the package.
- The self-install prompt near the top of README gains one step: after registering the MCP server, run `turbo-memory-mcp skill install`.

### 4. AGENTS.md

One-line addition noting the skill is the canonical agent manual and must be updated when memory policy changes (keeps AGENTS.md and the skill in sync going forward).

## Data flow

```
repo: src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md
        │  (packaged into wheel)
        ▼
turbo-memory-mcp skill install
        │  importlib.resources read
        ▼
~/.agents/skills/turbo-quant-memory/SKILL.md        (always)
~/.claude/skills/turbo-quant-memory/SKILL.md        (if detected)
~/.gemini/..., ~/.kimi-code/..., ~/.codex/...       (if detected)
```

Upgrade: same command re-run after `uv tool upgrade`; version marker comparison drives overwrite.

## Error handling

- Missing/unreadable package resource → hard CLI error with traceback (packaging bug, should fail loudly).
- Unwritable target dir → report that target as failed, continue others, exit non-zero at the end.
- Installed copy with unparsable/missing version marker → treat as stale, overwrite, report `reinstalled (unknown version)`.
- `--client` with unknown name → usage error listing known client names.

## Testing

New `tests/test_skill_install.py`:

- install into a tmp `$HOME` → file written to universal dir and detected client dirs;
- re-run → `already current`, file untouched (mtime/content unchanged);
- plant an older-version copy → run → overwritten, report shows `old → new`;
- planted copy without a version marker → `reinstalled (unknown version)`;
- `--dry-run` writes nothing;
- version-sync test: SKILL.md frontmatter version == `turbo_memory_mcp.__version__` (release-process guard);
- CLI failure path: unwritable target → non-zero exit, other targets still written.

Existing suites (`test_cli.py`, smoke) must stay green.

## Release-process impact

`pyproject.toml` version bump must be accompanied by the SKILL.md version bump; the version-sync test enforces this. CHANGELOG entry per release as usual.
