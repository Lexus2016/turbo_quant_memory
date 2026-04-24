"""Phase 4 stdio MCP server for Turbo Quant Memory."""

from __future__ import annotations

import json
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover - compatibility for current stable SDK
    from mcp.server.fastmcp import FastMCP as MCPServer

from .contracts import (
    DEFAULT_QUERY_MODE,
    PRODUCT_NAME,
    SERVER_ID,
    build_health_payload,
    build_note_write_payload,
    build_scope_payload,
    build_self_test_payload,
    build_server_info_payload,
)
from .daemon import (
    BootstrapResult,
    DaemonClient,
    DaemonListener,
    PrimaryUnreachable,
    acquire_daemon_role,
    release_daemon_lock,
)
from .hydration import hydrate
from .identity import (
    ENV_PROJECT_ID,
    ENV_PROJECT_NAME,
    ENV_PROJECT_ROOT,
    ProjectIdentity,
    resolve_project_identity,
)
from .ingestion import assess_project_index_freshness, index_paths_with_sync_plan
from .knowledge_lint import lint_knowledge_base
from .retrieval import semantic_search
from .retrieval_index import RetrievalIndex
from .store import (
    ENV_STORAGE_HOME,
    GLOBAL_SCOPE,
    MARKDOWN_FORMAT_VERSION,
    MARKDOWN_SOURCE_KIND,
    MemoryStore,
    NOTE_KINDS,
    NOTE_SOURCE_KIND,
    PROJECT_SCOPE,
    RETRIEVAL_FORMAT_VERSION,
    resolve_storage_root,
)
from .telemetry import build_usage_snapshot, record_hydration_usage, record_semantic_search_usage


Dispatcher = Callable[[str, Mapping[str, Any]], Any]

# Environment variables forwarded from proxy -> primary so the primary resolves
# the proxy's project identity (not its own cwd) for cwd-aware tools.
_FORWARDED_ENV_KEYS: tuple[str, ...] = (
    ENV_PROJECT_ROOT,
    ENV_PROJECT_ID,
    ENV_PROJECT_NAME,
    ENV_STORAGE_HOME,
)


def build_server(dispatcher: Dispatcher) -> MCPServer:
    mcp = MCPServer(
        SERVER_ID,
        instructions=(
            "Use remember_note(..., kind=..., scope=\"project\") to store typed project notes, "
            "promote reusable knowledge into global scope, deprecate stale notes without deleting "
            "history, retrieve compact "
            "project/global/hybrid memory, hydrate fuller local context through "
            "hydrate(...), index Markdown roots through index_paths(...), and run "
            "lint_knowledge_base(...) to detect broken links, orphans, and duplicate titles. "
            "To exclude directories or files from indexing, create a .tqmemoryignore file "
            "in the project root with glob patterns (one per line, # for comments). "
            "Example patterns: 'workspace-*' skips dirs matching the glob, "
            "'data/reports/*.md' skips specific paths."
        ),
        json_response=True,
        log_level="ERROR",
    )

    @mcp.tool()
    def health() -> dict[str, object]:
        return dispatcher("health", {})

    @mcp.tool()
    def server_info() -> dict[str, object]:
        return dispatcher("server_info", {})

    @mcp.tool()
    def list_scopes() -> dict[str, object]:
        return dispatcher("list_scopes", {})

    @mcp.tool()
    def self_test() -> dict[str, object]:
        return dispatcher("self_test", {})

    @mcp.tool()
    def remember_note(
        title: str,
        content: str,
        kind: str,
        tags: list[str] | None = None,
        source_refs: list[str] | None = None,
        scope: str = "project",
    ) -> dict[str, object]:
        return dispatcher(
            "remember_note",
            {
                "title": title,
                "content": content,
                "kind": kind,
                "tags": tags,
                "source_refs": source_refs,
                "scope": scope,
            },
        )

    @mcp.tool()
    def promote_note(note_id: str) -> dict[str, object]:
        return dispatcher("promote_note", {"note_id": note_id})

    @mcp.tool()
    def deprecate_note(
        note_id: str,
        scope: str = "project",
        replacement_note_id: str | None = None,
        replacement_scope: str | None = None,
        reason: str | None = None,
    ) -> dict[str, object]:
        return dispatcher(
            "deprecate_note",
            {
                "note_id": note_id,
                "scope": scope,
                "replacement_note_id": replacement_note_id,
                "replacement_scope": replacement_scope,
                "reason": reason,
            },
        )

    @mcp.tool()
    def semantic_search(
        query: str,
        scope: str = DEFAULT_QUERY_MODE,
        limit: int = 5,
    ) -> dict[str, object]:
        return dispatcher(
            "semantic_search",
            {"query": query, "scope": scope, "limit": limit},
        )

    @mcp.tool()
    def hydrate(
        item_id: str,
        scope: str,
        mode: str = "default",
    ) -> dict[str, object]:
        return dispatcher(
            "hydrate",
            {"item_id": item_id, "scope": scope, "mode": mode},
        )

    @mcp.tool()
    def index_paths(
        paths: list[str] | None = None,
        mode: str = "incremental",
    ) -> dict[str, object]:
        """Register and index Markdown directories into project memory.

        Supports .tqmemoryignore files (placed in project root or any indexed
        directory) with glob patterns to exclude paths from indexing.
        One pattern per line, # for comments.  Example patterns:
        ``workspace-*`` skips any directory matching the glob;
        ``data/reports/*.md`` skips files matching a path pattern.
        """
        return dispatcher("index_paths", {"paths": paths, "mode": mode})

    @mcp.tool()
    def lint_knowledge_base(
        paths: list[str] | None = None,
        max_issues: int = 200,
    ) -> dict[str, object]:
        return dispatcher(
            "lint_knowledge_base",
            {"paths": paths, "max_issues": max_issues},
        )

    return mcp


