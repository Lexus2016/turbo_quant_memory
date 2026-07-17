# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.22.0] - 2026-07-17

### Added
- **`source_filter` on `semantic_search`.** Pass `"notes"` to search only
  memory notes (decisions/lessons/patterns/handoffs) or `"markdown"` for
  indexed doc blocks only. Measured on this repository: in doc-heavy corpora
  the reference blocks crowd notes out of the top-5 on question-shaped
  queries ("what did we decide about X"); the filter gives agents a direct
  way out. Default behavior (mixed) is unchanged.
- **Embedding near-duplicate detection in `lint_knowledge_base`.** New
  `near_duplicate_notes` issue kind (severity medium): flags active note
  pairs whose title+summary embeddings exceed cosine 0.90 — typically the
  same note saved in two languages, which then crowds itself out of top-k
  retrieval. Embeds one short probe per active note (a few seconds on real
  stores; capped at 2000 notes, degrades to no-findings on any failure).
  Calibrated on real data: bilingual twins score 0.905–0.969 in probe space
  while the closest distinct pair stays at 0.877; the full-content vectors
  stored in the retrieval index cannot drive this check (cross-lingual twins
  drop below 0.70 there and mix with unrelated pairs). Complements the
  write-time `similar_notes` hint, which is now pinned by a real-model test
  to fire on cross-lingual twins (EN note + UK translation) at save time.

## [0.21.0] - 2026-07-17

### Changed
- **ONNX (fastembed) is now the default embedding backend — PyTorch is gone
  from the client install.** `fastembed` moved from the `[onnx]` extra into
  core dependencies and `TQMEMORY_EMBEDDING_BACKEND` now defaults to
  `fastembed`; `sentence-transformers` (and the heavy torch wheels it pulls
  in — hundreds of MB on macOS, multi-GB with CUDA on Linux) is no longer
  installed for clients. Retrieval quality is identical
  (measured MRR parity) and the embeddings are vector-compatible, so upgrading
  requires **no reindex**. The legacy PyTorch backend remains available for
  rollback via `pip install turbo-memory-mcp[torch]` +
  `TQMEMORY_EMBEDDING_BACKEND=sentence-transformers`, and stays in the dev
  dependency group as the reference for the backend parity test. The old
  `[onnx]` extra is kept as an empty alias so existing install instructions
  don't break. Note: the first run after upgrading downloads the ONNX model
  once (~0.2 GB) — the old PyTorch model cache is not reused. Requesting the
  PyTorch backend without the `[torch]` extra installed now fails with an
  actionable error instead of a bare ImportError.

### Tests
- Real-model backend parity test: encodes multilingual reference phrases with
  both backends and fails if per-phrase cosine similarity drops below 0.99 —
  guarding against silent drift in fastembed's ONNX conversions (a pooling
  change for our model has already happened once upstream). Skips when the
  PyTorch reference isn't installed (bare client install).

## [0.20.1] - 2026-07-15

### Fixed
- **Clean exit when the MCP client closes the stdio pipe (issue #2, reported by
  Alisa / Hermes Agent, Nous Research).** When the parent process closed
  stdout/stdin — client restart, session reload, graceful shutdown — the anyio
  stdio transport raised `BrokenPipeError`, usually wrapped in a
  `BaseExceptionGroup`, and it propagated out of the three daemon entry points
  (`_run_primary`, `_run_proxy`, `_run_standalone`), crashing the server with an
  unhandled `ExceptionGroup` traceback and leaving the parent to see "endpoint
  unreachable". The entry points now recognize a client disconnect — by
  `BrokenPipeError` / `ConnectionResetError` subclass and by benign `OSError`
  errno (`EPIPE`, `ECONNRESET`, `ESHUTDOWN`, `ENOTCONN`) for platforms that
  surface a bare `OSError` — and exit cleanly, while any genuine error travelling
  in the same `BaseExceptionGroup` is split out (`BaseExceptionGroup.split`,
  recursive) and re-raised so real failures are never masked. tqmemory is now
  resilient regardless of the matching gap in upstream `mcp/server/stdio.py`
  (whose `stdout_writer` catches only `anyio.ClosedResourceError`).

## [0.20.0] - 2026-07-15

