# Workspace Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let several agent sessions that share one working tree see each other, see what each is working on and which files changed, and recover files that another writer overwrote — automatically, through MCP only.

**Architecture:** A new `turbo_memory_mcp/workspace/` package. Every tqmemory process (primary, proxy, standalone) owns one `WorkspaceRuntime`: a session row in a per-project SQLite database opened directly by every process (the file is the arbiter, never the daemon). Five new MCP tools call the runtime directly, outside the memory dispatch lock; every other tool's dict result gets a `workspace` field when something new happened. A `watchdog` observer, owned by exactly one live process per workspace through a lease row, keeps file versions in a content-addressed store while two or more sessions share a git workspace.

**Tech Stack:** Python ≥3.11, stdlib `sqlite3` (WAL), `watchdog` (new), FastMCP from `mcp` 1.26, pytest.

**Spec:** `docs/superpowers/specs/2026-10-16-workspace-coordination-design.md` — read it before starting any task.

## Global Constraints

- Automatic and MCP-only. No client hooks, no writes into any other tool's configuration.
- The only new dependency: `watchdog>=6.0.0,<7.0`.
- Coordination code never acquires or waits on the memory dispatch lock (`server.py` `make_local_dispatcher`, `daemon.py:565`) and never imports retrieval, embedding or LanceDB modules.
- Fail open: no coordination error ever propagates into a memory tool; the `workspace` field is simply omitted.
- Kill switch: `TQMEMORY_WORKSPACE_DISABLE` set to `1`, `true` or `yes` (case-insensitive) turns the whole feature off.
- Storage: `<resolve_storage_root()>/projects/<project_id>/workspace/coord.sqlite` and `.../workspace/blobs/`. Nothing is ever written into the user's repository or `.git`, except a file that `restore_file` restores on request.
- Journal defaults: 1 MiB per file (`TQMEMORY_JOURNAL_MAX_FILE_BYTES=1048576`), 256 MiB per project (`TQMEMORY_JOURNAL_MAX_BYTES=268435456`), 14 days (`TQMEMORY_JOURNAL_RETENTION_DAYS=14`). The newest content version of every path changed in the last 24 hours is never evicted for size.
- The journal runs only in git workspaces, only while ≥2 live sessions share the workspace, never on the home directory or a filesystem root.
- Status line: at most 3 lines plus a pointer to `workspace_status`; absent when the session is alone in its workspace.
- An inferred writer is never stated as fact ("inside Codex's announced area", never "Codex changed it").
- Tool names, exactly: `announce_work`, `finish_work`, `workspace_status`, `file_history`, `restore_file`.
- Paths stored and returned as workspace-relative POSIX strings.
- Agent-facing text (docstrings, SKILL.md, server instructions) in English; README in en/uk/ru.
- Tests are flat files under `tests/`; nothing new is skipped on Windows except tests that need the daemon socket (existing `pytestmark_win_daemon` pattern in `tests/test_daemon.py`).
- CI gates: `uv run --no-sync ruff check src tests`, `uv run --no-sync mypy src/turbo_memory_mcp`, `uv run --no-sync pytest -q`.

## Review Focus

- **Agents in different projects at the same time** (one daemon serves the whole machine): a session must never appear in another project's or another workspace's status. Test pinned in Task 4.
- **A burst of thousands of file changes** (dependency install into a non-ignored dir, format-all, codegen): the status line stays one collapsed line, the journal stays within limits, nothing raises. Tests pinned in Tasks 6 and 8.
- **Large and binary files:** skipped by the journal without error. Test pinned in Task 7.
- **A restore path that escapes the workspace** (`../`, absolute path elsewhere): refused, nothing written. Tests pinned in Tasks 7 and 10.
- **Paths with spaces, Cyrillic, mixed case:** recorded, listed and restored unchanged. Test pinned in Task 7.

---

### Task 1: Workspace resolution and storage layout

**Files:**
- Create: `src/turbo_memory_mcp/workspace/__init__.py`, `src/turbo_memory_mcp/workspace/models.py`, `src/turbo_memory_mcp/workspace/paths.py`
- Test: `tests/test_workspace_paths.py`

**Interfaces:**
- Consumes: `identity.resolve_project_root(cwd, environ)` (`identity.py:238`), `identity.resolve_project_identity(cwd, environ).project_id`, `store.resolve_storage_root(environ)`.
- Produces:
  - `models.Workspace` (frozen dataclass): `root: Path`, `root_key: str` (= `root.as_posix()`), `project_id: str`, `is_git: bool`, `journal_refusal: str | None`.
  - `paths.resolve_workspace(cwd: Path | str | None = None, environ: Mapping[str, str] | None = None) -> Workspace`
  - `paths.workspace_storage_dir(storage_root: Path, project_id: str) -> Path`, `paths.coord_db_path(storage_root: Path, project_id: str) -> Path`, `paths.blobs_dir(storage_root: Path, project_id: str) -> Path`
  - `paths.is_disabled(environ: Mapping[str, str] | None = None) -> bool`
  - `paths.to_relative(workspace: Workspace, path: str | Path) -> str` — raises `ValueError("path outside workspace")`.

