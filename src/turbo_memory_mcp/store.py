"""Central namespace storage primitives for project and global memory."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, Mapping
from uuid import uuid4

from . import __version__
from .identity import ProjectIdentity

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
MARKDOWN_FORMAT_VERSION = 1
RETRIEVAL_FORMAT_VERSION = 1
USAGE_STATS_FORMAT_VERSION = 2


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
        resolved_project_id = project_id or self.project.project_id
        return self.storage_root / "projects" / resolved_project_id

    def project_notes_dir(self, project_id: str | None = None) -> Path:
        return self.project_dir(project_id) / "notes"

    def project_manifest_path(self, project_id: str | None = None) -> Path:
        return self.project_dir(project_id) / "manifest.json"

    def project_note_path(self, note_id: str, project_id: str | None = None) -> Path:
        return self.project_notes_dir(project_id) / f"{note_id}.json"

    def project_markdown_dir(self, project_id: str | None = None) -> Path:
        return self.project_dir(project_id) / "markdown"

    def project_markdown_manifest_path(self, project_id: str | None = None) -> Path:
        return self.project_markdown_dir(project_id) / "manifest.json"

    def project_markdown_roots_dir(self, project_id: str | None = None) -> Path:
        return self.project_markdown_dir(project_id) / "roots"

    def project_markdown_root_path(self, root_id: str, project_id: str | None = None) -> Path:
        return self.project_markdown_roots_dir(project_id) / f"{root_id}.json"

    def project_markdown_files_dir(self, project_id: str | None = None) -> Path:
        return self.project_markdown_dir(project_id) / "files"

    def project_markdown_file_path(self, file_key: str, project_id: str | None = None) -> Path:
        return self.project_markdown_files_dir(project_id) / f"{file_key}.json"

    def project_markdown_blocks_dir(self, project_id: str | None = None) -> Path:
        return self.project_markdown_dir(project_id) / "blocks"

    def project_markdown_block_path(self, block_id: str, project_id: str | None = None) -> Path:
        return self.project_markdown_blocks_dir(project_id) / f"{block_id}.json"

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
        return self.global_notes_dir() / f"{note_id}.json"

    def global_retrieval_dir(self) -> Path:
        return self.global_dir() / "retrieval"

    def global_retrieval_manifest_path(self) -> Path:
        return self.global_retrieval_dir() / "manifest.json"

    def telemetry_dir(self) -> Path:
        return self.storage_root / "telemetry"

    def usage_stats_path(self) -> Path:
        return self.telemetry_dir() / "usage.json"

    def ensure_layout(self) -> None:
        self.project_notes_dir().mkdir(parents=True, exist_ok=True)
        self.global_notes_dir().mkdir(parents=True, exist_ok=True)

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
        manifest = {
            "scope": PROJECT_SCOPE,
            **self.project.as_dict(),
            "updated_at": utc_now(),
        }
        _write_json_atomic(self.project_manifest_path(), manifest)
        return manifest

    def read_project_manifest(self, project_id: str | None = None) -> dict[str, Any] | None:
        return _read_json_if_exists(self.project_manifest_path(project_id))

    def write_global_manifest(self) -> dict[str, Any]:
        self.ensure_layout()
        manifest = {
            "scope": GLOBAL_SCOPE,
            "storage_root": str(self.storage_root),
            "updated_at": utc_now(),
        }
        _write_json_atomic(self.global_manifest_path(), manifest)
        return manifest

    def read_global_manifest(self) -> dict[str, Any] | None:
        return _read_json_if_exists(self.global_manifest_path())

    def write_markdown_manifest(self, project_id: str | None = None) -> dict[str, Any]:
        resolved_project_id = project_id or self.project.project_id
        self.ensure_markdown_layout(resolved_project_id)
        manifest = {
            "scope": PROJECT_SCOPE,
            "project_id": resolved_project_id,
            "source_kind": MARKDOWN_SOURCE_KIND,
            "format_version": MARKDOWN_FORMAT_VERSION,
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
        manifest = {
            "scope": PROJECT_SCOPE,
            "project_id": resolved_project_id,
            "source_kind": "retrieval",
            "format_version": RETRIEVAL_FORMAT_VERSION,
            "package_version": __version__,
            "updated_at": utc_now(),
        }
        _write_json_atomic(self.project_retrieval_manifest_path(resolved_project_id), manifest)
        return manifest

    def read_project_retrieval_manifest(self, project_id: str | None = None) -> dict[str, Any] | None:
        return _read_json_if_exists(self.project_retrieval_manifest_path(project_id))

    def write_global_retrieval_manifest(self) -> dict[str, Any]:
        self.ensure_retrieval_layout()
        manifest = {
            "scope": GLOBAL_SCOPE,
            "source_kind": "retrieval",
            "format_version": RETRIEVAL_FORMAT_VERSION,
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

    def list_notes(self, scope: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        if scope == PROJECT_SCOPE:
            note_dir = self.project_notes_dir()
        elif scope == GLOBAL_SCOPE:
            note_dir = self.global_notes_dir()
        else:
            raise ValueError(f"Unsupported scope: {scope}")

        if not note_dir.exists():
            return []
        notes = [self._normalize_note_record(_read_json(path)) for path in sorted(note_dir.glob("*.json"))]
        if include_inactive:
            return notes
        return [note for note in notes if note["note_status"] == ACTIVE_NOTE_STATUS]

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
        return [_read_json(path) for path in sorted(root_dir.glob("*.json"))]

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
        manifests = [_read_json(path) for path in sorted(manifest_dir.glob("*.json"))]
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
        blocks = [_read_json(path) for path in sorted(block_dir.glob("*.json"))]
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
    ) -> dict[str, Any]:
        resolved_created_at = created_at or utc_now()
        note = {
            "note_id": note_id or generate_note_id(),
            "scope": scope,
            "project_id": project_id or self.project.project_id,
            "project_name": project_name or self.project.project_name,
            "title": title.strip(),
            "content": content.strip(),
            "note_kind": normalize_note_kind(note_kind),
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
        return payload

    def _write_note_record(self, note: Mapping[str, Any]) -> None:
        scope = str(note["scope"])
        if scope == GLOBAL_SCOPE:
            self.write_global_manifest()
            _write_json_atomic(self.global_note_path(str(note["note_id"])), note)
            return

        self.write_project_manifest()
        _write_json_atomic(self.project_note_path(str(note["note_id"]), str(note["project_id"])), note)


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


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_path(value: str | Path) -> str:
    return sha256_text(str(Path(value).expanduser().resolve()))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read_json(path)


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
    "NOTE_STATUSES",
    "RETRIEVAL_FORMAT_VERSION",
    "SUPERSEDED_NOTE_STATUS",
    "PROJECT_SCOPE",
    "USAGE_STATS_FORMAT_VERSION",
    "generate_note_id",
    "normalize_note_status",
    "resolve_storage_root",
    "sha256_path",
    "sha256_text",
    "utc_now",
]
