# 🧠 Turbo Quant Memory for AI Agents (v0.7.1)

> **The first self-installable, trilingual local-first memory & knowledge graph for AI coding agents.** Save up to 60% of your token budget while giving your AI assistant a permanent, hyper-fast, and highly connected brain.

---

## 👋 What is this awesome tool? (For Humans)

Imagine you are working with an AI coding assistant (like Claude Code, Gemini CLI, Cursor, or Codex). Every time you restart a session, the AI forgets everything. It forgets your architectural decisions, custom styling rules, how you solved that tricky database bug, or even your coding preferences. You have to explain it all over again, or feed the AI huge files, which **wastes your time and burns through your token budget (costing you real money)**.

**Turbo Quant Memory** solves this once and for all. It is a local-first **Model Context Protocol (MCP) server** that gives your AI agents a persistent brain. It stores:
* 🎯 **Decisions & Lessons**: Why things were built this way, so the AI doesn't break them.
* 💡 **Patterns & Gotchas**: Reusable tricks and hard-won bug fixes.
* 🕸️ **Knowledge Graph Relations**: Structured associations linking memory notes, source files, tasks, or bugs.
* 📦 **Codebase Index**: Compact Markdown block search so the AI understands your project structure instantly.

### 💰 Cost-Saving Magic
Instead of reading massive files every time, your AI agent uses **Compact Retrieval** to query its memory and fetch only highly-relevant 600-token summaries.

| Metric | Value | Benefit for You |
| :--- | :--- | :--- |
| **Context Savings** | 📉 **~63.96% fewer bytes** | Reduced API costs, longer context windows |
| **Search Latency** | ⚡ **<70 ms** | Instant AI responses with zero lag |
| **Architectural Focus** | 🎯 **Dynamic Pruning** | AI sees only what matters, ignoring session noise |
| **Linked Knowledge** | 🕸️ **Knowledge Graph** | AI understands relationships between code, tasks, and decisions |
| **Self-Cleaning Graph** | 🔄 **Dynamic Lifecycle** | Stale relationships are deprecated or unlinked automatically |

---

## 🚀 DON'T INSTALL THIS MANUALLY! (Let the AI Do It)

You don't need to type commands in the terminal or configure JSON files. **Let your AI assistant handle the setup!**

Simply copy the link to this repository:
`https://github.com/Lexus2016/turbo_quant_memory`

And send this exact prompt to your AI assistant (Claude Code, Gemini CLI, Codex, etc.):

> "Hey! Please install and configure the Turbo Quant Memory server for my workspace using this repository: https://github.com/Lexus2016/turbo_quant_memory. Read the README.md, follow the 'Instructions for AI Agents' at the bottom of the file to install it via `uv tool`, register the `tqmemory` MCP server, run health checks, index this project, and set up our persistent memory. Let me know when you're ready!"

Your AI agent will automatically clone, install, register, and index everything for you!

---

## 🛠️ Quick Start (If You *Really* Want to Do It Yourself)

If you prefer the manual way, run this 60-second flow:

1. **Install the CLI Tool:**
   ```bash
   uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.7.1
   ```

2. **Add `tqmemory` MCP Server to your client:**
   ```bash
   # Codex
   codex mcp add tqmemory -- turbo-memory-mcp serve

   # Gemini CLI
   gemini mcp add tqmemory turbo-memory-mcp serve

   # Claude Code (Project scope)
   claude mcp add --scope project tqmemory -- turbo-memory-mcp serve
   ```

3. **Restart your client and let the magic begin!**

*For custom integrations (Cursor, OpenCode, Antigravity, etc.), see [CLIENT_INTEGRATIONS.md](CLIENT_INTEGRATIONS.md).*

---

## 🌟 Advanced Features (Under the Hood)

### 1. Hybrid BM25 + Vector Search
Every query searches both dense-vector spaces (for semantic meaning) and BM25 full-text indexes (for exact term matches like function names, file paths, or IDs) in parallel. Results are fused using Reciprocal Rank Fusion (RRF, `k=60`). If a lane fails, it degrades gracefully to vector-only search.

### 2. Knowledge Graph Relations
You can build associations between notes, source files, issues, or tasks using directed relations. The memory server automatically enriches search and hydration results with these relations, letting AI agents browse associated context effortlessly.

#### 🔄 Dynamic Relation Lifecycle (Core Strength):
* **Aging & Syncing:** Relations are created with a `created_at` timestamp and dynamically inherit entity state. If a linked note grows stale and is deprecated via `deprecate_note()`, the entire connected graph path is smartly flagged as outdated for AI agents.
* **Flexible Decoupling (Unlinking):** Any relation can be easily severed using the `unlink_entities()` tool. This gives the agent memory absolute flexibility to adapt to refactorings and design changes.
* **Auto-Diagnostics:** When calling `lint_knowledge_base()`, the system automatically runs integrity checks on the graph, pinpointing "orphan" relations and helping prevent stale-context build-up.