# ---------------------------------------------------------------------------
# Tool dispatch (local / proxy)
# ---------------------------------------------------------------------------


def _tool_health(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return build_health_payload()


def _tool_server_info(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return server_info_impl(cwd=cwd, environ=environ)


def _tool_list_scopes(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return build_scope_payload()


def _tool_self_test(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return self_test_impl(cwd=cwd, environ=environ)


def _tool_remember_note(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return remember_note_impl(
        str(kwargs["title"]),
        str(kwargs["content"]),
        kind=str(kwargs["kind"]),
        tags=kwargs.get("tags"),
        source_refs=kwargs.get("source_refs"),
        scope=str(kwargs.get("scope", "project")),
        cwd=cwd,
        environ=environ,
    )


def _tool_promote_note(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return promote_note_impl(str(kwargs["note_id"]), cwd=cwd, environ=environ)


def _tool_deprecate_note(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return deprecate_note_impl(
        str(kwargs["note_id"]),
        scope=str(kwargs.get("scope", "project")),
        replacement_note_id=kwargs.get("replacement_note_id"),
        replacement_scope=kwargs.get("replacement_scope"),
        reason=kwargs.get("reason"),
        cwd=cwd,
        environ=environ,
    )


def _tool_semantic_search(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return semantic_search_impl(
        str(kwargs["query"]),
        scope=str(kwargs.get("scope", DEFAULT_QUERY_MODE)),
        limit=int(kwargs.get("limit", 5)),
        cwd=cwd,
        environ=environ,
    )


def _tool_hydrate(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return hydrate_impl(
        str(kwargs["item_id"]),
        scope=str(kwargs["scope"]),
        mode=str(kwargs.get("mode", "default")),
        cwd=cwd,
        environ=environ,
    )


def _tool_index_paths(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return index_paths_impl(
        paths=kwargs.get("paths"),
        mode=str(kwargs.get("mode", "incremental")),
        cwd=cwd,
        environ=environ,
    )


def _tool_lint_knowledge_base(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return lint_knowledge_base_impl(
        paths=kwargs.get("paths"),
        max_issues=int(kwargs.get("max_issues", 200)),
        cwd=cwd,
        environ=environ,
    )


TOOL_HANDLERS: dict[str, Callable[..., Any]] = {
    "health": _tool_health,
    "server_info": _tool_server_info,
    "list_scopes": _tool_list_scopes,
    "self_test": _tool_self_test,
    "remember_note": _tool_remember_note,
    "promote_note": _tool_promote_note,
    "deprecate_note": _tool_deprecate_note,
    "semantic_search": _tool_semantic_search,
    "hydrate": _tool_hydrate,
    "index_paths": _tool_index_paths,
    "lint_knowledge_base": _tool_lint_knowledge_base,
}


_DEFAULT_LOCAL_LOCK = threading.RLock()


def make_local_dispatcher(
    *,
    dispatch_lock: threading.RLock | None = None,
    default_cwd: Path | str | None = None,
    default_environ: Mapping[str, str] | None = None,
) -> Dispatcher:
    """Return a dispatcher that runs tools in-process.

    The lock is shared between the primary's stdio handler and its daemon
    listener workers so MemoryStore / LanceDB access remains single-writer.
    """

    lock = dispatch_lock or _DEFAULT_LOCAL_LOCK

    def _dispatch(tool: str, kwargs: Mapping[str, Any]) -> Any:
        handler = TOOL_HANDLERS.get(tool)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool}")
        merged_kwargs = dict(kwargs)
        cwd_override = merged_kwargs.pop("_cwd", None)
        environ_override = merged_kwargs.pop("_environ", None)
        resolved_cwd = cwd_override if cwd_override is not None else default_cwd
        if environ_override:
            resolved_environ = {**os.environ, **{str(k): str(v) for k, v in environ_override.items()}}
        else:
            resolved_environ = default_environ
        with lock:
            return handler(merged_kwargs, cwd=resolved_cwd, environ=resolved_environ)

    return _dispatch


def make_proxy_dispatcher(client: DaemonClient) -> Dispatcher:
    """Return a dispatcher that forwards every call to the primary via RPC."""

    def _collect_env() -> dict[str, str]:
        collected: dict[str, str] = {}
        for key in _FORWARDED_ENV_KEYS:
            value = os.environ.get(key)
            if value is not None:
                collected[key] = value
        return collected

    def _dispatch(tool: str, kwargs: Mapping[str, Any]) -> Any:
        payload = dict(kwargs)
        payload["_cwd"] = str(Path(os.getcwd()).resolve())
        payload["_environ"] = _collect_env()
        return client.call(tool, payload)

    return _dispatch


class ProxyRuntime:
    """Dispatcher-shaped wrapper around a DaemonClient with auto-failover.

    When the primary process dies, all proxy processes detect the loss via
    :class:`PrimaryUnreachable` (raised by :meth:`DaemonClient.call` when the
    connect/send phase fails — i.e. the RPC provably never reached the primary
    and is safe to replay).

    On detection, the runtime re-runs :func:`acquire_daemon_role`:

    * If this process wins the promotion race (O_EXCL claim on the lockfile),
      it starts its own :class:`DaemonListener` and swaps the active dispatcher
      to an in-process local dispatcher. Subsequent calls execute directly,
      and other orphaned proxies can now connect to us as the new primary.
    * If another process already promoted, we replace our client with a fresh
      one bound to the new primary's endpoint.
    * Otherwise we fall back to standalone local dispatch (same as
      ``TQMEMORY_DAEMON_DISABLE``).

    A mid-call ``ConnectionError`` (send succeeded but recv failed) is NOT
    recovered from — retrying risks duplicating non-idempotent tools like
    ``remember_note``. Those errors surface to the MCP host.
    """

    def __init__(self, initial_client: DaemonClient) -> None:
        self._state_lock = threading.RLock()
        self._dispatch_lock = threading.RLock()
        self._client: DaemonClient | None = initial_client
        self._active_dispatcher: Dispatcher = make_proxy_dispatcher(initial_client)
        self._listener: DaemonListener | None = None
        self._endpoint: Any = None

    def __call__(self, tool: str, kwargs: Mapping[str, Any]) -> Any:
        with self._state_lock:
            active = self._active_dispatcher
        try:
            return active(tool, kwargs)
        except PrimaryUnreachable:
            self._failover()
            with self._state_lock:
                active = self._active_dispatcher
            return active(tool, kwargs)

    @property
    def is_primary(self) -> bool:
        with self._state_lock:
            return self._listener is not None

    def _failover(self) -> None:
        """Re-bootstrap after the primary died. Idempotent under concurrent calls."""

        with self._state_lock:
            if self._listener is not None:
                # Already promoted ourselves; nothing to do.
                return

            dead_client = self._client
            self._client = None

        if dead_client is not None:
            try:
                dead_client.close()
            except Exception:
                pass

        bootstrap = acquire_daemon_role()

        with self._state_lock:
            if self._listener is not None:
                # Another call promoted us between the unlocked bootstrap and
                # now. Discard this bootstrap's resources to avoid double-claim.
                if bootstrap.client is not None:
                    try:
                        bootstrap.client.close()
                    except Exception:
                        pass
                if bootstrap.role == "primary" and bootstrap.endpoint is not None:
                    try:
                        release_daemon_lock(bootstrap.endpoint)
                    except Exception:
                        pass
                return

            if bootstrap.role == "primary" and bootstrap.endpoint is not None:
                local_dispatcher = make_local_dispatcher(
                    dispatch_lock=self._dispatch_lock,
                )

                def _listener_handler(tool: str, kwargs: Mapping[str, Any]) -> Any:
                    return local_dispatcher(tool, kwargs)

                listener = DaemonListener(
                    bootstrap.endpoint,
                    _listener_handler,
                    dispatch_lock=self._dispatch_lock,
                )
                listener.start()
                self._listener = listener
                self._endpoint = bootstrap.endpoint
                self._active_dispatcher = local_dispatcher
            elif bootstrap.role == "proxy" and bootstrap.client is not None:
                self._client = bootstrap.client
                self._active_dispatcher = make_proxy_dispatcher(bootstrap.client)
            else:
                # Standalone fallback (retries exhausted or daemon disabled).
                self._active_dispatcher = make_local_dispatcher(
                    dispatch_lock=self._dispatch_lock,
                )

    def shutdown(self) -> None:
        """Release network/file resources. Safe to call multiple times."""

        with self._state_lock:
            listener = self._listener
            endpoint = self._endpoint
            client = self._client
            self._listener = None
            self._endpoint = None
            self._client = None

        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        if endpoint is not None:
            try:
                release_daemon_lock(endpoint)
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def run_stdio_server() -> None:
    """Entry point used by the CLI. Handles daemon bootstrap transparently."""

    bootstrap = acquire_daemon_role()
    if bootstrap.role == "primary" and bootstrap.endpoint is not None:
        _run_primary(bootstrap)
    elif bootstrap.role == "proxy" and bootstrap.client is not None:
        _run_proxy(bootstrap)
    else:
        _run_standalone()


def _run_primary(bootstrap: BootstrapResult) -> None:
    endpoint = bootstrap.endpoint
    assert endpoint is not None  # guarded by run_stdio_server
    dispatch_lock = threading.RLock()
    dispatcher = make_local_dispatcher(dispatch_lock=dispatch_lock)

    def _listener_handler(tool: str, kwargs: Mapping[str, Any]) -> Any:
        # Listener thread uses same dispatcher, which holds the shared lock.
        return dispatcher(tool, kwargs)

    listener = DaemonListener(endpoint, _listener_handler, dispatch_lock=dispatch_lock)
    listener.start()
    try:
        build_server(dispatcher).run(transport="stdio")
    finally:
        listener.stop()
        release_daemon_lock(endpoint)


def _run_proxy(bootstrap: BootstrapResult) -> None:
    client = bootstrap.client
    assert client is not None
    runtime = ProxyRuntime(client)
    try:
        build_server(runtime).run(transport="stdio")
    finally:
        runtime.shutdown()


def _run_standalone() -> None:
    dispatcher = make_local_dispatcher()
    build_server(dispatcher).run(transport="stdio")


# ---------------------------------------------------------------------------
# Tool implementations (unchanged)
# ---------------------------------------------------------------------------


def server_info_impl(
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    project, store = build_runtime_context(cwd=cwd, environ=environ)
    storage_stats = collect_storage_stats(store)
    index_status = collect_index_status(store, storage_stats=storage_stats)
    usage_stats = build_usage_snapshot(
        store,
        project_id=project.project_id,
        project_name=project.project_name,
        environ=environ,
    )
    return build_server_info_payload(
        storage_root=str(store.storage_root),
        current_project=build_current_project_payload(project),
        storage_stats=storage_stats,
        index_status=index_status,
        usage_stats=usage_stats,
    )


def self_test_impl(
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    project, store = build_runtime_context(cwd=cwd, environ=environ)
    return build_self_test_payload(
        storage_root=str(store.storage_root),
        current_project=build_current_project_payload(project),
    )


def remember_note_impl(
    title: str,
    content: str,
    *,
    kind: str,
    tags: list[str] | None = None,
    source_refs: list[str] | None = None,
    scope: str = "project",
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    resolved_scope = _normalize_scope(scope)
    if resolved_scope == GLOBAL_SCOPE:
        raise ValueError("Direct global writes are disabled; write to project scope and use promote_note.")
    if resolved_scope != PROJECT_SCOPE:
        raise ValueError(f"remember_note only supports scope='{PROJECT_SCOPE}' in this server.")
    if not title.strip():
        raise ValueError("remember_note requires a non-empty title.")
    if not content.strip():
        raise ValueError("remember_note requires non-empty content.")
    resolved_kind = kind.strip().lower()
    if resolved_kind not in NOTE_KINDS:
        supported = ", ".join(NOTE_KINDS)
        raise ValueError(f"remember_note requires kind in: {supported}.")

    _, store = build_runtime_context(cwd=cwd, environ=environ)
    note = store.write_project_note(title, content, note_kind=resolved_kind, tags=tags, source_refs=source_refs)
    warning = _sync_with_warning(lambda: _sync_project_note_change(store, str(note["note_id"])))
    return build_note_write_payload(
        note,
        source_path=str(store.note_source_path(note)),
        action="stored",
        content_preview=build_content_preview(note["content"]),
        warning=warning,
    )


def promote_note_impl(
    note_id: str,
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    resolved_note_id = note_id.strip()
    if not resolved_note_id:
        raise ValueError("promote_note requires a non-empty note_id.")

    _, store = build_runtime_context(cwd=cwd, environ=environ)
    note = store.promote_note(resolved_note_id)
    warning = _sync_with_warning(lambda: _sync_global_note_change(store, str(note["note_id"])))
    return build_note_write_payload(
        note,
        source_path=str(store.note_source_path(note)),
        action="promoted",
        content_preview=build_content_preview(note["content"]),
        warning=warning,
    )


def deprecate_note_impl(
    note_id: str,
    *,
    scope: str = PROJECT_SCOPE,
    replacement_note_id: str | None = None,
    replacement_scope: str | None = None,
    reason: str | None = None,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    resolved_note_id = note_id.strip()
    if not resolved_note_id:
        raise ValueError("deprecate_note requires a non-empty note_id.")

    resolved_scope = _normalize_scope(scope)
    if resolved_scope not in {PROJECT_SCOPE, GLOBAL_SCOPE}:
        raise ValueError(f"deprecate_note only supports scope='{PROJECT_SCOPE}' or scope='{GLOBAL_SCOPE}'.")

    resolved_replacement_scope = None
    if replacement_scope is not None:
        resolved_replacement_scope = _normalize_scope(replacement_scope)
        if resolved_replacement_scope not in {PROJECT_SCOPE, GLOBAL_SCOPE}:
            raise ValueError("replacement_scope must be 'project' or 'global'.")

    _, store = build_runtime_context(cwd=cwd, environ=environ)
    note = store.deprecate_note(
        resolved_note_id,
        scope=resolved_scope,
        replacement_note_id=replacement_note_id,
        replacement_scope=resolved_replacement_scope,
        reason=reason,
    )
    warning = _sync_with_warning(lambda: _remove_retrieval_note(store, resolved_scope, resolved_note_id))

    action = "superseded" if note["note_status"] == "superseded" else "archived"
    return build_note_write_payload(
        note,
        source_path=str(store.note_source_path(note)),
        action=action,
        content_preview=build_content_preview(note["content"]),
        warning=warning,
    )


def semantic_search_impl(
    query: str,
    *,
    scope: str = DEFAULT_QUERY_MODE,
    limit: int = 5,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    resolved_scope = scope.strip().lower()
    if resolved_scope in {PROJECT_SCOPE, "hybrid"}:
        _refresh_project_indexes_if_needed(store)
    if resolved_scope in {GLOBAL_SCOPE, "hybrid"}:
        _refresh_global_retrieval_if_needed(store)
    payload = semantic_search(store, query, scope=scope, limit=limit)
    milestone = record_semantic_search_usage(
        store,
        project_id=store.project.project_id,
        project_name=store.project.project_name,
        response_payload=payload,
        raw_source_bytes=_sum_raw_source_bytes(store, payload.get("items", [])),
        environ=environ,
    )
    if milestone is not None:
        payload["impact_milestone"] = milestone
    return payload


def hydrate_impl(
    item_id: str,
    *,
    scope: str,
    mode: str = "default",
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    if scope.strip().lower() == PROJECT_SCOPE:
        _refresh_project_indexes_if_needed(store)
    payload = hydrate(store, item_id, scope=scope, mode=mode)
    record_hydration_usage(
        store,
        project_id=store.project.project_id,
        project_name=store.project.project_name,
        response_payload=payload,
    )
    return payload


def index_paths_impl(
    paths: list[str] | None = None,
    *,
    mode: str = "incremental",
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    runtime_cwd = Path(cwd).expanduser().resolve() if cwd is not None else store.project.project_root
    payload, sync_plan = index_paths_with_sync_plan(store, paths=paths, mode=mode, cwd=runtime_cwd)
    _apply_project_index_sync_plan(store, sync_plan)
    return payload


def lint_knowledge_base_impl(
    paths: list[str] | None = None,
    *,
    max_issues: int = 200,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    runtime_cwd = Path(cwd).expanduser().resolve() if cwd is not None else store.project.project_root
    return lint_knowledge_base(store, paths=paths, max_issues=max_issues, cwd=runtime_cwd)


def build_runtime_context(
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[ProjectIdentity, MemoryStore]:
    project = resolve_project_identity(cwd=cwd, environ=environ)
    store = MemoryStore(project, storage_root=resolve_storage_root(environ))
    return project, store


def build_current_project_payload(project: ProjectIdentity) -> dict[str, object]:
    return {
        "project_id": project.project_id,
        "project_name": project.project_name,
        "project_root": str(project.project_root),
        "identity_kind": project.identity_kind,
    }


def collect_storage_stats(store: MemoryStore) -> dict[str, object]:
    snapshot = _cached_storage_snapshot(store)
    retrieval_index = RetrievalIndex(store)

    return {
        "project": {
            "note_count": snapshot["project_note_count"],
            "total_note_count": snapshot["project_total_note_count"],
            "inactive_note_count": snapshot["project_inactive_note_count"],
            "archived_note_count": snapshot["project_archived_note_count"],
            "superseded_note_count": snapshot["project_superseded_note_count"],
            "markdown_root_count": snapshot["markdown_root_count"],
            "markdown_file_count": snapshot["markdown_file_count"],
            "markdown_block_count": snapshot["markdown_block_count"],
            "retrieval_row_count": retrieval_index.count_rows(PROJECT_SCOPE),
        },
        "global": {
            "note_count": snapshot["global_note_count"],
            "total_note_count": snapshot["global_total_note_count"],
            "inactive_note_count": snapshot["global_inactive_note_count"],
            "archived_note_count": snapshot["global_archived_note_count"],
            "superseded_note_count": snapshot["global_superseded_note_count"],
            "retrieval_row_count": retrieval_index.count_rows(GLOBAL_SCOPE),
        },
    }


def collect_index_status(
    store: MemoryStore,
    *,
    storage_stats: Mapping[str, object] | None = None,
) -> dict[str, object]:
    stats = dict(storage_stats or collect_storage_stats(store))
    snapshot = _cached_storage_snapshot(store)
    project_stats = dict(stats["project"])
    global_stats = dict(stats["global"])
    freshness_report = assess_project_index_freshness(store, cwd=store.project.project_root)
    project_markdown_manifest = store.read_markdown_manifest()
    project_retrieval_manifest = store.read_project_retrieval_manifest()
    global_retrieval_manifest = store.read_global_retrieval_manifest()

    root_count = int(project_stats["markdown_root_count"])
    file_count = int(project_stats["markdown_file_count"])
    block_count = int(project_stats["markdown_block_count"])
    project_note_count = int(project_stats["note_count"])
    project_row_count = int(project_stats["retrieval_row_count"])
    global_note_count = int(global_stats["note_count"])
    global_row_count = int(global_stats["retrieval_row_count"])
    project_markdown_format_stale = bool(root_count) and (
        project_markdown_manifest is None
        or int(project_markdown_manifest.get("format_version", 0)) != MARKDOWN_FORMAT_VERSION
    )
    project_retrieval_format_stale = (block_count + project_note_count) > 0 and (
        project_retrieval_manifest is None
        or int(project_retrieval_manifest.get("format_version", 0)) != RETRIEVAL_FORMAT_VERSION
    )
    global_retrieval_format_stale = global_note_count > 0 and (
        global_retrieval_manifest is None
        or int(global_retrieval_manifest.get("format_version", 0)) != RETRIEVAL_FORMAT_VERSION
    )

    if root_count == 0 and file_count == 0 and block_count == 0 and project_note_count == 0:
        project_freshness = "empty"
    elif root_count > 0 and file_count == 0 and block_count == 0:
        project_freshness = "not_indexed"
    elif freshness_report["is_stale"] or project_markdown_format_stale or project_retrieval_format_stale:
        project_freshness = "stale"
    elif project_row_count == block_count + project_note_count:
        project_freshness = "fresh"
    else:
        project_freshness = "stale"

    if global_note_count == 0:
        global_freshness = "empty"
    elif not global_retrieval_format_stale and global_row_count == global_note_count:
        global_freshness = "fresh"
    else:
        global_freshness = "stale"

    return {
        "project": {
            "freshness": project_freshness,
            "last_indexed_at": snapshot["last_indexed_at"],
            "last_note_update": snapshot["project_last_note_update"],
            "markdown_format_version": int(project_markdown_manifest.get("format_version", 0))
            if project_markdown_manifest
            else 0,
            "retrieval_format_version": int(project_retrieval_manifest.get("format_version", 0))
            if project_retrieval_manifest
            else 0,
        },
        "global": {
            "freshness": global_freshness,
            "last_note_update": snapshot["global_last_note_update"],
            "retrieval_format_version": int(global_retrieval_manifest.get("format_version", 0))
            if global_retrieval_manifest
            else 0,
        },
    }


def build_content_preview(content: str, limit: int = 160) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _normalize_scope(scope: str) -> str:
    return scope.strip().lower()


def _sync_with_warning(action: Callable[[], None]) -> str | None:
    try:
        action()
    except Exception as exc:
        return f"Retrieval sync deferred: {exc}"
    return None


def _refresh_project_markdown_if_stale(store: MemoryStore) -> None:
    freshness = assess_project_index_freshness(store, cwd=store.project.project_root)
    if not freshness["is_stale"]:
        return

    try:
        _, sync_plan = index_paths_with_sync_plan(
            store,
            mode="incremental",
            cwd=store.project.project_root,
        )
    except FileNotFoundError:
        return
    _apply_project_index_sync_plan(store, sync_plan)


def _refresh_project_indexes_if_needed(store: MemoryStore) -> None:
    markdown_roots = store.list_markdown_roots()
    markdown_manifest = store.read_markdown_manifest()
    if markdown_roots and (
        markdown_manifest is None or int(markdown_manifest.get("format_version", 0)) != MARKDOWN_FORMAT_VERSION
    ):
        try:
            _, sync_plan = index_paths_with_sync_plan(
                store,
                mode="full",
                cwd=store.project.project_root,
            )
        except FileNotFoundError:
            pass
        else:
            _apply_project_index_sync_plan(store, sync_plan)
    else:
        _refresh_project_markdown_if_stale(store)

    if _project_retrieval_requires_rebuild(store):
        RetrievalIndex(store).sync_project()


def _refresh_global_retrieval_if_needed(store: MemoryStore) -> None:
    if _global_retrieval_requires_rebuild(store):
        RetrievalIndex(store).sync_global()


def _apply_project_index_sync_plan(store: MemoryStore, sync_plan: Mapping[str, object]) -> None:
    index = RetrievalIndex(store)
    upsert_block_ids = [str(item_id) for item_id in sync_plan.get("upsert_block_ids", [])]
    delete_block_ids = [str(item_id) for item_id in sync_plan.get("delete_block_ids", [])]

    try:
        if index.count_rows(PROJECT_SCOPE) == 0:
            if store.list_markdown_blocks() or store.list_notes(PROJECT_SCOPE):
                index.sync_project()
            return

        if delete_block_ids:
            index.delete_items(PROJECT_SCOPE, delete_block_ids)
        if upsert_block_ids:
            index.sync_project_blocks(upsert_block_ids)

        _repair_project_retrieval_if_needed(store, index)
    except Exception:
        # Lance incremental merge/delete paths can fail under spill pressure; rebuild the
        # derived retrieval mirror instead of surfacing an indexing failure to the caller.
        index.sync_project()


def _sync_project_note_change(store: MemoryStore, note_id: str) -> None:
    index = RetrievalIndex(store)
    try:
        if index.count_rows(PROJECT_SCOPE) == 0:
            index.sync_project()
            return
        index.sync_project_notes([note_id])
        _repair_project_retrieval_if_needed(store, index)
    except Exception:
        index.sync_project()


def _sync_global_note_change(store: MemoryStore, note_id: str) -> None:
    index = RetrievalIndex(store)
    try:
        if index.count_rows(GLOBAL_SCOPE) == 0:
            index.sync_global()
            return
        index.sync_global_notes([note_id])
        _repair_global_retrieval_if_needed(store, index)
    except Exception:
        index.sync_global()


def _remove_retrieval_note(store: MemoryStore, scope: str, note_id: str) -> None:
    index = RetrievalIndex(store)
    try:
        index.delete_items(scope, [note_id])
        if scope == PROJECT_SCOPE:
            _repair_project_retrieval_if_needed(store, index)
        else:
            _repair_global_retrieval_if_needed(store, index)
    except Exception:
        if scope == PROJECT_SCOPE:
            index.sync_project()
        else:
            index.sync_global()


def _repair_project_retrieval_if_needed(store: MemoryStore, index: RetrievalIndex) -> None:
    expected_rows = len(store.list_markdown_blocks()) + len(store.list_notes(PROJECT_SCOPE))
    if index.count_rows(PROJECT_SCOPE) != expected_rows:
        index.sync_project()


def _repair_global_retrieval_if_needed(store: MemoryStore, index: RetrievalIndex) -> None:
    expected_rows = len(store.list_notes(GLOBAL_SCOPE))
    if index.count_rows(GLOBAL_SCOPE) != expected_rows:
        index.sync_global()


def _project_retrieval_requires_rebuild(store: MemoryStore) -> bool:
    has_project_content = bool(store.list_markdown_blocks() or store.list_notes(PROJECT_SCOPE))
    if not has_project_content:
        return False
    manifest = store.read_project_retrieval_manifest()
    return manifest is None or int(manifest.get("format_version", 0)) != RETRIEVAL_FORMAT_VERSION


def _global_retrieval_requires_rebuild(store: MemoryStore) -> bool:
    if not store.list_notes(GLOBAL_SCOPE):
        return False
    manifest = store.read_global_retrieval_manifest()
    return manifest is None or int(manifest.get("format_version", 0)) != RETRIEVAL_FORMAT_VERSION


def _sum_raw_source_bytes(store: MemoryStore, items: list[dict[str, object]]) -> int:
    total = 0
    for item in items:
        source_kind = str(item.get("source_kind", ""))
        scope = str(item.get("scope", PROJECT_SCOPE))
        if source_kind == NOTE_SOURCE_KIND:
            note = store.read_note(str(item["item_id"]), scope)
            total += len(str(note["content"]).encode("utf-8"))
            continue
        if source_kind == MARKDOWN_SOURCE_KIND and item.get("block_id"):
            block = store.read_markdown_block(str(item["block_id"]))
            total += len(str(block["content_raw"]).encode("utf-8"))
    return total


@lru_cache(maxsize=32)
def _load_storage_snapshot(
    project_notes_dir: str,
    global_notes_dir: str,
    markdown_roots_dir: str,
    markdown_files_dir: str,
    markdown_blocks_dir: str,
    project_manifest_mtime_ns: int,
    global_manifest_mtime_ns: int,
    markdown_manifest_mtime_ns: int,
) -> dict[str, object]:
    del project_manifest_mtime_ns, global_manifest_mtime_ns, markdown_manifest_mtime_ns

    project_notes = _read_json_dir(Path(project_notes_dir))
    global_notes = _read_json_dir(Path(global_notes_dir))
    markdown_roots = _read_json_dir(Path(markdown_roots_dir))
    markdown_files = _read_json_dir(Path(markdown_files_dir))
    markdown_blocks = _read_json_dir(Path(markdown_blocks_dir))

    project_note_stats = _count_note_statuses(project_notes)
    global_note_stats = _count_note_statuses(global_notes)

    return {
        "project_note_count": project_note_stats["active"],
        "project_total_note_count": project_note_stats["total"],
        "project_inactive_note_count": project_note_stats["inactive"],
        "project_archived_note_count": project_note_stats["archived"],
        "project_superseded_note_count": project_note_stats["superseded"],
        "global_note_count": global_note_stats["active"],
        "global_total_note_count": global_note_stats["total"],
        "global_inactive_note_count": global_note_stats["inactive"],
        "global_archived_note_count": global_note_stats["archived"],
        "global_superseded_note_count": global_note_stats["superseded"],
        "markdown_root_count": len(markdown_roots),
        "markdown_file_count": len(markdown_files),
        "markdown_block_count": len(markdown_blocks),
        "project_last_note_update": _max_timestamp([note.get("updated_at") for note in project_notes]),
        "global_last_note_update": _max_timestamp([note.get("updated_at") for note in global_notes]),
        "last_indexed_at": _max_timestamp([manifest.get("indexed_at") for manifest in markdown_files]),
    }


def _cached_storage_snapshot(store: MemoryStore) -> dict[str, object]:
    return _load_storage_snapshot(
        str(store.project_notes_dir()),
        str(store.global_notes_dir()),
        str(store.project_markdown_roots_dir()),
        str(store.project_markdown_files_dir()),
        str(store.project_markdown_blocks_dir()),
        _path_mtime_ns(store.project_manifest_path()),
        _path_mtime_ns(store.global_manifest_path()),
        _path_mtime_ns(store.project_markdown_manifest_path()),
    )


def _read_json_dir(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []

    payloads: list[dict[str, object]] = []
    for item_path in sorted(path.glob("*.json")):
        with item_path.open("r", encoding="utf-8") as handle:
            payloads.append(json.load(handle))
    return payloads


def _count_note_statuses(notes: list[dict[str, object]]) -> dict[str, int]:
    archived = 0
    superseded = 0
    active = 0

    for note in notes:
        status = str(note.get("note_status", "active"))
        if status == "archived":
            archived += 1
        elif status == "superseded":
            superseded += 1
        else:
            active += 1

    return {
        "active": active,
        "total": len(notes),
        "inactive": archived + superseded,
        "archived": archived,
        "superseded": superseded,
    }


def _path_mtime_ns(path: Path) -> int:
    if not path.exists():
        return 0
    return int(path.stat().st_mtime_ns)


def _max_timestamp(values: list[object]) -> str | None:
    candidates = [str(value) for value in values if value]
    if not candidates:
        return None
    return max(candidates)


__all__ = [
    "Dispatcher",
    "MCPServer",
    "PRODUCT_NAME",
    "ProxyRuntime",
    "SERVER_ID",
    "TOOL_HANDLERS",
    "build_server",
    "build_runtime_context",
    "build_current_project_payload",
    "collect_index_status",
    "collect_storage_stats",
    "build_content_preview",
    "deprecate_note_impl",
    "hydrate_impl",
    "index_paths_impl",
    "lint_knowledge_base_impl",
    "make_local_dispatcher",
    "make_proxy_dispatcher",
    "promote_note_impl",
    "remember_note_impl",
    "run_stdio_server",
    "semantic_search_impl",
    "self_test_impl",
    "server_info_impl",
]