- [ ] **Step 1: Write the failing tests** in `tests/test_workspace_paths.py` (build real repos with `git init` in `tmp_path`; pass `TQMEMORY_HOME` in `environ`):
  - `test_two_cwds_in_one_tree_share_workspace` — `resolve_workspace(repo)` and `resolve_workspace(repo / "a" / "b")` have equal `root_key`.
  - `test_project_root_override_maps_to_git_toplevel` — `environ["TQMEMORY_PROJECT_ROOT"] = str(repo / "sub")` gives `root == repo.resolve()`.
  - `test_worktrees_are_different_workspaces` — after `git remote add origin https://example.com/x.git` and `git worktree add ../wt`, the two `root_key`s differ and the two `project_id`s are equal.
  - `test_non_git_root_refuses_journal` — plain dir: `is_git is False`, `journal_refusal == "not a git repository"`.
  - `test_home_root_refuses_journal` — monkeypatch `Path.home` to return the repo: `journal_refusal == "workspace root is the home directory"`.
  - `test_to_relative_rejects_outside_paths` — `"../outside"` raises `ValueError`; `"src/a b/файл.py"` returns `"src/a b/файл.py"`.
  - `test_disable_switch` — `"1"`, `"TRUE"`, `"yes"` → True; unset, `"0"`, `""` → False.
  - `test_storage_paths` — `coord_db_path(home, "p1") == home / "projects" / "p1" / "workspace" / "coord.sqlite"`; `blobs_dir` ends with `workspace/blobs`.

- [ ] **Step 2: Run** `uv run pytest tests/test_workspace_paths.py -v` — Expected: FAIL (module not found).

- [ ] **Step 3: Implement `paths.py` and `models.Workspace`.** Root = `resolve_project_root(cwd, environ)`, then `git -C <root> rev-parse --show-toplevel` when it succeeds, then `.resolve()`. Refusal checks in this order: filesystem root (`root.parent == root`) → `"workspace root is a filesystem root"`, home → `"workspace root is the home directory"`, not git → `"not a git repository"`.

- [ ] **Step 4: Run** `uv run pytest tests/test_workspace_paths.py -v` — Expected: PASS.

- [ ] **Step 5: Commit** — `git add src/turbo_memory_mcp/workspace tests/test_workspace_paths.py && git commit -m "feat(workspace): resolve the working tree and coordination storage paths"`

---

### Task 2: Process liveness

**Files:**
- Create: `src/turbo_memory_mcp/workspace/liveness.py`
- Test: `tests/test_workspace_liveness.py`

**Interfaces:**
- Consumes: `daemon._is_pid_alive(pid)` (`daemon.py:101`, cross-platform; never use `os.kill(pid, 0)` — on Windows signal 0 is `CTRL_C_EVENT`).
- Produces:
  - `process_start_time(pid: int) -> float | None` — seconds; stable across calls for one process; `None` when unknown. Linux: `/proc/<pid>/stat` field 22 ÷ `os.sysconf("SC_CLK_TCK")` + boot time; macOS: `ps -o lstart= -p <pid>` parsed to epoch; Windows: `GetProcessTimes` via `ctypes`.
  - `is_process_alive(pid: int, start_time: float | None) -> bool` — False when the pid is not running, or when `start_time` is given, the current start time is known and they differ by more than `LIVENESS_TOLERANCE_S = 1.0`. The current start time of a pid is cached for `START_TIME_CACHE_S = 60.0`.

- [ ] **Step 1: Write the failing tests:**
  - `test_current_process_is_alive` — `is_process_alive(os.getpid(), process_start_time(os.getpid())) is True`.
  - `test_exited_process_is_dead` — `Popen([sys.executable, "-c", "pass"])`, `wait()`, then `is_process_alive(p.pid, None) is False`.
  - `test_reused_pid_is_dead` — `is_process_alive(os.getpid(), process_start_time(os.getpid()) - 3600) is False`.
  - `test_start_time_is_stable` — two calls for `os.getpid()` differ by ≤ 1.0.
- [ ] **Step 2: Run** `uv run pytest tests/test_workspace_liveness.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `liveness.py` per the interface.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(workspace): process liveness with start-time check against PID reuse"`

---

### Task 3: Coordination database

**Files:**
- Create: `src/turbo_memory_mcp/workspace/db.py`
- Modify: `src/turbo_memory_mcp/workspace/models.py`
- Test: `tests/test_workspace_db.py`

