# Design: Workspace coordination for multi-agent work

- **Date:** 2026-10-16
- **Status:** Approved by the maintainer 2026-10-16. Brainstorming dialogue with the maintainer + consilium review (codex, grok, opencode, hermes answered; agy returned nothing). Not yet implemented.
- **Decisions locked with the maintainer:**
  - This is a tqmemory **product feature for all users**, not workflow advice to them.
  - It is **enabled automatically** and works **purely through MCP**: everything runs inside tqmemory itself.
  - **No client hooks.** tqmemory never installs or requires hooks in Claude Code, Codex, OpenCode, Hermes or any other client, and never writes into another tool's configuration.
  - It must work with several agents in **one shared working tree**. Git worktrees stay supported and are never required.
  - Agents learn what other agents do through three channels: a dedicated status tool, a skill rule to check it regularly, and a compact status line attached to the responses of the other tqmemory tools.

## Problem

The maintainer routinely runs several different coding agents (Claude Code, Codex, Hermes, OpenCode) at the same time in one working tree. Each works on a different feature, and different features legitimately modify the same files.

Incident that started this: one agent rewrote a file over another agent's changes and that work was lost. Agent B wrote a file based on a version it had read earlier; agent A had changed the file in between. Nothing at commit time could have helped: the loss happened at write time in the working tree.

Every one of these agents already has tqmemory registered. tqmemory is the only component all of them share, which makes it the natural place for them to see each other.

## Goals

1. Each agent session can see which other agent sessions are live in the same working tree, what each announced it is working on, and which files changed recently.
2. Agents learn about other sessions' activity without the human relaying it, through tqmemory responses they already receive.
3. Work overwritten by another writer is recoverable: tqmemory keeps the versions of files that change while several sessions share a working tree, with history and restore.
4. Zero configuration: a user who upgrades tqmemory gets Goals 1–3 with no further step and no edits to other tools' configs.
5. A user with a single agent pays nothing noticeable: no extra text in responses, no file watching.

## Non-goals

- **No LLM coordinator or leader election.** Considered and dropped (consilium unanimous): agents idle between prompts and die (context overflow, crashes), any "first session wins" rule is launch order, and the daemon holder is per machine, not per project. Deterministic rules plus escalation to the human cover what is needed.
- **No locks and no ordering.** Announced work is information only; it never blocks or queues anyone. Features legitimately share files.
- **No blocking of writes.** Blocking would need client hooks, and this feature uses none (see "Why no client hooks").
- No semantic-conflict detection (B's change compiles but breaks A's logic).
- No coordination across machines.
- No change to the note schema in this feature (recording which session wrote a note is a reasonable follow-up, not part of this design).

## Anti-goals (failure even if it technically works)

- **False confidence.** Claiming "agents can no longer overwrite each other". This feature makes overwrites visible and recoverable; it does not prevent them. Docs and `workspace_status` must say exactly what is covered.
- **Noise.** Status text in responses when nothing new happened, or for single-agent users.
- **Latency.** Any measurable slowdown of ordinary memory tool calls.
- **Breaking the user's work because tqmemory failed.** Every coordination failure degrades silently to "no coordination"; memory tools keep working.
- **Edits to other tools' configs.** This feature never touches them.

## Constraints

- Runs on CPU with ~2 GB RAM; macOS, Linux, Windows (CI covers Windows).
- The daemon (`daemon.py`) is **one per machine**, serving all projects; proxies forward their cwd/env (`server.py:95-124`). A proxy that cannot reach the daemon silently falls back to standalone (`daemon.py:686`). Therefore **coordination correctness must not depend on the daemon**.
- Memory tool calls are serialized under a dispatch lock (`server.py` `make_local_dispatcher`; the daemon listener also dispatches under one lock, `daemon.py:565`). Coordination work must **never take or wait on that lock**: a long `index_paths` in one agent must not delay another agent's status.
- Every tqmemory process (primary, proxy, standalone) runs its own MCP server via `build_server(dispatcher)` (`server.py:133`), so per-session logic can run locally in each process.
- Coordination state stays out of notes and the vector index (fuzzy search is wrong for "who is in this tree"; temporary state would pollute memory).
- User-facing docs are trilingual (README and CLIENT_INTEGRATIONS in en/ru/uk). The version string lives in three places (see tqmemory note `8a32c57442b14407`).

