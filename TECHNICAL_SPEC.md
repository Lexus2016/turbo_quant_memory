# Technical Specification

Other languages: [Ukrainian](TECHNICAL_SPEC.uk.md) | [Russian](TECHNICAL_SPEC.ru.md)

## Product Goal

Build a local-first MCP server that acts as practical long-term memory for AI coding agents.

The server should reduce repeated token usage by:

- moving cold context out of the active prompt
- returning compact, source-backed context first
- hydrating fuller detail only when the agent explicitly asks for it

## Product Boundary

| This project does | This project does not do |
|---|---|
| keeps project knowledge searchable on the MCP side | does not control hosted-model KV cache |
| stores notes and indexed Markdown locally | does not replace the model's internal memory |
| returns compact retrieval cards with provenance | does not promise universal savings for every repository |
| supports project and cross-project recall | does not try to solve every reasoning task with compression alone |

## Target Users

- Engineers working in real repositories with Claude Code, Codex, Cursor, OpenCode, and similar MCP clients.
- Teams that want local deployment with low operational overhead.
- Agent-heavy workflows that repeatedly revisit docs, notes, ADRs, and Markdown knowledge bases.

## Core Principles

| Principle | Meaning |
|---|---|
| Local-first | The core memory loop should work on the developer machine |
| Markdown-first | Human-readable Markdown stays a first-class source of truth |
| Compact-first retrieval | Return the smallest useful answer before opening more |
| Hydration on demand | Larger payloads must be explicitly requested |
| Traceability always | Every result must point back to a source |
| Easy setup | Installation and client wiring should take minutes, not hours |

## Technical Stack

| Area | Choice |
|---|---|
| Language | Python 3.11+ |
| MCP framework | official MCP Python SDK |
| Storage | local filesystem plus embedded LanceDB |
| Embeddings | Sentence Transformers with a lightweight local model |
| Config | environment variables plus typed settings |
| Packaging | `uv` first, `pip` fallback |

## Functional Scope

### 1. Source Ingestion

- Index one or more Markdown roots.
- Chunk content by headings, with deterministic fallbacks.
- Persist source metadata such as path, heading path, timestamps, tags, checksum, and block identity.
- Support incremental re-indexing for changed content.

### 2. Retrieval

- Accept free-text queries through `semantic_search(...)`.
- Return compact result cards instead of full raw file dumps.
- Keep provenance, relevance, confidence, and key points visible.
- Warn when retrieval confidence is low or ambiguous.

### 3. Hydration

- Open a fuller excerpt only when compact retrieval is not enough.
- Support a bounded local neighborhood around the selected hit.
- Keep hydration explicit so token budgets stay predictable.

### 4. Write-Back Memory

- Save decisions, lessons, handoffs, and reusable patterns.
- Store notes with type, tags, timestamps, and source references.
- Allow explicit promotion from `project` to `global`.
- Allow deprecation of outdated notes without deleting history.

### 5. Operations

- Expose health and runtime metadata.
- Show storage counts and index freshness.
- Provide a quick self-test contract.
- Keep smoke-test instructions for supported clients.

### 6. Knowledge-Base Hygiene

- Run structural lint checks on Markdown corpora.
- Detect broken internal Markdown links.
- Detect orphan candidates with no inbound or outbound internal links.
- Detect duplicate normalized titles that increase ambiguity during retrieval.

### 7. Knowledge Graph Relations

- Link notes, files, tasks, or issues via custom relation types.
- Query relations to traverse the knowledge network.
- Enrich retrieval results automatically with associated relations for enhanced context discovery.

## MCP Tool Surface