**Interfaces:**
- Produces:
  - `class CoordinationUnavailable(Exception)` — raised by every `CoordinationDB` method on `sqlite3.Error`.
  - `CoordinationDB.open(path: Path, *, busy_timeout_ms: int = 250) -> CoordinationDB` — creates parents, `journal_mode=WAL`, applies the schema below, `meta.schema_version = "1"`. Attributes `conn: sqlite3.Connection` (`check_same_thread=False`) and `lock: threading.Lock` (serializes use within one process). `close() -> None`.
  - Row dataclasses in `models.py`: `SessionRow(session_id, workspace, client_name, client_version, pid, proc_start, started_at, last_activity_at, feature, areas: tuple[str, ...], details)`, `EventRow(event_id, workspace, kind, session_id, path, detail: dict[str, Any], created_at)`, `LeaseRow(workspace, session_id, pid, proc_start, acquired_at)`.
  - Event kind constants in `models.py`: `SESSION_STARTED = "session_started"`, `SESSION_ENDED = "session_ended"`, `WORK_ANNOUNCED = "work_announced"`, `WORK_FINISHED = "work_finished"`, `FILE_CHANGED = "file_changed"`, `FILE_DELETED = "file_deleted"`.
  - Methods: `insert_session(row: SessionRow) -> None`, `update_session_client(session_id: str, client_name: str, client_version: str) -> None`, `touch_session(session_id: str, now: float) -> None`, `delete_session(session_id: str) -> None`, `list_sessions(workspace: str) -> list[SessionRow]`, `set_intent(session_id: str, feature: str, areas: Sequence[str], details: str | None) -> None`, `clear_intent(session_id: str) -> None`, `append_event(workspace: str, kind: str, *, now: float, session_id: str | None = None, path: str | None = None, detail: Mapping[str, Any] | None = None) -> int`, `events_after(workspace: str, after_id: int, limit: int = 200) -> list[EventRow]`, `max_event_id(workspace: str) -> int`, `prune_events(older_than: float) -> int`, `get_cursor(session_id: str) -> int | None`, `set_cursor(session_id: str, event_id: int) -> None`, `lease_owner(workspace: str) -> LeaseRow | None`, `try_acquire_lease(workspace: str, session_id: str, pid: int, proc_start: float | None, now: float, owner_alive: Callable[[LeaseRow], bool]) -> bool` (one `BEGIN IMMEDIATE` transaction: no row → take it; own row → True; `owner_alive(row)` False → replace; else False), `release_lease(workspace: str, session_id: str) -> None`.

Schema (the `versions` table is used by Task 7):

```sql
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
  client_name TEXT NOT NULL DEFAULT 'unknown', client_version TEXT NOT NULL DEFAULT '',
  pid INTEGER NOT NULL, proc_start REAL, started_at REAL NOT NULL, last_activity_at REAL NOT NULL,
  feature TEXT, areas TEXT NOT NULL DEFAULT '[]', details TEXT);
CREATE INDEX IF NOT EXISTS sessions_ws ON sessions(workspace);
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL, kind TEXT NOT NULL,
  session_id TEXT, path TEXT, detail TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_ws ON events(workspace, event_id);
CREATE TABLE IF NOT EXISTS cursors (session_id TEXT PRIMARY KEY, event_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS leases (
  workspace TEXT PRIMARY KEY, session_id TEXT NOT NULL, pid INTEGER NOT NULL,
  proc_start REAL, acquired_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS versions (
  version_id INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT NOT NULL, path TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('content', 'deletion')), sha256 TEXT, size INTEGER NOT NULL DEFAULT 0,
  observed_at REAL NOT NULL, attribution TEXT NOT NULL, session_id TEXT);
CREATE INDEX IF NOT EXISTS versions_path ON versions(workspace, path, version_id);
```

- [ ] **Step 1: Write the failing tests:**
  - `test_open_creates_schema_and_wal` — `PRAGMA journal_mode` is `"wal"`; all six tables exist; `schema_version == "1"`.
  - `test_concurrent_processes_write_without_errors` — `multiprocessing.get_context("spawn")`, 4 processes × 50 iterations of `insert_session` + `append_event` on one db file; afterwards 200 sessions, 200 events, no child exited non-zero.
  - `test_lease_is_exclusive_and_taken_over_from_dead_owner` — A acquires (True); B with `owner_alive=lambda r: True` gets False; B with `owner_alive=lambda r: False` gets True; `lease_owner(ws).session_id == "B"`.
  - `test_corrupt_file_raises_coordination_unavailable` — write `b"not a database" * 100` to the path; `CoordinationDB.open` raises `CoordinationUnavailable`.
  - `test_events_after_and_cursor` — three events; `events_after(ws, first_id)` returns the last two in order; `set_cursor`/`get_cursor` round-trip; `get_cursor("nobody") is None`.
- [ ] **Step 2: Run** `uv run pytest tests/test_workspace_db.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `db.py` and the row types.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(workspace): per-project coordination database in WAL mode"`

---

### Task 4: Session runtime and presence

