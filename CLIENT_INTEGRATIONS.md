# Client Integrations

Other languages: [Ukrainian](CLIENT_INTEGRATIONS.uk.md) | [Russian](CLIENT_INTEGRATIONS.ru.md)

## Goal

Use one local stdio MCP server everywhere, then adapt it to each client with the thinnest possible config.

Shared runtime contract:

- server id: `tqmemory`
- launch command: `turbo-memory-mcp serve`
- default write scope: `project`
- default read mode: `hybrid`

## Integration Matrix

| Client | Status | Quick connect | Ready file | Notes |
|---|---|---|---|---|
| Claude Code | production-ready | `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve` | [examples/clients/claude.project.mcp.json](examples/clients/claude.project.mcp.json) | supports project and user MCP scopes |
| Codex | production-ready | `codex mcp add tqmemory -- turbo-memory-mcp serve` | [examples/clients/codex.config.toml](examples/clients/codex.config.toml) | should be started from the target repository |
| Cursor | production-ready | use the fixture file | [examples/clients/cursor.project.mcp.json](examples/clients/cursor.project.mcp.json) | project config is the safest default |
| OpenCode | production-ready | use the fixture file | [examples/clients/opencode.config.json](examples/clients/opencode.config.json) | local MCP config under `mcp` |
| Antigravity | compatibility target | use the fixture file | [examples/clients/antigravity.mcp.json](examples/clients/antigravity.mcp.json) | architecture is compatible, but still smoke-test on the real app |

## Per-Client Notes

### Claude Code

- Supports `claude mcp add ...`, `.mcp.json`, and project or user scopes.
- Project scope is preferred when memory must stay repository-specific.
- Use the shared runtime contract without extra wrappers.

### Codex

- Supports MCP configuration and `codex mcp add ...`.
- Start Codex in the target repository, or set `TQMEMORY_PROJECT_ROOT` explicitly.
- Do not add the repository path to MCP `args`; the server resolves the project from the process working directory.

### Cursor

- Supports project `.cursor/mcp.json` and user `~/.cursor/mcp.json`.
- Use project config when memory should stay tied to the repository.
- Use user config only when a broader cross-project setup is intentional.

### OpenCode

- Supports local MCP definitions under `mcp`.
- The repository ships a ready-to-merge config object.
- Keep the command local and simple: `["turbo-memory-mcp", "serve"]`.

### Antigravity

- Current documentation and integration reports show a compatible custom MCP flow.
- The repository includes a raw config example.
- Treat Antigravity as supported in architecture, but verify it with a smoke test before calling it production-proven.

## Standardization Rules

Keep the same contract across every client:

| Item | Standard |
|---|---|
| MCP server name | `tqmemory` |
| Runtime command | `turbo-memory-mcp serve` |
| Write scope vocabulary | `project`, `global`, `hybrid` |
| Install guidance | release install first, source install second |

This consistency matters because agents, prompts, docs, and smoke tests all become simpler when the runtime contract never changes from one client to another.

## Recommended Shipping Set

Ship these assets together:

1. one ready file for each supported client
2. one smoke checklist covering all clients
3. one install contract tied to the current release
4. one server id and one launch command everywhere

## Summary

The integration strategy is intentionally boring:

- one server
- one launch command
- one vocabulary for scopes
- thin client-specific wrappers only where the client requires them
