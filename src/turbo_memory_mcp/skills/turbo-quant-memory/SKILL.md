---
name: turbo-quant-memory
description: Install, configure, and operate the Turbo Quant Memory (tqmemory) MCP server. Use when setting up persistent agent memory in a workspace, registering the tqmemory MCP server in a client, or working with tqmemory tools (remember_note, semantic_search, recent_context, link_entities, secrets vault).
version: 0.24.0
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
# or pinned: uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.24.0
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

Any other client (Cursor, OpenCode, Antigravity, Kimi Code) takes the same server under its own config format — merge `{"tqmemory": {"command": "turbo-memory-mcp", "args": ["serve"]}}` into its MCP server map. Hermes runs a systemd gateway: add the same entry under `mcp_servers:` in `~/.hermes/config.yaml`, then `systemctl --user restart hermes-gateway`. Per-client details: <https://github.com/Lexus2016/turbo_quant_memory/blob/main/CLIENT_INTEGRATIONS.md>. Restart the client afterwards.

### 2.4 Verify and index

1. After the client restarts, call `health()` — expect `status: "ok"`.
2. Index the project's Markdown docs: `index_paths(paths=["."])`. The first run MUST name a root — a bare `index_paths()` only re-indexes roots already registered, and raises `ValueError` when there are none.
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
- Any vault error (`master_key_unavailable`, `master_key_mismatch`, `vault_error`) carries a `setup_hint` field with the exact commands the user needs. Print it verbatim and stop — never invent or regenerate a key.
- `master_key_mismatch` specifically means the resolved key does not match the vault — usually a `TQMEMORY_SECRETS_PASSPHRASE` shadowing a keyring-keyed vault (that variable is an Argon2id passphrase, never the raw keyring key). The fix is normally to UNSET it. Reads never mint a key, so the vault is intact.

## 4. Troubleshoot

| Symptom | Action |
| --- | --- |
| `migrations_pending` in `health()` | Surface `migrations_hint` verbatim; the user runs `turbo-memory-mcp migrate --apply` with MCP clients closed. Never self-apply. |
| MCP tool timeouts | Run `turbo-memory-mcp doctor` FIRST — it prints the lockfile path and whether its owner PID is still alive. Owner dead → just restart the MCP client; the server reclaims stale locks itself. Owner alive → leave the lock alone. Never `pkill -f turbo-memory-mcp`: it kills every tqmemory server on the host, including your own. |
| `master_key_unavailable` / `master_key_mismatch` | Print the `setup_hint` field verbatim; stop. For a mismatch, suspect a `TQMEMORY_SECRETS_PASSPHRASE` shadowing a keyring-keyed vault. |
| Anything else | `turbo-memory-mcp doctor` runs lock / migration / storage / socket diagnostics and prints PASS/FAIL per check. |