**Files:**
- Create: `src/turbo_memory_mcp/workspace/runtime.py`
- Test: `tests/test_workspace_runtime.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces `class WorkspaceRuntime` with:
  - `WorkspaceRuntime.start(cwd: Path | str | None = None, environ: Mapping[str, str] | None = None) -> WorkspaceRuntime | None` — `None` when `is_disabled(environ)`. Resolves the workspace, opens the db; on `CoordinationUnavailable` returns a runtime with `degraded_reason` set. Otherwise inserts the session row (client `"unknown"`, `proc_start = process_start_time(os.getpid())`), appends `SESSION_STARTED`, sets this session's cursor to `max_event_id` (a new session gets no backlog), calls `tick()` once, then starts a daemon ticker thread calling `tick()` every `TICK_INTERVAL_S = 5.0`.
  - Attributes: `session_id: str` (`uuid4().hex[:12]`), `workspace: Workspace`, `db: CoordinationDB | None`, `degraded_reason: str | None`.
  - `note_client(name: str | None, version: str | None) -> None` — writes `update_session_client` once, when the name is first known.
  - `touch() -> None`.
  - `tick(now: float | None = None) -> None` — every session of this workspace whose process is not alive (`is_process_alive(pid, proc_start)`) is deleted with a `SESSION_ENDED` event (`detail = {"client": ..., "feature": ...}`); events older than `EVENT_RETENTION_S = 86400` are pruned; then calls `self._on_tick(live_sessions)` (a no-op hook until Task 8).
  - `live_sessions() -> list[SessionRow]` (includes this session), `others() -> list[SessionRow]` — read the rows of this workspace as last pruned by `tick()`; no process checks here, because `digest()` calls them on every tool response.
  - `shutdown() -> None` — idempotent: stops the ticker, deletes the session row, appends `SESSION_ENDED`, closes the db.
  - Fail-open rule for every public method: catch `CoordinationUnavailable`, set `degraded_reason`, return an empty value.

- [ ] **Step 1: Write the failing tests** (env = isolated `TQMEMORY_HOME` + a git repo cwd):
  - `test_three_processes_see_each_other` — start two child processes with `subprocess.Popen([sys.executable, "-c", CHILD], cwd=repo, env=...)`, where `CHILD` starts a runtime, prints its `session_id`, flushes and sleeps 60 s; the parent's runtime `live_sessions()` contains all 3 session ids. Kill the children in `finally`.
  - `test_killed_session_disappears_with_ended_event` — kill one child (`proc.kill()`, `wait()`), call `tick()`: its id is absent from `live_sessions()` and `events_after(ws, 0)` contains a `SESSION_ENDED` event with its `session_id`.
  - `test_runtimes_in_different_workspaces_do_not_see_each_other` — two repos, one `TQMEMORY_HOME`: each runtime's `live_sessions()` holds only itself.
  - `test_disabled_switch_returns_none` — `TQMEMORY_WORKSPACE_DISABLE=1` → `start()` returns `None`.
  - `test_degraded_when_db_corrupt` — garbage in `coord_db_path`: runtime is not `None`, `degraded_reason` is set, `live_sessions() == []`.
  - `test_shutdown_is_idempotent_and_removes_session` — after two `shutdown()` calls a second runtime sees only itself.
- [ ] **Step 2: Run** `uv run pytest tests/test_workspace_runtime.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `runtime.py`.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(workspace): session runtime with process-based presence"`

---

### Task 5: Announced work, status and attribution

**Files:**
- Create: `src/turbo_memory_mcp/workspace/status.py`
- Modify: `src/turbo_memory_mcp/workspace/runtime.py`
- Test: `tests/test_workspace_status.py`

**Interfaces:**
- Consumes: Task 4 runtime, `paths.to_relative`.
- Produces:
  - `status.area_matches(path: str, area: str) -> bool` — `area` with any of `*?[` → `fnmatch.fnmatchcase(path, area)`; otherwise `path == area` or `path.startswith(area.rstrip("/") + "/")`.
  - `status.infer_attribution(path: str, sessions: Sequence[SessionRow]) -> tuple[str, str | None]` — `("inferred", session_id)` iff exactly one session's areas match; else `("unknown", None)`.
  - `status.writer_hint(attribution: str, session: SessionRow | None) -> str` — inferred: `f"inside {client}'s announced area ({feature})"`; unknown: `"writer unknown"`; restore: `"restored by an agent"`.
  - `status.COVERAGE_TEXT` — exact copy: `"Versions are kept for files that change while two or more agent sessions share this working tree. Writes are not blocked. Agents that do not call tqmemory do not see this status."`
  - `runtime.announce_work(feature: str, areas: Sequence[str] | None = None, details: str | None = None) -> dict[str, Any]` — areas normalized via `to_relative` (outside → `ValueError`); stores the intent; appends `WORK_ANNOUNCED`; returns `{"status": "ok", "feature", "areas", "overlaps": [{"session_id", "client", "feature", "shared_areas"}], "recent_changes_in_areas": [{"path", "when", "attribution", "writer_hint"}]}`.
  - `runtime.finish_work() -> dict[str, Any]` — clears the intent, appends `WORK_FINISHED`, returns `{"status": "ok"}`.
  - `runtime.status() -> dict[str, Any]` — keys `workspace` (root), `you` (`session_id`, `client`, `feature`, `areas`), `sessions` (others: `client`, `feature`, `areas`, `started_at`, `last_activity_at`), `recent_changes` (up to 20 `FILE_CHANGED`/`FILE_DELETED` events from the last `STATUS_RECENT_WINDOW_S = 7200`: `path`, `when`, `attribution`, `writer_hint`), `overlaps`, `journal` (`{"state": "off", "reason": None}` until Task 8), `coverage` (`COVERAGE_TEXT`). Advances this session's cursor to `max_event_id`.

- [ ] **Step 1: Write the failing tests:**
  - `test_area_matches_prefix_and_glob` — `("src/retrieval/x.py", "src/retrieval")` True; `("src/retrieval_old.py", "src/retrieval")` False; `("src/a.py", "src/*.py")` True.
  - `test_infer_attribution_requires_exactly_one_match` — one matching session → inferred with its id; two → unknown; none → unknown.
  - `test_announce_reports_overlap_with_other_session` — A announces `["src"]`, B announces `["src/retrieval.py"]`: B's result lists A in `overlaps` with `shared_areas == ["src/retrieval.py"]`.
  - `test_announce_rejects_area_outside_workspace` — `areas=["../x"]` raises `ValueError`.
  - `test_status_lists_other_sessions_and_coverage_text` — `status()["sessions"]` has B's feature; `status()["coverage"] == COVERAGE_TEXT`.
  - `test_inferred_writer_never_stated_as_fact` — `writer_hint("inferred", codex_row)` contains `"announced area"` and does not start with `"codex"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_workspace_status.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `status.py` and the runtime methods.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(workspace): announced work, status and inferred attribution"`

