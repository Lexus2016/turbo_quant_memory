# 🔐 Secrets Vault (NEW in v0.7.0)

Tired of pasting SSH keys, DB connection strings, or API tokens into every new chat session? The secrets vault solves that — **without you giving up an inch of control over your data**.

### Why this exists
Agents kept asking you for the same prod-DB DSN, the same staging SSH host, the same bearer token, every session. Project memory wasn't the right home for those (anything indexed is at risk of leaking back into search results). So Phase 9 adds a separate, encrypted, **strictly project-scoped** vault next to your notes.

### What changes in your install
* Four new MCP tools: `set_secret`, `get_secret`, `list_secrets`, `delete_secret`. Tool count grew `14 → 18` (now `19` with the v0.12.0 `recent_context` bootstrap tool).
* A one-time migration provisions an empty `secrets/` directory under each existing project on first `turbo-memory-mcp migrate --apply` after upgrade.

### What does NOT change (read this if you're nervous)
* Your existing notes, markdown index, `semantic_search`, `hydrate`, and `lint_knowledge_base` behave **byte-identically**. The upgrade does not touch them.
* The vault is **opt-in**. If you never call `set_secret`, the only thing on disk is an empty 28-byte encrypted blob per project. Zero impact.
* If you remove the feature mentally, you can ignore the four new tools forever and nothing breaks.

### Where your secrets live (and where they don't)
* **On your machine, encrypted at rest:** `~/.turbo-quant-memory/projects/<project_id>/secrets/vault.tqv`, AES-256-GCM, per-project master key.
* **Never anywhere else:** the `src/` tree of this package contains zero outbound HTTP code — no `requests`, no `httpx`, no `urllib.request`, no raw sockets. We have nothing to send your secrets to, even if we wanted to. (Verify with `grep -rE 'requests|httpx|urllib\.request|aiohttp' src/` — clean.)
* **Never in your retrieval index:** the ingestion walker and the lint walker hard-refuse to traverse any `secrets/` subdirectory. `semantic_search` cannot reach the vault by design.
* **Never in agent transcripts (when used right):** `get_secret` returns the value in a dedicated `secret_value` field, separate from any descriptive text. Agents are instructed to pass it through programmatically, not echo it.

### How to use it
1. **One-time master-key setup** (pick one path):
   ```bash
   # macOS (auto-uses Keychain after first set_secret if you skip this step):
   keyring set turbo-quant-memory secrets-master-<project_id> <32-byte-base64>

   # Headless / Linux / CI / Docker:
   export TQMEMORY_SECRETS_PASSPHRASE='your-long-passphrase'   # add to shell rc
   ```
   > ⚠️ **`TQMEMORY_SECRETS_PASSPHRASE` is a passphrase, not the raw key.** It is
   > run through Argon2id to *derive* the master key. Do **not** paste the
   > `keyring` base64 value into this env var — that derives a *different* key and
   > a vault created via the keyring will fail to decrypt with a
   > `master_key_mismatch` error. Pick **one** path: keyring **or** passphrase, and
   > if you share one daemon across MCP clients, set the same passphrase on all of
   > them or on none. The env var always wins over the keyring when both are set.
2. **Save a secret once, reuse forever** — two paths, picked by *whether the value is already in the chat*:
   * **Value NOT yet in the chat — use the CLI (prophylactic path):**
     ```bash
     turbo-memory-mcp secret-set prod-db-dsn
     # prompts: Value for 'prod-db-dsn' (input hidden): ******
     ```
     The value is read via `getpass` — it never enters shell history, scrollback, or any chat transcript. Recommended when you're about to provision a fresh credential and want to keep it out of the conversation entirely.
   * **Value already in the chat — let the agent write it (reactive path):**
     ```
     set_secret("prod-db-dsn", "postgresql://user:pass@host:5432/db")
     ```
     Use this whenever the value is already visible: you pasted it, or the agent generated it inside the conversation. The agent resolves the active `project_id` deterministically from `cwd` — better than asking the user to retype the value in a terminal where their cwd may not match the intended project. Once exposure has happened in chat, the CLI offers no additional secrecy; `set_secret` is the safer write path.
3. **Agents fetch on demand**:
   ```
   get_secret("prod-db-dsn") → {"status": "ok", "secret_value": "postgresql://..."}
   ```

### Threat model — what we protect, what we don't
**We protect against** (the realistic single-developer threats):
* Accidental backup leaks (Time Machine, rsync, iCloud Desktop sync of plaintext files).
* Share-screen / screenshot mishaps showing a stored credential.
* Accidental `git add` of the wrong file under your home directory.

**We do not protect against** (and never claim to):
* A compromised root user on your laptop.
* A live attacker who has already taken over the running daemon process.
* Hardware-level attacks, evil-maid attacks, cold-boot attacks.

If your threat model is bigger than ours, use a dedicated secret manager (1Password, HashiCorp Vault) and keep tqmemory for knowledge only.

### FAQ
* **"Will my existing memory be affected?"** No. Notes, markdown, search, hydrate, lint — all unchanged.
* **"Do I need to do anything to upgrade?"** Only if you want to start using `set_secret`. Otherwise, ignore.
* **"Where exactly are secrets stored?"** Per project, under `~/.turbo-quant-memory/projects/<project_id>/secrets/` (encrypted blob + minimal metadata + an audit log of access timestamps that never contains the values).
* **"Can my secrets leak into `semantic_search`?"** No. Hard isolation enforced at the ingester, the linter, and verified by a regression test that plants a sentinel and proves search returns zero hits.
* **"What if I want this feature off?"** It already is — the vault is empty until you populate it. There is nothing running, nothing transmitted, nothing scheduled.
* **"What if I delete a project?"** Its vault, audit log, and per-project master key entry are all under that project's directory and go with it.