### Added
- **Knowledge-graph discoverability (issues #1, #2, #5).** `remember_note` now
  auto-links any `source_refs` that are entity URIs (`note://`, `file://`,
  `issue://`, `https://`, …) as `references` relations, and its response carries
  a `hints` list that nudges `link_entities` and 2-3 tags when a note has none.
  Its description now documents `tags`, `source_refs`, `provenance` (including
  when to use `human-explicit`), and `tier`.
- **Stale episodic reporting (issue #3).** `lint_knowledge_base` reports handoff
  / episodic notes older than `TQMEMORY_EPISODIC_STALE_DAYS` (default 14; `0`
  disables) so accumulated session notes can be pruned with `deprecate_note`.

### Fixed
- **`lint_knowledge_base` no longer fails on an MCP-only store (issue #4).** A
  store with notes but no indexed Markdown roots previously returned a failure;
  it now lints cleanly (`status: "ok"`, `markdown_configured: false`) and still
  runs the note-level checks.
- **`link_entities` validates entity URIs (issue #6).** The common `note:abc`
  (missing `//`) typo used to be stored as a broken relation; it now raises a
  clear error. `note`/`file`/`issue`/`task`/`http(s)` require the `scheme://`
  form; other RFC schemes (`mailto:`, `urn:`, …) are accepted. The
  `get_related_entities` / `unlink_entities` descriptions now document the URI
  forms too.

## [0.19.1] - 2026-07-15

### Fixed
- **Knowledge-base lint no longer mangles non-ASCII (Cyrillic) titles.**
  `_normalize_title` used an ASCII-only class (`[^a-z0-9]+`), so every UK/RU
  title collapsed to `"untitled"` — producing false `duplicate_title` reports
  across translated docs and colliding non-ASCII filenames in the wikilink
  lookup. It now keeps Unicode letters/digits (`[\W_]+`), so distinct Cyrillic
  titles get distinct keys (verified: 3 false duplicate-title groups → 0 on the
  repo's tri-lingual docs).

## [0.19.0] - 2026-07-15

### Performance
- **Project identity is cached instead of re-forking git on every tool call.**
  `resolve_project_identity` forked git twice (`rev-parse --show-toplevel` +
  `remote get-url origin`) on every tool invocation, under the daemon's
  single-writer dispatch lock — ~30 ms of head-of-line blocking for all clients
  per call. It is now memoized per `(resolved cwd, TQMEMORY_PROJECT_* env,
  git-config fingerprint)` with a 30 s TTL backstop; the cached path is ~780×
  faster (~0.04 ms vs ~30 ms). The cache key includes the full identity inputs,
  not just cwd, so a shared daemon serving proxies from different repos never
  crosses namespaces (preserves the issue-#1 fix); it keys on the *resolved*
  absolute path so a process that changes its working directory between
  `cwd=None` calls can't collide on a stale entry. A cheap `.git/config` mtime
  fingerprint invalidates the entry the instant a repo gains or loses a remote —
  including submodules and worktrees, where the `.git` file's `gitdir:` pointer
  is followed to the real config — so identity is never stale.

### Security
- **Path-traversal guard on client-supplied ids.** `note_id` (via
  hydrate/deprecate/promote), `project_id` (via `TQMEMORY_PROJECT_ID`), and the
  markdown `block_id`/`root_id`/`file_key` are interpolated into filesystem paths
  such as `projects/<project_id>/notes/<note_id>.json`. A value containing a path
  separator or a `..`/`.` segment could read or clobber another project's files,
  breaking project isolation. `MemoryStore` now validates every such id through
  `_ensure_safe_id` and fails closed with a clear `ValueError`; internally minted
  ids (uuid/sha hex, `mdblk-…`) are unaffected. Crucially, a client-set
  `TQMEMORY_PROJECT_ID` is validated at the resolution *source*
  (`resolve_project_identity`), so the bucket name can never point the notes
  store **or the encrypted secrets vault** (`SecretsStore`) outside the storage
  root. Same-user threat model, but the isolation invariant is now enforced
  rather than assumed.

### Fixed
- **`migrate --status` / `--apply` no longer crash on a corrupt manifest.**
  `_status_for` read each subsystem's version outside any guard, so one
  truncated/unreadable manifest raised an uncaught traceback out of the exact
  command you run to recover. The read is now wrapped: the subsystem is reported
  with an `error` and an empty pending chain, so `apply` safely skips it instead
  of crashing, and the startup warning now points at
  `migrate --list-snapshots` / `--restore-from` for recovery.
- **Pre-migration snapshots are safer.** The default retention is now 2 (was 1)
  so re-running a failed `--apply` — which snapshots the half-migrated state —
  can no longer immediately prune the clean pre-migration snapshot you need to
  restore from (explicit `TQMEMORY_SNAPSHOTS_KEEP` is still honored). And a
  snapshot is now built into a dot-prefixed staging dir and published with a
  single atomic `os.replace`, so a copy that dies partway leaves nothing that
  `--list-snapshots` / `--restore-from` could mistake for a complete backup.
- **One corrupt markdown-cache file no longer takes down all retrieval.** Notes
  were already quarantined (skip-with-warning), but `list_markdown_blocks` /
  `list_markdown_file_manifests` / `list_markdown_roots` used a bare `_read_json`,
  so a single unreadable/partial block/manifest/root JSON raised out of
  `semantic_search`, `hydrate`, and `server_info` — taking down search for the
  whole project. These reads now skip a corrupt file with a `[tqmemory]` stderr
  warning, mirroring the note quarantine.
- **Telemetry milestone was re-announced on every search.** `_maybe_emit_milestone`
  bumps the `last_announced_*` markers, but `record_semantic_search_usage`
  persisted the stats *before* that bump, so the marker never reached disk and the
  same "N tokens saved / N retrievals" milestone re-fired on every subsequent
  search. The milestone is now computed before the write.
- **`build_block_id` / `build_file_key` collision for dot-leading paths.**
  `str.lstrip("./")` strips a character set, not a prefix, so `.github/x.md` and
  `github/x.md` produced the same block id / file key (silent overwrite). Both
  use `removeprefix("./")` now.
- **`lint_knowledge_base` crashed on a non-UTF-8 file.** One file with invalid
  UTF-8 aborted the entire lint; reads now use `errors="replace"`.
- **Removed dead redundant `try/except` in `RetrievalIndex.reset_scope`** (both
  branches were identical).
- **Daemon proxy no longer inherits the primary's project namespace (issue
  #1).** When a shared primary daemon was started with an explicit
  `TQMEMORY_PROJECT_ROOT`, a second stdio client attaching as a proxy from a
  different repository could resolve the primary's project instead of its own,
  so `server_info.current_project`, project-scoped retrieval, and
  project-scoped writes pointed at the wrong namespace. `make_local_dispatcher`
  now distinguishes an absent `_environ` field (a direct primary call, behaviour
  unchanged) from a present-but-empty/partial proxy `_environ`, and strips the
  primary's project-identity keys (`TQMEMORY_PROJECT_ROOT` / `_PROJECT_ID` /
  `_PROJECT_NAME`) before applying the proxy's forwarded values. A proxy that
  forwards no identity now resolves its project from its own forwarded `_cwd`.
  Storage home and the secrets passphrase are intentionally left untouched.
  No schema change; no migration required.

## [0.18.0] - 2026-06-23

### Fixed
- **Cross-process lock around secrets-vault writes (M1).** The daemon
  (`set_secret`) and the standalone `secret-set` CLI mutate the same
  `vault.tqv` from different processes; an interleaved read-modify-write could
  lose an update (last writer wins, the other secret vanishes). Writes
  (`set` / `delete` / `provision`) now hold an exclusive `fcntl.flock` on a
  stable `.vault.lock` for the whole RMW (POSIX; reads stay lock-free —
  an atomic rename means a reader sees a whole file). Crash-safety bonus
  (peer-review): a new env vault now persists its random salt in `meta.json`
  BEFORE the vault ciphertext, so an interrupted first write leaves a
  recoverable not-yet-created vault instead of one whose salt was lost. No
  schema change.

### Security
- **Random per-vault Argon2id salt for new env vaults (M5).** A new
  passphrase-derived (`TQMEMORY_SECRETS_PASSPHRASE`) vault now generates a
  random 32-byte salt stored in `meta.json`, instead of deriving it
  deterministically from `project_id` (predictable for a public-remote
  project). Fully backward compatible: a vault whose `meta.json` has no `salt`
  keeps deriving the legacy deterministic key, so existing secrets never lose
  access; an existing vault is never re-keyed. Keyring-backed vaults are
  unaffected (their key is random, not passphrase-derived). No migration —
  `migrate --apply` not required; safe to roll back.

## [0.17.0] - 2026-06-22

### Added
- **`TQMEMORY_FTS_LANGUAGE` env var (default `English`).** Selects the Snowball
  stemmer for the BM25 full-text lane. A Cyrillic-dominant deployment can set
  `Russian` to also match *inflected* Ukrainian/Russian forms (e.g. `документ` ↔
  `документами`), at the cost of English stemming — LanceDB applies one stemmer
  per index. Unsupported values (including `Ukrainian`, which has no Snowball
  stemmer) fall back to English with a stderr warning instead of silently
  killing the FTS lane. Apply a change with a retrieval reset + reindex (or
  `RetrievalIndex.rebuild_fts`). Empirically verified on LanceDB 0.30.1: the
  default tokenizer already matches UA/RU/EN *exact* terms (case- and
  accent-insensitive, Cyrillic preserved); a stemmer only adds inflection
  matching, which the dense vector lane already covers semantically.

### Changed
- **FTS tokenizer config is now pinned explicitly** in `_fts_index_kwargs()`
  rather than inherited from LanceDB's implicit defaults, so a future LanceDB
  upgrade cannot silently change retrieval tokenization. Behavior is
  byte-identical for existing English indexes — **no rebuild required**.

### Fixed
- **Retrieval drift repair no longer re-embeds the whole corpus (M4).**
  `_repair_project/global_retrieval_if_needed` compared row counts and, on any
  drift, ran a full `O(corpus)` re-embed synchronously under the dispatch lock.
  It now reconciles by id — diffing the index's `item_id` set against the
  store's notes∪blocks, deleting stale rows and re-embedding only the missing
  ids. Cheaper, and strictly more correct: it also catches a
  count-matches-but-membership-differs drift the count-only check was blind to.
  Drift is rare (all mutation paths sync incrementally), so this is a
  worst-case latency/cost fix. No schema change — `migrate --apply` not required.

### Security
- **Documented the same-user daemon IPC trust boundary (M7).** The secrets-vault
  threat model now states explicitly that `TQMEMORY_SECRETS_PASSPHRASE` is
  forwarded to the primary on every RPC over the `multiprocessing` pickle
  channel, and that a same-user attacker able to read the `0600` lockfile authkey
  could inject a pickle payload (RCE) and observe the passphrase — an accepted
  same-user compromise. Documentation only; no behavior change.

## [0.16.0] - 2026-06-10

### Fixed
- **Split-brain race in daemon bootstrap (H1).** Between claiming the lockfile
  and starting its listener a freshly-elected primary was briefly unreachable;
  a racing process pinged once, judged the live primary stale, evicted its
  lockfile and became a second primary — two processes holding LanceDB write
  handles. `acquire_daemon_role()` now retries the ping with backoff before
  evicting a live-pid endpoint, so the loser of the atomic claim waits for the
  winner's listener and proxies instead.
- **A hung `git` froze the whole daemon (H2).** `resolve_project_identity()`
  ran `git` with no timeout on every tool call; a stuck git (network disk,
  credential helper) blocked every client behind the single dispatch lock. The
  git subprocess now has a 3s timeout and falls back to path identity; a
  missing git binary degrades the same way instead of raising.
- **One corrupt note broke every scan (H3).** A single malformed note JSON (or
  an unknown status/kind) made `list_notes()` raise, taking down
  `recent_context`, scope sync, retrieval repair and `server_info`. Unreadable
  notes are now quarantined — skipped with a `[tqmemory]` warning and surfaced
  per scope in `server_info` as `quarantined_notes`; a malformed `updated_at`
  no longer breaks retrieval ordering.
- **A wedged primary hung every new client.** The multiprocessing connect
  handshake had no timeout, so a stale primary that accepted the socket but
  never answered blocked all new clients indefinitely (a server that "never
  connects"). The client connect is now time-bounded, after which the bootstrap
  reclaims a genuinely wedged primary.

### Changed
- **Listener binds before the startup migration (H1 follow-up).** A minutes-long
  startup re-embed used to run between the lockfile claim and the listener
  start, re-opening the split-brain window. The primary now binds its listener
  first and answers HELLO/ping immediately while deferring tool calls until the
  migration finishes, so a racing process always reaches a live primary.
- **Resilient markdown indexing.** A non-UTF-8 or oversized file no longer
  aborts the whole index/staleness pass — files are decoded with replacement
  and capped at 5 MiB (skipped with a warning).
- **Silent expensive work is now visible.** Retrieval search-lane failures and
  every full re-sync (row-count drift, post-upgrade format rebuild, and
  incremental-update fallbacks) log a `[tqmemory]` line, so a multi-minute
  re-embed in the middle of a normal write is no longer silent.

### Notes
- No schema change — `migrate --apply` is **not** required for this release.

## [0.15.0] - 2026-06-09

### Added
- **Daemon startup observability.** `acquire_daemon_role()` now logs every
  bootstrap decision to stderr with a `[tqmemory]` prefix — primary claimed,
  proxy connected, endpoint unreachable, stale lock reclaimed, lock
  contention, standalone fallback. A client that sees an MCP timeout can read
  *why* from the gateway logs (`[tqmemory] role=...`) instead of guessing
  between a network, lock, or migration problem.
- **Opt-in migration auto-apply on startup.** Set
  `TQMEMORY_MIGRATE_ON_STARTUP=1` and a primary/standalone server applies any
  pending schema migrations on boot, taking a rolling snapshot first. Off by
  default — apply stays an explicit, snapshotted operation. A proxy never
  migrates (it does not own storage), and a failure is captured rather than
  crashing startup.
- **`turbo-memory-mcp doctor`** — one-shot diagnostics for the failure mode
  behind silent MCP timeouts: storage-root presence, stale `.daemon.lock`
  (dead-PID detection with the exact `rm` command), socket reachability,
  pending migrations, and project identity. Exit code equals the number of
  issues found.
- **`health()` reports daemon state.** The payload now carries `daemon_role`
  (primary / proxy / standalone, snapshotted at startup) and
  `migration_auto_result`, so a client can distinguish a lock/migration
  problem from a network one without waiting for a 120s timeout.
- README troubleshooting section (EN/RU/UK) for the Hermes Agent: stale-lock
  recovery, auto-migration setup, and a symptom → cause → fix table.

### Notes
- No schema change — `migrate --apply` is **not** required for this release.

## [0.14.0] - 2026-06-06

### Added
- **`prune-orphans` CLI — the action half of orphaned-bucket lifecycle.**
  `turbo-memory-mcp prune-orphans` lists project buckets whose recorded
  `project_root` no longer exists on disk (the same set surfaced in
  `server_info.orphaned_buckets`); `--apply` MOVES them to
  `staging/orphan-prune-<ts>/` (reversible) rather than deleting. Dry run by
  default, never a hard delete, never automatic — a missing root is not proof a
  project is dead (an unmounted volume, or storage shared across machines).
  Orphan buckets are by definition not the active project, so the move is safe
  with a daemon running.

## [0.13.0] - 2026-06-06

### Added
- **Sticky project-identity resolution — a repo no longer loses its memory when
  a git remote is added (or removed).** Previously `resolve_project_identity`
  was a pure function of the current git/path state, so adding `origin` to a
  repo that already had path-keyed notes flipped `identity_source` and minted a
  brand-new, empty bucket — stranding every existing note (the kind of split
  that had to be healed by hand). New `store.reconcile_project_identity` reads
  the manifests already on disk and adopts the matching bucket: by a
  previously-seen identity source, or by repo root — **unless** a different
  recorded remote proves a different project reused the same path, in which case
  a new bucket is minted (the safety boundary). It is wired into the single
  `build_runtime_context` chokepoint, so every MCP tool and the CLI get it.
  Identity-preserving for all already-established projects; it only changes the
  answer at the moment the identity source actually transitions.
- **`identity_sources` on the project manifest.** `write_project_manifest` now
  accumulates every identity source ever resolved to a bucket (union, lazy-
  seeded from the legacy single `identity_source`). This pins the bucket on a
  later remote add/remove and is a transparent on-disk record of how a project
  has been addressed. Additive — **no `format_version` bump and no migration**;
  v2 manifests converge on next write (same lazy-normalize approach as the
  provenance field).
- **Orphaned-bucket detection in `server_info`.** New `orphaned_buckets` field
  lists project buckets whose recorded `project_root` no longer exists on disk
  (`{project_id, project_name, project_root, note_count}`) so dead weight is
  visible instead of accumulating silently forever. Read-only and surfaced on
  the diagnostic call only (not `health`, the liveness probe). It never deletes:
  a missing root is not proof a project is dead (an external/network volume may
  be unmounted, or the storage root shared across machines), so pruning stays a
  deliberate, assisted action.

### Notes
- Backward compatible; no migration required. The bucket id of an established
  project never changes — see `docs/superpowers/specs/2026-06-06-project-identity-lifecycle-design.md`.

## [0.12.0] - 2026-06-06

### Added
- **`recent_context` tool — query-free session bootstrap.** A new MCP tool (the
  19th) that returns the most recently updated notes, newest first, reading
  canonical note JSON directly (no embedding, no vector search, deterministic).
  It includes the `episodic` tier by default, so session `handoff` notes surface
  here even though `semantic_search` hides them. This is the reliable
  "where did I leave off" entry point for a fresh session or a post-compaction
  recovery, closing the cold-start gap where an agent had to guess a query
  against context it could not yet know existed. `scope` defaults to `project`;
  `tier_filter` defaults to all tiers.

### Fixed
- **`semantic_search` now exposes `tier_filter` on the MCP surface.** The
  `episodic` tier (session handoffs) was reachable only from the internal
  `semantic_search_impl`, never from the `@mcp.tool()` wrapper, so MCP clients
  could not retrieve their own handoff notes — the very mechanism meant to bridge
  sessions was structurally invisible. The wrapper and dispatcher now thread
  `tier_filter` through; pass `tier_filter=["episodic"]` to recover handoffs.

### Changed
- **`remember_note` accepts an explicit `tier` override.** Storage already
  supported it; the MCP tool now exposes it. An agent can force a `handoff` into
  the durable (default-searched) set with `tier="durable"`, or keep a noisy note
  out of regular search with `tier="episodic"`. Without it the kind→tier default
  is unchanged (handoff → episodic, everything else → durable).

## [0.11.0] - 2026-06-01

### Fixed
- **Secrets vault: `InvalidTag` no longer escapes as an empty, hintless MCP
  error.** When the resolved master key did not match the vault (most commonly a
  `TQMEMORY_SECRETS_PASSPHRASE` shadowing — or forwarded onto — a keyring-keyed
  vault), `get_secret` / `list_secrets` / `set_secret` / `delete_secret` raised a
  bare `cryptography.exceptions.InvalidTag` whose `str()` is `""`, producing an
  opaque `Error executing tool …:` with no `code` and no `setup_hint`. A new typed
  `VaultDecryptError` is now raised at the store boundary and every secrets impl
  translates it (and any other unexpected error) into a structured payload with a
  distinct `code` (`master_key_mismatch` / `vault_error`) and an actionable
  `setup_hint`. (DEFECT A)
- **Latent data-loss: a transient keyring READ failure no longer mints a new
  key.** `resolve_master_key` previously swallowed any `KeyringError` on read and
  fell through to bootstrap, which could mint a fresh key and permanently orphan
  an existing vault (locked keychain, ACL change, headless "interaction not
  allowed"). Read paths now pass `allow_bootstrap=False` and a non-`NoKeyringError`
  read failure raises `MasterKeyUnavailable` instead of minting. (DEFECT D)
- **CLI `secret-set` mirrors the structured error.** A key mismatch on an
  existing vault now exits `4` with the actionable hint on stderr instead of
  printing a raw `VaultDecryptError` traceback.
- **`provision()` no longer clobbers an existing vault's key fingerprint.**
  Because provisioning never decrypts, a freshly resolved key cannot be trusted
  to match a pre-existing vault; the recorded fingerprint is now preserved on
  re-provision (only a vault created in the same call is stamped).

### Added
- **Key-provenance fingerprint.** A one-way `key_fingerprint` is recorded in
  `meta.json` on vault creation / first write and verified on resolve, so a wrong
  key fails fast with `master_key_mismatch` *before* any ciphertext is touched.
  Legacy vaults without a fingerprint fall back to the wrapped decrypt-time check.
  (DEFECT B)
- **Env-var footgun warning.** When `TQMEMORY_SECRETS_PASSPHRASE` base64-decodes
  to exactly 32 bytes (i.e. looks like the raw keyring key pasted in by mistake),
  a one-time warning is logged. Docs and `setup_hint` now state plainly that the
  env var is an Argon2id passphrase, not the raw key. (DEFECT C)

## [0.10.0] - 2026-05-30

### Added
- **User-flagged memory (`provenance`).** `remember_note` now accepts an optional
  `provenance` parameter (`human-explicit` | `agent`, default `agent`). Notes the
  user explicitly asks to remember are flagged `human-explicit` and rank above
  agent-written notes of equal relevance — via a deterministic provenance
  tie-breaker plus a small score bonus for close matches. The field is optional
  and backward compatible: legacy notes read as `agent` (lazy normalize-on-read),
  so no migration and no format-version bump are required.

## [0.9.0] - 2026-05-29

### Added
- **Opt-in ONNX embedding backend.** `TQMEMORY_EMBEDDING_BACKEND=fastembed` (with
  `pip install turbo-memory-mcp[onnx]`) runs the same multilingual model via ONNX
  Runtime instead of PyTorch — a much smaller RAM footprint for ~2 GB machines,
  vector-compatible with the default backend so no reindex is needed. The default
  backend (`sentence-transformers`) is unchanged.
- **Write-time duplicate/conflict hints.** `remember_note` now surfaces highly
  similar existing project notes as `similar_notes` (supersede or
  review-for-conflict candidates) so the agent can reconcile. The server only
  surfaces candidates — it never auto-deprecates.

### Changed
- **Retrieval fusion is now vector-first and gated.** When the dense lane's top
  hit is confident the BM25 lane is skipped; otherwise BM25 is fused via RRF as a
  down-weighted rescue (previously an equal-weight RRF). Measured to cut the
  hybrid-vs-vector MRR deficit from -0.049 to -0.006 across 28 real corpora while
  preserving the cases where the keyword lane genuinely helps.
- The retrieval vector dimension is now derived from the active embedding model
  (no longer hardcoded to 384), so switching to a different-dimension model needs
  no schema change.

## [0.8.1] - 2026-05-29

Patch release. Fixes the reported server version, which was hardcoded and had
drifted from the actual release.

### Fixed
- `__version__` (surfaced by `server_info`, `health`, the CLI `--version`, and
  the daemon handshake) was a hardcoded literal still reading `0.7.1` even on
  the 0.7.2 and 0.8.0 installs. It now derives from the installed package
  metadata (single source of truth, driven by `pyproject.toml`), so it can no
  longer drift from the release. Falls back to a literal only when running from
  un-installed source. Note: the 0.7.2 and 0.8.0 tags still report `0.7.1` via
  `server_info`; upgrade to 0.8.1 to get accurate version reporting.

## [0.8.0] - 2026-05-29

Minor release. Switches the default embedding model to a multilingual one,
dramatically improving retrieval for non-English content (Cyrillic, Polish,
Spanish, Chinese). Requires a one-time re-embed migration.

### Changed
- Default embedding model is now
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (was
  `all-MiniLM-L6-v2`). On a controlled 6-language probe (EN/UK/RU/PL/ES/ZH),
  avg recall@1 rose from 0.55 to 0.98; Cyrillic in particular went from ~0.25
  to 1.0. The model is still 384-dimensional, so the LanceDB schema is
  unchanged.
- The embedding model is now overridable per install via the
  `TQMEMORY_EMBEDDING_MODEL` environment variable, with no code change.

### Migration
- New retrieval migration v3 -> v4 re-embeds every block and note with the new
  model. Source data (note JSONs, markdown) is untouched; the derived vector
  index is rebuilt from the canonical store, so nothing is lost. After
  upgrading, stop all MCP clients and run `turbo-memory-mcp migrate --apply`.
  Because it re-encodes the whole corpus it can take a while on large stores —
  the `health` / `server_info` migration hint now says so explicitly.

### Tests
- Added `test_upgrade_retrieval_v3_to_v4_reembeds_both_scopes`; made two
  retrieval-manifest version assertions version-agnostic so future format
  bumps no longer require touching them.

## [0.7.2] - 2026-05-29

Patch release. Improves hybrid-retrieval ranking so exact keyword (BM25)
matches are no longer suppressed, and hardens secrets test isolation.

### Fixed
- Hybrid retrieval scored every BM25/FTS-only hit with a flat synthetic
  distance (0.5) regardless of its BM25 rank, capping even a perfect
  exact-term match at "medium" confidence and discarding the BM25 lane's
  ranking signal in the additive scorer. An FTS-only hit's synthetic distance
  is now derived from its BM25 rank (rank 1 → 0.15, +0.08 per rank, capped at
  0.6), so a strong exact match can reach high confidence. The
  `_rank_candidates` sort-key rounding was also tightened (2 → 3 decimals) so
  fine relevance differences are no longer flattened into recency-ordered
  ties. Runtime-only: no stored data, schema, or format-version change;
  existing indexes work unchanged (no migration required).

### Tests
- Added `test_semantic_search_bm25_only_hit_ranks_first_with_high_confidence`
  as a regression guard for the BM25 scoring fix, and updated `test_rrf_merge`
  to assert the rank-aware synthesized distance.
- Isolated the two `*_unavailable_key_returns_structured_error` secrets tests
  from a `TQMEMORY_SECRETS_PASSPHRASE` inherited from the developer's shell
  (the key resolver reads the real process env via priority 1), which produced
  false failures on machines that have the passphrase configured.

## [0.7.1] - 2026-05-28

Patch release. Fixes a stale-lockfile deadlock that could block
`turbo-memory-mcp migrate --apply` (and `--restore-from`) indefinitely after a
daemon exited uncleanly.

### Fixed
- The migration guard treated the mere *existence* of `<storage_root>/.daemon.lock`
  as proof that a primary daemon was running. A daemon that exits uncleanly
  (SIGKILL, crash, host sleep) never runs its release hook, leaving behind a
  lockfile that names a now-dead PID — so `migrate --apply` / `--restore-from`
  would refuse forever even though nothing was writing, forcing operators to
  reach for `--force` or delete the file by hand. The guard now reads the PID
  from the lockfile and checks liveness via the same `_is_pid_alive` helper the
  daemon uses at startup: only a lock whose PID is still alive blocks. A
  lockfile naming a dead PID is correctly treated as stale, and a malformed or
  unreadable lock stays conservative (reported as present) so `--force` remains
  the deliberate escape hatch. This makes the migrate guard and daemon-startup
  staleness detection agree on what counts as a live owner.

### Tests
- Added regression coverage in `tests/test_migrations.py`:
  `test_cli_apply_ignores_stale_lockfile_with_dead_pid` (dead PID → migration
  proceeds) and `test_cli_apply_refuses_when_daemon_pid_alive` (live PID via
  `os.getpid()` → migration blocked).

## [0.7.0] - 2026-05-25

Project-scoped encrypted secrets vault. Agents can now persist and retrieve
connection credentials (SSH keys, DB DSNs, API tokens) across sessions and
machine reboots — encrypted at rest, hard-isolated from `semantic_search`,
`hydrate`, and `lint_knowledge_base`, and never transmitted anywhere
(`src/` continues to contain zero outbound HTTP code).

### Added
- Four new MCP tools: `set_secret(name, value)`, `get_secret(name)`,
  `list_secrets()`, `delete_secret(name)`. Each is implicitly scoped to the
  active project; there is no `scope` parameter.
- `Subsystem.SECRETS` migration chain (`v1 -> v2`) that walks
  `<storage_root>/projects/*` on first `turbo-memory-mcp migrate --apply`
  after upgrade and provisions an empty per-project vault under
  `projects/<project_id>/secrets/`. Idempotent and unconditionally green —
  if the master key cannot be resolved during migration, a stub `meta.json`
  is written and the vault initializes on first successful `set_secret`.
- Per-project encrypted store: `vault.tqv` (AES-256-GCM, 12-byte nonce,
  16-byte GCM tag), `meta.json` (KDF params + key_mode for diagnostics,
  no key material), `audit.jsonl` (append-only access log;
  `(timestamp, action, name)` only, values never logged).
- Subsystem-level marker `<storage_root>/secrets-manifest.json` tracking
  the SECRETS migration `format_version`.
- Master-key resolution priority: env `TQMEMORY_SECRETS_PASSPHRASE`
  (Argon2id, per-project salt) → existing OS keyring entry
  (`turbo-quant-memory` / `secrets-master-<project_id>`) → keyring
  auto-bootstrap (generate + store) on a writable backend → hard fail with
  an actionable setup hint. No interactive prompt fallback — it would
  silently die on reboot.
- Three new runtime dependencies (all pure-Python wheels with native
  CPython extensions, no system-level deps): `keyring>=25.0.0,<26.0`,
  `cryptography>=43.0.0,<47.0`, `argon2-cffi>=23.1.0,<24.0`.
- New CLI subcommand `turbo-memory-mcp secret-set NAME` for first-time
  secret provisioning without exposing the value to a chat transcript.
  On a TTY it reads via `getpass` (hidden input, never enters shell
  history or scrollback); on a pipe it consumes stdin verbatim. Exit
  codes: `0` stored, `2` invalid input (empty / bad name), `3` master
  key unavailable (the setup hint is printed to stderr verbatim).
- 73 new unit / integration tests (`tests/test_secrets_*.py`) covering
  crypto round-trips, KDF determinism, key-resolver branches,
  store CRUD + permissions + tampering, migration provisioning,
  MCP response shapes, ingest/lint/retrieval isolation, and a
  sentinel-grep regression that proves planted secret values never
  surface via `semantic_search`.

### Changed
- `self_test.tool_count` grows `14 -> 18`. `EXPECTED_TOOL_NAMES` extended
  in `tests/test_tools.py` and `scripts/smoke_test.py`. Smoke
  `tool_count` assertion also bumped from `11` (stale) to `18` in the
  same pass.
- `scripts/smoke_test.py` now performs a full `set_secret -> list_secrets
  -> get_secret -> delete_secret -> get_secret(==missing)` round-trip
  against a temp `TQMEMORY_HOME` with a smoke-only
  `TQMEMORY_SECRETS_PASSPHRASE`. New `PASS secrets vault round-trip` line
  in the success summary.

### Security
- New `Security and Trust > Secrets vault threat model` section in
  `TECHNICAL_SPEC` (EN/UK/RU). In scope: accidental backup leaks
  (Time Machine, rsync, iCloud), share-screen / screenshot leaks,
  accidental `git add`. Explicitly out of scope: root compromise, live
  daemon takeover, hardware attacks, multi-tenant isolation. Users with
  bigger threat models should use a dedicated secret manager.
- Ingestion (`ingestion._resolve_roots`) and lint (`knowledge_lint._resolve_roots`)
  refuse to register any path inside the vault subtree with a clear
  `ValueError`. Both `_iter_markdown_files` walkers skip files under
  `projects/<id>/secrets/` as defense in depth.
- `get_secret` response keeps the value in a dedicated `secret_value`
  field only; `set` / `list` / `delete` responses never echo the value.
- Agent recipe documented in `AGENTS.md`, project `CLAUDE.md`, README
  EN/UK/RU section 5, and MEMORY_STRATEGY EN/UK/RU. Split by whether
  the value is already in the chat:
    * NOT yet in chat -> recommend `turbo-memory-mcp secret-set NAME`
      from a terminal (getpass hidden input keeps it out of any
      transcript). This is the prophylactic path.
    * Already in chat (user pasted or agent generated) -> call
      `set_secret(name, value)` directly. The agent's deterministic
      `project_id` resolution is the safer write path once exposure
      has happened; pushing the user back to the CLI just to redo a
      value already in the transcript is friction without protection
      and risks landing the secret in the wrong project if the user's
      terminal cwd does not match the intended one.

### Migration
- Stop all MCP clients, then run `turbo-memory-mcp migrate --apply`.
  A rolling snapshot is taken automatically; on failure the CLI prints
  the exact `--restore-from` command. The v1->v2 step creates the
  vault directory structure for every existing project, then bumps
  `secrets-manifest.json` to `format_version=2`. The migration is
  idempotent — re-running on a fully-provisioned tree is a no-op.

### Documentation
- New "Secrets Vault (NEW in v0.7.0)" sections in all three README
  variants (EN/UK/RU), written in user-reassurance tone: WHY built,
  WHAT CHANGES, WHAT DOES NOT CHANGE, WHERE secrets live and where
  they don't, HOW to use, THREAT MODEL, FAQ.
- `TECHNICAL_SPEC` (EN/UK/RU): MCP Tool Surface table extended; new
  "Secrets vault" subsection under Data Model; "Security and Trust"
  extended with the explicit "src/ contains zero outbound HTTP code"
  claim plus the threat-model breakdown.
- `MEMORY_STRATEGY` (EN/UK/RU): new "Secrets vs Notes" subsection plus
  guardrails extension.
- Per-phase planning artifacts under `.planning/phases/09-secrets-vault/`
  with 3 wave plans + 2 wave summaries + 1 context document.

## [0.6.1] - 2026-05-22

Knowledge Graph Relations implementation. Allows linking memory notes, source files, issues, or tasks in a structured graph at both project and global scopes.

### Added
- Three new MCP tools: `link_entities(...)`, `unlink_entities(...)`, and `get_related_entities(...)`.
- Automatic graph relation enrichment: `semantic_search` and `hydrate` now automatically query the graph and return matching relations inside the payload (`relations` field).
- Cross-scope capability: relations can be created and queried at both `project` and `global` scopes.
- Fully automated Pytest suite for graph relations verifying store logic, search enrichment, and contract compliance.

### Changed
- Refactored `CURRENT_TOOL_NAMES` in `contracts.py` to support 14 tools (up from 11).
- Enhanced retrieval decoration pipeline to look up relations on candidate blocks during retrieval.

## [0.6.0] - 2026-05-22

Phase 3 of the Memory Quality v1 milestone: hybrid retrieval combining
dense-vector search with BM25 (full-text-search) via Reciprocal Rank
Fusion. Particularly addresses the structural overhead-on-short-notes
problem documented in v0.5.x — exact-term hits (function names, file
paths, IDs) now bubble up reliably even when their vector signal is
weak.

### Added
- BM25 lane in `RetrievalIndex.search`: every query now hits the
  dense-vector index AND the FTS index in parallel, results combined
  via RRF (`k=60`). Synthetic `_distance=0.5` for FTS-only hits keeps
  downstream scoring stable.
- `_ensure_fts_index(table)` idempotent helper that creates the BM25
  index on `content_search` on first use.
- `_safe_vector_search` / `_safe_fts_search` defensive wrappers — one
  broken lane no longer takes down the whole query.
- `_rrf_merge` utility producing reproducible rankings (preserves
  vector-row `_distance` when an item appears in both lanes).

### Migration
- New `Subsystem.RETRIEVAL` upgrade v2 -> v3 in `migrations/upgrades.py`:
  no data movement, just creates the FTS index on existing tables.
  Fast on any size of corpus.
- `RETRIEVAL_FORMAT_VERSION` bumped to 2 in `store.py` so fresh
  installs land at v2 and only the v2 -> v3 step is pending.
- Pre-existing v1 installs run both `v1 -> v2` (tier column reset) and
  `v2 -> v3` (FTS index) in a single `migrate --apply` invocation.

### Backwards compatibility
- Legacy installs that have not yet run `migrate --apply` get an empty
  FTS lane and the same vector-only behavior as v0.5.x. No regression.
- The agent-visible `migrations_pending` / `migrations.pending` signals
  introduced in v0.5.1 surface the new pending step exactly the same
  way: agents say "run `turbo-memory-mcp migrate --apply`" to the user.

### Tests
- `_rrf_merge` correctness (combining lanes, synthetic distance,
  skipping rows without `item_id`).
- `_ensure_fts_index` idempotency on a real LanceDB instance.
- `_safe_fts_search` graceful degradation contract.
- Live LanceDB hybrid probe: an item that tops both lanes ranks #1.
- `upgrade_retrieval_v2_to_v3` exercise via stubbed `RetrievalIndex` —
  both scopes open, both get `_ensure_fts_index`. No reset.
- Full suite: 168/168 green.

## [0.5.1] - 2026-05-22

Agent-visible pending-migration signal. Closes the UX gap from v0.5.0:
the stderr warning at daemon startup is only visible to humans reading
client logs. After upgrading the binary, agents still ran against the
legacy v1 schema silently. v0.5.1 surfaces the same detection through
two MCP probes every agent calls at session start.

### Added
- `mcp__tqmemory__health` payload now includes:
  - `migrations_pending: bool` — single flag so agents can branch cheaply.
  - `migrations_hint: str` (only when pending) — one-line operator
    instruction safe to surface in a tool response.
- `mcp__tqmemory__server_info` payload now includes a `migrations`
  block with per-subsystem detail:
  ```json
  "migrations": {
    "pending": true,
    "subsystems": [
      {"subsystem": "notes", "current_version": 1, "latest_version": 2,
       "pending": true, "step_count": 1},
      ...
    ],
    "hint": "Stop all MCP clients, then run `turbo-memory-mcp migrate --apply` ..."
  }
  ```
- Tests cover both the payload shape and the live store -> probe path
  (legacy manifest yields `pending=true`; clean store yields
  `pending=false`).

### Agent integration recommendation
Agents (Claude Code / Codex / Gemini CLI / Cursor / OpenCode / etc.)
should query `mcp__tqmemory__server_info` (or the cheaper
`mcp__tqmemory__health`) on session start. If `migrations.pending`
(or `migrations_pending`) is true, surface the included `hint`
verbatim to the user — it already names the exact command and
notes the lockfile requirement.

## [0.5.0] - 2026-05-22

Phase A (migration framework) and Phase 2 (tier separation) of the
Memory Quality v1 milestone. Behavior change: episodic notes (handoffs)
no longer pollute default semantic_search.

Backward-compatible upgrade: legacy LanceDB tables keep working until
`migrate --apply` runs. The daemon-lock guard blocks `--apply` while a
primary MCP daemon is running so users do not race with a live writer.

### Phase 2 — Tier separation (this release)

- Notes now carry a `tier` field: `durable` (decisions / patterns /
  lessons), `episodic` (handoffs), or `reference` (markdown blocks).
- `semantic_search` accepts a new `tier_filter` argument. Default is
  `("durable", "reference")` so session handoffs no longer drown durable
  knowledge in the default search. Opt episodic in via
  `tier_filter=("episodic",)` or list every tier to query everything.
- `remember_note` auto-assigns tier from `kind`: `handoff` -> episodic,
  everything else -> durable. Explicit `tier=` overrides for tests.
- New `Subsystem.NOTES` migration chain in the framework. Pre-Phase-2
  manifests (without a `format_version` field) are detected as v1 and
  the `notes v1->v2` migration tags every existing note with the right
  tier — idempotent on re-run.
- New `Subsystem.RETRIEVAL` v1->v2 migration resets the LanceDB tables
  on first run so the new `tier` column appears in the vector index.
- Migration framework: project + global manifests now record
  `format_version` (was absent before Phase 2).
- MCP wrapper `semantic_search_impl` exposes `tier_filter` so MCP
  clients can opt into episodic search (e.g. `tier_filter=("episodic",)`)
  when needed; default still excludes episodic.
- `promote_note` preserves an explicit `tier` set on the project note
  when copying it to global scope (previously the tier was silently
  re-derived from `kind` on the global side).
- Graceful upgrade path: until `migrate --apply` runs, existing LanceDB
  tables stay on the v1 schema (no `tier` column). `RetrievalIndex.search`
  introspects the live table and skips the WHERE clause when the column
  is missing, so search keeps working between upgrade and migrate.
- `semantic_search` and `hydrate` payloads now expose the `tier` field
  on every item (both note and markdown). Clients can filter or render
  by tier without re-fetching the source record. `tier` is omitted on
  legacy hits that pre-date Phase 2; markdown blocks always carry
  `tier="reference"`.
- All manifest writers (`write_project_manifest`, `write_global_manifest`,
  `write_markdown_manifest`, `write_*_retrieval_manifest`) now preserve
  any bumped `format_version` on disk instead of overwriting it with
  the in-code constant. Without this every `remember_note` /
  `index_paths` after a `migrate --apply` would silently revert the
  manifest and re-trigger the detect/apply loop forever. Auto-repair
  of stale `format_version=0` manifests still works because the
  effective value is `max(existing, in_code_baseline)`.
- `_bump_manifest` for `NOTES` now creates the proper full project /
  global manifest payloads (scope, identity, storage_root) when they
  do not exist on disk, instead of writing a stripped
  `{format_version, updated_at}` dict.

### Phase A — Migration framework foundation

Foundation activated earlier this cycle. Internal — surfaces only when
the daemon detects a pending upgrade or when the operator runs
`turbo-memory-mcp migrate`.

### Added
- `migrations/` package: `@migration` decorator, `Subsystem` enum
  (`markdown`, `retrieval`, `usage_stats`), linear-chain registry,
  per-subsystem `format_version` detection.
- Runner with detect / dry-run / atomic apply (manifest is bumped last
  per step so a crash leaves storage at the previous version and the
  upgrade can be safely retried).
- Rolling-backup snapshot helper under `<storage_root>/.snapshots/` with
  microsecond-precision timestamps and configurable retention
  (`TQMEMORY_SNAPSHOTS_KEEP`, default 1). `restore_snapshot` uses a
  staging-then-copy pattern so a failed copy rolls back to the original
  state instead of leaving storage half-written.
- Structured JSONL log at `~/.turbo-quant-memory/migration.log`
  (overridable via `TQMEMORY_MIGRATION_LOG_PATH`).
- Daemon startup (primary / standalone roles only) detects pending
  upgrades and writes a single-line warning to stderr. Detection never
  blocks startup; proxies skip the check.
- `turbo-memory-mcp migrate` CLI subcommand:
  - `--status` (default): per-subsystem current vs latest.
  - `--dry-run`: list pending upgrades without touching storage.
  - `--apply`: snapshot + atomic apply. Refuses to run if a daemon
    lockfile is present unless `--force` is set. On failure, prints the
    exact `--restore-from` command to roll back.
  - `--snapshot-only`: take a backup without applying.
  - `--list-snapshots`: list available snapshots.
  - `--restore-from <path>`: restore live storage from a snapshot.
  - `--no-snapshot`: escape hatch (tests only).
  - `--force`: bypass daemon-lockfile check.
- Test coverage for registry chain/gap/duplicate, runner atomicity /
  idempotency / failure path / multi-subsystem, snapshot
  create / restore / rollback / retention, log JSONL shape, and the
  full CLI surface (status, dry-run, apply, list, restore, daemon-lock
  guard, snapshot-failure handling).

## [0.4.3] - 2026-05-22

Maintenance baseline before the next architecture cycle. No behavioral changes.

### Added
- `CHANGELOG.md` with the full release history.
- `.claude/` directory listed in `.gitignore` so Claude Code local configuration is no longer tracked.

### Changed
- README and SMOKE checklist install commands now point at `v0.4.3`.

## [0.4.2] - 2026-04-28

### Added
- Gemini CLI fixture and the bundled `.gemini/settings.json` now ship `"context": {"fileName": ["AGENTS.md", "GEMINI.md"]}`, so Gemini CLI picks up the same `AGENTS.md` project prompts the rest of the agents already use — no duplicate `GEMINI.md` mirror required.
- New SMOKE checklist step warning operators that merging the Gemini fixture into an existing `settings.json` must preserve the `context` block.
- Tracking entry `.planning/todos/2026-04-28-lint-false-positives.md` for two `lint_knowledge_base` issues (ASCII-only title-key normalization, `broken_link` reports for `DEFAULT_IGNORED_DIR_NAMES`).

### Changed
- README and SMOKE checklist install commands updated to point at the actual current release, with a new `Upgrading` subsection covering `uv tool install --reinstall` and the one-time `~/.gemini/settings.json` migration.

## [0.4.1] - 2026-04-24

### Fixed
- Automatic proxy failover when the primary process dies. Proxies detect the loss via `PrimaryUnreachable` on the next RPC call, one surviving proxy atomically promotes itself to primary, and every other orphaned proxy reconnects. No MCP client restart required.
- Race in concurrent promotion serialized via lockfile ordering.
- Phase-aware RPC error handling: connect/send-phase failures translate into `PrimaryUnreachable` (safe to replay); mid-call failures surface unchanged so non-idempotent tools like `remember_note` are never silently duplicated.
- Hardened singleton rollout from v0.4.0.

### Added
- Gemini CLI fixture ships `context.fileName: ["AGENTS.md", "GEMINI.md"]` for consistent project prompts across agents.

## [0.4.0] - 2026-04-24

### Added
- Singleton daemon transport for cross-process memory sharing. Only one `turbo-memory-mcp` process per machine holds the sentence-transformers model and LanceDB handles; every additional MCP-client launch becomes a thin stdio↔socket proxy.
- Cross-platform coordination: `AF_UNIX` socket on Unix/macOS (short path, 0600 perms), named pipe on Windows. Authenticated via a 32-byte random authkey stored in `~/.turbo-quant-memory/.daemon.lock`.
- Lazy imports in `retrieval_index` so proxy processes do not pay the ~470 MB cost of PyTorch / LanceDB / PyArrow when they only forward RPC.

### Changed
- Measured savings: ~1 GB RSS for four concurrent MCP clients (primary 530 MB, proxies ~50 MB each vs 437 MB each before).

### Notes
- Existing on-disk state (JSON notes, LanceDB tables, Markdown blocks, manifests) is unchanged and fully backward-compatible.
- Escape hatch: set `TQMEMORY_DAEMON_DISABLE=1` to fall back to per-process mode.

## [0.3.2] - 2026-04-12

### Added
- `.tqmemoryignore` support for excluding paths from Markdown indexing. One glob pattern per line, `#` for comments; works in project root or any indexed directory.

### Fixed
- `__version__` bump and telemetry payload corrections.

## [0.3.1] - 2026-04-04

### Added
- Shared-memory guidance for Codex and Gemini CLI handoffs in README and client integration docs.
- Ready Gemini CLI fixture plus smoke-check steps for validating the same `tqmemory` server across clients.

### Changed
- Clarified that shared memory is local same-machine continuity, not remote cloud sync.

## [0.3.0] - 2026-04-03

### Added
- Resilience and telemetry rollout.
- Audit hardening todos captured in `.planning/`.

### Changed
- Trilingual README copy (English, Ukrainian, Russian).

## [0.2.4] - 2026-04-03

### Added
- Knowledge-base linting (`lint_knowledge_base`) for broken links, orphans, and duplicate titles.

### Changed
- Release notes and documentation refresh.

## [0.2.3] - 2026-03-28

### Added
- Trilingual documentation set across README, TECHNICAL_SPEC, MEMORY_STRATEGY, and SMOKE checklists.

## [0.2.2] - 2026-03-26

### Fixed
- Package metadata corrections for PyPI/GitHub release artifacts.

## [0.2.1] - 2026-03-26

### Changed
- Memory hygiene improvements (note lifecycle housekeeping, telemetry payload cleanup).

## [0.2.0] - 2026-03-25

### Added
- Note lifecycle tools: `deprecate_note`, `promote_note`, and release-grade documentation around them.

## [0.1.0] - 2026-03-15

### Added
- Initial release.
- `remember_note`, `semantic_search`, `hydrate`, `index_paths`, `lint_knowledge_base` core tools.
- Project/global scope memory.
- Hydration paths and benchmark suite.
- Trilingual documentation (English, Ukrainian, Russian).

[0.6.1]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.4.3...v0.5.0
[0.4.3]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.2.4...v0.3.0
[0.2.4]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Lexus2016/turbo_quant_memory/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Lexus2016/turbo_quant_memory/releases/tag/v0.1.0