---

### Task 6: Status line digest

**Files:**
- Modify: `src/turbo_memory_mcp/workspace/status.py`, `src/turbo_memory_mcp/workspace/runtime.py`
- Test: `tests/test_workspace_digest.py`

**Interfaces:**
- Consumes: Task 5.
- Produces:
  - `status.build_digest(events: Sequence[EventRow], me: SessionRow, sessions: Mapping[str, SessionRow], max_lines: int = 3) -> list[str]` — pure. Relevant events: session/work events whose `session_id != me.session_id`; file events relevant when `me.areas` is empty or the path matches one of them, skipping file events whose `detail["attribution"] == "inferred"` and `detail["session_id"] == me.session_id`. All relevant file events collapse into one line: `f"{n} files changed: {a}, {b}, {c}" + (" …" if n > 3 else "")` (newest first). Session lines name the client and feature. More than `max_lines` lines → the last kept line becomes `f"+{k} more updates"`.
  - `runtime.digest() -> dict[str, Any] | None` — reads `events_after(cursor)`, always advances the cursor to the newest id read; returns `None` when this session has no live others or nothing is relevant; else `{"new": lines, "more": "call workspace_status for details"}`.

- [ ] **Step 1: Write the failing tests:**
  - `test_digest_silent_when_alone` — a lone runtime with file events in the db: `digest() is None`.
  - `test_digest_appears_once_per_event` — B starts: A's first `digest()` has a line containing B's client; the second returns `None`.
  - `test_digest_caps_at_three_lines` — 5 other sessions start: `len(new) == 3` and `new[-1] == "+3 more updates"`.
  - `test_file_burst_collapses_to_one_line` — 2000 `FILE_CHANGED` events: exactly one line starts with `"2000 files changed:"`.
  - `test_digest_filters_by_my_areas` — A announced `["src"]`: a change to `docs/x.md` gives `None`, a change to `src/y.py` gives a line.
  - `test_digest_skips_changes_inferred_to_me`.
- [ ] **Step 2: Run** `uv run pytest tests/test_workspace_digest.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `build_digest` and `runtime.digest`.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(workspace): compact status digest with per-session cursor"`

---

### Task 7: Version journal

**Files:**
- Create: `src/turbo_memory_mcp/workspace/journal.py`
- Modify: `src/turbo_memory_mcp/workspace/models.py`
- Test: `tests/test_workspace_journal.py`

**Interfaces:**
- Consumes: `Workspace`, `CoordinationDB` (`versions` table, `append_event`), `paths.to_relative`, `paths.blobs_dir`.
- Produces:
  - `models.VersionRecord` (frozen): `version_id: int`, `path: str`, `kind: Literal["content", "deletion"]`, `sha256: str | None`, `size: int`, `observed_at: float`, `attribution: Literal["inferred", "unknown", "restore"]`, `session_id: str | None`.
  - `JournalLimits(max_file_bytes: int = 1_048_576, max_total_bytes: int = 268_435_456, retention_days: int = 14)` with `JournalLimits.from_environ(environ) -> JournalLimits` reading the three `TQMEMORY_JOURNAL_*` variables.
  - `Journal(workspace: Workspace, db: CoordinationDB, blobs_root: Path, limits: JournalLimits)`:
    - `record(rel_path: str, *, attribution: str = "unknown", session_id: str | None = None, now: float | None = None) -> VersionRecord | None` — `None` when skipped: file missing, larger than `max_file_bytes`, binary (NUL byte in the first 8192 bytes), or content equal to the latest content version. Otherwise stores the blob, inserts a `content` version, appends `FILE_CHANGED` with `detail = {"attribution", "session_id", "version_id"}`.
    - `record_deletion(rel_path: str, *, attribution: str = "unknown", session_id: str | None = None, now: float | None = None) -> VersionRecord | None` — `None` when the latest version is already a deletion; otherwise inserts a `deletion` version and appends `FILE_DELETED`.
    - `history(rel_path: str, limit: int = 20) -> list[VersionRecord]` — newest first.
    - `read_version(version_id: int) -> bytes`.
    - `restore(rel_path: str, version: int | Literal["HEAD"]) -> dict[str, Any]` — validates via `to_relative` (outside → `ValueError("path outside workspace")`, nothing written); snapshots the current file first with attribution `"restore"` when it exists; writes atomically (temp file in the same dir + `os.replace`); `"HEAD"` reads `git show HEAD:<path>`; a version of another path, another workspace or kind `deletion` → `ValueError`. Returns `{"status": "ok", "path", "restored_version", "previous_version_id", "diff": {"added": int, "removed": int}}` (line counts via `difflib`).
    - `evict(now: float | None = None) -> int` — removes versions older than `retention_days`; then, while referenced blob bytes exceed `max_total_bytes`, removes the oldest versions except the newest content version of each path whose `observed_at >= now - 86400`; deletes unreferenced blob files; sets `over_cap: bool`; returns the number of versions removed.
  - Blob layout: `blobs/<sha[:2]>/<sha>.z`, zlib level 6, temp + `os.replace`; writing an existing blob is a no-op.

