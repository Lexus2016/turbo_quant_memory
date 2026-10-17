# Search Refresh and Dispatch Locking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One project's slow memory work must never make another project's tqmemory calls time out. A search must answer in bounded time even when its own project needs a large reindex. Peak memory must not grow with the corpus.

**Architecture:** Five independent fixes, ordered by risk.
1. The daemon listener stops taking the dispatch lock without a bound, so proxied calls get the existing 30 s "server busy" error.
2. The per-search checks stop reading every block JSON and every vector.
3. The single process-wide dispatch lock becomes one lock per project plus one global-scope lock.
4. A full mirror sync streams rows in chunks instead of holding the corpus in memory.
5. The search-time refresh does a bounded amount of work inline and hands the rest to a background worker that runs in batches.

**Tech Stack:** Python ≥3.11, `threading`, LanceDB, fastembed/ONNX, pytest.

**Spec:** No separate spec. Design and evidence are in this file (§Design) and in tqmemory notes `e1a17b6549504248` (diagnosis of the 2026-10-17 daemon timeout) and `363d34ffd9c74993` (dispatch-lock bound, issue #103).

## Design

Evidence, measured warm through `semantic_search_impl` on a copy of this repo's store (4,977 blocks):

| Phase | Time |
|---|---|
| `semantic_search_impl` total | 1,531 ms |
| `_repair_project_retrieval_if_needed` | 616 ms (reads every block JSON) |
| `_project_retrieval_requires_rebuild` | 414 ms (reads every block JSON for a `bool`) |
| `assess_project_index_freshness` | 411 ms |
| core retrieval | 134 ms |
| listing block ids from file names | 9 ms |

At 40k blocks each full block read takes about 4 s.

Memory, measured on a copy of the same mirror (5,111 rows):
- `existing_item_updated_at` runs on every search. It calls `table.to_arrow()`, which reads 25.6 MB in 189 ms. `table.search().select(["item_id", "updated_at"]).limit(n).to_arrow()` returns the same ids from 0.27 MB in 75 ms. At 40k rows that is about 200 MB versus 2 MB per search.
- `sync_project` and `sync_global` build every row, embed every text in one call and return every row with its vector. A 384-float Python list is about 12 KB, so 40k rows peak at several hundred MB.
- `index_paths_with_sync_plan` loads every block's full JSON only to group ids. That costs about 200 MB at 40k blocks.

Decisions:

- **Listener.** `DaemonListener._serve_conn` wraps the handler in an unbounded `with self._dispatch_lock:`. The dispatcher inside then re-enters the same RLock, so its 30 s bound never applies to proxied calls. Proxies therefore wait for the 120 s RPC timeout instead of getting "server busy". The listener stops locking; serialization lives only in the dispatcher.
- **Block ids.** A block file is `<block_id>.json` (`store.py:124`). Temp files from `_write_json_atomic` have no `.json` suffix, so file stems are the block ids.
- **Lock keys.** Every call takes the lock of its project, resolved from `_cwd`/`_environ` with `build_runtime_context` before locking. `reconcile_project_identity` never writes, and the lookup costs 6–12 ms warm. A call also takes the global lock when it touches global scope. The order is always project, then global. A call holds at most one project lock. The background worker takes only the project lock.
- **Shared state between projects:**
  - The embedder (one `lru_cache` model per process): one lock around loading and encoding, with encoding in chunks of 64 texts.
  - The usage-stats file: a process lock around its read-modify-write.
  - LanceDB datasets and JSON stores are per project. Cross-process telemetry races stay out of scope.
- **Bounded refresh.** The search-time refresh processes at most `INLINE_REFRESH_FILES` changed files and embeds at most `INLINE_REFRESH_ITEMS` mirror rows. The rest goes to a background `IndexRefresher`.
  - The refresher is stateless: each batch rediscovers pending work from file manifests and the mirror diff.
  - It takes the project lock per batch, never for the whole run.
  - While a job for a project is active, searches in that project skip the inline refresh and only report status.
  - Full mode is never budgeted. The rare full rebuilds (markdown or retrieval format change, error fallback) stay unbounded but now block only their own project.
- **Out of scope:** an `os.walk` rewrite of `_iter_markdown_files` (about 0.4 s here, with symlink and pattern-pruning risk), cross-process telemetry locking, and making format-change rebuilds incremental.

## Global Constraints

- No new dependencies. Python ≥3.11 (`pyproject.toml`).
- `DISPATCH_LOCK_TIMEOUT_SECONDS = 30.0` and `RPC_TIMEOUT_SECONDS = 120.0` keep their values. `TQMEMORY_DISPATCH_LOCK_TIMEOUT` keeps its semantics, where a value ≤ 0 means an unbounded wait.
- Lock order is project then global. No code path acquires global then project, or two project locks.
- `make_local_dispatcher(dispatch_lock=...)` keeps its current single-lock behavior and its `_DISPATCH_HOLDER` bookkeeping, because existing tests in `tests/test_daemon.py` and `tests/test_workspace_server.py` rely on it.
- Tests that open a real daemon socket use the `pytestmark_win_daemon` marker and the `storage_env` fixture from `tests/test_daemon.py`.
- Verification per task:
  - `uv run pytest -p no:cacheprovider -W ignore` exits 0. `-q` prints no summary line, so check the exit code.
  - `uv run ruff check src tests` and `uv run mypy src/turbo_memory_mcp` are clean.
  - Never run `ruff format` on whole files; most files predate formatting.
- Each task adds its user-visible change under `## [Unreleased]` in `CHANGELOG.md`.
- No version bump, tag or release inside Tasks 1–5.
- Task 6 (release and install) is authorized. On 2026-10-17 the user asked for a delivered, verified product and said not to ask about process.

## Review Focus

1. **Unresolvable project identity.** The dispatcher must not turn a handler's own error into a lock error, for example when `_cwd` does not exist. → Task 3, `test_unresolvable_project_still_reaches_the_handler`.
2. **Shutdown during a background batch.** `IndexRefresher.shutdown()` must return promptly, and the store must stay consistent, because manifests are written per file. → Task 5, `test_refresher_shutdown_returns_while_a_job_is_pending`.
3. **A file edited while a background job runs.** The next batch must pick up the new content. → Task 5, `test_background_refresh_picks_up_files_edited_mid_job`.
4. **A hybrid search while the project's job is active.** It must not wait on the worker for the global lock, because the worker never takes it. → Task 5, `test_worker_takes_only_the_project_lock`.
5. **Proxy processes.** A proxy never runs tool impls locally, so no worker thread may start at import or before the first `schedule`. → Task 5, `test_refresher_starts_no_thread_until_scheduled`.

---

### Task 1: Proxied calls honour the dispatch-lock bound

**Files:**
- Modify: `src/turbo_memory_mcp/daemon.py` (`DaemonListener.__init__`, `dispatch_lock` property, `_serve_conn` ~L565, class docstring)
- Modify: `src/turbo_memory_mcp/server.py` (`_run_primary` ~L1430 and `ProxyRuntime._failover` ~L1209: stop passing `dispatch_lock=` to `DaemonListener`)
- Test: `tests/test_daemon.py`

**Interfaces:**
- Produces: `DaemonListener(endpoint, handler, *, ready_event=None)`. The `dispatch_lock` parameter and property are removed, and the listener calls `handler` without any lock.

- [ ] **Step 1: Write the failing test** `test_proxied_call_gets_busy_error_naming_the_holder` (marked `pytestmark_win_daemon`, uses `storage_env`):
  - Monkeypatch `server.TOOL_HANDLERS["index_paths"]` to a slow handler that sets `entered` and waits on `release`, and `["health"]` to return `{"ok": True}`.
  - Build `dispatch = server.make_local_dispatcher(dispatch_lock=threading.RLock(), dispatch_lock_timeout=0.2)` and start `DaemonListener(endpoint, dispatch)`.
  - Run `dispatch("index_paths", {})` in a daemon thread and wait for `entered`.
  - Call `DaemonClient(endpoint).call("health", {})` and assert it raises `RuntimeError` matching `"'index_paths' holds the dispatch lock"`, with `elapsed < 5.0`.
- [ ] **Step 2: Run it and confirm it fails.** `uv run pytest tests/test_daemon.py::test_proxied_call_gets_busy_error_naming_the_holder -p no:cacheprovider`. Expected: FAIL, because the call waits for `release` (10 s) or times out instead of raising busy.
- [ ] **Step 3: Implement.** Remove the `with self._dispatch_lock:` block in `_serve_conn`, keeping the try/except that builds the `MESSAGE_OK`/`MESSAGE_ERROR` reply. Remove the `dispatch_lock` parameter and property, update both call sites in `server.py`, and change the class docstring so it says serialization is the handler's job.
- [ ] **Step 4: Move the serialization guarantee.** Replace `test_listener_serializes_concurrent_calls` with `test_local_dispatcher_serializes_proxied_calls`:
  - Same peak-tracking handler, registered as `server.TOOL_HANDLERS["health"]` via monkeypatch.
  - The listener handler is `server.make_local_dispatcher(dispatch_lock=threading.RLock(), dispatch_lock_timeout=0.0)`.
  - 4 clients; assert `peak == 1`.
- [ ] **Step 5: Run `tests/test_daemon.py`, then the full suite, ruff and mypy.** Expected: all pass.
- [ ] **Step 6: Add a CHANGELOG entry** under `[Unreleased]` → `### Fixed`: proxied calls now get the "memory server busy: '<tool>' holds the dispatch lock" error after `TQMEMORY_DISPATCH_LOCK_TIMEOUT` (30 s) instead of the opaque 120 s `daemon connection timeout`.
- [ ] **Step 7: Commit.** `git commit -m "fix(daemon): proxied calls honour the dispatch-lock bound"`

### Task 2: Per-search checks stop reading every block

**Files:**
- Modify: `src/turbo_memory_mcp/store.py` (new methods next to `list_markdown_blocks` ~L669)
- Modify: `src/turbo_memory_mcp/server.py` (`_apply_project_index_sync_plan` ~L2492, `_repair_project_retrieval_if_needed` ~L2595, `_project_retrieval_requires_rebuild` ~L2657)
- Modify: `src/turbo_memory_mcp/retrieval.py` (`_ensure_scope_synced` ~L233)
- Modify: `src/turbo_memory_mcp/retrieval_index.py` (`sync_project_blocks` ~L292)
- Test: `tests/test_ingestion_store.py`, `tests/test_index_drift_repair.py`

**Interfaces:**
- Produces:
  - `MemoryStore.list_markdown_block_ids(project_id: str | None = None) -> list[str]`: sorted stems of `*.json` in the blocks dir, with no file reads.
  - `MemoryStore.has_markdown_blocks(project_id: str | None = None) -> bool`: true on the first `*.json`.
  - `MemoryStore.iter_markdown_blocks(project_id: str | None = None) -> Iterator[dict[str, Any]]`: a generator that skips corrupt files with the existing log line. `list_markdown_blocks` is built on it.
- `ingestion._block_ids_by_file` and `MemoryStore.delete_markdown_root_data` iterate blocks instead of listing them. `index_paths_with_sync_plan` computes `block_count` as `len(store.list_markdown_block_ids())`.
- `RetrievalIndex.existing_item_updated_at` reads only the `item_id` and `updated_at` columns via `table.search().select([...]).limit(count_rows).to_arrow()`. The legacy fallback for tables without `updated_at` stays.
- `sync_project_blocks` skips blocks whose JSON is unreadable (`ValueError`, `OSError`) with a `[tqmemory] skipping unreadable markdown block <id>` stderr line, instead of raising.

- [ ] **Step 1: Write the failing tests.**
  - `test_list_markdown_block_ids_matches_stored_blocks` (`test_ingestion_store.py`):
    - Write 3 blocks via `write_markdown_block`, plus a stray `tmpabc123` file and a `notes.txt` in the blocks dir.
    - Assert `store.list_markdown_block_ids() == sorted(b["block_id"] for b in store.list_markdown_blocks())`.
    - Assert `store.has_markdown_blocks() is True`, and `False` on a fresh store.
  - `test_search_on_unchanged_project_reads_no_block_files` (`test_index_drift_repair.py`):
    - Index 5 docs with `index_paths_impl`, then call `semantic_search_impl` once.
    - Patch `MemoryStore._load_json_records_skipping_corrupt` with `autospec=True` and a side effect that records `label` and delegates to the original.
    - Call `semantic_search_impl` again and assert `labels.count("markdown block") == 0`.
  - `test_corrupt_block_file_does_not_trigger_full_rebuild` (`test_index_drift_repair.py`):
    - Index 3 docs and search once.
    - Overwrite one block file with `"{"` and delete its row from the mirror with `RetrievalIndex(store).delete_items(PROJECT_SCOPE, [that_id])`.
    - Patch `RetrievalIndex.sync_project` to raise `AssertionError`, then call `semantic_search_impl` and assert `payload["status"] == "ok"`.
- [ ] **Step 2: Run them and confirm they fail.** Expected:
  - `AttributeError` for the new methods.
  - A non-zero block-scan count.
  - The `AssertionError` from the full-rebuild path.
- [ ] **Step 3: Implement.**
  - Add the two store methods.
  - In `server.py`, use `store.has_markdown_blocks()` in the two `bool(...)` checks, and `set(store.list_markdown_block_ids())` for `expected_block_ids` in the repair.
  - In `retrieval.py`, replace the `list_markdown_blocks(...)` truthiness check with `has_markdown_blocks(...)`.
  - In `sync_project_blocks`, catch `(ValueError, OSError)`, which includes `FileNotFoundError`, then log and continue.
- [ ] **Step 4: Run the tests, the full suite, ruff and mypy.** Expected: pass.
- [ ] **Step 5: Add a CHANGELOG entry** (`### Fixed`): a search no longer reads every markdown block file to decide whether the index needs repair. Measured on 5k blocks: about 1.5 s down to about 0.6 s per search, and several seconds saved at 40k blocks.
- [ ] **Step 6: Commit.** `git commit -m "perf(search): freshness checks list block ids instead of reading every block"`

### Task 3: One dispatch lock per project

**Files:**
- Create: `src/turbo_memory_mcp/locks.py`
- Modify: `src/turbo_memory_mcp/server.py` (`make_local_dispatcher` ~L1031; production call sites in `_run_primary`, `ProxyRuntime.__init__`, `ProxyRuntime._failover` and standalone fallback; remove the now-unused `ProxyRuntime._dispatch_lock`)
- Modify: `src/turbo_memory_mcp/retrieval_index.py` (embedder lock and chunked encoding)
- Modify: `src/turbo_memory_mcp/knowledge_lint.py:79` (use `encode_texts`)
- Modify: `src/turbo_memory_mcp/telemetry.py` (usage-stats lock)
- Test: `tests/test_scope_locks.py` (new), `tests/test_retrieval_index.py`

**Interfaces:**
- Produces, in `locks.py`:
  - `GLOBAL_LOCK_KEY = "global"`
  - `class DispatchBusy(RuntimeError)`. The message keeps the existing form: `"memory server busy: '<holder>' holds the dispatch lock (waited <t>s); retry this call later"`.
  - `class ScopeLocks` with:
    - `lock_for(key: str) -> threading.RLock`, created lazily under an internal `threading.Lock`.
    - `holder(key: str) -> str | None`
    - `@contextmanager hold(keys: Sequence[str], *, timeout: float, tool: str) -> Iterator[None]`. It acquires in order, with `timeout <= 0` meaning unbounded. On a timeout it releases the keys it already acquired and raises `DispatchBusy` naming the holder of the key that timed out. It raises `ValueError` if `GLOBAL_LOCK_KEY` appears anywhere but last, or if more than one non-global key is given. On release it restores the previous holder of each key.
  - `DEFAULT_SCOPE_LOCKS = ScopeLocks()`
- Produces, in `server.py`:
  - `GLOBAL_SCOPE_TOOLS = frozenset({"promote_note", "self_test"})`
  - `lock_keys_for(tool: str, kwargs: Mapping[str, Any], project_id: str) -> list[str]`. It returns `[project_id]`, plus `GLOBAL_LOCK_KEY` when the tool is in `GLOBAL_SCOPE_TOOLS` or `str(kwargs.get("scope", "")).strip().lower()` is `"global"` or `"hybrid"`.
  - `make_local_dispatcher(*, dispatch_lock=None, scope_locks: ScopeLocks | None = None, dispatch_lock_timeout=None, default_cwd=None, default_environ=None)`:
    - With `dispatch_lock` given, it behaves exactly as today.
    - Otherwise it uses `scope_locks or DEFAULT_SCOPE_LOCKS`. It resolves `project_id` via `build_runtime_context(cwd=resolved_cwd, environ=resolved_environ)[0].project_id`. If that raises, it uses the key `"unresolved"` and lets the handler raise its own error.
- Produces, in `retrieval_index.py`:
  - `EMBED_CHUNK_SIZE = 64`
  - `encode_texts(texts: Sequence[str], *, embedder: TextEmbedder | None = None) -> list[list[float]]`. It encodes chunk by chunk, each chunk under a module `_EMBED_LOCK`. `build_default_embedder()` also loads under `_EMBED_LOCK`, so the model is never constructed twice. `RetrievalIndex._embed_texts` and the probe at ~L911 delegate to it.
- Produces, in `telemetry.py`: `_USAGE_STATS_LOCK = threading.Lock()` held across load → write in `record_semantic_search_usage` and `record_hydration_usage`.

- [ ] **Step 1: Write the failing tests** in `tests/test_scope_locks.py`:
  - Use `make_local_dispatcher(scope_locks=ScopeLocks(), dispatch_lock_timeout=0.2)`, a slow handler patched into `TOOL_HANDLERS` with the entered/release events pattern from `tests/test_daemon.py`, and per-call `_environ` dicts with distinct `TQMEMORY_PROJECT_ID` / `TQMEMORY_PROJECT_ROOT` values and a shared `TQMEMORY_HOME`.
  - `test_slow_call_in_one_project_does_not_block_another_project`: hold `"index_paths"` in project alpha. `"health"` in project beta returns `{"ok": True}` in under 1 s.
  - `test_second_call_in_same_project_gets_busy_naming_holder`: `"health"` in alpha raises `DispatchBusy` matching `"'index_paths' holds the dispatch lock"`.
  - `test_global_scope_call_waits_only_for_global_holder`:
    - Hold `"semantic_search"` with `{"scope": "global"}` in alpha.
    - `"semantic_search"` with `{"scope": "project"}` in beta returns.
    - `{"scope": "hybrid"}` in beta raises `DispatchBusy`.
  - `test_hold_releases_project_lock_when_global_times_out`: another thread holds `GLOBAL_LOCK_KEY`, and `hold(["p1", GLOBAL_LOCK_KEY], timeout=0.1, tool="x")` raises `DispatchBusy`. Then `lock_for("p1").acquire(timeout=0.5)` from a third thread returns `True`.
  - `test_hold_rejects_global_before_project_and_two_projects`: `hold([GLOBAL_LOCK_KEY, "p1"], ...)` and `hold(["p1", "p2"], ...)` raise `ValueError`.
  - `test_unresolvable_project_still_reaches_the_handler`: with `_cwd` set to a nonexistent path, the handler's own exception type comes back, not `DispatchBusy`.
  - `test_embed_texts_encodes_in_chunks_under_lock` (`tests/test_retrieval_index.py`): a fake embedder records batch sizes and asserts `_EMBED_LOCK.locked()` inside `encode`. Calling `encode_texts` on 150 texts yields batches `[64, 64, 22]`, with the vectors in input order.
  - `test_concurrent_usage_records_are_not_lost`: 8 threads × 25 calls of `record_semantic_search_usage` across two stores that share one storage root. Afterwards `load_usage_stats(store)["totals"]["search_calls"] == 200`.
- [ ] **Step 2: Run them and confirm they fail.** Expected: `ImportError` for `turbo_memory_mcp.locks`, then busy and blocking failures.
- [ ] **Step 3: Implement `locks.py`.** No other module edits in this step.
- [ ] **Step 4: Implement the dispatcher, the call sites, `encode_texts` and the telemetry lock** as specified in Interfaces.
  - `ProxyRuntime` and `_run_primary` pass `scope_locks=DEFAULT_SCOPE_LOCKS`.
  - The legacy single-lock path is unchanged.
- [ ] **Step 5: Run the new tests, `tests/test_daemon.py`, `tests/test_workspace_server.py`, the full suite, ruff and mypy.** Expected: pass.
- [ ] **Step 6: Add a CHANGELOG entry** (`### Changed`): calls for different projects no longer wait on each other. Global and hybrid calls also take a global-scope lock. The busy error names the blocking tool per project.
- [ ] **Step 7: Commit.** `git commit -m "feat(daemon): one dispatch lock per project plus a global-scope lock"`

### Task 4: Full mirror sync streams in chunks

**Files:**
- Modify: `src/turbo_memory_mcp/retrieval_index.py` (`sync_project`, `sync_global`, `_build_markdown_rows`, `_merge_scope_rows`, `delete_items`)
- Test: `tests/test_retrieval_index.py`

**Interfaces:**
- Consumes: `encode_texts` (Task 3), `MemoryStore.iter_markdown_blocks` (Task 2).
- Produces:
  - `SYNC_CHUNK_ROWS = 256` and `DELETE_CHUNK_IDS = 500`.
  - `sync_project()` and `sync_global()` take rows lazily (blocks via `iter_markdown_blocks`, then notes) and process `SYNC_CHUNK_ROWS` at a time. For each chunk they embed it, then create the table if missing or `merge_insert` update/insert without deleting by source. Afterwards they delete `existing_ids - seen_ids` with `delete_items`, write the scope manifest once, and return the rows **without** the `"vector"` key. An empty corpus still resets the table to the empty schema.
  - `delete_items` issues one `IN (...)` delete per `DELETE_CHUNK_IDS` ids.
  - `upsert_rows` (incremental) keeps its behavior but also returns rows without vectors and merges in `SYNC_CHUNK_ROWS` chunks.

- [ ] **Step 1: Write the failing tests** (`tests/test_retrieval_index.py`):
  - `test_sync_project_merges_in_bounded_chunks`: write 600 blocks and spy on `table.merge_insert` / `create_table` row counts (or patch `RetrievalIndex._write_chunk`). Every chunk is at most 256 rows, `count_rows("project") == 600`, and the returned rows have no `"vector"` key.
  - `test_sync_project_removes_rows_missing_from_the_store`: sync, delete 3 block files, sync again. `count_rows` drops by 3, and those ids are absent from `existing_item_updated_at`.
  - `test_delete_items_batches_large_id_lists`: delete 1,200 ids and assert `table.delete` is called 3 times.
  - The existing `sync_project` and `sync_global` tests keep passing unchanged.
- [ ] **Step 2: Run them and confirm they fail, implement, and confirm they pass.** Then run the full suite, ruff and mypy.
- [ ] **Step 3: Add a CHANGELOG entry** (`### Changed`): a full index rebuild keeps at most 256 rows and their vectors in memory at a time, instead of the whole project.
- [ ] **Step 4: Commit.** `git commit -m "perf(index): stream full mirror syncs in bounded chunks"`

### Task 5: Bounded search-time refresh with a background worker

**Files:**
- Modify: `src/turbo_memory_mcp/ingestion.py` (`index_paths_with_sync_plan` budget)
- Modify: `src/turbo_memory_mcp/contracts.py` (`build_indexing_payload` gains `remaining_files: int = 0`)
- Create: `src/turbo_memory_mcp/refresher.py`
- Modify: `src/turbo_memory_mcp/server.py` (`_repair_project_retrieval_if_needed` budget, `_apply_project_index_sync_plan`, `_refresh_project_indexes_if_needed`, `semantic_search_impl`, `hydrate_impl`, primary shutdown calls `DEFAULT_REFRESHER.shutdown()`)
- Modify: `src/turbo_memory_mcp/skills/turbo-quant-memory/SKILL.md` (one line on `index_refresh`)
- Test: `tests/test_ingestion_tools.py`, `tests/test_index_drift_repair.py`, `tests/test_refresher.py` (new)

**Interfaces:**
- Consumes: `ScopeLocks.hold`, `DEFAULT_SCOPE_LOCKS` (Task 3); `list_markdown_block_ids` (Task 2).
- Produces, in `ingestion.py`: `index_paths_with_sync_plan(store, paths=None, *, mode="incremental", cwd=None, max_changed_files: int | None = None)`.
  - Files that need parsing beyond the budget are still added to `seen_source_paths`, so they are never treated as deleted. They are skipped and counted in the payload's `remaining_files`.
  - Touched-only files (manifest rewrite) and deletions are not budgeted.
  - `mode="full"` with a budget raises `ValueError`.
- Produces, in `server.py`:
  - `_repair_project_retrieval_if_needed(store, index, *, max_items: int | None = None) -> int`. It returns the count of missing or stale rows left. Stale deletions are never budgeted; embedding takes notes first, then blocks, in sorted order.
  - `@dataclass(frozen=True) class RefreshProgress: remaining_files: int; remaining_items: int`, with property `pending: bool`.
  - `refresh_project_batch(store: MemoryStore, *, max_files: int | None, max_items: int | None) -> RefreshProgress`. It is the budgeted form of `_refresh_project_indexes_if_needed`; the markdown and retrieval format-change rebuilds stay unbounded. `_apply_project_index_sync_plan` no longer calls `sync_project()` when the mirror is empty; it upserts the plan and relies on the budgeted repair.
  - `INLINE_REFRESH_FILES = 25`, `INLINE_REFRESH_ITEMS = 500`, overridable by `TQMEMORY_INLINE_REFRESH_FILES` and `TQMEMORY_INLINE_REFRESH_ITEMS`, where unparseable values fall back to the defaults.
  - Search and hydrate payloads gain `"index_refresh": {"state": "refreshing", "pending_files": int, "pending_items": int}`, present only while work is pending.
- Produces, in `refresher.py`:
  - `class IndexRefresher(scope_locks: ScopeLocks, *, batch_files: int = 50, batch_items: int = 1000)` with:
    - `schedule(project_id: str, *, cwd: str | None, environ: Mapping[str, str] | None) -> None`, idempotent per project, which starts one daemon worker thread lazily.
    - `is_active(project_id: str) -> bool`
    - `status(project_id: str) -> dict[str, Any] | None`
    - `wait_idle(timeout: float) -> bool`
    - `shutdown(timeout: float = 2.0) -> None`
  - Each batch runs `refresh_project_batch` inside `scope_locks.hold([project_id], timeout=0, tool="background_refresh")`. A batch error is logged and ends that job.
  - `DEFAULT_REFRESHER = IndexRefresher(DEFAULT_SCOPE_LOCKS)`.

- [ ] **Step 1: Write the failing ingestion tests** (`tests/test_ingestion_tools.py`):
  - `test_budgeted_index_never_treats_unprocessed_files_as_deleted`: 10 docs indexed, all 10 edited, `max_changed_files=2`. Assert:
    - `deleted_files == 0` and `changed_files == 2`
    - `remaining_files == 8`
    - all 10 manifests still exist
  - `test_budgeted_index_converges_over_repeated_batches`: call repeatedly until `remaining_files == 0`; at most 5 calls, and every block contains the edited text.
  - `test_full_mode_rejects_a_file_budget`: `pytest.raises(ValueError)`.
- [ ] **Step 2: Run them and confirm they fail, implement the budget, and confirm they pass.**
- [ ] **Step 3: Write the failing repair and refresh tests.**
  - `test_repair_embeds_at_most_the_item_budget` (`tests/test_index_drift_repair.py`): write 30 notes, then delete their rows from the mirror. `_repair_project_retrieval_if_needed(store, index, max_items=10)` returns `20`, and the mirror holds 10 of them.
  - In `tests/test_refresher.py`, each test calls `DEFAULT_REFRESHER.wait_idle(30)` in teardown.
  - `test_large_change_search_reports_refresh_then_converges`: 30 new docs, environ `TQMEMORY_INLINE_REFRESH_FILES=5`.
    - The first `semantic_search_impl` payload has `index_refresh.state == "refreshing"` and `pending_files == 25`.
    - After `wait_idle(30)`, all 30 manifests exist and the next search has no `index_refresh` key.
  - `test_search_skips_inline_refresh_while_background_job_runs`: with `is_active` patched to `True`, `index_paths_with_sync_plan` patched to raise `AssertionError`, and `status` patched to a fixed dict, the search returns that dict as `index_refresh`.
  - `test_worker_takes_only_the_project_lock`: spy on `ScopeLocks.hold`; every worker call has `keys == [project_id]`.
  - `test_background_refresh_picks_up_files_edited_mid_job`: `batch_files=2` and 6 docs; edit doc 1 after the first batch via a hook on `refresh_project_batch`. After `wait_idle`, doc 1's block holds the new text.
  - `test_refresher_shutdown_returns_while_a_job_is_pending`: the batch function blocks on an event; `shutdown(timeout=1.0)` returns in under 2 s.
  - `test_refresher_starts_no_thread_until_scheduled`: a fresh `IndexRefresher` has no thread named `tqmemory-index-refresher` until `schedule` is called.
  - `test_refresher_ends_job_after_batch_error`: the batch raises `RuntimeError`, then `wait_idle(5)` is `True` and `is_active(pid)` is `False`.
- [ ] **Step 4: Run them and confirm they fail, then implement** `refresher.py` and the `server.py` changes. In `semantic_search_impl` and `hydrate_impl`:
  - If `DEFAULT_REFRESHER.is_active(project_id)`, attach its `status` and skip the inline refresh.
  - Otherwise run `refresh_project_batch` with the inline budgets. If `progress.pending`, call `schedule` and attach the status.
- [ ] **Step 5: Run the new tests, the full suite, ruff and mypy.** Existing search-time refresh tests must still pass unchanged, because their projects are under the inline budget.
- [ ] **Step 6: Update the docs.**
  - SKILL.md, one line in the search section: when a result carries `index_refresh`, the index is still catching up; results can miss very recent doc edits, and repeating the search later is enough.
  - CHANGELOG (`### Changed`): a search now does at most 25 files and 500 rows of refresh work inline and finishes the rest in the background, reporting `index_refresh`.
- [ ] **Step 7: Commit.** `git commit -m "feat(search): bounded search-time refresh with a background worker"`

### Task 6: Release 0.32.0 and install (authorized)

- [ ] Get a fresh whole-branch review and fix every confirmed finding.
- [ ] Follow the release checklist in tqmemory note `973b494c4b9d4926`:
  - Bump to 0.32.0 in `pyproject.toml`, `server.json` (both fields), `__init__.py` `_FALLBACK_VERSION` and SKILL.md (frontmatter and pinned install).
  - Run `uv lock`.
  - Rename `[Unreleased]` to `[0.32.0] - <date>`.
  - Run the full verification.
  - Commit `chore(release): 0.32.0` and add the annotated tag `v0.32.0`.
  - Push `main` and the tag.
  - Run `gh release create` with the CHANGELOG section plus the Upgrade block.
- [ ] Install with `uv tool upgrade turbo-quant-memory` and `turbo-memory-mcp skill install`.
- [ ] Restart every running `turbo-memory-mcp serve` process, then tell the user to `/mcp` reconnect.