#### 📊 Visual Memory Architecture:
```mermaid
graph TD
    A[AI Agent / Query] -->|1. semantic_search| B[tqmemory Server]
    B -->|2. Vector Index| C[Dense Vector Search]
    B -->|2. Full-Text Index| D[BM25 FTS Search]
    C -->|3. RRF Fusion| E[Knowledge Candidates]
    D -->|3. RRF Fusion| E
    E -->|4. Graph Enrichment| F[Knowledge Graph / Associations]
    F -->|5. Enriched Context| A
    
    subgraph Relation Lifecycle
        G[Create Link: link_entities] -->|Knowledge Evolution| H[Deprecate Note: deprecate_note]
        H -->|Diagnosis: lint_knowledge_base| I[Sever Link: unlink_entities]
    end
```

### 3. Tiered Memory Architecture
Memory notes are separated into logical tiers:
* `durable`: Decisions, architectural patterns, lessons.
* `episodic`: Session handoffs, daily progress.
* `reference`: Markdown blocks, file references.

Default searches return only `durable` + `reference` so session noise never drowns out critical architectural decisions!

---

## 🔐 Secrets Vault (NEW in v0.7.0)

Tired of pasting SSH keys, DB connection strings, or API tokens into every new chat session? The secrets vault solves that — **without you giving up an inch of control over your data**.

### Why this exists
Agents kept asking you for the same prod-DB DSN, the same staging SSH host, the same bearer token, every session. Project memory wasn't the right home for those (anything indexed is at risk of leaking back into search results). So Phase 9 adds a separate, encrypted, **strictly project-scoped** vault next to your notes.

### What changes in your install
* Four new MCP tools: `set_secret`, `get_secret`, `list_secrets`, `delete_secret`. Tool count grows `14 → 18`.
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

---

## 🤖 Instructions for AI Agents (System Directive)

> [!IMPORTANT]
> **ATTENTION AI AGENT (Claude, Gemini, Codex, or Antigravity):**
> You are equipped with the `tqmemory` MCP server. This is your long-term memory. Read and obey these instructions to ensure seamless integration and zero-friction memory retrieval.

### 1. Pre-Flight Ritual (Every Session Start)
1. **Check Migrations & Health:** Call `health()` and `server_info()`. If `migrations_pending` (in `health`) or `migrations.pending` (in `server_info`) is `true`, surface the `migrations_hint` verbatim to the user so they can run the CLI upgrade. **Do not run `migrate --apply` yourself** as it requires closing active MCP clients.
2. **Retrieve Context:** Before starting any non-trivial task, run `semantic_search(query="<task_topic>", scope="hybrid")` to retrieve existing architectural decisions, styling rules, lessons, or guidelines.

### 2. Memory Writing Discipline
When you learn something important, solve a complex bug, or make an architectural decision, **immediately save it** using `remember_note()`. Do not wait until the end of the session.
* **Lessons (`kind="lesson"`):** Store hard-won patterns or bug fixes.
* **Decisions (`kind="decision"`):** Store structural choices or tooling decisions.
* **Patterns (`kind="pattern"`):** Store reusable templates or styling conventions.
* **Handoffs (`kind="handoff"`):** Store episodic progress before pausing or ending a session (automatically assigned to `episodic` tier).

*Format your notes in English. Keep them concise, technical, and actionable. Add semantic tags.*

### 3. Knowledge Graph Linking
Use relations to connect ideas, files, and tasks:
* When saving a note about a bug fix in `src/auth.py`, call `link_entities(source="note:[note_id]", target="file:///absolute/path/to/src/auth.py", relation_type="fixes")`.
* Link related notes: `link_entities(source="note:[new_note]", target="note:[old_note]", relation_type="supersedes")`.
* Link files to tasks: `link_entities(source="file:///path/to/file", target="task:[task_id]", relation_type="implements")`.

### 4. Zero Ambiguity & Memory Updates
* **Deprecate Outdated Notes:** When a pattern or decision changes, write the new note, then call `deprecate_note()` on the old one to avoid search pollution.
* **No Smoke Notes:** Do not write temporary or smoke test notes.
* **Provenance:** Always preserve file paths and line numbers in your memory payloads.

### 5. Secrets Vault (v0.7.0+)
* **Discover, don't guess:** Find the right `get_secret(name)` call by `semantic_search` for a `pattern`-kind recipe note that documents the credential. Never fish names from chat history.
* **Fetch through the dedicated field:** `get_secret("name")` returns the value in `secret_value`. Pass it programmatically (env var injection, subprocess argument). Do NOT echo it into summaries, logs, or `remember_note`.
* **Write what the user gives you:** if the user pastes a credential into the chat (or you generated one inside the conversation), just call `set_secret(name, value)`. You know the exact active `project_id` from cwd resolution; the user running the CLI from terminal may not. Do NOT push the user back to the CLI just to redo what is already done — the exposure happened when they typed it; friction won't undo it.
* **Reach for the CLI only as prevention:** if the user is ABOUT to share a credential but hasn't pasted yet, then suggest `turbo-memory-mcp secret-set NAME` from a terminal — getpass keeps the value out of the chat in the first place. After the value is already in chat, CLI is friction with no payoff.
* **Surface `master_key_unavailable` errors verbatim:** the response carries a `setup_hint` field with the exact `export` / `keyring set` commands the user needs. Print it, then stop — do not try to invent keys.

---

## 🌍 Language Versions
This documentation is maintained in three synchronized languages:
* 🇺🇸 [English README](README.md)
* 🇺🇦 [Ukrainian README](README.uk.md)
* 🇷🇺 [Russian README](README.ru.md)