- [ ] **Step 1: Write the failing tests:**
  - `test_record_dedupes_unchanged_content` — two `record` calls on unchanged content → the second returns `None`; `len(history) == 1`.
  - `test_record_skips_large_and_binary` — a 2 MiB file and a file with a NUL byte → `None`, no exception, no version rows.
  - `test_history_newest_first_and_read_version` — three edits → `history` ids descending; `read_version(oldest) == first content`.
  - `test_restore_is_undoable` — v1, v2 recorded; `restore(path, v1)` → file == v1 content and `previous_version_id` points to a `"restore"` snapshot equal to v2; restoring that id → file == v2.
  - `test_restore_deleted_file_from_last_version_and_from_head` — a dirty file deleted after being recorded → restore by id recreates it; a clean tracked file deleted → `restore(path, "HEAD")` recreates it.
  - `test_restore_rejects_path_outside_workspace` — `restore("../outside.txt", "HEAD")` raises `ValueError` and creates nothing outside the repo.
  - `test_unicode_and_space_paths_roundtrip` — `"docs/Мої нотатки/Read Me.md"` recorded, listed and restored with an identical path string.
  - `test_eviction_respects_age_cap_and_protects_recent_newest` — `retention_days=14`: a 15-day-old version is gone; with `max_total_bytes` smaller than the data, the newest version of a path changed 1 h ago survives and `over_cap` reports honestly.
  - `test_blobs_live_outside_repository` — `git status --porcelain` output is identical before and after `record`; the blob exists under `blobs_root`.
- [ ] **Step 2: Run** `uv run pytest tests/test_workspace_journal.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `journal.py`.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(workspace): content-addressed version journal with undoable restore"`

---

### Task 8: Watcher, lease and baseline

**Files:**
- Create: `src/turbo_memory_mcp/workspace/watcher.py`
- Modify: `src/turbo_memory_mcp/workspace/runtime.py`, `pyproject.toml`, `uv.lock`
- Test: `tests/test_workspace_watcher.py`

**Interfaces:**
- Consumes: Tasks 4–7; `ingestion._load_ignore_patterns(root)` and `ingestion._matches_ignore(rel_posix, patterns)` (`ingestion.py:415`, `:436`).
- Produces:
  - `WorkspaceWatcher(workspace: Workspace, journal: Journal, attribute: Callable[[str], tuple[str, str | None]], *, debounce_s: float = 0.3, observer_factory: Callable[[], BaseObserver] = Observer)` with `start() -> None` (baseline, then a recursive observer on the root), `stop() -> None`, `running: bool`, `flush_now() -> None` (processes queued paths synchronously; for tests).
  - Per debounced batch: drop paths under `.git/`; drop paths reported by one `git check-ignore -z --stdin` call; drop `.tqmemoryignore` matches; then `journal.record(...)` for existing files and `journal.record_deletion(...)` for missing ones, attribution from `attribute(path)`. A move event → `record_deletion(src)` + `record(dest)`.
  - Baseline: every path from `git status --porcelain=v1 -z --untracked-files=all` that exists (for renames, the new path) → `journal.record(...)`.
  - `runtime.journal: Journal | None` — built in `start()` when the workspace has no `journal_refusal` and the db opened.
  - The runtime builds its watcher with `attribute=lambda path: infer_attribution(path, self.live_sessions())`.
  - `runtime._on_tick(live)` — refusal → journal state `"unavailable"` with the reason; `len(live) >= 2` → `try_acquire_lease(..., owner_alive=lambda row: is_process_alive(row.pid, row.proc_start) and any(s.session_id == row.session_id for s in live))`: acquired → start the watcher if not running; not acquired → stop it if running. `len(live) < 2` → stop and `release_lease`. The lease owner runs `journal.evict()` at most once per hour.
  - Never start or stop the watcher while holding `db.lock`: the watcher's flush thread takes `db.lock` inside `journal.record`, so stopping it under the lock deadlocks. `_on_tick` decides under the lock, releases it, then starts or stops.
  - `shutdown()` now also stops the watcher and releases the lease when this session owns it.
  - `runtime.status()["journal"]` now reports `{"state": "watching" | "off" | "unavailable", "reason", "owner_session", "over_cap"}`.

- [ ] **Step 1: Add the dependency** — `uv add 'watchdog>=6.0.0,<7.0'`; Expected: `pyproject.toml` and `uv.lock` updated.
- [ ] **Step 2: Write the failing tests** (use `observer_factory=lambda: PollingObserver(timeout=0.1)`, then `flush_now()`):
  - `test_baseline_snapshots_dirty_and_untracked_files_only` — a committed clean file, a modified tracked file, an untracked file → only the latter two have versions.
  - `test_change_is_recorded_with_inferred_attribution` — B announced `["src"]`; editing `src/a.py` → a version with `attribution == "inferred"` and B's session id.
  - `test_ignored_files_are_not_recorded` — a `.gitignore`d file, a `.tqmemoryignore`d file and a change under `.git/` → no versions.
  - `test_delete_and_rename_are_journaled` — delete → a `deletion` version; rename `a.py` → `b.py` → a deletion for `a.py` and content for `b.py`.
  - `test_watcher_starts_with_second_session_and_stops_when_alone` — a lone runtime: state `"off"`; a second runtime starts, `tick()` on both → exactly one reports `"watching"`; the second shuts down, `tick()` → `"off"`, no lease row.
  - `test_lease_taken_over_after_owner_death` — insert a lease row owned by the pid of an exited child process (`Popen([sys.executable, "-c", "pass"]).wait()`) and a session id that is not live; with two live runtimes, `tick()` acquires the lease and one runtime reports `"watching"`.
  - `test_burst_of_files_stays_within_limits` — 300 untracked files created at once with `max_total_bytes` set low → no exception, `over_cap` True after `evict()`, the other session's `digest()` has one file line.
  - `test_refused_workspace_never_watches` — a non-git workspace with two runtimes → state `"unavailable"`, no lease taken.