| Tool | Purpose |
|---|---|
| `health()` | basic health check |
| `server_info()` | runtime, project, storage, and install contract |
| `list_scopes()` | available scopes and write/read defaults |
| `self_test()` | quick contract validation |
| `remember_note(...)` | store a typed note |
| `promote_note(note_id)` | copy a project note into reusable global memory |
| `deprecate_note(...)` | retire outdated knowledge |
| `semantic_search(...)` | retrieve compact context |
| `hydrate(...)` | open bounded fuller context |
| `index_paths(...)` | index or refresh Markdown roots |
| `lint_knowledge_base(...)` | run structural checks for link integrity and wiki consistency |
| `link_entities(...)` | create a link between two knowledge entities in the graph |
| `unlink_entities(...)` | remove a link between two knowledge entities in the graph |
| `get_related_entities(...)` | query relations involving a specific entity URI |
| `set_secret(name, value)` | store an encrypted secret in the active project's vault |
| `get_secret(name)` | fetch a project secret by exact name; value returned in dedicated `secret_value` field |
| `list_secrets()` | list secret names in the active project; never returns values |
| `delete_secret(name)` | delete a project secret by exact name |

## Data Model

### Markdown block

| Field | Meaning |
|---|---|
| `block_id` | stable identity for the indexed block |
| `file_path` | source Markdown path |
| `heading_path` | heading hierarchy for the block |
| `content_raw` | full source content |
| `content_compressed` | compact retrieval representation |
| `embedding` | vector representation for search |
| `checksum` | change detection |
| `created_at` / `updated_at` | timing metadata |
| `tags` | optional labels |
| `source_kind` | `markdown` |

### Memory note

| Field | Meaning |
|---|---|
| `note_id` | note identity |
| `title` | note title |
| `content` | full note body |
| `note_kind` | `decision`, `lesson`, `handoff`, or `pattern` |
| `summary` | compact summary for retrieval |
| `tags` | optional labels |
| `session_id` | session linkage when relevant |
| `project_id` | owning project |
| `created_at` | creation timestamp |
| `source_refs` | provenance references |

### Secrets vault (project-scoped, encrypted)

Per-project encrypted store kept entirely separate from notes and markdown. Lives under `<storage_root>/projects/<project_id>/secrets/`; never read by `semantic_search`, `hydrate`, or `lint_knowledge_base`.

| File | Meaning |
|---|---|
| `vault.tqv` | AES-256-GCM blob containing JSON `{version, entries: {name: {value, created_at, updated_at}}}`. 12-byte random nonce per write, 16-byte GCM tag appended by `cryptography`'s `AESGCM`. Mode `0o600`. |
| `meta.json` | `{version, kdf, kdf_params, key_mode, vault_initialized, created_at, updated_at}`. KDF parameters and resolution mode for diagnostics; no key material. Mode `0o600`. |
| `audit.jsonl` | Append-only access log. One JSON line per access: `{ts, action ∈ {set,get,list,delete}, name}`. `project_id` implicit from path; values never logged. Mode `0o600`. |

Subsystem-level marker `<storage_root>/secrets-manifest.json` tracks the SECRETS migration chain (`format_version`); it carries no secret content.

Master key per project: 32 bytes resolved at call time in priority order
(1) env var `TQMEMORY_SECRETS_PASSPHRASE` (Argon2id-derived with project-specific
salt `sha256("tqv-salt-v1:" + project_id)`); (2) existing OS keyring entry at
service `turbo-quant-memory`, account `secrets-master-<project_id>`;
(3) keyring auto-bootstrap (generate + store) if the backend is writable;
(4) hard fail with an actionable setup hint. No interactive-prompt fallback —
that would silently die on reboot.

## Performance Targets

- local startup should stay comfortable on a developer laptop
- first indexing pass should fit normal interactive setup expectations
- retrieval latency should feel interactive on small and medium corpora
- recall-heavy workflows should usually save substantial context versus naive full-file opening

## Security and Trust

- Restrict indexing to explicitly selected paths.
- Keep output sizes bounded by default.
- Preserve source boundaries and provenance.
- Treat notes and retrieved memory as tool data, not authority.
- Avoid hidden outbound-network requirements for the core local flow — `src/` contains zero outbound HTTP code (no `requests` / `httpx` / `aiohttp` / `urllib.request` / `urlopen` / raw `socket`).

