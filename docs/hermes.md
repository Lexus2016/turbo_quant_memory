# 🛰️ Platform-Specific: Hermes Agent

[Hermes](https://github.com/nicepkg/hermes) runs MCP servers via a systemd-managed gateway — a different setup from Claude Code or Cursor.

### Installation

```bash
uv tool install git+https://github.com/Lexus2016/turbo_quant_memory
```

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  tqmemory:
    command: turbo-memory-mcp
    args: ["serve"]
    enabled: true
```

Restart the gateway:

```bash
systemctl --user restart hermes-gateway
```

### Troubleshooting MCP Timeouts

If MCP tools timeout with "MCP call timed out after 120.0s", the daemon lock is likely stale from a previous crash or host sleep. Recovery:

```bash
# 1. Kill all daemon processes
pkill -f turbo-memory-mcp

# 2. Remove stale lock file
rm -f ~/.turbo-quant-memory/.daemon.lock

# 3. Check and apply pending migrations
turbo-memory-mcp migrate --status
turbo-memory-mcp migrate --apply

# 4. Quick health check
turbo-memory-mcp doctor

# 5. Restart gateway
systemctl --user restart hermes-gateway

# 6. Wait 30-60s for MCP reconnect
```

#### "memory server busy" (v0.27.0+)

A different failure: `memory server busy: 'index_paths' holds the dispatch lock
(waited 30s); retry this call later`. This is **not** a stale lock — it means a
concurrent operation is genuinely still running and holding the single-writer
dispatch lock, and your call gave up rather than queueing until the MCP host's
own tool-call timeout (the 420-600s hard timeouts seen before v0.27.0, which
silently dropped memory writes). The error names the tool that is blocking, and
the same line is logged to stderr with the `[tqmemory]` prefix.

Just retry the call — nothing was written, so a retry cannot duplicate. If a
deployment legitimately holds the lock longer than the 30s default (a large
`index_paths` run over a big repo, a cold embedding backend), raise the bound
or opt back out of it entirely:

```bash
export TQMEMORY_DISPATCH_LOCK_TIMEOUT=90   # seconds; default 30
export TQMEMORY_DISPATCH_LOCK_TIMEOUT=0    # <= 0: wait without a bound (pre-0.27.0 behaviour)
```

Keep the bound below the proxy's own `RPC_TIMEOUT_SECONDS` (120s) so the
explicit "busy" error wins over an opaque RPC timeout.

### Auto-Migration on Startup

Set `TQMEMORY_MIGRATE_ON_STARTUP=1` in the environment to have the server automatically apply pending schema migrations (with a rolling snapshot) when it starts as primary or standalone:

```yaml
mcp_servers:
  tqmemory:
    command: turbo-memory-mcp
    args: ["serve"]
    enabled: true
    env:
      TQMEMORY_MIGRATE_ON_STARTUP: "1"
```

Auto-migration result is visible in the `health()` response under `migration_auto_result`.

### Common Hermes Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| MCP timeout | Stale `.daemon.lock` | `rm -f ~/.turbo-quant-memory/.daemon.lock` |
| Multiple daemons | Crash left orphans | `pkill -f turbo-memory-mcp` |
| Tools return errors | Pending schema migration | `turbo-memory-mcp migrate --apply` |
| Gateway won't load MCP | Config syntax error | Validate `config.yaml` |
| Silent startup failure | No visibility into daemon role | Check stderr: `[tqmemory] role=...` |