- [ ] **Step 3: Run** `uv run pytest tests/test_workspace_watcher.py -v` — Expected: FAIL.
- [ ] **Step 4: Implement** `watcher.py` and the runtime lifecycle.
- [ ] **Step 5: Run** the same command — Expected: PASS.
- [ ] **Step 6: Commit** — `git add pyproject.toml uv.lock src/turbo_memory_mcp/workspace tests/test_workspace_watcher.py && git commit -m "feat(workspace): leased file watcher with git baseline"`

---

### Task 9: MCP integration

**Files:**
- Modify: `src/turbo_memory_mcp/server.py` (`build_server` at `:133`, `_run_primary` `:1114`, `_run_proxy` `:1147`, `_run_standalone` `:1159`), `src/turbo_memory_mcp/contracts.py:33`, `tests/test_tools.py` (`EXPECTED_TOOL_NAMES`)
- Modify: `src/turbo_memory_mcp/workspace/runtime.py` (adds `describe()`)
- Test: `tests/test_workspace_server.py`

**Interfaces:**
- Consumes: the full `WorkspaceRuntime` API from Tasks 4–8.
- Produces:
  - `build_server(dispatcher: Dispatcher, workspace: WorkspaceRuntime | None = None) -> MCPServer`.
  - New tools, calling the runtime directly and never `dispatcher`: `announce_work(feature: str, areas: list[str] | None = None, details: str | None = None)`, `finish_work()`, `workspace_status()`, `file_history(path: str, limit: int = 20)` → `{"status": "ok", "path", "versions": [...]}`, `restore_file(path: str, version: str)` (an id or `"HEAD"`). With `workspace is None` each returns `{"status": "disabled", "reason": "TQMEMORY_WORKSPACE_DISABLE is set"}`; when degraded, `{"status": "degraded", "reason": ...}`; when the journal is unavailable, `file_history`/`restore_file` return `{"status": "unavailable", "reason": ...}`.
  - Every existing tool's result passes through `_decorate(result)` inside `build_server`: `workspace.note_client(*client_info)` (from `mcp.get_context().request_context.session.client_params.clientInfo`, guarding `LookupError`/`AttributeError`), `workspace.touch()`, then `result["workspace"] = digest` when `result` is a dict and `workspace.digest()` is not `None`. The whole body is inside `try/except Exception` → return the result unchanged.
  - The five new tools also call `note_client` and `touch` first. `restore_file` accepts `version == "HEAD"` or a decimal id string; anything else returns `{"status": "error", "reason": "version must be a version id or HEAD"}`.
  - `runtime.describe() -> dict[str, Any]` — `{"enabled", "state": "ok" | "degraded" | "disabled", "reason", "workspace", "db_path", "live_sessions", "journal", "coverage"}`, attached as `"workspace"` to `health` and `server_info` results.
  - `contracts.PHASE_11_TOOL_NAMES = (*PHASE_10_TOOL_NAMES, "announce_work", "finish_work", "workspace_status", "file_history", "restore_file")`; `CURRENT_TOOL_NAMES = PHASE_11_TOOL_NAMES`.
  - Run modes: each creates `workspace = WorkspaceRuntime.start()` before `build_server(..., workspace=workspace)` and calls `workspace.shutdown()` in its `finally` when not `None`.
  - Server `instructions` (`server.py:136`) gain this exact sentence: `"When several agents share one working tree: call workspace_status at session start, announce_work when starting a feature and finish_work when done; read the `workspace` field whenever a response carries it and re-read any file another session changed before writing it; file_history and restore_file recover overwritten files."`

- [ ] **Step 1: Write the failing tests** with `create_connected_server_and_client_session(build_server(make_local_dispatcher(default_environ=env), workspace=rt))` (pattern: `tests/test_recent_context.py:346`):
  - `test_new_tools_are_listed` — the five names are in `list_tools()`; `EXPECTED_TOOL_NAMES` in `tests/test_tools.py` updated to `CURRENT_TOOL_NAMES`.
  - `test_lone_session_responses_have_no_workspace_field` — `remember_note` result has no `"workspace"` key.
  - `test_other_session_appears_in_next_response_once` — a second runtime starts and `note_client("codex", "1.0")`; the next `recent_context` result's `workspace.new` mentions `codex`; the call after that has no field.
  - `test_client_name_recorded_from_initialize` — after one call, the session's `client_name == mcp.client.session.DEFAULT_CLIENT_INFO.name`.
  - `test_workspace_tools_do_not_wait_for_memory_lock` — pass an explicit `threading.RLock()` to `make_local_dispatcher(dispatch_lock=...)`, hold it in another thread; `workspace_status` returns status `"ok"` within 2 s.
  - `test_memory_tools_survive_coordination_failure` — monkeypatch `rt.digest` to raise `RuntimeError`; `remember_note` still succeeds without the field.
  - `test_disabled_workspace_tools_report_disabled` — `workspace=None` → `workspace_status()["status"] == "disabled"`.
  - `test_server_info_reports_workspace_block` — `server_info()["workspace"]["state"] == "ok"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_workspace_server.py tests/test_tools.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the server changes.
- [ ] **Step 4: Run** `uv run pytest -q` (full suite) — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(workspace): MCP tools and status line on every response"`

