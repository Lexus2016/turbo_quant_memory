"""Phase 4 stdio MCP server for Turbo Quant Memory."""

from __future__ import annotations

import errno
import json
import os
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.types import CallToolResult, TextContent

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover - compatibility for current stable SDK
    from mcp.server.fastmcp import FastMCP as MCPServer

from .contracts import (
    DEFAULT_QUERY_MODE,
    PRODUCT_NAME,
    RECENT_CONTEXT_FORMATS,
    SERVER_ID,
    build_delete_secret_payload,
    build_get_secret_missing_payload,
    build_get_secret_payload,
    build_health_payload,
    build_list_secrets_payload,
    build_note_write_payload,
    build_recent_context_hook_payload,
    build_recent_context_item_payload,
    build_recent_context_payload,
    build_scope_payload,
    build_secret_error_payload,
    build_self_test_payload,
    build_server_info_payload,
    build_set_secret_payload,
    render_recent_context_text,
)
from .daemon import (
    ENV_MIGRATE_ON_STARTUP,
    BootstrapResult,
    DaemonClient,
    DaemonEndpoint,
    DaemonListener,
    PrimaryUnreachable,
    _startup_log,
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
from .locks import DEFAULT_SCOPE_LOCKS, GLOBAL_LOCK_KEY, ScopeLocks, project_lock_key
from .migrations import format_pending_warning
from .refresher import IndexRefresher, RefreshProgress
from .retrieval import MAX_SEMANTIC_LIMIT, format_probe_error, probe_read_path, semantic_search
from .retrieval_index import RetrievalIndex
from .secrets import (
    AuditLog,
    MasterKeyUnavailable,
    SecretsStore,
    VaultDecryptError,
)
from .store import (
    ENV_STORAGE_HOME,
    GLOBAL_SCOPE,
    MARKDOWN_FORMAT_VERSION,
    MARKDOWN_SOURCE_KIND,
    NOTE_KINDS,
    NOTE_SOURCE_KIND,
    NOTE_TIERS,
    PROJECT_SCOPE,
    RETRIEVAL_FORMAT_VERSION,
    MemoryStore,
    detect_orphaned_buckets,
    reconcile_project_identity,
    resolve_storage_root,
)
from .telemetry import build_usage_snapshot, record_hydration_usage, record_semantic_search_usage
from .workspace.models import FILE_CHANGED, FILE_DELETED, SessionRow, VersionRecord
from .workspace.paths import to_relative
from .workspace.runtime import WorkspaceRuntime
from .workspace.status import PEER_IDLE_AFTER_S, iso_time, writer_hint

_cached_bootstrap: BootstrapResult | None = None
_auto_migration_result: str | None = None


Dispatcher = Callable[[str, Mapping[str, Any]], Any]

# Environment variables forwarded from proxy -> primary so the primary resolves
# the proxy's project identity (not its own cwd) for cwd-aware tools, AND so the
# secrets vault master-key resolver sees the same passphrase the user set in
# the interactive shell (the primary daemon typically started earlier with a
# stale env and would otherwise raise MasterKeyUnavailable in multi-client
# setups). The daemon's AF_UNIX socket is 0o600 with a 32-byte authkey, so
# the passphrase stays within the same-user local trust boundary.
#
# DEFECT E (cross-client poisoning): because the primary is shared, key
# resolution depends on whichever client last forwarded a value, so a single
# misconfigured client could break secrets for all of them. TQMEMORY_SECRETS_
# PASSPHRASE must therefore be identical across all clients that share the
# daemon, or set on none of them. A forwarded passphrase that does NOT match
# the vault no longer fails silently: the key-fingerprint check (DEFECT B) now
# converts it into a structured ``master_key_mismatch`` error instead of a
# wrong-key derivation that crashes on decrypt.
_FORWARDED_ENV_KEYS: tuple[str, ...] = (
    ENV_PROJECT_ROOT,
    ENV_PROJECT_ID,
    ENV_PROJECT_NAME,
    ENV_STORAGE_HOME,
    "TQMEMORY_SECRETS_PASSPHRASE",
)

# Project-identity keys a proxy call must resolve from its OWN forwarded
# environment (or, when it forwards none, from its forwarded ``_cwd``). They are
# stripped from the shared primary environment before applying a proxy override
# so a proxy started in repository B cannot inherit the primary's repository-A
# namespace. Storage home and the secrets passphrase are deliberately NOT listed
# here: those are process-level and stay the primary's unless the proxy forwards
# its own value.
_PROJECT_IDENTITY_ENV_KEYS: tuple[str, ...] = (
    ENV_PROJECT_ROOT,
    ENV_PROJECT_ID,
    ENV_PROJECT_NAME,
)


WORKSPACE_DISABLED_REASON = "TQMEMORY_WORKSPACE_DISABLE is set"
_WORKSPACE_INSTRUCTIONS = (
    " When several agents share one working tree: call workspace_status at session start, "
    "announce_work when starting a feature and finish_work when done; read the `workspace` field "
    "whenever a response carries it and re-read any file another session changed before writing it; "
    "file_history and restore_file recover overwritten files. Call workspace_status before editing a file "
    "another session may have touched, and before every commit while other sessions are live. While other "
    "sessions are live, never run destructive git commands (checkout -- ., reset --hard, stash, clean, branch "
    "switch) without the human's explicit confirmation. A session under `idle_sessions` made no tqmemory call "
    f"for {int(PEER_IDLE_AFTER_S // 60)}+ minutes and is not an active peer; a session's mere presence or old "
    "last_activity_at is never a reason to postpone work, only `overlaps` and `recent_changes` are."
)


HOOK_OUTPUT_KEY = "hookSpecificOutput"
_TEXT_DIGEST_MORE = "[workspace] call workspace_status for details"


def _text_digest(digest: Mapping[str, Any]) -> str:
    """Pending workspace lines appended to a plain-text tool result."""

    lines = [f"[workspace] {line}" for line in digest.get("new", [])]
    return "\n\n" + "\n".join([*lines, _TEXT_DIGEST_MORE])


def _describe_workspace(workspace: WorkspaceRuntime | None) -> dict[str, Any]:
    """The ``workspace`` block of health and server_info; fails open to a degraded block."""

    if workspace is None:
        return _fallback_workspace_block(
            enabled=False, state="disabled", reason=WORKSPACE_DISABLED_REASON
        )
    try:
        return workspace.describe()
    except Exception as exc:
        return _fallback_workspace_block(
            enabled=True, state="degraded", reason=str(exc) or type(exc).__name__
        )


def _fallback_workspace_block(*, enabled: bool, state: str, reason: str) -> dict[str, Any]:
    """Same keys as WorkspaceRuntime.describe(), for when no runtime can describe itself."""

    return {
        "enabled": enabled,
        "state": state,
        "reason": reason,
        "workspace": None,
        "db_path": None,
        "live_sessions": None,
        "idle_sessions": None,
        "journal": {
            "state": "off",
            "reason": None,
            "owner_session": None,
            "over_cap": False,
            "this_session_owns": False,
        },
        "coverage": None,
    }


_NOT_RUNNING_REASON = "change journal is not running"
_HINT_PAGE_SIZE = 200
_HINT_SCAN_LIMIT = 5000


def _no_journal(workspace: WorkspaceRuntime) -> dict[str, Any]:
    """Why the journal cannot serve a request: a failed database is degraded, a refused or stopped journal unavailable."""

    if workspace.db is None:
        return {"status": "degraded", "reason": workspace.degraded_reason or "coordination database is not open"}
    reason = (
        workspace.workspace.journal_refusal
        or workspace.journal_state().get("reason")
        or workspace.degraded_reason
        or _NOT_RUNNING_REASON
    )
    return {"status": "unavailable", "reason": reason}


def _change_details(workspace: WorkspaceRuntime, version_ids: set[int]) -> dict[int, dict[str, Any]]:
    """Recorded event detail for the given versions, found by scanning the change events newest first.

    Bounded by ``_HINT_SCAN_LIMIT``; a read failure yields no details, so hints fall back to what the version row says.
    """

    db = workspace.db
    if db is None or not version_ids:
        return {}
    root_key = workspace.workspace.root_key
    found: dict[int, dict[str, Any]] = {}
    try:
        upper = db.max_event_id(root_key)
        before = upper + 1
        scanned = 0
        while len(found) < len(version_ids) and scanned < _HINT_SCAN_LIMIT:
            page = db.events_window(
                root_key,
                (FILE_CHANGED, FILE_DELETED),
                upper_id=upper,
                before_id=before,
                limit=_HINT_PAGE_SIZE,
            )
            if not page:
                break
            for event in page:
                version_id = event.detail.get("version_id")
                if isinstance(version_id, int) and version_id in version_ids:
                    found[version_id] = event.detail
            scanned += len(page)
            before = page[-1].event_id
    except Exception:
        return found
    return found


def _session_rows(workspace: WorkspaceRuntime) -> dict[str, SessionRow] | None:
    """Live session rows by id, or None when the table could not be read. A failed read is never "no sessions"."""

    db = workspace.db
    if db is None:
        return None
    try:
        return {row.session_id: row for row in db.list_sessions(workspace.workspace.root_key)}
    except Exception:
        return None


def _writer_hint(
    record: VersionRecord,
    sessions: Mapping[str, SessionRow] | None,
    detail: Mapping[str, Any] | None,
) -> str:
    """A hint about who may have written a version. Inferred writers are never stated as fact, a failed lookup
    never asserts that a session ended, and a version without a recorded feature is never labelled with the
    session's current feature."""

    recorded = detail.get("feature") if detail is not None else None
    feature = recorded if isinstance(recorded, str) else ""
    if record.attribution != "inferred":
        return writer_hint(record.attribution, None, feature)
    if sessions is None:
        uncertain = "inside another session's announced area"
        return f"{uncertain} ({feature})" if feature else uncertain
    session = sessions.get(record.session_id) if record.session_id is not None else None
    if session is None and feature:
        return f"inside an announced area ({feature}); that session has ended"
    return writer_hint(record.attribution, session, feature)


def _version_view(record: VersionRecord, hint: str) -> dict[str, Any]:
    return {
        "version_id": record.version_id,
        "kind": record.kind,
        "size": record.size,
        "observed_at": iso_time(record.observed_at),
        "attribution": record.attribution,
        "session_id": record.session_id,
        "writer_hint": hint,
    }


def _file_history(workspace: WorkspaceRuntime, path: str, limit: int) -> dict[str, Any]:
    journal = workspace.journal
    if journal is None:
        return _no_journal(workspace)
    rel = to_relative(workspace.workspace, path.replace("\\", "/"))
    records = journal.history(rel, limit)
    details = _change_details(workspace, {record.version_id for record in records})
    sessions = _session_rows(workspace)
    return {
        "status": "ok",
        "path": rel,
        "versions": [
            _version_view(record, _writer_hint(record, sessions, details.get(record.version_id)))
            for record in records
        ],
    }


def _restore_file(workspace: WorkspaceRuntime, path: str, version: str) -> dict[str, Any]:
    target: int | Literal["HEAD"]
    if version == "HEAD":
        target = "HEAD"
    elif version.isdecimal():
        target = int(version)
    else:
        return {"status": "error", "reason": "version must be a version id or HEAD"}
    journal = workspace.journal
    if journal is None:
        return _no_journal(workspace)
    rel = to_relative(workspace.workspace, path.replace("\\", "/"))
    return journal.restore(rel, target)


def build_server(dispatcher: Dispatcher, workspace: WorkspaceRuntime | None = None) -> MCPServer:
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
            "'data/reports/*.md' skips specific paths." + _WORKSPACE_INSTRUCTIONS
        ),
        json_response=True,
        log_level="ERROR",
    )

    def _begin_workspace_call() -> None:
        """Record the caller's client name once and refresh this session's activity. Raises on failure."""
        if workspace is None:
            return
        try:
            client = mcp.get_context().request_context.session.client_params.clientInfo
            name, version = client.name, client.version
        except (LookupError, AttributeError):
            name, version = None, None
        workspace.note_client(name, version)
        workspace.touch()

    def _decorate(result: Any) -> Any:
        """Presence on every memory call. Pending lines go into dict results as a field and into text results as
        trailing lines. Fails open to the result unchanged."""
        if workspace is None:
            return result
        try:
            _begin_workspace_call()
            # A hook payload is consumed by Claude Code, so it must not advance the cursor and lose the update.
            if isinstance(result, dict) and HOOK_OUTPUT_KEY in result:
                return result
            digest = workspace.digest()
            if digest is None:
                return result
            if isinstance(result, str):
                return result + _text_digest(digest)
            if isinstance(result, dict):
                return {**result, "workspace": digest}
            return result
        except Exception:
            return result

    def _memory_call(tool: str, kwargs: Mapping[str, Any]) -> Any:
        return _decorate(dispatcher(tool, kwargs))

    def _with_workspace_block(result: Any) -> Any:
        """health and server_info carry the workspace description; a digest, if any, is merged into it."""
        if not isinstance(result, dict):
            return result
        block = _describe_workspace(workspace)
        digest = result.get("workspace")
        if isinstance(digest, dict):
            block = {**block, **digest}
        return {**result, "workspace": block}

    def _workspace_call(action: Callable[[WorkspaceRuntime], dict[str, Any]]) -> dict[str, Any]:
        """Run a workspace tool directly against the runtime. Never touches the memory dispatcher or its lock."""
        if workspace is None:
            return {"status": "disabled", "reason": WORKSPACE_DISABLED_REASON}
        try:
            _begin_workspace_call()
            return action(workspace)
        except ValueError as exc:
            return {"status": "error", "reason": str(exc)}
        except Exception as exc:
            return {"status": "degraded", "reason": str(exc) or type(exc).__name__}

    @mcp.tool()
    def health() -> dict[str, Any]:
        return _with_workspace_block(_memory_call("health", {}))

    @mcp.tool()
    def server_info() -> dict[str, Any]:
        return _with_workspace_block(_memory_call("server_info", {}))

    @mcp.tool()
    def list_scopes() -> dict[str, Any]:
        return _memory_call("list_scopes", {})

    @mcp.tool()
    def self_test() -> dict[str, Any]:
        return _memory_call("self_test", {})

    @mcp.tool()
    def remember_note(
        title: str,
        content: str,
        kind: str,
        tags: list[str] | None = None,
        source_refs: list[str] | None = None,
        scope: str = "project",
        provenance: str = "agent",
        tier: str | None = None,
    ) -> dict[str, Any]:
        """Store a typed project note. kind must be exactly one of: lesson, decision, pattern, handoff.

        tags: 2-3 lowercase tags are strongly recommended — tags are the only way
          to FILTER notes; an untagged note is reachable by semantic_search alone.
        source_refs: the files / notes / issues this note is about. Pass them as
          entity URIs (note://<id>, file://<relative/path>, issue://KEY,
          https://...) and they are auto-linked into the knowledge graph as
          `references` relations, so get_related_entities can later surface them.
        provenance: who originated the note. Use "human-explicit" when the USER
          asked to remember something ("remember this", "save that"); "agent" for
          your own observations (the default); "external-tool" for API/tool data;
          "system" for automatic processes.
        tier: normally derived from `kind` (handoff -> episodic, every other kind
          -> durable). Override e.g. tier="durable" to keep a `handoff` in the
          default-searchable set, or tier="episodic" to keep a noisy lesson out of
          regular search.

        After storing, connect the note with link_entities(source_uri="note://<id>",
        ...) when it relates to a file, issue, or prior note — the response echoes
        the note's uri and hints this when the note still has no relations.
        """
        return _memory_call(
            "remember_note",
            {
                "title": title,
                "content": content,
                "kind": kind,
                "tags": tags,
                "source_refs": source_refs,
                "scope": scope,
                "provenance": provenance,
                "tier": tier,
            },
        )

    @mcp.tool()
    def promote_note(note_id: str) -> dict[str, Any]:
        return _memory_call("promote_note", {"note_id": note_id})

    @mcp.tool()
    def deprecate_note(
        note_id: str,
        scope: str = "project",
        replacement_note_id: str | None = None,
        replacement_scope: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return _memory_call(
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
        tier_filter: list[str] | None = None,
        source_filter: str | None = None,
    ) -> dict[str, Any]:
        """Compact memory retrieval (dense vector + BM25, fused via RRF).

        By default only the `durable` and `reference` tiers are searched, so
        session `handoff` notes (which live in the `episodic` tier) are NOT
        returned. To recover handoffs / session summaries pass
        ``tier_filter=["episodic"]`` (or list every tier to opt everything in).
        For a query-free "where did I leave off" bootstrap at session start,
        prefer the `recent_context` tool instead.

        source_filter narrows results by source type: ``"notes"`` returns only
        memory notes (decisions/lessons/patterns/handoffs), ``"markdown"`` only
        indexed doc blocks. In doc-heavy projects the reference blocks often
        crowd notes out of the top ranks on question-shaped queries — when you
        are asking "what did we decide/learn about X", pass
        ``source_filter="notes"``.
        """
        return _memory_call(
            "semantic_search",
            {
                "query": query,
                "scope": scope,
                "limit": limit,
                "tier_filter": tier_filter,
                "source_filter": source_filter,
            },
        )

    @mcp.tool()
    def hydrate(
        item_id: str,
        scope: str,
        mode: str = "default",
    ) -> dict[str, Any]:
        return _memory_call(
            "hydrate",
            {"item_id": item_id, "scope": scope, "mode": mode},
        )

    @mcp.tool()
    def index_paths(
        paths: list[str] | None = None,
        mode: str = "incremental",
    ) -> dict[str, Any]:
        """Register and index Markdown directories into project memory.

        Supports .tqmemoryignore files (placed in project root or any indexed
        directory) with glob patterns to exclude paths from indexing.
        One pattern per line, # for comments.  Example patterns:
        ``workspace-*`` skips any directory matching the glob;
        ``data/reports/*.md`` skips files matching a path pattern;
        ``backups/`` (trailing slash, as in .gitignore) skips a directory.
        Agent worktrees under ``.claude/worktrees`` are skipped by default.
        """
        return _memory_call("index_paths", {"paths": paths, "mode": mode})

    @mcp.tool()
    def lint_knowledge_base(
        paths: list[str] | None = None,
        max_issues: int = 200,
    ) -> dict[str, Any]:
        return _memory_call(
            "lint_knowledge_base",
            {"paths": paths, "max_issues": max_issues},
        )

    @mcp.tool()
    def link_entities(
        source_uri: str,
        target_uri: str,
        relation_type: str,
        scope: str = "project",
    ) -> dict[str, Any]:
        """Create a Knowledge Graph link between two entities.
        
        Entities are specified using URIs:
          - Note: note://<note_id>
          - File: file://<relative_path> (relative to project root)
          - External: e.g. issue://BUG-404, task://TASK-101
        """
        return _memory_call(
            "link_entities",
            {
                "source_uri": source_uri,
                "target_uri": target_uri,
                "relation_type": relation_type,
                "scope": scope,
            },
        )

    @mcp.tool()
    def unlink_entities(
        source_uri: str,
        target_uri: str,
        relation_type: str | None = None,
        scope: str = "project",
    ) -> dict[str, Any]:
        """Remove a Knowledge Graph link between two entities.

        source_uri / target_uri use the same URI forms as link_entities:
        note://<note_id>, file://<relative/path>, issue://KEY, https://...
        """
        return _memory_call(
            "unlink_entities",
            {
                "source_uri": source_uri,
                "target_uri": target_uri,
                "relation_type": relation_type,
                "scope": scope,
            },
        )

    @mcp.tool()
    def get_related_entities(
        uri: str,
        relation_type: str | None = None,
        scope: str = "hybrid",
    ) -> dict[str, Any]:
        """Query relations involving a specific entity URI.

        uri: the entity to look up, as note://<note_id>, file://<relative/path>,
        issue://KEY, task://KEY, or https://... — the same URI forms link_entities
        accepts. Returns nothing for an entity that has no links yet.
        """
        return _memory_call(
            "get_related_entities",
            {
                "uri": uri,
                "relation_type": relation_type,
                "scope": scope,
            },
        )

    @mcp.tool()
    def set_secret(name: str, value: str) -> dict[str, Any]:
        """Store an encrypted secret in the active project's vault.

        The value is encrypted with AES-256-GCM under a per-project master
        key resolved from TQMEMORY_SECRETS_PASSPHRASE or the OS keyring.
        Secrets are NEVER indexed, embedded, or returned via
        ``semantic_search`` / ``hydrate``. They live in
        ``~/.turbo-quant-memory/projects/<project_id>/secrets/vault.tqv``
        and stay on this machine.
        """
        return _memory_call("set_secret", {"name": name, "value": value})

    @mcp.tool()
    def get_secret(name: str) -> dict[str, Any]:
        """Fetch a project secret by exact name.

        Returns the value in a dedicated ``secret_value`` field (never in
        descriptive text). Status is ``"ok"`` on hit, ``"missing"`` when
        no such name exists, or ``"error"`` with ``setup_hint`` when no
        master key is configured yet.
        """
        return _memory_call("get_secret", {"name": name})

    @mcp.tool()
    def list_secrets() -> dict[str, Any]:
        """List secret names in the active project. Never returns values."""
        return _memory_call("list_secrets", {})

    @mcp.tool()
    def delete_secret(name: str) -> dict[str, Any]:
        """Delete a project secret by exact name."""
        return _memory_call("delete_secret", {"name": name})

    # Annotated[CallToolResult, dict]: the output schema stays the plain dict
    # schema, while format="text" can return bare text content. A `dict | str`
    # return would make FastMCP wrap every payload as {"result": ...}.
    @mcp.tool()
    def recent_context(
        scope: str = DEFAULT_QUERY_MODE,
        limit: int = 10,
        tier_filter: list[str] | None = None,
        format: str = "json",
    ) -> Annotated[CallToolResult, dict[str, Any]]:
        """Query-free session bootstrap: the most recently updated notes.

        Call this FIRST when starting a new session or resuming after a context
        compaction, when you do not yet know what to search for. Returns notes
        ordered by recency (newest first), NOT by relevance — including
        `handoff` notes (episodic tier), which a plain semantic_search hides by
        default. This is the reliable "where did I leave off" entry point.

        scope: 'project' (default), 'global', or 'hybrid'. Use 'hybrid' to also
        surface promoted cross-project knowledge.
        tier_filter: defaults to all tiers (so handoffs are included). Pass e.g.
        ["durable"] to exclude episodic session notes.
        format: 'json' (default); 'text' — compact plain text, one line per
        note with its hydrate handle (about 20% of the JSON size); 'hook' — the
        same text as Claude Code SessionStart hook output. Use 'hook' from a
        SessionStart mcp_tool hook: Claude Code shows the model only an
        mcp_tool hook's JSON additionalContext, never its plain text.
        """
        result = _memory_call(
            "recent_context",
            {"scope": scope, "limit": limit, "tier_filter": tier_filter, "format": format},
        )
        if isinstance(result, str):
            return CallToolResult(
                content=[TextContent(type="text", text=result)],
                structuredContent={"status": "ok", "mode": "recent_context", "format": "text", "text": result},
            )
        return result

    @mcp.tool()
    def announce_work(
        feature: str,
        areas: list[str] | None = None,
        details: str | None = None,
    ) -> dict[str, Any]:
        """Tell the other agents in this working tree what you are changing.

        feature: short name of the work. areas: workspace-relative files or directories
        (globs allowed). Returns the peers whose announced areas overlap yours and recent
        changes inside them. Inferred writers are guesses from announced areas, never facts.
        """
        return _workspace_call(lambda ws: ws.announce_work(feature, areas, details))

    @mcp.tool()
    def finish_work() -> dict[str, Any]:
        """Clear the work this session announced with announce_work."""
        return _workspace_call(lambda ws: ws.finish_work())

    @mcp.tool()
    def workspace_status() -> dict[str, Any]:
        """Overview of the sessions sharing this working tree: peers, overlaps and recent file changes.

        Call it at session start. Inferred writers are guesses from announced areas, never facts.
        Peers that made no tqmemory call for a long time are listed under `idle_sessions`, not in
        `sessions`, and their announced areas are left out of `overlaps`.
        """
        return _workspace_call(lambda ws: ws.status())

    @mcp.tool()
    def file_history(path: str, limit: int = 20) -> dict[str, Any]:
        """Journaled versions of one file in this working tree, newest first.

        Versions are snapshots taken while several sessions share the tree. Use restore_file
        with a version id to bring one back; the overwritten content is kept as a new version.
        """
        return _workspace_call(lambda ws: _file_history(ws, path, limit))

    @mcp.tool()
    def restore_file(path: str, version: str) -> dict[str, Any]:
        """Write a journaled version of one file back into the working tree.

        version: a version id from file_history, or "HEAD" for the committed content.
        The content being overwritten is kept as a new version, so the restore can be undone.
        """
        return _workspace_call(lambda ws: _restore_file(ws, path, version))

    return mcp


# ---------------------------------------------------------------------------
# Tool dispatch (local / proxy)
# ---------------------------------------------------------------------------


def _tool_health(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    pending, hint = _migration_pending_signal(cwd=cwd, environ=environ)
    role = _cached_bootstrap.role if _cached_bootstrap else None
    payload = build_health_payload(
        migrations_pending=pending,
        migrations_hint=hint,
        daemon_role=role,
        migration_auto_result=_auto_migration_result,
    )
    if role == "standalone":
        # F6: make the silent standalone fallback loud. In this mode there is no
        # cross-process coordination for notes/relations/manifests/LanceDB.
        payload["daemon_warning"] = (
            "running in uncoordinated standalone mode (daemon bootstrap fell "
            "back after retries); concurrent processes on the same storage root "
            "are NOT serialized. Restart, or ensure a single client, if you rely "
            "on multi-client coordination."
        )
    return payload


def _tool_server_info(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return server_info_impl(cwd=cwd, environ=environ)


def _migration_pending_signal(
    *, cwd: Any = None, environ: Any = None
) -> tuple[bool, str | None]:
    """Cheap detection of pending schema migrations for client-visible payloads.

    Returns ``(pending, hint)``. ``hint`` is a one-line operator instruction
    safe to surface in MCP responses; ``None`` if nothing is pending.
    Detection failures are swallowed and reported as "no pending" so a
    broken status check never blocks a legitimate tool call.
    """
    try:
        from .migrations import detect_status

        _, store = build_runtime_context(cwd=cwd, environ=environ)
        statuses = detect_status(store)
        pending_subs = [s.subsystem.value for s in statuses.values() if s.needs_upgrade]
        if not pending_subs:
            return False, None
        hint = (
            "Pending tqmemory schema upgrade(s): "
            + ", ".join(pending_subs)
            + ". Stop all MCP clients, then run "
            "`turbo-memory-mcp migrate --apply`. A rolling snapshot is taken "
            "automatically; on failure the CLI prints the exact "
            "`--restore-from` command."
        )
        if "retrieval" in pending_subs:
            hint += (
                " Note: the retrieval upgrade re-embeds every block and note "
                "with the current embedding model, so it can take a while on "
                "large corpora; canonical notes and markdown are not modified."
            )
        return True, hint
    except Exception:
        return False, None


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
        provenance=str(kwargs.get("provenance", "agent")),
        tier=kwargs.get("tier"),
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
    source_filter = kwargs.get("source_filter")
    return semantic_search_impl(
        str(kwargs["query"]),
        scope=str(kwargs.get("scope", DEFAULT_QUERY_MODE)),
        limit=int(kwargs.get("limit", 5)),
        tier_filter=kwargs.get("tier_filter"),
        source_filter=None if source_filter is None else str(source_filter),
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


def _tool_recent_context(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return recent_context_impl(
        scope=str(kwargs.get("scope", DEFAULT_QUERY_MODE)),
        limit=int(kwargs.get("limit", 10)),
        tier_filter=kwargs.get("tier_filter"),
        format=str(kwargs.get("format", "json")),
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


def _tool_link_entities(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return link_entities_impl(
        str(kwargs["source_uri"]),
        str(kwargs["target_uri"]),
        str(kwargs["relation_type"]),
        scope=str(kwargs.get("scope", "project")),
        cwd=cwd,
        environ=environ,
    )


def _tool_unlink_entities(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return unlink_entities_impl(
        str(kwargs["source_uri"]),
        str(kwargs["target_uri"]),
        relation_type=kwargs.get("relation_type"),
        scope=str(kwargs.get("scope", "project")),
        cwd=cwd,
        environ=environ,
    )


def _tool_get_related_entities(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return get_related_entities_impl(
        str(kwargs["uri"]),
        relation_type=kwargs.get("relation_type"),
        scope=str(kwargs.get("scope", "hybrid")),
        cwd=cwd,
        environ=environ,
    )


def _tool_set_secret(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return set_secret_impl(
        str(kwargs["name"]),
        str(kwargs["value"]),
        cwd=cwd,
        environ=environ,
    )


def _tool_get_secret(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return get_secret_impl(str(kwargs["name"]), cwd=cwd, environ=environ)


def _tool_list_secrets(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return list_secrets_impl(cwd=cwd, environ=environ)


def _tool_delete_secret(kwargs: Mapping[str, Any], *, cwd: Any, environ: Any) -> Any:
    return delete_secret_impl(str(kwargs["name"]), cwd=cwd, environ=environ)


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
    "recent_context": _tool_recent_context,
    "index_paths": _tool_index_paths,
    "lint_knowledge_base": _tool_lint_knowledge_base,
    "link_entities": _tool_link_entities,
    "unlink_entities": _tool_unlink_entities,
    "get_related_entities": _tool_get_related_entities,
    "set_secret": _tool_set_secret,
    "get_secret": _tool_get_secret,
    "list_secrets": _tool_list_secrets,
    "delete_secret": _tool_delete_secret,
}


# Bounded wait for the shared dispatch lock before failing fast (issue #103).
#
# Without a bound, a slow or runaway operation (e.g. a hydration stuck on a
# dead embedding backend, or a wedged primary) makes every concurrent caller —
# the primary's stdio handler AND proxied daemon calls from other agent
# profiles — queue on the RLock for the full MCP host tool-call timeout (up to
# 600s in the field), surfacing as the 420s-class hard timeouts. A short bound
# converts that queue into an explicit, retryable "server busy" error so the
# host fails fast instead of hanging and can back off.
DISPATCH_LOCK_TIMEOUT_SECONDS = 30.0

# Field override for the bound above, in seconds. A value <= 0 restores the old
# unbounded wait — the escape hatch when a deployment legitimately holds the
# lock longer than the default (a large `index_paths` run, a cold embedding
# backend) and would rather queue than get "server busy". Unparseable values
# fall back to the default.
ENV_DISPATCH_LOCK_TIMEOUT = "TQMEMORY_DISPATCH_LOCK_TIMEOUT"

# Name of the tool currently holding the shared dispatch lock, so a contention
# failure can say WHICH operation is blocking instead of only that something
# is. Guarded by its own short lock; a plain dict avoids a `global` rebind.
_DISPATCH_HOLDER: dict[str, str | None] = {"tool": None}
_DISPATCH_HOLDER_LOCK = threading.Lock()


def _resolve_dispatch_lock_timeout(environ: Mapping[str, str] | None = None) -> float:
    """Read the dispatch-lock bound from the environment, else the default."""

    env = os.environ if environ is None else environ
    raw = str(env.get(ENV_DISPATCH_LOCK_TIMEOUT, "")).strip()
    if not raw:
        return DISPATCH_LOCK_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        _startup_log(
            f"{ENV_DISPATCH_LOCK_TIMEOUT}={raw!r} is not a number, "
            f"using default {DISPATCH_LOCK_TIMEOUT_SECONDS:g}s"
        )
        return DISPATCH_LOCK_TIMEOUT_SECONDS


# Tools that write global memory whatever their arguments say.
GLOBAL_SCOPE_TOOLS = frozenset({"promote_note", "self_test"})
# Tools that write global memory when called with scope="global". Reading
# global memory (search, hydrate, recent_context, related entities) takes no
# global lock: every store write is an atomic file replace and LanceDB reads
# are versioned. A global or hybrid search takes the global lock itself, only
# around its refresh of the global mirror, so it never holds it across its
# project's refresh.
_GLOBAL_WRITE_TOOLS = frozenset({"remember_note", "deprecate_note", "link_entities", "unlink_entities"})
# Lock key for a call whose project cannot be resolved; its handler then
# reports the real error. Outside the project namespace, so no project's key.
_UNRESOLVED_LOCK_KEY = "unresolved"


def lock_keys_for(tool: str, kwargs: Mapping[str, Any], project_id: str | None) -> list[str]:
    """Lock keys for one call: its project, then the global scope if touched."""

    project_key = project_lock_key(project_id) if project_id is not None else _UNRESOLVED_LOCK_KEY
    scope = str(kwargs.get("scope") or "").strip().lower()
    if tool in GLOBAL_SCOPE_TOOLS or (tool in _GLOBAL_WRITE_TOOLS and scope == GLOBAL_SCOPE):
        return [project_key, GLOBAL_LOCK_KEY]
    return [project_key]


def _lock_project_id(cwd: Path | str | None, environ: Mapping[str, str] | None) -> str | None:
    """Resolve a call's project like build_runtime_context, minus the store."""

    try:
        candidate = resolve_project_identity(cwd=cwd, environ=environ)
        return reconcile_project_identity(candidate, resolve_storage_root(environ)).project_id
    except Exception:
        return None


def make_local_dispatcher(
    *,
    dispatch_lock: threading.RLock | None = None,
    scope_locks: ScopeLocks | None = None,
    dispatch_lock_timeout: float | None = None,
    default_cwd: Path | str | None = None,
    default_environ: Mapping[str, str] | None = None,
) -> Dispatcher:
    """Return a dispatcher that runs tools in-process.

    Each call takes the lock of the project it acts on, plus the global-scope
    lock when it touches global memory, so MemoryStore / LanceDB access stays
    single-writer per scope while different projects run in parallel. The
    locks are shared by the primary's stdio handler and its daemon listener
    workers (``DEFAULT_SCOPE_LOCKS`` unless ``scope_locks`` is given).
    Passing ``dispatch_lock`` instead serializes every call on that one lock.

    Lock acquisition is bounded by ``dispatch_lock_timeout``: if another
    operation still holds the lock after that, the call fails fast with an
    explicit "server busy" error instead of queueing until the MCP host's
    tool-call timeout (issue #103). ``None`` resolves the bound from
    ``TQMEMORY_DISPATCH_LOCK_TIMEOUT``; a value <= 0 waits without a bound.
    """

    locks = scope_locks or DEFAULT_SCOPE_LOCKS
    timeout = (
        _resolve_dispatch_lock_timeout() if dispatch_lock_timeout is None else dispatch_lock_timeout
    )

    def _dispatch(tool: str, kwargs: Mapping[str, Any]) -> Any:
        handler = TOOL_HANDLERS.get(tool)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool}")
        merged_kwargs = dict(kwargs)
        cwd_override = merged_kwargs.pop("_cwd", None)
        environ_override = merged_kwargs.pop("_environ", None)
        resolved_cwd = cwd_override if cwd_override is not None else default_cwd
        resolved_environ: Mapping[str, str] | None = None
        if environ_override is not None:
            # Proxy call: resolve project identity from the proxy's forwarded
            # values only. Drop the primary's identity keys first so an empty or
            # partial ``_environ`` cannot leak the primary's namespace (issue #1).
            resolved_environ = {
                key: value
                for key, value in os.environ.items()
                if key not in _PROJECT_IDENTITY_ENV_KEYS
            }
            resolved_environ.update({str(k): str(v) for k, v in environ_override.items()})
        else:
            resolved_environ = default_environ
        if dispatch_lock is None:
            keys = lock_keys_for(tool, merged_kwargs, _lock_project_id(resolved_cwd, resolved_environ))
            with locks.hold(keys, timeout=timeout, tool=tool):
                return handler(merged_kwargs, cwd=resolved_cwd, environ=resolved_environ)
        lock = dispatch_lock
        acquired = lock.acquire(timeout=timeout) if timeout > 0 else lock.acquire()
        if not acquired:
            with _DISPATCH_HOLDER_LOCK:
                holder = _DISPATCH_HOLDER["tool"]
            blocker = f"{holder!r}" if holder else "another operation"
            message = (
                f"memory server busy: {blocker} holds the dispatch lock "
                f"(waited {timeout:g}s); retry this call later"
            )
            # Log too: the caller only sees its own failed tool, while the
            # holder's name is the part that makes the stall diagnosable.
            _startup_log(f"dispatch lock contention: {tool!r} gave up, held by {blocker}")
            raise RuntimeError(message)
        with _DISPATCH_HOLDER_LOCK:
            previous_holder = _DISPATCH_HOLDER["tool"]
            _DISPATCH_HOLDER["tool"] = tool
        try:
            return handler(merged_kwargs, cwd=resolved_cwd, environ=resolved_environ)
        finally:
            # Restore rather than clear: RLock is reentrant, so a nested
            # dispatch must hand the slot back to its caller, not blank it.
            with _DISPATCH_HOLDER_LOCK:
                _DISPATCH_HOLDER["tool"] = previous_holder
            lock.release()

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
        """Re-bootstrap after the primary died. Serialized: only one thread
        runs the recovery flow at a time; concurrent callers block on the
        state lock, then observe the installed state and return.

        Holding the lock across :func:`acquire_daemon_role` is intentional.
        Without it, a second thread could race into ``acquire_daemon_role``
        between this thread's lockfile claim and listener start, and (seeing
        our advertised but not-yet-accepting socket) wrongly conclude the
        endpoint is stale and unlink it. The O_EXCL retry loop would then
        let *both* threads install lockfiles, breaking the singleton
        invariant within a single process. ``acquire_daemon_role`` performs
        no callbacks into this runtime, so no deadlock is possible.
        """

        with self._state_lock:
            if self._listener is not None:
                # Another call already promoted us.
                return

            dead_client = self._client
            self._client = None
            if dead_client is not None:
                try:
                    dead_client.close()
                except Exception:
                    pass

            bootstrap = acquire_daemon_role()

            if bootstrap.role == "primary" and bootstrap.endpoint is not None:
                local_dispatcher = make_local_dispatcher(scope_locks=DEFAULT_SCOPE_LOCKS)

                def _listener_handler(tool: str, kwargs: Mapping[str, Any]) -> Any:
                    return local_dispatcher(tool, kwargs)

                ready = threading.Event()

                listener = DaemonListener(
                    bootstrap.endpoint,
                    _listener_handler,
                    ready_event=ready,
                )
                listener.start()
                self._listener = listener
                self._endpoint = bootstrap.endpoint
                # F8: gate the promoted primary through the same
                # migrate -> warn -> ready path as a normal startup so it never
                # serves writes against a possibly-unmigrated store. This runs
                # under _state_lock (like acquire_daemon_role above): a
                # concurrent caller blocks until ready rather than briefly
                # busy-failing on a stale dispatcher — correct, at the cost of a
                # short availability dip if a migration is pending during
                # failover. On gate failure, tear the half-promoted primary down
                # (fail closed) instead of leaving the listener up with the
                # ready gate stuck shut.
                try:
                    _gate_primary_ready(ready)
                except BaseException:
                    try:
                        listener.stop()
                    except Exception:
                        pass
                    self._listener = None
                    self._endpoint = None
                    raise
                self._active_dispatcher = local_dispatcher
            elif bootstrap.role == "proxy" and bootstrap.client is not None:
                self._client = bootstrap.client
                self._active_dispatcher = make_proxy_dispatcher(bootstrap.client)
            else:
                # Standalone fallback (retries exhausted or daemon disabled).
                self._active_dispatcher = make_local_dispatcher(scope_locks=DEFAULT_SCOPE_LOCKS)

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
        # A promoted proxy may be running background index batches: finish
        # them before another process can take the lock over.
        DEFAULT_REFRESHER.shutdown(timeout=REFRESHER_SHUTDOWN_TIMEOUT_SECONDS)
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


def _startup_auto_migrate() -> str | None:
    """Apply pending migrations automatically on startup when opted in.

    Controlled by ``TQMEMORY_MIGRATE_ON_STARTUP=1``. Only runs when this
    process is primary or standalone (we own storage). Takes a rolling
    snapshot before applying. Returns a short status string on attempt,
    or None if skipped.
    """
    global _auto_migration_result
    if os.environ.get(ENV_MIGRATE_ON_STARTUP, "").strip() not in {"1", "true", "yes"}:
        return None
    if _cached_bootstrap is None or _cached_bootstrap.role not in ("primary", "standalone"):
        return None
    try:
        _, store = build_runtime_context()
        from .migrations import apply_pending, create_snapshot, detect_status

        statuses = detect_status(store)
        if not any(s.needs_upgrade for s in statuses.values()):
            _startup_log("auto-migrate: no pending migrations")
            _auto_migration_result = "skipped: nothing pending"
            return _auto_migration_result

        _startup_log("auto-migrate: creating snapshot before applying...")
        snap_path = create_snapshot(store.storage_root)
        _startup_log(f"auto-migrate: snapshot at {snap_path}")

        outcomes = apply_pending(store, dry_run=False, snapshot=False)
        failed = [o for o in outcomes if not o.success]
        if failed:
            errs = "; ".join(f"{o.migration.subsystem.value}: {o.error}" for o in failed)
            _startup_log(f"auto-migrate: {len(failed)} step(s) FAILED — {errs}")
            _auto_migration_result = f"failed: {len(failed)} step(s)"
        else:
            _startup_log(f"auto-migrate: applied {len(outcomes)} step(s) OK")
            _auto_migration_result = f"applied: {len(outcomes)} step(s)"
    except Exception as exc:
        _startup_log(f"auto-migrate: error — {exc}")
        _auto_migration_result = f"error: {exc}"
    return _auto_migration_result


def _warn_about_pending_migrations() -> None:
    """Detect pending schema migrations and surface a warning on stderr.

    Called only in primary/standalone (we own the storage in those roles).
    Never raises — detection failure must not block daemon startup.
    """
    try:
        _, store = build_runtime_context()
        msg = format_pending_warning(store)
        if msg:
            print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass


_BENIGN_DISCONNECT_ERRNOS: frozenset[int] = frozenset(
    {errno.EPIPE, errno.ECONNRESET, errno.ESHUTDOWN, errno.ENOTCONN}
)


def _is_benign_stdio_disconnect(exc: BaseException) -> bool:
    """True when ``exc`` is the client closing the stdio pipe, not a real error.

    Matches the dedicated ``OSError`` subclasses (which may carry no ``errno``,
    e.g. when constructed in tests) and a bare ``OSError`` whose ``errno`` names
    a peer-gone condition on platforms that do not map it to
    ``BrokenPipeError`` / ``ConnectionResetError`` (macOS/Windows can surface
    ``ESHUTDOWN`` / ``ENOTCONN``). Scoped to the transport boundary, where the
    only ``OSError`` that escapes ``.run(transport="stdio")`` is a disconnect —
    tool-handler errors are caught by the MCP framework and never propagate here.
    """
    if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
        return True
    return isinstance(exc, OSError) and exc.errno in _BENIGN_DISCONNECT_ERRNOS


def _reraise_unless_stdio_disconnect(exc: BaseException) -> None:
    """Swallow a client closing the stdio pipe; re-raise anything else.

    When the MCP client (our parent process) closes stdout/stdin — on restart,
    session reload, or graceful shutdown — the anyio stdio transport surfaces a
    disconnect error, usually wrapped in a ``BaseExceptionGroup`` by the
    transport's TaskGroup. That is a normal shutdown signal, not a crash: the
    server should exit cleanly instead of dumping an ``ExceptionGroup``
    traceback and leaving the parent to see "endpoint unreachable".

    Only the benign disconnect leaves are stripped. If the group also carries a
    genuine error (or a cancellation / ``KeyboardInterrupt`` / ``SystemExit``),
    that survivor is re-raised so real failures are never masked; a lone benign
    error returns cleanly. ``BaseExceptionGroup.split`` recurses, so nested
    groups are handled too.
    """
    if isinstance(exc, BaseExceptionGroup):
        _matched, remaining = exc.split(_is_benign_stdio_disconnect)
        if remaining is not None:
            raise remaining
        return
    if _is_benign_stdio_disconnect(exc):
        return
    raise exc


def run_stdio_server() -> None:
    """Entry point used by the CLI. Handles daemon bootstrap transparently."""
    global _cached_bootstrap
    bootstrap = acquire_daemon_role()
    _cached_bootstrap = bootstrap

    _startup_log(
        f"role={bootstrap.role} PID={os.getpid()} "
        f"storage={resolve_storage_root()}"
    )

    if bootstrap.role == "primary" and bootstrap.endpoint is not None:
        # _run_primary migrates AFTER binding the listener (audit H1-residual),
        # so the startup migration is not done here for the primary path.
        _run_primary(bootstrap)
    elif bootstrap.role == "proxy" and bootstrap.client is not None:
        _run_proxy(bootstrap)
    else:
        # Standalone has no listener, so migrate inline before serving.
        _startup_auto_migrate()
        _warn_about_pending_migrations()
        _run_standalone()


def _gate_primary_ready(ready: threading.Event) -> None:
    """Run the startup migration + pending-migration warning, then open the
    ready gate. Shared by normal startup (_run_primary) and failover promotion
    (ProxyRuntime._failover) so a promoted primary never serves writes against a
    possibly-unmigrated store (audit F8)."""
    _startup_auto_migrate()
    _warn_about_pending_migrations()
    ready.set()


def _run_primary(bootstrap: BootstrapResult) -> None:
    endpoint = bootstrap.endpoint
    assert endpoint is not None  # guarded by run_stdio_server
    dispatcher = make_local_dispatcher(scope_locks=DEFAULT_SCOPE_LOCKS)
    ready = threading.Event()

    def _listener_handler(tool: str, kwargs: Mapping[str, Any]) -> Any:
        # Listener thread uses same dispatcher, which holds the shared lock.
        return dispatcher(tool, kwargs)

    # Bind the listener BEFORE the (possibly minutes-long) startup migration so
    # a racing process can reach us and proxy instead of judging our lockfile
    # stale and evicting it mid-migration (audit H1-residual). The listener
    # answers HELLO/ping immediately; tool calls are held behind `ready` until
    # migration finishes, so no proxy observes a half-migrated store.
    listener = DaemonListener(
        endpoint,
        _listener_handler,
        ready_event=ready,
    )
    listener.start()
    workspace: WorkspaceRuntime | None = None
    try:
        workspace = WorkspaceRuntime.start()
        _gate_primary_ready(ready)
        build_server(dispatcher, workspace=workspace).run(transport="stdio")
    except BaseException as exc:
        _reraise_unless_stdio_disconnect(exc)
    finally:
        _stop_primary(listener, endpoint, workspace)


# How long shutdown waits for a running background index batch. The daemon
# lock is released only after it, so it bounds how long a successor waits.
REFRESHER_SHUTDOWN_TIMEOUT_SECONDS = 30.0


def _stop_primary(listener: DaemonListener, endpoint: DaemonEndpoint, workspace: WorkspaceRuntime | None) -> None:
    """Stop serving, finish the background batch, then hand the lock over.

    Another process may become primary as soon as the lock is released; it
    must not write the same project while this process's batch still runs.
    """
    try:
        listener.stop()
    finally:
        try:
            DEFAULT_REFRESHER.shutdown(timeout=REFRESHER_SHUTDOWN_TIMEOUT_SECONDS)
        finally:
            try:
                release_daemon_lock(endpoint)
            finally:
                if workspace is not None:
                    workspace.shutdown()


def _run_proxy(bootstrap: BootstrapResult) -> None:
    client = bootstrap.client
    assert client is not None
    runtime = ProxyRuntime(client)
    workspace: WorkspaceRuntime | None = None
    try:
        workspace = WorkspaceRuntime.start()
        build_server(runtime, workspace=workspace).run(transport="stdio")
    except BaseException as exc:
        _reraise_unless_stdio_disconnect(exc)
    finally:
        try:
            runtime.shutdown()
        finally:
            _shutdown_background(workspace)


def _run_standalone() -> None:
    dispatcher = make_local_dispatcher()
    workspace: WorkspaceRuntime | None = None
    try:
        workspace = WorkspaceRuntime.start()
        build_server(dispatcher, workspace=workspace).run(transport="stdio")
    except BaseException as exc:
        _reraise_unless_stdio_disconnect(exc)
    finally:
        _shutdown_background(workspace)


def _shutdown_background(workspace: WorkspaceRuntime | None) -> None:
    try:
        DEFAULT_REFRESHER.shutdown(timeout=REFRESHER_SHUTDOWN_TIMEOUT_SECONDS)
    finally:
        if workspace is not None:
            workspace.shutdown()


# ---------------------------------------------------------------------------
# Tool implementations (unchanged)
# ---------------------------------------------------------------------------


def server_info_impl(
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    project, store = build_runtime_context(cwd=cwd, environ=environ)
    storage_stats = collect_storage_stats(store)
    index_status = collect_index_status(store, storage_stats=storage_stats)
    usage_stats = build_usage_snapshot(
        store,
        project_id=project.project_id,
        project_name=project.project_name,
        environ=environ,
    )
    migrations = _collect_migrations_status(store)
    orphaned_buckets = detect_orphaned_buckets(store.storage_root)
    return build_server_info_payload(
        storage_root=str(store.storage_root),
        current_project=build_current_project_payload(project),
        storage_stats=storage_stats,
        index_status=index_status,
        usage_stats=usage_stats,
        migrations=migrations,
        orphaned_buckets=orphaned_buckets,
    )


def _collect_migrations_status(store: Any) -> dict[str, Any]:
    """Detailed per-subsystem migration state for server_info responses.

    Lets agents decide on session start whether they should ask the user to
    run `turbo-memory-mcp migrate --apply`. Always returns a dict so clients
    can rely on the field being present; on detection failure the dict
    carries `pending=False` so callers can branch on a single flag.
    """
    try:
        from .migrations import detect_status

        statuses = detect_status(store)
        items = []
        any_pending = False
        for status in statuses.values():
            entry = {
                "subsystem": status.subsystem.value,
                "current_version": int(status.current_version),
                "latest_version": int(status.latest_version),
                "pending": status.needs_upgrade,
                "step_count": len(status.pending),
            }
            if status.error:
                entry["error"] = status.error
            if status.needs_upgrade:
                any_pending = True
            items.append(entry)
        result: dict[str, Any] = {"pending": any_pending, "subsystems": items}
        if any_pending:
            result["hint"] = (
                "Stop all MCP clients, then run "
                "`turbo-memory-mcp migrate --apply` to upgrade. A rolling "
                "snapshot is taken automatically; on failure the CLI prints "
                "the exact `--restore-from` command."
            )
        return result
    except Exception as exc:
        return {"pending": False, "subsystems": [], "error": str(exc)}


def self_test_impl(
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    project, store = build_runtime_context(cwd=cwd, environ=environ)
    payload = build_self_test_payload(
        storage_root=str(store.storage_root),
        current_project=build_current_project_payload(project),
    )
    try:
        read_probe = probe_read_path(store)
    except Exception as exc:
        # Advisory: a probe that itself breaks is not evidence of a broken index.
        payload["read_probe"] = {"state": "unavailable", "error": format_probe_error(exc)}
        return payload
    payload["read_probe"] = read_probe
    if any(scope_probe["state"] == "failed" for scope_probe in read_probe.values()):
        payload["status"] = "degraded"
    return payload


# Entity schemes that are hierarchical (authority-based) and therefore MUST use
# the '<scheme>://<rest>' form. This is exactly where agents typo 'note:abc' for
# 'note://abc'. Other schemes (mailto:, urn:, tel:, ...) are accepted in the
# bare '<scheme>:<rest>' form per RFC 3986.
_HIERARCHICAL_ENTITY_SCHEMES = frozenset({"note", "file", "issue", "task", "http", "https"})


def _is_entity_uri(value: str) -> bool:
    """True if `value` is a well-formed entity URI. Caller must pre-strip."""
    scheme, sep, rest = value.partition(":")
    if not (
        sep
        and rest
        and scheme
        and scheme[0].isalpha()
        and all(ch.isalnum() or ch in "+.-" for ch in scheme)
    ):
        return False
    if scheme.lower() in _HIERARCHICAL_ENTITY_SCHEMES:
        # These require the '//' authority separator; 'note:abc' is a typo.
        return rest.startswith("//") and len(rest) > 2
    return True


def _validate_entity_uri(uri: str, *, field: str) -> str:
    text = uri.strip()
    if not _is_entity_uri(text):
        raise ValueError(
            f"Invalid {field} {uri!r}: entity URIs must be '<scheme>://<rest>' for "
            "note/file/issue/task/http(s) (e.g. note://<note_id>, file://<relative/path>, "
            "issue://BUG-404) — did you forget the '//'?"
        )
    return text


def link_entities_impl(
    source_uri: str,
    target_uri: str,
    relation_type: str,
    *,
    scope: str = "project",
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    resolved_scope = _normalize_scope(scope)
    if resolved_scope not in (PROJECT_SCOPE, GLOBAL_SCOPE):
        raise ValueError(f"Unsupported scope: {scope}")
    if not relation_type.strip():
        raise ValueError("link_entities requires a non-empty relation_type.")
    source_clean = _validate_entity_uri(source_uri, field="source_uri")
    target_clean = _validate_entity_uri(target_uri, field="target_uri")

    _, store = build_runtime_context(cwd=cwd, environ=environ)
    relation = store.add_relation(
        source=source_clean,
        target=target_clean,
        relation_type=relation_type.strip(),
        scope=resolved_scope,
    )
    
    return {
        "action": "linked",
        "relation": relation,
        "scope": resolved_scope,
    }


def unlink_entities_impl(
    source_uri: str,
    target_uri: str,
    *,
    relation_type: str | None = None,
    scope: str = "project",
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    resolved_scope = _normalize_scope(scope)
    if resolved_scope not in (PROJECT_SCOPE, GLOBAL_SCOPE):
        raise ValueError(f"Unsupported scope: {scope}")
    if not source_uri.strip():
        raise ValueError("unlink_entities requires a non-empty source_uri.")
    if not target_uri.strip():
        raise ValueError("unlink_entities requires a non-empty target_uri.")
        
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    changed = store.remove_relation(
        source=source_uri.strip(),
        target=target_uri.strip(),
        relation_type=relation_type.strip() if relation_type else None,
        scope=resolved_scope,
    )
    
    return {
        "action": "unlinked",
        "changed": changed,
        "scope": resolved_scope,
    }


def get_related_entities_impl(
    uri: str,
    *,
    relation_type: str | None = None,
    scope: str = "hybrid",
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not uri.strip():
        raise ValueError("get_related_entities requires a non-empty uri.")
        
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    relations = store.get_relations_for_entity(
        uri=uri.strip(),
        relation_type=relation_type.strip() if relation_type else None,
        scope=scope,
    )
    
    return {
        "uri": uri.strip(),
        "relations": relations,
        "scope": scope,
    }


# Write-time similarity surfacing thresholds. A near-duplicate (>= DUP) of the
# SAME kind is offered as a supersede candidate; merely related notes (>= RELATED)
# are surfaced for the agent to check for contradiction/overlap. The server never
# auto-deprecates — it only surfaces candidates; the calling agent (an LLM) judges.
_DUPLICATE_SCORE_THRESHOLD = 0.88
_RELATED_SCORE_THRESHOLD = 0.78
_SIMILARITY_HINT_LIMIT = 4


def _build_similarity_hints(store: MemoryStore, note: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Best-effort write-time hints: pre-existing project notes highly similar to
    the one just written. Lets the agent decide whether the new note SUPERSEDES an
    old one (near-duplicate) or CONTRADICTS it (related). Any failure degrades to an
    empty list and never blocks the write."""
    try:
        index = RetrievalIndex(store)
        text = f"{note.get('title', '')}\n{note.get('content', '')}"
        neighbours = index.find_similar(
            text,
            PROJECT_SCOPE,
            limit=_SIMILARITY_HINT_LIMIT,
            exclude_item_id=str(note.get("note_id", "")),
        )
    except Exception:
        return []

    new_kind = str(note.get("note_kind", ""))
    hints: list[dict[str, Any]] = []
    for neighbour in neighbours:
        # The project index also holds markdown doc blocks; only surface notes,
        # otherwise a hint could point deprecate_note at a non-note item_id.
        if neighbour.get("source_kind") != NOTE_SOURCE_KIND:
            continue
        score = float(neighbour.get("score", 0.0))
        if score < _RELATED_SCORE_THRESHOLD:
            continue
        same_kind = bool(new_kind) and neighbour.get("note_kind") == new_kind
        if score >= _DUPLICATE_SCORE_THRESHOLD and same_kind:
            suggestion = "supersede_candidate"
            hint = (
                "Near-duplicate of an existing note. If this note replaces it, link a "
                "'supersedes' relation and deprecate_note(old, replacement_note_id=new)."
            )
        else:
            suggestion = "review_for_conflict"
            hint = (
                "Closely related existing note. Check for contradiction or overlap "
                "and reconcile if they disagree."
            )
        hints.append(
            {
                "item_id": neighbour.get("item_id"),
                "title": neighbour.get("title"),
                "note_kind": neighbour.get("note_kind"),
                "score": score,
                "suggestion": suggestion,
                "hint": hint,
            }
        )
    return hints


def remember_note_impl(
    title: str,
    content: str,
    *,
    kind: str,
    tags: list[str] | None = None,
    source_refs: list[str] | None = None,
    scope: str = "project",
    provenance: str = "agent",
    tier: str | None = None,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
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
    resolved_tier: str | None = None
    if tier is not None:
        resolved_tier = tier.strip().lower()
        if resolved_tier not in NOTE_TIERS:
            supported = ", ".join(NOTE_TIERS)
            raise ValueError(f"remember_note tier must be one of: {supported}.")

    _, store = build_runtime_context(cwd=cwd, environ=environ)
    note = store.write_project_note(
        title, content, note_kind=resolved_kind, tags=tags,
        source_refs=source_refs, provenance=provenance, tier=resolved_tier,
    )
    warning = _sync_with_warning(lambda: _sync_project_note_change(store, str(note["note_id"])))
    payload = build_note_write_payload(
        note,
        source_path=str(store.note_source_path(note)),
        action="stored",
        content_preview=build_content_preview(note["content"]),
        warning=warning,
    )
    similar_notes = _build_similarity_hints(store, note)
    if similar_notes:
        payload["similar_notes"] = similar_notes

    note_uri = f"note://{note['note_id']}"
    # Auto-link any source_refs that are already entity URIs, so the knowledge
    # graph fills in from the intent the caller already expressed. Best-effort:
    # a linking failure never fails the note write (the note is already stored).
    linked_refs: list[dict[str, str]] = []
    seen_targets: set[str] = set()
    refs = [source_refs] if isinstance(source_refs, str) else (source_refs or [])
    for ref in refs:
        candidate = str(ref).strip()
        if not candidate or candidate == note_uri or candidate in seen_targets:
            continue  # skip blanks, self-loops, and duplicate refs
        if not _is_entity_uri(candidate):
            continue
        seen_targets.add(candidate)
        try:
            linked_refs.append(
                store.add_relation(
                    source=note_uri,
                    target=candidate,
                    relation_type="references",
                    scope=resolved_scope,
                )
            )
        except Exception:
            pass
    if linked_refs:
        payload["linked_refs"] = linked_refs

    hints: list[str] = []
    if not tags:
        hints.append(
            "No tags — add 2-3 lowercase tags (tags=[...]) so this note is filterable, "
            "not only reachable by semantic_search."
        )
    if not linked_refs:
        hints.append(
            f'Connect this note in the knowledge graph: link_entities(source_uri="{note_uri}", '
            'target_uri="file://…|note://…|issue://…", '
            'relation_type="implements|fixes|references|supersedes"). '
            "Or pass source_refs as URIs to auto-link."
        )
    if hints:
        payload["hints"] = hints
    return payload


def promote_note_impl(
    note_id: str,
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
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
) -> dict[str, Any]:
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
    tier_filter: Sequence[str] | None = None,
    source_filter: str | None = None,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    resolved_scope = scope.strip().lower()
    index_refresh: dict[str, Any] | None = None
    if resolved_scope in {PROJECT_SCOPE, "hybrid"}:
        index_refresh = _refresh_project_for_read(store, cwd=cwd, environ=environ)
        RetrievalIndex(store).heal_null_list_columns(PROJECT_SCOPE)
    if resolved_scope in {GLOBAL_SCOPE, "hybrid"}:
        # The only global write a search makes; see _GLOBAL_WRITE_TOOLS.
        with DEFAULT_SCOPE_LOCKS.hold(
            [GLOBAL_LOCK_KEY], timeout=_resolve_dispatch_lock_timeout(), tool="semantic_search"
        ):
            _refresh_global_retrieval_if_needed(store)
            RetrievalIndex(store).heal_null_list_columns(GLOBAL_SCOPE)
    payload = semantic_search(
        store, query, scope=scope, limit=limit, tier_filter=tier_filter,
        source_filter=source_filter, sync_empty_project=False,
    )
    if index_refresh is not None:
        payload["index_refresh"] = index_refresh
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
) -> dict[str, Any]:
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    index_refresh: dict[str, Any] | None = None
    if scope.strip().lower() == PROJECT_SCOPE:
        index_refresh = _refresh_project_for_read(store, cwd=cwd, environ=environ)
    payload = hydrate(store, item_id, scope=scope, mode=mode)
    if index_refresh is not None:
        payload["index_refresh"] = index_refresh
    record_hydration_usage(
        store,
        project_id=store.project.project_id,
        project_name=store.project.project_name,
        response_payload=payload,
    )
    return payload


def recent_context_impl(
    *,
    scope: str = DEFAULT_QUERY_MODE,
    limit: int = 10,
    tier_filter: Sequence[str] | None = None,
    format: str = "json",
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | str:
    """Query-free session bootstrap: most-recently-updated notes, newest first.

    Reads canonical note JSON directly (no embedding, no vector search), so it
    is deterministic and cheap. Includes every tier by default — crucially the
    `episodic` tier, so session `handoff` notes surface here even though a plain
    semantic_search hides them. This closes the cold-start gap: a fresh session
    can recover "where did I leave off" without guessing a query.

    format="text" returns the same window as compact plain text (see
    render_recent_context_text); format="hook" wraps that text as SessionStart
    hook output for a Claude Code mcp_tool hook.
    """
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    resolved_scope = scope.strip().lower()
    if resolved_scope not in {PROJECT_SCOPE, GLOBAL_SCOPE, "hybrid"}:
        raise ValueError(f"Unsupported scope: {scope}")
    resolved_format = format.strip().lower()
    if resolved_format not in RECENT_CONTEXT_FORMATS:
        raise ValueError(f"Unsupported format: {format}")

    allowed_tiers: set[str] | None = None
    resolved_tier_filter: list[str] | None = None
    if tier_filter is not None:
        normalized = [str(t).strip().lower() for t in tier_filter]
        if not normalized:
            raise ValueError("tier_filter must be None or a non-empty sequence.")
        unknown = [t for t in normalized if t not in NOTE_TIERS]
        if unknown:
            raise ValueError(f"Unknown tier(s) in tier_filter: {unknown}")
        allowed_tiers = set(normalized)
        resolved_tier_filter = normalized

    normalized_limit = max(1, min(int(limit), MAX_SEMANTIC_LIMIT))

    def _collect_notes(read_scope: str) -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        for note in store.list_notes(read_scope):
            note_tier = str(note.get("tier") or "")
            if allowed_tiers is not None and note_tier and note_tier not in allowed_tiers:
                continue
            notes.append(note)
        # Newest first; note_id as a stable tiebreaker for equal timestamps.
        notes.sort(
            key=lambda n: (str(n.get("updated_at", "")), str(n.get("note_id", ""))),
            reverse=True,
        )
        return notes

    ordered: list[tuple[str, dict[str, Any]]]
    if resolved_scope == "hybrid":
        # Session-bootstrap contract (consensus 2026-08-23): hybrid answers
        # "where did I leave off HERE", so project notes fill the window first
        # (newest first); global notes only backfill leftover slots. The merge
        # is deliberately NOT re-sorted by recency — pure cross-scope recency
        # let fresher other-project promotions evict this project's own
        # handoff from every slot (measured live failure).
        project_notes = _collect_notes(PROJECT_SCOPE)
        selected = [
            (PROJECT_SCOPE, note) for note in project_notes[:normalized_limit]
        ]
        taken_ids = {str(note.get("note_id", "")) for _, note in selected}
        for note in _collect_notes(GLOBAL_SCOPE):
            if len(selected) >= normalized_limit:
                break
            promoted_from = note.get("promoted_from") or {}
            # A promoted copy shares the original's note_id. Suppress it only
            # when this project's original already occupies a slot; promotions
            # of OTHER projects and born-global notes pass through.
            if str(promoted_from.get("note_id", "")) in taken_ids:
                continue
            taken_ids.add(str(note.get("note_id", "")))
            selected.append((GLOBAL_SCOPE, note))
        ordered = selected
    else:
        ordered = [(resolved_scope, note) for note in _collect_notes(resolved_scope)]

    items: list[dict[str, object]] = []
    for read_scope, note in ordered[:normalized_limit]:
        relations = store.get_relations_for_entity(
            uri=f"note://{note['note_id']}",
            scope="hybrid",
            project_id=note.get("project_id"),
        )
        items.append(
            build_recent_context_item_payload(
                note,
                scope=read_scope,
                source_path=str(store.note_source_path(note)),
                compressed_summary=build_content_preview(str(note.get("content", "")), limit=220),
                relations=relations,
            )
        )

    payload = build_recent_context_payload(
        scope=resolved_scope, items=items, tier_filter=resolved_tier_filter
    )
    if resolved_format == "text":
        return render_recent_context_text(payload)
    if resolved_format == "hook":
        return build_recent_context_hook_payload(render_recent_context_text(payload))
    return payload


def index_paths_impl(
    paths: list[str] | None = None,
    *,
    mode: str = "incremental",
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
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
) -> dict[str, Any]:
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    runtime_cwd = Path(cwd).expanduser().resolve() if cwd is not None else store.project.project_root
    return lint_knowledge_base(store, paths=paths, max_issues=max_issues, cwd=runtime_cwd)


def build_runtime_context(
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[ProjectIdentity, MemoryStore]:
    candidate = resolve_project_identity(cwd=cwd, environ=environ)
    storage_root = resolve_storage_root(environ)
    # Reconcile the pure git/path identity against existing buckets so a repo
    # that gains (or loses) a git remote keeps its original memory bucket
    # instead of silently splitting into a fresh one.
    project = reconcile_project_identity(candidate, storage_root)
    store = MemoryStore(project, storage_root=storage_root)
    return project, store


# ---------------------------------------------------------------------------
# Secrets (Phase 9)
# ---------------------------------------------------------------------------


def _vault_error_hint(exc: Exception) -> str:
    """Build a non-empty, surfaceable hint for an unexpected vault error.

    Guards against the original bug where ``str(InvalidTag()) == ""`` produced
    an opaque, message-less MCP error (DEFECT A backstop).
    """
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def set_secret_impl(
    name: str,
    value: str,
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    project, mem_store = build_runtime_context(cwd=cwd, environ=environ)
    vault = SecretsStore(mem_store.storage_root, project.project_id)
    try:
        vault.set(name, value)
    except MasterKeyUnavailable as exc:
        return build_secret_error_payload(
            name=name,
            project_id=project.project_id,
            code="master_key_unavailable",
            setup_hint=str(exc),
        )
    except VaultDecryptError as exc:
        return build_secret_error_payload(
            name=name,
            project_id=project.project_id,
            code="master_key_mismatch",
            setup_hint=str(exc),
        )
    except Exception as exc:  # backstop: never an empty, opaque MCP error again
        return build_secret_error_payload(
            name=name,
            project_id=project.project_id,
            code="vault_error",
            setup_hint=_vault_error_hint(exc),
        )
    AuditLog(vault.secrets_dir).record("set", name)
    return build_set_secret_payload(name=name, project_id=project.project_id)


def get_secret_impl(
    name: str,
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    project, mem_store = build_runtime_context(cwd=cwd, environ=environ)
    vault = SecretsStore(mem_store.storage_root, project.project_id)
    try:
        value = vault.get(name)
    except MasterKeyUnavailable as exc:
        return build_secret_error_payload(
            name=name,
            project_id=project.project_id,
            code="master_key_unavailable",
            setup_hint=str(exc),
        )
    except VaultDecryptError as exc:
        return build_secret_error_payload(
            name=name,
            project_id=project.project_id,
            code="master_key_mismatch",
            setup_hint=str(exc),
        )
    except Exception as exc:  # backstop: never an empty, opaque MCP error again
        return build_secret_error_payload(
            name=name,
            project_id=project.project_id,
            code="vault_error",
            setup_hint=_vault_error_hint(exc),
        )
    if vault.secrets_dir.is_dir():
        AuditLog(vault.secrets_dir).record("get", name)
    if value is None:
        return build_get_secret_missing_payload(
            name=name, project_id=project.project_id
        )
    return build_get_secret_payload(
        name=name, project_id=project.project_id, secret_value=value
    )


def list_secrets_impl(
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    project, mem_store = build_runtime_context(cwd=cwd, environ=environ)
    vault = SecretsStore(mem_store.storage_root, project.project_id)
    try:
        names = vault.list_names()
    except MasterKeyUnavailable as exc:
        return build_secret_error_payload(
            name="*",
            project_id=project.project_id,
            code="master_key_unavailable",
            setup_hint=str(exc),
        )
    except VaultDecryptError as exc:
        return build_secret_error_payload(
            name="*",
            project_id=project.project_id,
            code="master_key_mismatch",
            setup_hint=str(exc),
        )
    except Exception as exc:  # backstop: never an empty, opaque MCP error again
        return build_secret_error_payload(
            name="*",
            project_id=project.project_id,
            code="vault_error",
            setup_hint=_vault_error_hint(exc),
        )
    if vault.secrets_dir.is_dir():
        AuditLog(vault.secrets_dir).record("list", "*")
    return build_list_secrets_payload(
        names=names, project_id=project.project_id
    )


def delete_secret_impl(
    name: str,
    *,
    cwd: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    project, mem_store = build_runtime_context(cwd=cwd, environ=environ)
    vault = SecretsStore(mem_store.storage_root, project.project_id)
    try:
        deleted = vault.delete(name)
    except MasterKeyUnavailable as exc:
        return build_secret_error_payload(
            name=name,
            project_id=project.project_id,
            code="master_key_unavailable",
            setup_hint=str(exc),
        )
    except VaultDecryptError as exc:
        return build_secret_error_payload(
            name=name,
            project_id=project.project_id,
            code="master_key_mismatch",
            setup_hint=str(exc),
        )
    except Exception as exc:  # backstop: never an empty, opaque MCP error again
        return build_secret_error_payload(
            name=name,
            project_id=project.project_id,
            code="vault_error",
            setup_hint=_vault_error_hint(exc),
        )
    if vault.secrets_dir.is_dir():
        AuditLog(vault.secrets_dir).record("delete", name)
    return build_delete_secret_payload(
        name=name, project_id=project.project_id, deleted=deleted
    )


def build_current_project_payload(project: ProjectIdentity) -> dict[str, Any]:
    return {
        "project_id": project.project_id,
        "project_name": project.project_name,
        "project_root": str(project.project_root),
        "identity_kind": project.identity_kind,
    }


def collect_storage_stats(store: MemoryStore) -> dict[str, Any]:
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
            "quarantined_notes": store.scan_quarantined_notes(PROJECT_SCOPE),
        },
        "global": {
            "note_count": snapshot["global_note_count"],
            "total_note_count": snapshot["global_total_note_count"],
            "inactive_note_count": snapshot["global_inactive_note_count"],
            "archived_note_count": snapshot["global_archived_note_count"],
            "superseded_note_count": snapshot["global_superseded_note_count"],
            "retrieval_row_count": retrieval_index.count_rows(GLOBAL_SCOPE),
            "quarantined_notes": store.scan_quarantined_notes(GLOBAL_SCOPE),
        },
    }


def collect_index_status(
    store: MemoryStore,
    *,
    storage_stats: Mapping[str, object] | None = None,
) -> dict[str, Any]:
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


# Search-time refresh budget: what one search or hydrate does inline before it
# answers. The rest goes to DEFAULT_REFRESHER, in batches under the project
# lock. Overridable per deployment; unparseable or non-positive values fall
# back to the defaults.
INLINE_REFRESH_FILES = 25
INLINE_REFRESH_ITEMS = 500
ENV_INLINE_REFRESH_FILES = "TQMEMORY_INLINE_REFRESH_FILES"
ENV_INLINE_REFRESH_ITEMS = "TQMEMORY_INLINE_REFRESH_ITEMS"


def _inline_refresh_budget(environ: Mapping[str, str] | None, name: str, default: int) -> int:
    env = os.environ if environ is None else environ
    try:
        value = int(str(env.get(name, "")).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def refresh_project_batch(
    store: MemoryStore, *, max_files: int | None, max_items: int | None
) -> RefreshProgress:
    """Bring the project index up to date, doing at most the given work.

    ``None`` budgets mean "everything". With budgets, the markdown pass parses
    at most ``max_files`` changed files and the mirror repair embeds at most
    ``max_items`` rows; the rest is reported for a later batch. A markdown or
    retrieval format change still rebuilds in one go: those are rare, and a
    full markdown pass cannot be split (it ignores the manifests).
    """

    bounded = max_files is not None or max_items is not None
    remaining_files = 0
    root = store.project.project_root
    markdown_roots = store.list_markdown_roots()
    markdown_manifest = store.read_markdown_manifest()
    if markdown_roots and (
        markdown_manifest is None or int(markdown_manifest.get("format_version", 0)) != MARKDOWN_FORMAT_VERSION
    ):
        try:
            _, sync_plan = index_paths_with_sync_plan(store, mode="full", cwd=root)
        except FileNotFoundError:
            pass
        else:
            _apply_project_index_sync_plan(store, sync_plan, bounded=bounded)
    elif assess_project_index_freshness(store, cwd=root)["needs_reindex"]:
        try:
            payload, sync_plan = index_paths_with_sync_plan(
                store, mode="incremental", cwd=root, max_changed_files=max_files
            )
        except FileNotFoundError:
            pass
        else:
            remaining_files = int(str(payload.get("remaining_files", 0)))
            _apply_project_index_sync_plan(store, sync_plan, bounded=bounded)

    if _project_retrieval_requires_rebuild(store):
        _rebuild_scope_index_for_format_change(store, PROJECT_SCOPE)

    # Notes-only projects never enter the markdown branch above, so drift
    # repair must also run here: catches added/removed notes AND on-disk
    # content edits (updated_at drift) before they surface as stale results.
    index = RetrievalIndex(store)
    remaining_items = 0
    try:
        embedded, still_pending = _mirror_pending_blocks(store, index, max_items=max_items)
        repair_budget = None if max_items is None else max(max_items - embedded, 0)
        remaining_items = len(still_pending) + _repair_project_retrieval_if_needed(
            store, index, max_items=repair_budget, skip_block_ids=still_pending
        )
    except Exception as exc:
        _rebuild_scope_index_after_error(index, PROJECT_SCOPE, exc)
    return RefreshProgress(remaining_files=remaining_files, remaining_items=remaining_items)


def _run_refresh_batch(
    cwd: str | None, environ: Mapping[str, str] | None, max_files: int, max_items: int
) -> RefreshProgress:
    _, store = build_runtime_context(cwd=cwd, environ=environ)
    return refresh_project_batch(store, max_files=max_files, max_items=max_items)


DEFAULT_REFRESHER = IndexRefresher(DEFAULT_SCOPE_LOCKS, _run_refresh_batch)


def _refresh_project_for_read(
    store: MemoryStore, *, cwd: Path | str | None, environ: Mapping[str, str] | None
) -> dict[str, Any] | None:
    """Refresh inline within budget; hand the rest to the background worker.

    Returns the ``index_refresh`` status while work is pending, else None.
    While a background job runs for the project, a read does no refresh of
    its own and only reports the job.
    """

    project_id = store.project.project_id
    if DEFAULT_REFRESHER.is_active(project_id):
        return DEFAULT_REFRESHER.status(project_id)
    progress = refresh_project_batch(
        store,
        max_files=_inline_refresh_budget(environ, ENV_INLINE_REFRESH_FILES, INLINE_REFRESH_FILES),
        max_items=_inline_refresh_budget(environ, ENV_INLINE_REFRESH_ITEMS, INLINE_REFRESH_ITEMS),
    )
    if not progress.pending:
        return None
    DEFAULT_REFRESHER.schedule(
        project_id,
        cwd=str(cwd) if cwd is not None else None,
        environ=environ,
        progress=progress,
    )
    return DEFAULT_REFRESHER.status(project_id) or {
        "state": "refreshing",
        "pending_files": progress.remaining_files,
        "pending_items": progress.remaining_items,
    }


def _refresh_project_markdown_if_stale(store: MemoryStore) -> None:
    freshness = assess_project_index_freshness(store, cwd=store.project.project_root)
    if not freshness["needs_reindex"]:
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
    refresh_project_batch(store, max_files=None, max_items=None)


def _refresh_global_retrieval_if_needed(store: MemoryStore) -> None:
    if _global_retrieval_requires_rebuild(store):
        _rebuild_scope_index_for_format_change(store, GLOBAL_SCOPE)

    index = RetrievalIndex(store)
    try:
        _repair_global_retrieval_if_needed(store, index)
    except Exception as exc:
        _rebuild_scope_index_after_error(index, GLOBAL_SCOPE, exc)


def _apply_project_index_sync_plan(
    store: MemoryStore, sync_plan: Mapping[str, Any], *, bounded: bool = False
) -> None:
    """Mirror one index run's block changes.

    ``bounded`` (a budgeted refresh) never falls back to a full sync of an
    empty mirror and skips the repair: the caller runs a budgeted repair.
    """
    index = RetrievalIndex(store)
    delete_block_ids = [str(item_id) for item_id in sync_plan.get("delete_block_ids", [])]

    try:
        if not bounded and index.count_rows(PROJECT_SCOPE) == 0:
            if store.has_markdown_blocks() or store.list_notes(PROJECT_SCOPE):
                _full_project_sync(store, index)
            return

        if delete_block_ids:
            index.delete_items(PROJECT_SCOPE, delete_block_ids)
        # The changed blocks are in the manifests' pending lists; a bounded
        # caller mirrors them within its budget.
        if not bounded:
            _mirror_pending_blocks(store, index, max_items=None)
            _repair_project_retrieval_if_needed(store, index)
    except Exception as exc:
        _rebuild_scope_index_after_error(index, PROJECT_SCOPE, exc)


def _sync_project_note_change(store: MemoryStore, note_id: str) -> None:
    index = RetrievalIndex(store)
    try:
        if index.count_rows(PROJECT_SCOPE) == 0:
            index.sync_project()
            return
        index.sync_project_notes([note_id])
        _repair_project_retrieval_if_needed(store, index)
    except Exception as exc:
        _rebuild_scope_index_after_error(index, PROJECT_SCOPE, exc)


def _sync_global_note_change(store: MemoryStore, note_id: str) -> None:
    index = RetrievalIndex(store)
    try:
        if index.count_rows(GLOBAL_SCOPE) == 0:
            index.sync_global()
            return
        index.sync_global_notes([note_id])
        _repair_global_retrieval_if_needed(store, index)
    except Exception as exc:
        _rebuild_scope_index_after_error(index, GLOBAL_SCOPE, exc)


def _remove_retrieval_note(store: MemoryStore, scope: str, note_id: str) -> None:
    index = RetrievalIndex(store)
    try:
        index.delete_items(scope, [note_id])
        if scope == PROJECT_SCOPE:
            _repair_project_retrieval_if_needed(store, index)
        else:
            _repair_global_retrieval_if_needed(store, index)
    except Exception as exc:
        _rebuild_scope_index_after_error(index, scope, exc)


def _rebuild_scope_index_after_error(index: RetrievalIndex, scope: str, exc: BaseException) -> None:
    """Log and full-rebuild a retrieval scope after an incremental update failed.

    The incremental Lance merge/delete paths can fail under spill pressure; we
    rebuild the derived mirror rather than surface an indexing error to the
    caller. Logging keeps that expensive fallback from being silent (audit M2/M4).
    """
    print(
        f"[tqmemory] incremental {scope} index update failed "
        f"({type(exc).__name__}: {exc}); full re-sync",
        file=sys.stderr,
    )
    if scope == GLOBAL_SCOPE:
        index.sync_global()
    else:
        _full_project_sync(index.store, index)


def _rebuild_scope_index_for_format_change(store: MemoryStore, scope: str) -> None:
    """Full re-embed when a scope's retrieval manifest is missing or its
    format_version changed (the lazy post-upgrade rebuild).

    Logged so the first search in a project that was dormant during an upgrade
    is not a silent multi-minute re-embed (audit M4 / Logic-Analysis note).
    """
    print(
        f"[tqmemory] {scope} retrieval index out of date "
        f"(missing/changed format_version); full re-embed",
        file=sys.stderr,
    )
    index = RetrievalIndex(store)
    if scope == GLOBAL_SCOPE:
        index.sync_global()
    else:
        _full_project_sync(store, index)


def _full_project_sync(store: MemoryStore, index: RetrievalIndex) -> None:
    """Mirror the whole project, then clear what ingestion left pending."""
    index.sync_project()
    for manifest in store.list_markdown_file_manifests():
        if manifest.get("mirror_pending_block_ids"):
            store.write_markdown_file_manifest({**manifest, "mirror_pending_block_ids": []})


def _mirror_pending_blocks(
    store: MemoryStore, index: RetrievalIndex, *, max_items: int | None
) -> tuple[int, set[str]]:
    """Mirror the blocks ingestion recorded as pending, at most ``max_items``.

    Each manifest is rewritten with what is still pending once its batch is
    in the mirror, so a run that dies midway resumes where it stopped and a
    huge file is split across batches. Returns how many rows were embedded
    and the block ids still pending.
    """
    embedded = 0
    still_pending: set[str] = set()
    for manifest in store.list_markdown_file_manifests():
        pending = [str(block_id) for block_id in manifest.get("mirror_pending_block_ids", [])]
        if not pending:
            continue
        room = len(pending) if max_items is None else max(max_items - embedded, 0)
        batch, rest = pending[:room], pending[room:]
        if batch:
            index.sync_project_blocks(batch)
            embedded += len(batch)
            store.write_markdown_file_manifest({**manifest, "mirror_pending_block_ids": rest})
        still_pending.update(rest)
    return embedded, still_pending


def _repair_project_retrieval_if_needed(
    store: MemoryStore,
    index: RetrievalIndex,
    *,
    max_items: int | None = None,
    skip_block_ids: set[str] | frozenset[str] = frozenset(),
) -> int:
    """Reconcile the project index against the store, cheaply.

    The old contract here was ``count != expected -> sync_project()``, i.e. a
    full O(corpus) re-embed under the dispatch lock on any drift (audit M4).
    Instead, diff the id sets: delete stale rows and re-embed ONLY the missing
    ones. Cheaper, and strictly more correct — it also catches the case where
    the count happens to match but the membership differs.

    Notes are additionally compared by ``updated_at``: an on-disk edit to an
    existing note_id leaves the id set unchanged but the mirror row stale
    (embedded from the pre-edit text). Blocks skip this — markdown content
    changes are already caught by the checksum-based staleness path.

    ``max_items`` bounds how many rows are embedded (notes first, then
    blocks); stale rows are always deleted. Blocks in ``skip_block_ids`` are
    still pending from ingestion and counted there instead. Returns how many
    missing or edited rows are left for a later call.
    """
    notes = store.list_notes(PROJECT_SCOPE)
    expected_note_ids = {str(note["note_id"]) for note in notes}
    expected_block_ids = set(store.list_markdown_block_ids())
    expected_ids = expected_note_ids | expected_block_ids
    mirror_updated = index.existing_item_updated_at(PROJECT_SCOPE)
    existing_ids = set(mirror_updated)
    changed_notes = {
        str(note["note_id"])
        for note in notes
        if str(note["note_id"]) in existing_ids
        and str(note.get("updated_at") or "") != mirror_updated[str(note["note_id"])]
    }
    if existing_ids == expected_ids and not changed_notes:
        return 0

    stale_ids = existing_ids - expected_ids
    missing_notes = sorted((expected_note_ids - existing_ids) | changed_notes)
    missing_blocks = sorted(expected_block_ids - existing_ids - skip_block_ids)
    if not (stale_ids or missing_notes or missing_blocks):
        return 0
    print(
        f"[tqmemory] project retrieval drift (have {len(existing_ids)}, expected "
        f"{len(expected_ids)}); reconciling by id "
        f"+{len(missing_notes) + len(missing_blocks)}/-{len(stale_ids)}"
        + (f" (~{len(changed_notes)} edited)" if changed_notes else ""),
        file=sys.stderr,
    )
    if stale_ids:
        index.delete_items(PROJECT_SCOPE, sorted(stale_ids))
    pending = len(missing_notes) + len(missing_blocks)
    if max_items is not None:
        missing_notes = missing_notes[:max_items]
        missing_blocks = missing_blocks[: max(max_items - len(missing_notes), 0)]
    if missing_notes:
        index.sync_project_notes(missing_notes)
    if missing_blocks:
        index.sync_project_blocks(missing_blocks)
    return pending - len(missing_notes) - len(missing_blocks)


def _repair_global_retrieval_if_needed(store: MemoryStore, index: RetrievalIndex) -> None:
    """Reconcile the global index against the store (see project variant)."""
    notes = store.list_notes(GLOBAL_SCOPE)
    expected_ids = {str(note["note_id"]) for note in notes}
    mirror_updated = index.existing_item_updated_at(GLOBAL_SCOPE)
    existing_ids = set(mirror_updated)
    changed_ids = {
        str(note["note_id"])
        for note in notes
        if str(note["note_id"]) in existing_ids
        and str(note.get("updated_at") or "") != mirror_updated[str(note["note_id"])]
    }
    if existing_ids == expected_ids and not changed_ids:
        return

    stale_ids = existing_ids - expected_ids
    missing_ids = (expected_ids - existing_ids) | changed_ids
    print(
        f"[tqmemory] global retrieval drift (have {len(existing_ids)}, expected "
        f"{len(expected_ids)}); reconciling by id "
        f"+{len(missing_ids)}/-{len(stale_ids)}"
        + (f" (~{len(changed_ids)} edited)" if changed_ids else ""),
        file=sys.stderr,
    )
    if stale_ids:
        index.delete_items(GLOBAL_SCOPE, sorted(stale_ids))
    if missing_ids:
        index.sync_global_notes(sorted(missing_ids))


def _project_retrieval_requires_rebuild(store: MemoryStore) -> bool:
    has_project_content = store.has_markdown_blocks() or bool(store.list_notes(PROJECT_SCOPE))
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
        try:
            if source_kind == NOTE_SOURCE_KIND:
                note = store.read_note(str(item["item_id"]), scope)
                total += len(str(note["content"]).encode("utf-8"))
                continue
            if source_kind == MARKDOWN_SOURCE_KIND and item.get("block_id"):
                block = store.read_markdown_block(str(item["block_id"]))
                total += len(str(block["content_raw"]).encode("utf-8"))
        except (OSError, ValueError, KeyError) as exc:
            # Telemetry byte-count must never break the (already successful)
            # search/hydrate it measures — an unreadable source counts as 0.
            print(
                f"[tqmemory] telemetry byte-count skipped an unreadable source: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
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
) -> dict[str, Any]:
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


def _cached_storage_snapshot(store: MemoryStore) -> dict[str, Any]:
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
        try:
            with item_path.open("r", encoding="utf-8") as handle:
                payloads.append(json.load(handle))
        except (OSError, ValueError) as exc:
            print(
                f"[tqmemory] skipping unreadable json {item_path}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
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
    "DISPATCH_LOCK_TIMEOUT_SECONDS",
    "ENV_DISPATCH_LOCK_TIMEOUT",
    "GLOBAL_SCOPE_TOOLS",
    "PRODUCT_NAME",
    "SERVER_ID",
    "TOOL_HANDLERS",
    "Dispatcher",
    "MCPServer",
    "ProxyRuntime",
    "build_content_preview",
    "build_current_project_payload",
    "build_runtime_context",
    "build_server",
    "collect_index_status",
    "collect_storage_stats",
    "deprecate_note_impl",
    "hydrate_impl",
    "index_paths_impl",
    "lint_knowledge_base_impl",
    "lock_keys_for",
    "make_local_dispatcher",
    "make_proxy_dispatcher",
    "promote_note_impl",
    "recent_context_impl",
    "remember_note_impl",
    "run_stdio_server",
    "self_test_impl",
    "semantic_search_impl",
    "server_info_impl",
]
