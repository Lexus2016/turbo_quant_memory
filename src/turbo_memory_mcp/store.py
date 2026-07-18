"""Central namespace storage primitives for project and global memory."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, Mapping
from uuid import uuid4

from . import __version__
from .identity import ProjectIdentity, _ensure_safe_id

ENV_STORAGE_HOME = "TQMEMORY_HOME"
DEFAULT_STORAGE_DIRNAME = ".turbo-quant-memory"
PROJECT_SCOPE = "project"
GLOBAL_SCOPE = "global"
NOTE_SOURCE_KIND = "memory_note"
MARKDOWN_SOURCE_KIND = "markdown"
NOTE_KINDS = ("decision", "lesson", "handoff", "pattern")
DEFAULT_NOTE_KIND = "lesson"
ACTIVE_NOTE_STATUS = "active"
ARCHIVED_NOTE_STATUS = "archived"
SUPERSEDED_NOTE_STATUS = "superseded"
NOTE_STATUSES = (
    ACTIVE_NOTE_STATUS,
    ARCHIVED_NOTE_STATUS,
    SUPERSEDED_NOTE_STATUS,
)
# Phase 2: knowledge tier separation. `durable` = long-lived knowledge
# (decisions / patterns / lessons); `episodic` = session handoffs and
# summaries (excluded from default search); `reference` = indexed
# markdown blocks (always durable in nature).
NOTE_TIER_DURABLE = "durable"
NOTE_TIER_EPISODIC = "episodic"
NOTE_TIER_REFERENCE = "reference"
NOTE_TIERS = (NOTE_TIER_DURABLE, NOTE_TIER_EPISODIC, NOTE_TIER_REFERENCE)
DEFAULT_SEARCH_TIERS = (NOTE_TIER_DURABLE, NOTE_TIER_REFERENCE)
# User-flagged memory: who created this note. `human-explicit` = the user
# explicitly ordered it remembered; `agent` = the agent wrote it on its own
# initiative. Used to rank human-flagged knowledge above agent guesses.
NOTE_PROVENANCE_HUMAN = "human-explicit"
NOTE_PROVENANCE_AGENT = "agent"
NOTE_PROVENANCES = (NOTE_PROVENANCE_HUMAN, NOTE_PROVENANCE_AGENT)
DEFAULT_PROVENANCE = NOTE_PROVENANCE_AGENT
MARKDOWN_FORMAT_VERSION = 1
RETRIEVAL_FORMAT_VERSION = 4
USAGE_STATS_FORMAT_VERSION = 2
NOTES_FORMAT_VERSION = 2
SECRETS_FORMAT_VERSION = 2
# RETRIEVAL is at 4 (post multilingual re-embed; v3 was Phase 3 BM25 FTS index).
# NOTES ships at 2: a fresh install stamps 2 (nothing to migrate) while a
# pre-Phase-2 layout is detected by the ABSENCE of format_version
# (runner._legacy_v1_or_format_version), NOT by this constant — and
# write_project_manifest never advances the on-disk version
# (_notes_manifest_format_version), so a legacy manifest write cannot skip the
# v1->v2 tier reclass. SECRETS ships at 2: v1 is the "subsystem exists but no
# per-project vaults provisioned yet" baseline; ensure_layout stamps the v2
# secrets-manifest marker on a genuinely fresh storage root so it reports no
# phantom pending migration, while a legacy root (buckets but no marker) still
# runs the provisioning migration.


def tier_for_kind(note_kind: str | None) -> str:
    """Map a note kind to its default tier. `handoff` -> episodic, else durable."""
    kind = (note_kind or "").strip().lower()
    if kind == "handoff":
        return NOTE_TIER_EPISODIC
    return NOTE_TIER_DURABLE


class MemoryStore:
    """Filesystem-backed namespace store for project and global notes."""

    def __init__(
        self,
        project: ProjectIdentity,
        storage_root: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.project = project
        self.storage_root = storage_root or resolve_storage_root(environ)

    def project_dir(self, project_id: str | None = None) -> Path:
        resolved_project_id = _ensure_safe_id(project_id or self.project.project_id, field="project_id")
        return self.storage_root / "projects" / resolved_project_id

    def project_notes_dir(self, project_id: str | None = None) -> Path:
        return self.project_dir(project_id) / "notes"

    def project_manifest_path(self, project_id: str | None = None) -> Path:
        return self.project_dir(project_id) / "manifest.json"

    def project_note_path(self, note_id: str, project_id: str | None = None) -> Path:
        return self.project_notes_dir(project_id) / f"{_ensure_safe_id(note_id, field='note_id')}.json"

    def project_markdown_dir(self, project_id: str | None = None) -> Path:
        return self.project_dir(project_id) / "markdown"

    def project_markdown_manifest_path(self, project_id: str | None = None) -> Path:
        return self.project_markdown_dir(project_id) / "manifest.json"

    def project_markdown_roots_dir(self, project_id: str | None = None) -> Path:
        return self.project_markdown_dir(project_id) / "roots"

    def project_markdown_root_path(self, root_id: str, project_id: str | None = None) -> Path:
        return self.project_markdown_roots_dir(project_id) / f"{_ensure_safe_id(root_id, field='root_id')}.json"

    def project_markdown_files_dir(self, project_id: str | None = None) -> Path:
        return self.project_markdown_dir(project_id) / "files"

    def project_markdown_file_path(self, file_key: str, project_id: str | None = None) -> Path:
        return self.project_markdown_files_dir(project_id) / f"{_ensure_safe_id(file_key, field='file_key')}.json"

    def project_markdown_blocks_dir(self, project_id: str | None = None) -> Path:
        return self.project_markdown_dir(project_id) / "blocks"

    def project_markdown_block_path(self, block_id: str, project_id: str | None = None) -> Path:
        return self.project_markdown_blocks_dir(project_id) / f"{_ensure_safe_id(block_id, field='block_id')}.json"

    def project_retrieval_dir(self, project_id: str | None = None) -> Path:
        return self.project_dir(project_id) / "retrieval"

    def project_retrieval_manifest_path(self, project_id: str | None = None) -> Path:
        return self.project_retrieval_dir(project_id) / "manifest.json"

    def global_dir(self) -> Path:
        return self.storage_root / "global"

    def global_notes_dir(self) -> Path:
        return self.global_dir() / "notes"

    def global_manifest_path(self) -> Path:
        return self.global_dir() / "manifest.json"

    def global_note_path(self, note_id: str) -> Path:
        return self.global_notes_dir() / f"{_ensure_safe_id(note_id, field='note_id')}.json"

    def global_retrieval_dir(self) -> Path:
        return self.global_dir() / "retrieval"

    def global_retrieval_manifest_path(self) -> Path:
        return self.global_retrieval_dir() / "manifest.json"

    def telemetry_dir(self) -> Path:
        return self.storage_root / "telemetry"

    def usage_stats_path(self) -> Path:
        return self.telemetry_dir() / "usage.json"

    def secrets_manifest_path(self) -> Path:
        """Subsystem-level marker for the SECRETS migration chain.

        Sits at storage_root and tracks ``format_version`` for the whole
        secrets subsystem. Per-project ``secrets/meta.json`` files have
        their own ``version`` field and are independent of this manifest.
        """
        return self.storage_root / "secrets-manifest.json"

    def read_secrets_manifest(self) -> dict[str, Any] | None:
        return _read_json_if_exists(self.secrets_manifest_path())

    def ensure_layout(self) -> None:
        self._ensure_fresh_secrets_marker()
        self.project_notes_dir().mkdir(parents=True, exist_ok=True)
        self.global_notes_dir().mkdir(parents=True, exist_ok=True)

    def _ensure_fresh_secrets_marker(self) -> None:
        """Stamp the v2 secrets manifest on a genuinely fresh storage root so a
        new install does not report a phantom SECRETS pending migration (M#1).

        Only writes when the marker is absent AND no project bucket exists yet.
        A root that already has buckets but no marker is a pre-v0.7 install; the
        marker stays absent so the SECRETS v1->v2 provisioning migration runs.
        """
        marker = self.secrets_manifest_path()
        if marker.exists():
            return
        projects_root = self.storage_root / "projects"
        try:
            has_buckets = projects_root.is_dir() and any(projects_root.iterdir())
        except OSError:
            has_buckets = True  # can't tell -> be conservative, don't stamp
        if has_buckets:
            return
        _write_json_atomic(
            marker,
            {"format_version": SECRETS_FORMAT_VERSION, "updated_at": utc_now()},
        )

    def ensure_markdown_layout(self, project_id: str | None = None) -> None:
        self.project_markdown_roots_dir(project_id).mkdir(parents=True, exist_ok=True)
        self.project_markdown_files_dir(project_id).mkdir(parents=True, exist_ok=True)
        self.project_markdown_blocks_dir(project_id).mkdir(parents=True, exist_ok=True)

    def ensure_retrieval_layout(self, project_id: str | None = None) -> None:
        self.project_retrieval_dir(project_id).mkdir(parents=True, exist_ok=True)
        self.global_retrieval_dir().mkdir(parents=True, exist_ok=True)

    def ensure_telemetry_layout(self) -> None:
        self.telemetry_dir().mkdir(parents=True, exist_ok=True)

    def write_project_manifest(self) -> dict[str, Any]:
        self.ensure_layout()
        existing = _read_json_if_exists_safe(self.project_manifest_path(), label="project manifest") or {}
        # Preserve the on-disk version and never advance it here — the
        # migration runner owns bumps. Stamp the current schema only for a
        # genuinely empty/new notes layout, so a fresh install reports no
        # phantom pending migration and a legacy write cannot skip the reclass.
        format_version = self._notes_manifest_format_version(existing, self.project_notes_dir())
        # Accumulate every identity source ever resolved to this bucket. A
        # later remote-add (or -remove) then keeps pinning the same id via
        # reconcile_project_identity instead of minting a new bucket, and the
        # set is a transparent on-disk record of how the project has been
        # addressed. Lazy: seed from a legacy single ``identity_source`` when
        # the list field is absent, so v2 manifests converge without a
        # migration (same approach as the provenance field).
        prior_sources = existing.get("identity_sources")
        if not prior_sources:
            legacy = existing.get("identity_source")
            prior_sources = [legacy] if legacy else []
        identity_sources = sorted({*prior_sources, self.project.identity_source})
        manifest = {
            "scope": PROJECT_SCOPE,
            **self.project.as_dict(),
            "identity_sources": identity_sources,
            "format_version": format_version,
            "updated_at": utc_now(),
        }
        _write_json_atomic(self.project_manifest_path(), manifest)
        return manifest

    def read_project_manifest(self, project_id: str | None = None) -> dict[str, Any] | None:
        return _read_json_if_exists(self.project_manifest_path(project_id))

    def write_global_manifest(self) -> dict[str, Any]:
        self.ensure_layout()
        existing = _read_json_if_exists_safe(self.global_manifest_path(), label="global manifest") or {}
        format_version = self._notes_manifest_format_version(existing, self.global_notes_dir())
        manifest = {
            "scope": GLOBAL_SCOPE,
            "storage_root": str(self.storage_root),
            "format_version": format_version,
            "updated_at": utc_now(),
        }
        _write_json_atomic(self.global_manifest_path(), manifest)
        return manifest

    def read_global_manifest(self) -> dict[str, Any] | None:
        return _read_json_if_exists(self.global_manifest_path())

    def _notes_manifest_format_version(self, existing: Mapping[str, Any], notes_dir: Path) -> int:
        """Format-version to stamp on a notes manifest write (audit M#1).

        Never advances the on-disk version — the migration runner owns bumps;
        advancing here would make a manifest write skip the v1->v2 tier reclass
        for a legacy install. A brand-new layout (no versioned manifest and no
        notes on disk) starts at the current schema so it reports no phantom
        pending migration; a pre-Phase-2 layout (notes exist without a versioned
        manifest) reports v1 so the reclass runs.
        """
        on_disk = existing.get("format_version")
        if on_disk is not None:
            try:
                return int(on_disk)
            except (TypeError, ValueError):
                pass  # invalid version -> treat like missing (records -> v1)
        return 1 if _dir_has_records(notes_dir) else NOTES_FORMAT_VERSION

    def write_markdown_manifest(self, project_id: str | None = None) -> dict[str, Any]:
        resolved_project_id = project_id or self.project.project_id
        self.ensure_markdown_layout(resolved_project_id)
        existing = self.read_markdown_manifest(resolved_project_id) or {}
        format_version = max(int(existing.get("format_version", 0)), MARKDOWN_FORMAT_VERSION)
        manifest = {
            "scope": PROJECT_SCOPE,
            "project_id": resolved_project_id,
            "source_kind": MARKDOWN_SOURCE_KIND,
            "format_version": format_version,
            "package_version": __version__,
            "updated_at": utc_now(),
        }
        _write_json_atomic(self.project_markdown_manifest_path(resolved_project_id), manifest)
        return manifest

    def read_markdown_manifest(self, project_id: str | None = None) -> dict[str, Any] | None:
        return _read_json_if_exists(self.project_markdown_manifest_path(project_id))

    def write_project_retrieval_manifest(self, project_id: str | None = None) -> dict[str, Any]:
        resolved_project_id = project_id or self.project.project_id
        self.ensure_retrieval_layout(resolved_project_id)
        existing = self.read_project_retrieval_manifest(resolved_project_id) or {}
        format_version = max(int(existing.get("format_version", 0)), RETRIEVAL_FORMAT_VERSION)
        manifest = {
            "scope": PROJECT_SCOPE,
            "project_id": resolved_project_id,
            "source_kind": "retrieval",
            "format_version": format_version,
            "package_version": __version__,
            "updated_at": utc_now(),
        }
        _write_json_atomic(self.project_retrieval_manifest_path(resolved_project_id), manifest)
        return manifest

    def read_project_retrieval_manifest(self, project_id: str | None = None) -> dict[str, Any] | None:
        return _read_json_if_exists(self.project_retrieval_manifest_path(project_id))

    def write_global_retrieval_manifest(self) -> dict[str, Any]:
        self.ensure_retrieval_layout()
        existing = self.read_global_retrieval_manifest() or {}
        format_version = max(int(existing.get("format_version", 0)), RETRIEVAL_FORMAT_VERSION)
        manifest = {
            "scope": GLOBAL_SCOPE,
            "source_kind": "retrieval",
            "format_version": format_version,
            "package_version": __version__,
            "updated_at": utc_now(),
        }
        _write_json_atomic(self.global_retrieval_manifest_path(), manifest)
        return manifest

    def read_global_retrieval_manifest(self) -> dict[str, Any] | None:
        return _read_json_if_exists(self.global_retrieval_manifest_path())

    def write_usage_stats(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.ensure_telemetry_layout()
        record = dict(payload)
        _write_json_atomic(self.usage_stats_path(), record)
        return record

    def read_usage_stats(self) -> dict[str, Any] | None:
        return _read_json_if_exists(self.usage_stats_path())

    def write_project_note(
        self,
        title: str,
        content: str,
        *,
        note_kind: str | None = None,
        tags: Iterable[str] | None = None,
        source_refs: Iterable[str] | None = None,
        note_id: str | None = None,
        created_at: str | None = None,
        promoted_from: dict[str, Any] | None = None,
        tier: str | None = None,
        provenance: str | None = None,
    ) -> dict[str, Any]:
        note = self._build_note_record(
            scope=PROJECT_SCOPE,
            title=title,
            content=content,
            note_kind=note_kind,
            tags=tags,
            source_refs=source_refs,
            note_id=note_id,
            created_at=created_at,
            promoted_from=promoted_from,
            tier=tier,
            provenance=provenance,
        )
        self.write_project_manifest()
        _write_json_atomic(self.project_note_path(note["note_id"]), note)
        return note

    def write_global_note(
        self,
        title: str,
        content: str,
        *,
        note_kind: str | None = None,
        tags: Iterable[str] | None = None,
        source_refs: Iterable[str] | None = None,
        note_id: str | None = None,
        created_at: str | None = None,
        project_id: str | None = None,
        project_name: str | None = None,
        promoted_from: dict[str, Any] | None = None,
        tier: str | None = None,
        provenance: str | None = None,
    ) -> dict[str, Any]:
        note = self._build_note_record(
            scope=GLOBAL_SCOPE,
            title=title,
            content=content,
            note_kind=note_kind,
            tags=tags,
            source_refs=source_refs,
            note_id=note_id,
            created_at=created_at,
            project_id=project_id,
            project_name=project_name,
            promoted_from=promoted_from,
            tier=tier,
            provenance=provenance,
        )
        self.write_global_manifest()
        _write_json_atomic(self.global_note_path(note["note_id"]), note)
        return note

    def read_project_note(self, note_id: str, project_id: str | None = None) -> dict[str, Any]:
        return self._normalize_note_record(_read_json(self.project_note_path(note_id, project_id)))

    def read_global_note(self, note_id: str) -> dict[str, Any]:
        return self._normalize_note_record(_read_json(self.global_note_path(note_id)))

    def read_note(self, note_id: str, scope: str) -> dict[str, Any]:
        if scope == PROJECT_SCOPE:
            return self.read_project_note(note_id)
        if scope == GLOBAL_SCOPE:
            return self.read_global_note(note_id)
        raise ValueError(f"Unsupported scope: {scope}")

    def _notes_dir_for_scope(self, scope: str) -> Path:
        if scope == PROJECT_SCOPE:
            return self.project_notes_dir()
        if scope == GLOBAL_SCOPE:
            return self.global_notes_dir()
        raise ValueError(f"Unsupported scope: {scope}")

    def _try_load_note_record(self, path: Path) -> tuple[dict[str, Any] | None, str | None]:
        """Parse and normalize a single note file.

        Returns ``(record, None)`` on success, or ``(None, reason)`` when the
        file is unreadable or malformed. This isolates one corrupt note so it
        cannot break a whole scan and every tool that depends on it (audit H3).
        """
        try:
            return self._normalize_note_record(_read_json(path)), None
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def list_notes(self, scope: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        note_dir = self._notes_dir_for_scope(scope)
        if not note_dir.exists():
            return []
        notes: list[dict[str, Any]] = []
        for path in sorted(note_dir.glob("*.json")):
            record, reason = self._try_load_note_record(path)
            if record is None:
                print(f"[tqmemory] skipping unreadable note {path}: {reason}", file=sys.stderr)
                continue
            notes.append(record)
        if include_inactive:
            return notes
        return [note for note in notes if note["note_status"] == ACTIVE_NOTE_STATUS]

    def scan_quarantined_notes(self, scope: str) -> list[dict[str, str]]:
        """List note files in a scope that fail to parse, for diagnostics.

        Pairs with ``list_notes`` skip-with-warning so corruption that would
        otherwise be silent can be surfaced (e.g. in ``server_info``). Does not
        warn or raise.
        """
        note_dir = self._notes_dir_for_scope(scope)
        if not note_dir.exists():
            return []
        quarantined: list[dict[str, str]] = []
        for path in sorted(note_dir.glob("*.json")):
            _record, reason = self._try_load_note_record(path)
            if reason is not None:
                quarantined.append({"path": str(path), "reason": reason})
        return quarantined

    def _load_json_records_skipping_corrupt(self, directory: Path, *, label: str) -> list[dict[str, Any]]:
        """Read every ``*.json`` in a derived-cache dir, skipping unreadable files.

        Mirrors ``list_notes`` quarantine for the markdown caches: one corrupt
        block / file-manifest / root JSON must not raise out of semantic_search /
        hydrate / server_info and take down all retrieval for the whole project.
        """
        records: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                records.append(_read_json(path))
            except (OSError, ValueError, TypeError) as exc:
                print(
                    f"[tqmemory] skipping unreadable {label} {path}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
        return records

    def note_source_path(self, note: Mapping[str, Any]) -> Path:
        note_id = str(note["note_id"])
        scope = str(note["scope"])
        if scope == GLOBAL_SCOPE:
            return self.global_note_path(note_id)
        return self.project_note_path(note_id, str(note["project_id"]))

    def promote_note(self, note_id: str) -> dict[str, Any]:
        project_note = self.read_project_note(note_id)
        if project_note["note_status"] != ACTIVE_NOTE_STATUS:
            raise ValueError("Only active project notes can be promoted.")
        promoted_from = {
            "scope": PROJECT_SCOPE,
            "project_id": project_note["project_id"],
            "project_name": project_note["project_name"],
            "note_id": project_note["note_id"],
            "source_path": str(self.project_note_path(note_id, project_note["project_id"])),
        }
        return self.write_global_note(
            title=project_note["title"],
            content=project_note["content"],
            note_kind=project_note["note_kind"],
            tags=project_note.get("tags", []),
            source_refs=project_note.get("source_refs", []),
            note_id=project_note["note_id"],
            created_at=project_note.get("created_at"),
            project_id=project_note["project_id"],
            project_name=project_note["project_name"],
            promoted_from=promoted_from,
            tier=project_note.get("tier"),
            provenance=project_note.get("provenance"),
        )

    def deprecate_note(
        self,
        note_id: str,
        *,
        scope: str,
        replacement_note_id: str | None = None,
        replacement_scope: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        note = self.read_note(note_id, scope)
        if note["note_status"] != ACTIVE_NOTE_STATUS:
            raise ValueError("Only active notes can be deprecated.")

        replacement_reference: dict[str, Any] | None = None
        next_status = ARCHIVED_NOTE_STATUS
        resolved_reason = (reason or "").strip() or None

        if replacement_note_id is not None:
            resolved_replacement_id = replacement_note_id.strip()
            if not resolved_replacement_id:
                raise ValueError("replacement_note_id must be non-empty when provided.")
            resolved_replacement_scope = (replacement_scope or scope).strip().lower()
            replacement_note = self.read_note(resolved_replacement_id, resolved_replacement_scope)
            if replacement_note["note_status"] != ACTIVE_NOTE_STATUS:
                raise ValueError("Replacement note must be active.")
            if note["note_id"] == replacement_note["note_id"] and scope == resolved_replacement_scope:
                raise ValueError("A note cannot supersede itself.")
            replacement_reference = {
                "scope": replacement_note["scope"],
                "project_id": replacement_note["project_id"],
                "project_name": replacement_note["project_name"],
                "note_id": replacement_note["note_id"],
                "title": replacement_note["title"],
                "source_path": str(self.note_source_path(replacement_note)),
            }
            next_status = SUPERSEDED_NOTE_STATUS

        updated_note = dict(note)
        updated_note["note_status"] = next_status
        updated_note["deprecated_at"] = utc_now()
        updated_note["updated_at"] = updated_note["deprecated_at"]
        if resolved_reason is not None:
            updated_note["deprecation_reason"] = resolved_reason
        else:
            updated_note.pop("deprecation_reason", None)
        if replacement_reference is not None:
            updated_note["superseded_by"] = replacement_reference
        else:
            updated_note.pop("superseded_by", None)

        self._write_note_record(updated_note)
        return self._normalize_note_record(updated_note)

    def write_markdown_root(self, root_record: Mapping[str, Any]) -> dict[str, Any]:
        record = {
            "root_id": str(root_record["root_id"]),
            "scope": PROJECT_SCOPE,
            "project_id": self.project.project_id,
            "path": str(root_record["path"]),
            "path_hash": str(root_record["path_hash"]),
            "registered_at": str(root_record.get("registered_at", utc_now())),
            "updated_at": str(root_record.get("updated_at", utc_now())),
        }
        self.write_markdown_manifest(record["project_id"])
        _write_json_atomic(self.project_markdown_root_path(record["root_id"], record["project_id"]), record)
        return record

    def read_markdown_root(self, root_id: str, project_id: str | None = None) -> dict[str, Any]:
        return _read_json(self.project_markdown_root_path(root_id, project_id))

    def list_markdown_roots(self, project_id: str | None = None) -> list[dict[str, Any]]:
        root_dir = self.project_markdown_roots_dir(project_id)
        if not root_dir.exists():
            return []
        return self._load_json_records_skipping_corrupt(root_dir, label="markdown root")

    def write_markdown_file_manifest(self, manifest_record: Mapping[str, Any]) -> dict[str, Any]:
        record = {
            "root_id": str(manifest_record["root_id"]),
            "source_path": str(manifest_record["source_path"]),
            "file_key": str(manifest_record["file_key"]),
            "size": int(manifest_record["size"]),
            "mtime_ns": int(manifest_record["mtime_ns"]),
            "source_checksum": str(manifest_record["source_checksum"]),
            "block_ids": [str(block_id) for block_id in manifest_record.get("block_ids", [])],
            "indexed_at": str(manifest_record.get("indexed_at", utc_now())),
        }
        self.write_markdown_manifest()
        _write_json_atomic(self.project_markdown_file_path(record["file_key"]), record)
        return record

    def read_markdown_file_manifest(self, file_key: str, project_id: str | None = None) -> dict[str, Any]:
        return _read_json(self.project_markdown_file_path(file_key, project_id))

    def list_markdown_file_manifests(
        self,
        *,
        project_id: str | None = None,
        root_id: str | None = None,
    ) -> list[dict[str, Any]]:
        manifest_dir = self.project_markdown_files_dir(project_id)
        if not manifest_dir.exists():
            return []
        manifests = self._load_json_records_skipping_corrupt(manifest_dir, label="markdown file manifest")
        if root_id is None:
            return manifests
        return [manifest for manifest in manifests if manifest["root_id"] == root_id]

    def delete_markdown_file_manifest(self, file_key: str, project_id: str | None = None) -> None:
        self.project_markdown_file_path(file_key, project_id).unlink(missing_ok=True)
        self.write_markdown_manifest(project_id)

    def write_markdown_block(self, block_record: Mapping[str, Any]) -> dict[str, Any]:
        record = {
            "block_id": str(block_record["block_id"]),
            "scope": PROJECT_SCOPE,
            "project_id": self.project.project_id,
            "source_kind": MARKDOWN_SOURCE_KIND,
            "root_id": str(block_record["root_id"]),
            "source_path": str(block_record["source_path"]),
            "heading_path": [str(heading) for heading in block_record.get("heading_path", [])],
            "chunk_index": int(block_record["chunk_index"]),
            "content_raw": str(block_record["content_raw"]),
            "block_checksum": str(block_record["block_checksum"]),
            "source_checksum": str(block_record["source_checksum"]),
            "updated_at": str(block_record.get("updated_at", utc_now())),
        }
        self.write_markdown_manifest(record["project_id"])
        _write_json_atomic(self.project_markdown_block_path(record["block_id"], record["project_id"]), record)
        return record

    def read_markdown_block(self, block_id: str, project_id: str | None = None) -> dict[str, Any]:
        return _read_json(self.project_markdown_block_path(block_id, project_id))

    def resolve_project_item(self, item_id: str, project_id: str | None = None) -> dict[str, Any]:
        note_path = self.project_note_path(item_id, project_id)
        block_path = self.project_markdown_block_path(item_id, project_id)
        note_exists = note_path.exists()
        block_exists = block_path.exists()

        if note_exists and block_exists:
            raise ValueError(f"Ambiguous project item_id: {item_id}")
        if note_exists:
            return self.read_project_note(item_id, project_id)
        if block_exists:
            return self.read_markdown_block(item_id, project_id)
        raise FileNotFoundError(f"Project item not found: {item_id}")

    def list_markdown_blocks(
        self,
        *,
        project_id: str | None = None,
        root_id: str | None = None,
        source_path: str | None = None,
    ) -> list[dict[str, Any]]:
        block_dir = self.project_markdown_blocks_dir(project_id)
        if not block_dir.exists():
            return []
        blocks = self._load_json_records_skipping_corrupt(block_dir, label="markdown block")
        if root_id is not None:
            blocks = [block for block in blocks if block["root_id"] == root_id]
        if source_path is not None:
            blocks = [block for block in blocks if block["source_path"] == source_path]
        return blocks

    def read_markdown_neighborhood(
        self,
        block_id: str,
        *,
        before: int,
        after: int,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if before < 0 or after < 0:
            raise ValueError("Markdown neighborhood windows must be non-negative.")

        target = self.read_markdown_block(block_id, project_id)
        source_blocks = sorted(
            self.list_markdown_blocks(project_id=project_id, source_path=str(target["source_path"])),
            key=lambda block: (int(block["chunk_index"]), str(block["block_id"])),
        )
        target_index = next(
            (index for index, block in enumerate(source_blocks) if str(block["block_id"]) == str(block_id)),
            None,
        )
        if target_index is None:
            raise FileNotFoundError(f"Markdown neighborhood target not found: {block_id}")

        return {
            "item": target,
            "neighbors_before": source_blocks[max(0, target_index - before) : target_index],
            "neighbors_after": source_blocks[target_index + 1 : target_index + 1 + after],
            "neighbor_window": {
                "before": before,
                "after": after,
            },
        }

    def delete_markdown_block(self, block_id: str, project_id: str | None = None) -> None:
        self.project_markdown_block_path(block_id, project_id).unlink(missing_ok=True)
        self.write_markdown_manifest(project_id)

    def delete_blocks_for_file(
        self,
        root_id: str,
        source_path: str,
        *,
        project_id: str | None = None,
    ) -> list[str]:
        block_ids = [
            str(block["block_id"])
            for block in self.list_markdown_blocks(project_id=project_id, root_id=root_id, source_path=source_path)
        ]
        for block_id in block_ids:
            self.delete_markdown_block(block_id, project_id=project_id)
        return sorted(block_ids)

    def delete_markdown_root_data(
        self,
        root_id: str,
        *,
        project_id: str | None = None,
    ) -> dict[str, list[str]]:
        manifests = self.list_markdown_file_manifests(project_id=project_id, root_id=root_id)
        deleted_file_keys: list[str] = []
        deleted_block_ids: list[str] = []

        for manifest in manifests:
            deleted_file_keys.append(str(manifest["file_key"]))
            deleted_block_ids.extend(
                self.delete_blocks_for_file(
                    root_id,
                    str(manifest["source_path"]),
                    project_id=project_id,
                )
            )
            self.delete_markdown_file_manifest(str(manifest["file_key"]), project_id=project_id)

        self.project_markdown_root_path(root_id, project_id).unlink(missing_ok=True)
        self.write_markdown_manifest(project_id)
        return {
            "file_keys": sorted(deleted_file_keys),
            "block_ids": sorted(set(deleted_block_ids)),
        }

    def replace_blocks_for_file(
        self,
        root_id: str,
        source_path: str,
        block_records: Iterable[Mapping[str, Any]],
        *,
        project_id: str | None = None,
    ) -> list[str]:
        existing_ids = {
            str(block["block_id"])
            for block in self.list_markdown_blocks(project_id=project_id, root_id=root_id, source_path=source_path)
        }
        next_ids: list[str] = []
        for block_record in block_records:
            if str(block_record["root_id"]) != root_id:
                raise ValueError("All block records must share the target root_id.")
            if str(block_record["source_path"]) != source_path:
                raise ValueError("All block records must share the target source_path.")
            block = self.write_markdown_block(block_record)
            next_ids.append(str(block["block_id"]))

        for stale_block_id in sorted(existing_ids.difference(next_ids)):
            self.delete_markdown_block(stale_block_id, project_id=project_id)
        return next_ids

    def _build_note_record(
        self,
        *,
        scope: str,
        title: str,
        content: str,
        note_kind: str | None,
        tags: Iterable[str] | None,
        source_refs: Iterable[str] | None,
        note_id: str | None,
        created_at: str | None,
        project_id: str | None = None,
        project_name: str | None = None,
        promoted_from: dict[str, Any] | None = None,
        tier: str | None = None,
        provenance: str | None = None,
    ) -> dict[str, Any]:
        resolved_created_at = created_at or utc_now()
        resolved_kind = normalize_note_kind(note_kind)
        resolved_tier = tier if tier in NOTE_TIERS else tier_for_kind(resolved_kind)
        resolved_provenance = normalize_provenance(provenance)
        note = {
            "note_id": note_id or generate_note_id(),
            "scope": scope,
            "project_id": project_id or self.project.project_id,
            "project_name": project_name or self.project.project_name,
            "title": title.strip(),
            "content": content.strip(),
            "note_kind": resolved_kind,
            "tier": resolved_tier,
            "provenance": resolved_provenance,
            "tags": list(tags or []),
            "source_refs": list(source_refs or []),
            "source_kind": NOTE_SOURCE_KIND,
            "note_status": ACTIVE_NOTE_STATUS,
            "created_at": resolved_created_at,
            "updated_at": utc_now(),
        }
        if promoted_from:
            note["promoted_from"] = dict(promoted_from)
        return note

    def _normalize_note_record(self, note: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(note)
        payload["note_kind"] = normalize_note_kind(
            payload.get("note_kind") or payload.get("kind"),
        )
        payload["note_status"] = normalize_note_status(payload.get("note_status"))
        payload["provenance"] = normalize_provenance(payload.get("provenance"))
        return payload

    def _write_note_record(self, note: Mapping[str, Any]) -> None:
        scope = str(note["scope"])
        if scope == GLOBAL_SCOPE:
            self.write_global_manifest()
            _write_json_atomic(self.global_note_path(str(note["note_id"])), note)
            return

        self.write_project_manifest()
        _write_json_atomic(self.project_note_path(str(note["note_id"]), str(note["project_id"])), note)

    def project_relations_path(self, project_id: str | None = None) -> Path:
        return self.project_dir(project_id) / "relations.json"

    def global_relations_path(self) -> Path:
        return self.global_dir() / "relations.json"

    def read_relations(self, scope: str = PROJECT_SCOPE, project_id: str | None = None) -> list[dict[str, str]]:
        if scope == GLOBAL_SCOPE:
            path = self.global_relations_path()
        else:
            path = self.project_relations_path(project_id)
        
        data = _read_json_if_exists_safe(path, label="relations")
        if data is None or not isinstance(data, dict) or "relations" not in data:
            return []
        return data["relations"]

    def write_relations(self, relations: list[dict[str, str]], scope: str = PROJECT_SCOPE, project_id: str | None = None) -> None:
        if scope == GLOBAL_SCOPE:
            path = self.global_relations_path()
        else:
            path = self.project_relations_path(project_id)
        
        payload = {
            "format_version": 1,
            "relations": relations,
            "updated_at": utc_now(),
        }
        _write_json_atomic(path, payload)

    def _read_relations_for_write(self, scope: str, project_id: str | None) -> list[dict[str, str]]:
        """Strict read for write paths: a corrupt relations file RAISES rather
        than being silently overwritten with just the new relation (audit X4).
        The read paths (recent_context / search enrichment) use the tolerant
        ``read_relations`` instead.
        """
        if scope == GLOBAL_SCOPE:
            path = self.global_relations_path()
        else:
            path = self.project_relations_path(project_id)
        try:
            data = _read_json_if_exists(path)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"relations file is unreadable and would be overwritten: {path} "
                f"({type(exc).__name__}: {exc}) — fix or remove it first."
            ) from exc
        if data is None or not isinstance(data, dict) or "relations" not in data:
            return []
        return data["relations"]

    def add_relation(self, source: str, target: str, relation_type: str, scope: str = PROJECT_SCOPE, project_id: str | None = None) -> dict[str, str]:
        relations = self._read_relations_for_write(scope, project_id)
        for rel in relations:
            if rel.get("source") == source and rel.get("target") == target and rel.get("type") == relation_type:
                return rel
        
        new_rel = {
            "source": source,
            "target": target,
            "type": relation_type,
            "created_at": utc_now(),
        }
        relations.append(new_rel)
        self.write_relations(relations, scope, project_id)
        return new_rel

    def remove_relation(self, source: str, target: str, relation_type: str | None = None, scope: str = PROJECT_SCOPE, project_id: str | None = None) -> bool:
        relations = self._read_relations_for_write(scope, project_id)
        initial_len = len(relations)
        
        if relation_type is not None:
            relations = [
                rel for rel in relations
                if not (rel.get("source") == source and rel.get("target") == target and rel.get("type") == relation_type)
            ]
        else:
            relations = [
                rel for rel in relations
                if not (rel.get("source") == source and rel.get("target") == target)
            ]
        
        changed = len(relations) < initial_len
        if changed:
            self.write_relations(relations, scope, project_id)
        return changed

    def get_relations_for_entity(self, uri: str, relation_type: str | None = None, scope: str = "hybrid", project_id: str | None = None) -> list[dict[str, str]]:
        all_relations = []
        if scope in (PROJECT_SCOPE, "hybrid"):
            all_relations.extend(self.read_relations(PROJECT_SCOPE, project_id))
        if scope in (GLOBAL_SCOPE, "hybrid"):
            all_relations.extend(self.read_relations(GLOBAL_SCOPE))
            
        filtered = []
        for rel in all_relations:
            if rel.get("source") == uri or rel.get("target") == uri:
                if relation_type is None or rel.get("type") == relation_type:
                    filtered.append(rel)
        return filtered



def reconcile_project_identity(
    candidate: ProjectIdentity,
    storage_root: Path,
) -> ProjectIdentity:
    """Return the canonical identity for a repo, reusing an existing bucket
    instead of minting a new id when a project's identity *source* changes.

    ``resolve_project_identity`` is a pure function of the current git/path
    state, so adding a git remote to a repo that already has path-keyed notes
    flips ``identity_source`` and would mint a brand-new (empty) bucket,
    stranding the existing notes. This is the only storage-aware seam: it reads
    the manifests already on disk and decides whether ``candidate`` should
    adopt an existing bucket.

    Rules (override always wins and is handled first):
      1. A previously-seen identity source (remote or path) pins its bucket.
      2. Otherwise, the same repo root adopts the existing bucket — UNLESS a
         different recorded remote proves a different project reused the path,
         in which case we mint a new bucket (the safety boundary).
      3. Otherwise the candidate is a genuinely new project; mint as-is.

    It never writes; ``MemoryStore.write_project_manifest`` records the
    accumulated sources. Idempotent and side-effect free.
    """
    if candidate.identity_kind == "override":
        return candidate

    projects_root = storage_root / "projects"
    if not projects_root.is_dir():
        return candidate

    source_to_bucket: dict[str, str] = {}
    root_to_bucket: dict[str, tuple[str, str | None]] = {}
    for child in sorted(projects_root.iterdir()):
        if not child.is_dir():
            continue
        manifest = _read_json_if_exists_safe(child / "manifest.json", label="project manifest")
        if not manifest or manifest.get("scope") != PROJECT_SCOPE:
            continue
        project_id = manifest.get("project_id") or child.name
        sources = manifest.get("identity_sources")
        if not sources:
            legacy = manifest.get("identity_source")
            sources = [legacy] if legacy else []
        for source in sources:
            source_to_bucket.setdefault(source, project_id)
        root = manifest.get("project_root")
        if root:
            root_to_bucket.setdefault(str(Path(root)), (project_id, manifest.get("remote_url")))

    pinned = source_to_bucket.get(candidate.identity_source)
    if pinned is not None:
        return replace(candidate, project_id=pinned)

    match = root_to_bucket.get(str(candidate.project_root))
    if match is not None:
        existing_id, existing_remote = match
        remote_conflict = (
            candidate.remote_url is not None
            and existing_remote is not None
            and existing_remote != candidate.remote_url
        )
        if not remote_conflict:
            return replace(candidate, project_id=existing_id)

    return candidate


def detect_orphaned_buckets(storage_root: Path) -> list[dict[str, Any]]:
    """List project buckets whose recorded ``project_root`` no longer exists
    on disk — candidates for pruning, surfaced in ``server_info``.

    Read-only: this never deletes anything. A missing root is NOT proof the
    project is dead — an external/network volume may be unmounted, or the
    storage root may be shared across machines where the path does exist. So
    removal stays a deliberate, assisted action; this only makes dead weight
    visible instead of letting it accumulate silently forever.
    """
    projects_root = storage_root / "projects"
    if not projects_root.is_dir():
        return []

    orphans: list[dict[str, Any]] = []
    for child in sorted(projects_root.iterdir()):
        if not child.is_dir():
            continue
        manifest = _read_json_if_exists_safe(child / "manifest.json", label="project manifest")
        if not manifest or manifest.get("scope") != PROJECT_SCOPE:
            continue
        root = manifest.get("project_root")
        if not root or Path(root).exists():
            continue
        notes_dir = child / "notes"
        try:
            note_count = (
                sum(1 for entry in notes_dir.iterdir() if entry.suffix == ".json")
                if notes_dir.is_dir()
                else 0
            )
        except OSError:
            note_count = 0
        orphans.append(
            {
                "project_id": manifest.get("project_id") or child.name,
                "project_name": manifest.get("project_name"),
                "project_root": str(root),
                "note_count": note_count,
            }
        )
    return orphans


def resolve_storage_root(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(ENV_STORAGE_HOME)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / DEFAULT_STORAGE_DIRNAME


def generate_note_id() -> str:
    return uuid4().hex[:16]



def normalize_note_kind(value: str | None) -> str:
    if value is None or not str(value).strip():
        return DEFAULT_NOTE_KIND

    resolved = str(value).strip().lower()
    if resolved not in NOTE_KINDS:
        supported = ", ".join(NOTE_KINDS)
        raise ValueError(f"Unsupported note kind: {value}. Expected one of: {supported}.")
    return resolved


def normalize_note_status(value: str | None) -> str:
    if value is None or not str(value).strip():
        return ACTIVE_NOTE_STATUS

    resolved = str(value).strip().lower()
    if resolved not in NOTE_STATUSES:
        supported = ", ".join(NOTE_STATUSES)
        raise ValueError(f"Unsupported note status: {value}. Expected one of: {supported}.")
    return resolved


def normalize_provenance(value: str | None) -> str:
    """Normalize a provenance value. Unknown/empty -> DEFAULT_PROVENANCE.

    Unlike normalize_note_kind, this NEVER raises: provenance is advisory
    metadata and legacy notes lack the field entirely, so a missing or
    unrecognized value degrades gracefully to `agent`.
    """
    if value is None or not str(value).strip():
        return DEFAULT_PROVENANCE
    resolved = str(value).strip().lower()
    if resolved not in NOTE_PROVENANCES:
        return DEFAULT_PROVENANCE
    return resolved


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_path(value: str | Path) -> str:
    return sha256_text(str(Path(value).expanduser().resolve()))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dir_has_records(path: Path) -> bool:
    """True if a notes directory holds at least one real ``*.json`` record.

    Ignores partial-write temp files (``.tmp-*.json``) so a crashed write does
    not make a fresh install look like a legacy one.
    """
    if not path.exists():
        return False
    for entry in path.glob("*.json"):
        if entry.name.startswith(".tmp-"):
            continue
        return True
    return False


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path)


def _read_json_if_exists_safe(path: Path, *, label: str = "file") -> dict[str, Any] | None:
    """Tolerant ``_read_json_if_exists``: a corrupt/unreadable file yields
    ``None`` (with a one-line stderr warning) instead of raising, so one bad
    JSON on a read path cannot break a whole tool call (audit X1/X4). Callers
    that must distinguish "missing" from "corrupt" should not use this.
    """
    if not path.exists():
        return None
    try:
        return _read_json(path)
    except (OSError, ValueError, TypeError) as exc:
        print(
            f"[tqmemory] skipping unreadable {label} {path}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


__all__ = [
    "DEFAULT_STORAGE_DIRNAME",
    "ACTIVE_NOTE_STATUS",
    "ARCHIVED_NOTE_STATUS",
    "ENV_STORAGE_HOME",
    "GLOBAL_SCOPE",
    "MARKDOWN_FORMAT_VERSION",
    "MARKDOWN_SOURCE_KIND",
    "MemoryStore",
    "NOTE_SOURCE_KIND",
    "NOTE_PROVENANCE_HUMAN",
    "NOTE_PROVENANCE_AGENT",
    "NOTE_PROVENANCES",
    "DEFAULT_PROVENANCE",
    "NOTE_STATUSES",
    "RETRIEVAL_FORMAT_VERSION",
    "SUPERSEDED_NOTE_STATUS",
    "PROJECT_SCOPE",
    "USAGE_STATS_FORMAT_VERSION",
    "generate_note_id",
    "normalize_note_status",
    "normalize_provenance",
    "resolve_storage_root",
    "sha256_path",
    "sha256_text",
    "utc_now",
]