---

### Task 10: CLI `files log` and `files restore`

**Files:**
- Modify: `src/turbo_memory_mcp/cli.py` (new `files` subparser next to `skill`, `:152`)
- Test: `tests/test_workspace_cli.py`

**Interfaces:**
- Consumes: `resolve_workspace`, `coord_db_path`, `blobs_dir`, `CoordinationDB`, `Journal`, `JournalLimits` — opened directly from the process cwd; no runtime, no session row, no daemon.
- Produces:
  - `turbo-memory-mcp files log PATH [--limit N]` — one line per version: `f"{version_id}  {iso_time}  {kind}  {size} B  {attribution}"`; exit 0; no versions → `"no versions recorded for PATH"`, exit 0.
  - `turbo-memory-mcp files restore PATH VERSION` (id or `HEAD`) — prints `f"restored {path} to {version} (previous content saved as version {previous_version_id})"`; exit 0. Any `ValueError` or unavailable journal → message on stderr, exit 2.
- [ ] **Step 1: Write the failing tests** (call `cli.main([...])` with `monkeypatch.chdir(repo)` and env):
  - `test_files_log_lists_versions` — two recorded versions → two lines, newest first.
  - `test_files_restore_roundtrip` — restore an older id → file content matches; exit 0.
  - `test_files_restore_rejects_outside_path_exit_2` — `["files", "restore", "../x", "HEAD"]` returns 2.
- [ ] **Step 2: Run** `uv run pytest tests/test_workspace_cli.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the subcommands.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(cli): files log and files restore"`

---

### Task 11: Agent instructions, README and version

**Files:**
- Modify: `src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md`, `README.md`, `README.uk.md`, `README.ru.md`, `CHANGELOG.md`, `pyproject.toml`, `server.json` (two version fields), `src/turbo_memory_mcp/__init__.py` (`_FALLBACK_VERSION`), `uv.lock`

- [ ] **Step 1: SKILL.md** — add section `### 3.6 Working alongside other agents` after 3.5 with exactly these rules:
  - At session start, call `workspace_status`.
  - When starting a feature, call `announce_work(feature, areas)`; when done, call `finish_work()`.
  - While other sessions are live: call `workspace_status` before editing a file you have not touched recently, before every commit, and every few edits.
  - When any tqmemory response carries a `workspace` field, read it. Before writing a file another session changed since you read it, re-read it.
  - While other sessions are live, do not run destructive git commands (`checkout -- .`, `reset --hard`, `stash`, `clean`, branch switch) without the human's explicit confirmation.
  - If your edit overwrote someone's work, or theirs overwrote yours: `file_history(path)` then `restore_file(path, version)`; a restore is itself undoable.
- [ ] **Step 2: README.md** — under "What it does" add the bullet `* **Several agents, one project — automatically.** …`, and a new section `## Several agents in one project` stating: it is on with no setup; agents see each other's sessions, announced work and recent file changes; files that change while two or more sessions share a git working tree are versioned and restorable (`file_history`, `restore_file`, `turbo-memory-mcp files`); it is partial protection, not a guarantee — writes are not blocked, agents that do not call tqmemory see nothing, it works on one computer; turn off with `TQMEMORY_WORKSPACE_DISABLE=1`. Retitle `## The 19 MCP tools` to `## The 24 MCP tools` and add the row `| Workspace | announce_work · finish_work · workspace_status · file_history · restore_file |`. `README.uk.md` and `README.ru.md`: the same content, faithfully translated, keeping the "not 100% / not a guarantee" statement.
- [ ] **Step 3: Version 0.31.0** — replace `0.30.0` in `pyproject.toml`, both fields of `server.json`, `_FALLBACK_VERSION`, SKILL.md frontmatter and its pinned install line; run `uv lock && uv sync` (the installed metadata feeds `__version__`). Add `## [0.31.0] - unreleased` to `CHANGELOG.md` (Added: workspace coordination, the five tools, `files` CLI, `watchdog` dependency; the release date is set by the release workflow, tqmemory note `8a32c57442b14407`).
- [ ] **Step 4: Verify** — `uv run --no-sync ruff check src tests`, `uv run --no-sync mypy src/turbo_memory_mcp`, `uv run --no-sync pytest -q`; Expected: all clean. `uv run turbo-memory-mcp skill install --dry-run` reports an upgrade to v0.31.0.
- [ ] **Step 5: Commit** — `git commit -m "docs: workspace coordination in SKILL.md and README; bump to 0.31.0"`