### Secrets vault threat model

In scope (the secrets vault must protect against these):
- Accidental backups exposing plaintext credentials (Time Machine, rsync, iCloud sync of the home directory).
- Share-screen / screenshot leaks of stored credentials.
- Accidental `git add` of credential-bearing files under `~/`.

Out of scope (the secrets vault does NOT defend against these; users with stronger threat models should use a dedicated secret manager):
- Compromise of the root user on the local machine.
- A live attacker that has already taken over the running daemon process.
- The same-user daemon IPC channel. The daemon's `multiprocessing` socket is `0600`, guarded by a 32-byte authkey in the `0600` lockfile, and `TQMEMORY_SECRETS_PASSPHRASE` is forwarded to the primary on every RPC over that pickle channel. A same-user attacker who can read the lockfile authkey can inject a pickle payload (RCE) into the primary and observe the passphrase. Accepted within the same-user model: it presupposes an attacker already running as the same user — a stronger position that is subsumed by the daemon-takeover row above.
- Hardware-level attacks (cold-boot, evil-maid, hardware key extraction).
- Anything requiring multi-tenant isolation or compliance certifications.

Enforcement points:
- AES-256-GCM at rest with per-project master keys; nonce per write; MAC failure raises `cryptography.exceptions.InvalidTag`.
- Indexer (`ingestion._resolve_roots`) and linter (`knowledge_lint._resolve_roots`) refuse registration of any path inside `<storage_root>/projects/<project_id>/secrets/`. Both `_iter_markdown_files` walkers skip files under that subtree as defense in depth.
- `set_secret` / `get_secret` / `list_secrets` / `delete_secret` MCP responses keep secret values strictly within a dedicated `secret_value` field on `get_secret` only — never interpolated into descriptive `summary` / `message` text.
- Audit log records the access by `(timestamp, action, name)` and never by value; sentinel-grep regression test asserts this invariant.

## Deployment Contract

| Step | Expected contract |
|---|---|
| Recommended install | `uv tool install git+https://github.com/Lexus2016/turbo_quant_memory@v0.23.0` |
| Fallback install | `python -m pip install git+https://github.com/Lexus2016/turbo_quant_memory@v0.23.0` |
| Runtime command | `turbo-memory-mcp serve` |
| Claude Code example | `claude mcp add --scope project tqmemory -- turbo-memory-mcp serve` |
| Equivalent examples | Codex, Cursor, OpenCode, and Antigravity configs ship in the repository |

## Testing Strategy

| Layer | Coverage |
|---|---|
| Unit tests | chunking, IDs, payload contracts, provenance mapping |
| Integration tests | index -> search -> hydrate, note write-back, and knowledge-base lint flows |
| Smoke tests | fresh install, client connection, indexing, retrieval, hydration |
| Benchmarking | repository-level context-savings report with real measurements |

## Acceptance Criteria

1. A new user can install the package and connect it to a supported client in minutes.
2. The server can index Markdown files and search them semantically.
3. Default retrieval returns compact, source-backed context instead of raw dumps.
4. Agents can explicitly hydrate fuller context when needed.
5. Notes can be saved, promoted, deprecated, and found later.
6. Operators can inspect health, freshness, and storage state quickly.
7. Operators can lint indexed Markdown knowledge bases for broken links, orphan candidates, and duplicate titles.

## Non-Goals

- replacing the model's internal memory mechanisms
- claiming direct token quantization or hosted KV-cache control
- building enterprise multi-tenant governance in the current scope
- solving all repository reasoning through compression alone

## Summary

Turbo Quant Memory is a practical MCP memory layer:

- local-first
- compact by default
- traceable on every retrieval
- explicit about when more context is opened
- easy to install and operate in normal developer workflows