## Concepts

- **Project** — the existing `project_id` (`identity.py`). It groups all worktrees/clones of one remote. Used only as the storage namespace.
- **Workspace** — one physical working tree: the canonical real path of the git top-level (or the resolved project root outside git). All coordination is keyed by workspace. Two worktrees of one repo are two workspaces and never see each other's file activity as a conflict.
  - Resolution (my call): start from the same root the memory side uses, `resolve_project_root()` in `identity.py` (the `TQMEMORY_PROJECT_ROOT` override first, then the process cwd), resolve symlinks, then take its git top-level. Memory project and coordination workspace therefore never disagree about which tree a session is in. Coordination runs in the session's own process, so the process's own cwd/env is the input; the `_cwd` a proxy forwards to the daemon plays no part.
- **Session** — one tqmemory MCP process. Each agent session launches its own MCP server process, so process ≈ agent session. The session id is minted at process start; the client name and version come from the MCP `initialize` handshake (`clientInfo`), which the server does not read today. Known limit: a client that serves several chats from one MCP process appears as one session.
- **Liveness** — a session is live while its process is alive, checked by PID plus process start time (to defeat PID reuse). No heartbeats, no TTLs. Rows of dead sessions are pruned lazily.

## Architecture

### Coordination store

- One SQLite database per project in WAL mode, under tqmemory storage (for example `~/.turbo-quant-memory/projects/<project_id>/workspace/coord.sqlite`; exact layout is the builder's).
- **Opened directly by every tqmemory process.** The file is the arbiter, not the daemon; a standalone process participates exactly like a proxy. Python stdlib `sqlite3`, short busy timeout, idempotent writes.
- Holds: sessions, announced work, the file-change journal index, per-session delivery cursors, the watcher lease, schema version. Never indexed, never mirrored into notes or LanceDB.
- Lives in its own module (for example `turbo_memory_mcp/workspace/`) with its own tests; it does not import retrieval or embedding code.

### Execution path

- Coordination tools and the status line are computed **in the local process** against SQLite. They are not forwarded to the daemon and do not take the memory dispatch lock.
- Memory tools keep their current dispatch (local or proxied) unchanged; the status line is attached after the memory result returns.

### Session registration

On process start (once the workspace is known), the process records: session id, client name and version from `clientInfo`, PID and process start time, workspace, project id, start time. Every tqmemory tool call updates the session's last-activity time. Clean exit removes the row; a crash leaves a row that liveness pruning removes.

### Awareness channel (automatic)

1. **`announce_work(feature, areas=None, details=None)`** — records or replaces what this session is working on; `areas` are paths or globs relative to the workspace. Returns overlaps with other live sessions' announced areas and recent changes inside this session's areas. **`finish_work()`** clears it. Announcements disappear when the session dies. Information only: never blocks, never orders.
2. **`workspace_status()`** — live sessions in this workspace (client, announced feature and areas, started, last activity); recent file changes (path, time, attribution level); overlaps between this session's areas and others' areas or changes; journal state; and a plain coverage statement ("versions are kept for files changed while several sessions share this tree; writes are not blocked").
3. **Status line in other responses** — every other tqmemory tool response gains a `workspace` field **only when something new happened since this session last saw status**: another session started or ended, announced or finished work, or a file changed by someone else inside this session's announced areas (or anywhere, if this session announced nothing). At most ~3 short lines plus a pointer to `workspace_status`. A per-session cursor prevents repeats. **When this session is alone in its workspace the field never appears.**
4. **Agent instructions** — the canonical SKILL.md and the server `instructions` string (`server.py:136`) gain a short rule set:
   - At session start, call `workspace_status`.
   - When starting a feature, call `announce_work`; when done, `finish_work`.
   - Call `workspace_status` regularly while working with other live sessions: before editing a file you have not touched recently, before a commit, and every few edits.
   - When a response carries a `workspace` field, read it; re-read any file another session changed since you read it before writing to it.
   - While other sessions are live in the workspace, do not run destructive git commands (`checkout -- .`, `reset --hard`, `stash`, `clean`, branch switch) without the human's explicit confirmation.

### Attribution

Each recorded change carries one level:
- **inferred** — the file lies inside the areas announced by exactly one live session;
- **unknown** — everything else.

Text never presents an inferred writer as fact ("changed — inside Codex's announced area", not "Codex changed it").

### Version journal (automatic)

- A filesystem watcher (my call: the `watchdog` package, the one new dependency) observes a workspace **only while two or more live sessions share it**. It stops when the workspace is back to one session; recorded data stays until retention removes it.
- **Baseline on start:** when the watcher starts, it snapshots every non-ignored file that differs from git `HEAD` (modified and untracked), so uncommitted work done before the second session appeared is covered.
- **Git workspaces only (my call).** Outside git there is no cheap baseline and no natural bound, so the journal does not run there; awareness still works and `workspace_status` says the journal is unavailable. The watcher also refuses a workspace whose root is the user's home directory or a filesystem root, and reports that state instead of watching.
- On each change event it reads the file promptly and stores the content in a content-addressed store under tqmemory storage (compressed, keyed by SHA-256), indexed in the coordination DB (workspace, relative path, hash, size, time, attribution level). Never written into the user's repository or `.git`.
- **Exactly one watcher per workspace** through a lease row: the owner must be a live process; any live tqmemory process of that workspace takes over when the owner is gone (checked on tool calls and on a background timer). Duplicate snapshots are harmless because storage is content-addressed and inserts are idempotent.
- **Journal log:** every recorded version and deletion is also appended as one JSON line to `workspace/journal.log` next to the blobs. When the versions table is empty (a new or healed database), the index is rebuilt from this log, skipping entries whose blob is gone. Eviction rewrites the log so it stays bounded.
- **Deletions and renames (my call):** a deletion is recorded as a journal entry for that path, and `restore_file` can bring a deleted file back from its last stored version (or from git `HEAD` when the file was clean). A rename is recorded as a deletion of the old path plus a new version at the new path.
- **Filters and limits:** `.gitignore`, `.tqmemoryignore`, `.git/`, binary files; defaults (my call) 1 MiB per file, 256 MiB per project, 14 days retention, all overridable by environment variables.
- **Eviction (my call):** versions older than the retention age are removed. Over the size cap, the oldest versions go first, but the newest version of every path changed in the last 24 hours is never evicted for size. Recording never stops because of the cap; when the cap cannot be met, `workspace_status` reports it.
- **`file_history(path, limit)`** lists versions (time, size, attribution). **`restore_file(path, version)`** first snapshots the current content (so a restore is itself undoable), then writes the chosen version and returns a short diff summary. CLI equivalents (for example `turbo-memory-mcp files log PATH` / `files restore PATH VERSION`) work without the daemon.
- **Documented limits:** two writes within the watcher's event window can lose the intermediate version; the watcher cannot block a write and cannot tell who wrote.

### Failure behavior and switch

- Any coordination failure (database busy past the short timeout, corrupt database, watcher error) leaves memory tools fully working: the `workspace` field is omitted, coordination tools report a degraded state, nothing is raised into memory tools.
- **A corrupt coordination database heals itself.** On open the database is checked (`PRAGMA quick_check`); a damaged file is set aside as `coord.sqlite.corrupt-<timestamp>` (only the newest such copy is kept) and a fresh database is created. Sessions re-register on their own and the version index is rebuilt from the journal log. Periodic backup copies were considered and rejected (maintainer agreed): the database is mostly ephemeral, and a periodic copy misses exactly the newest versions a restore needs.
- `TQMEMORY_WORKSPACE_DISABLE=1` (name is my call) turns the whole feature off; independent of `TQMEMORY_DAEMON_DISABLE`.
- `health()` / `server_info()` report: feature on/off, database path and state, watcher owner per active workspace, coverage.

### Amendment 2026-10-19: idle peers

The approved design pruned sessions only when their process died. A process can outlive its agent's activity by hours (a client window left open), and such a session kept appearing in `sessions` and `overlaps`, which led another agent to hold back work for no reason.

- A peer whose `last_activity_at` is older than `PEER_IDLE_AFTER_S` (30 minutes) is *idle*. `last_activity_at` is refreshed by every tqmemory tool response.
- This is a view-layer split only. `workspace_status` lists idle peers under `idle_sessions` (with `idle_for_s`) and an `idle_note`, and leaves them out of `sessions` and `overlaps`; `announce_work` ignores their areas. `health` and `server_info` report `live_sessions` for active sessions and add `idle_sessions`.
- The session row is never deleted for idleness. The peer's next call makes it active again, journaling and lease decisions still follow process liveness, and the digest's "alone" check is unchanged.
- Known limit: an agent that edits files without calling tqmemory looks idle. Its changes still show in `recent_changes`, with the writer unknown.

## Why no client hooks

Considered and rejected by the maintainer. Hooks would add write blocking and exact attribution, but each client has its own hook system, several require the human to trust or approve every hook (Codex `/hooks`, Hermes consent), and installing them means editing other tools' configs. This feature relies on MCP alone: what tqmemory can see and say through its own tools, responses and instructions.

## Testing that must exist

- Three processes on one workspace (primary, proxy, standalone) see each other; a `kill -9` session disappears from status; PID reuse does not resurrect a session.
- A coordination call completes while another process holds the memory dispatch lock in a long `index_paths`.
- The status line is absent for a lone session, appears once per new event, respects the cap.
- Watcher: starts when the second session appears with a correct baseline, snapshots changes, honors ignores and limits, hands the lease over after the owner dies; `restore_file` is undoable; a deleted file can be restored; eviction keeps the newest recent version of each path; a home-directory or non-git root is refused with a reported state.
- Two sessions started with different cwd inside one tree, or with `TQMEMORY_PROJECT_ROOT` set, resolve to the same workspace; two worktrees of one repo resolve to different workspaces.
- Fail-open: locked or corrupt database leaves every memory tool working.
- A corrupt coordination database is set aside and replaced automatically, and file history survives through the journal log.
- Windows CI passes.

## Docs and release

- SKILL.md (canonical agent manual; bump its version marker so `skill install` upgrades installed copies) and the server `instructions`.
- README en/ru/uk (after release, required by the maintainer): present this as an **automatic feature** — protection, explicitly not 100%, against the problems of several agents working on one project on one computer. State what it does with no setup (agents see each other, changed files are versioned and restorable) and what it does not do (it does not block writes; it does not span machines).
- CHANGELOG; minor version bump.

## Calls I made (please check)

- No LLM coordinator (Non-goals).
- The watcher runs only while two or more live sessions share a workspace; single-agent users get no file watching and no status line.
- `watchdog` as the one new dependency.
- Retention defaults: 1 MiB per file, 256 MiB per project, 14 days.
- Tool names (`announce_work`, `finish_work`, `workspace_status`, `file_history`, `restore_file`), env switch name, storage layout.
- Recording which session wrote a note is left out of this feature.
- Workspace resolution reuses `resolve_project_root()`, then the git top-level.
- The journal runs only in git workspaces, never on a home directory or filesystem root.
- Deletions and renames are journaled; deleted files are restorable.
- Eviction: oldest first, the newest recent version of each path protected, recording never stops.

## Left to the builder

SQLite schema and migrations; how to read `clientInfo` from the MCP session; watcher debounce; exact wording and JSON shape of the status line; CLI subcommand names within the existing `turbo-memory-mcp` style.
