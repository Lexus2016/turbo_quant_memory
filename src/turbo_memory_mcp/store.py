"""Central namespace storage primitives for project and global memory."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .identity import ProjectIdentity

ENV_STORAGE_HOME = "TQMEMORY_HOME"
DEFAULT_STORAGE_DIRNAME = ".turbo-quant-memory"
PROJECT_SCOPE = "project"
GLOBAL_SCOPE = "global"
NOTE_SOURCE_KIND = "memory_note"


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

    def global_dir(self) -> Path:
        return self.storage_root / "global"

    def global_notes_dir(self) -> Path:
        return self.global_dir() / "notes"

    def global_manifest_path(self) -> Path:
        return self.global_dir() / "manifest.json"

    def global_note_path(self, note_id: str) -> Path:
        return self.global_notes_dir() / f"{note_id}.json"

    def ensure_layout(self) -> None:
        self.project_notes_dir().mkdir(parents=True, exist_ok=True)
        self.global_notes_dir().mkdir(parents=True, exist_ok=True)

    def write_project_manifest(self) -> dict[str, Any]:
        self.ensure_layout()
        manifest = {
            "scope": PROJECT_SCOPE,
            **self.project.as_dict(),
            "updated_at": utc_now(),
        }
        _write_json_atomic(self.project_manifest_path(), manifest)
        return manifest

    def write_global_manifest(self) -> dict[str, Any]:
        self.ensure_layout()
        manifest = {
            "scope": GLOBAL_SCOPE,
            "storage_root": str(self.storage_root),
            "updated_at": utc_now(),
        }
        _write_json_atomic(self.global_manifest_path(), manifest)
        return manifest

    def write_project_note(
        self,
        title: str,
        content: str,
        *,
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
        return _read_json(self.project_note_path(note_id, project_id))

    def read_global_note(self, note_id: str) -> dict[str, Any]:
        return _read_json(self.global_note_path(note_id))

    def read_note(self, note_id: str, scope: str) -> dict[str, Any]:
        if scope == PROJECT_SCOPE:
            return self.read_project_note(note_id)
        if scope == GLOBAL_SCOPE:
            return self.read_global_note(note_id)
        raise ValueError(f"Unsupported scope: {scope}")

    def list_notes(self, scope: str) -> list[dict[str, Any]]:
        if scope == PROJECT_SCOPE:
            note_dir = self.project_notes_dir()
        elif scope == GLOBAL_SCOPE:
            note_dir = self.global_notes_dir()
        else:
            raise ValueError(f"Unsupported scope: {scope}")

        if not note_dir.exists():
            return []
        return [_read_json(path) for path in sorted(note_dir.glob("*.json"))]

    def note_source_path(self, note: Mapping[str, Any]) -> Path:
        note_id = str(note["note_id"])
        scope = str(note["scope"])
        if scope == GLOBAL_SCOPE:
            return self.global_note_path(note_id)
        return self.project_note_path(note_id, str(note["project_id"]))

    def promote_note(self, note_id: str) -> dict[str, Any]:
        project_note = self.read_project_note(note_id)
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
            tags=project_note.get("tags", []),
            source_refs=project_note.get("source_refs", []),
            note_id=project_note["note_id"],
            created_at=project_note.get("created_at"),
            project_id=project_note["project_id"],
            project_name=project_note["project_name"],
            promoted_from=promoted_from,
        )

    def _build_note_record(
        self,
        *,
        scope: str,
        title: str,
        content: str,
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
            "tags": list(tags or []),
            "source_refs": list(source_refs or []),
            "source_kind": NOTE_SOURCE_KIND,
            "created_at": resolved_created_at,
            "updated_at": utc_now(),
        }
        if promoted_from:
            note["promoted_from"] = dict(promoted_from)
        return note


def resolve_storage_root(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(ENV_STORAGE_HOME)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / DEFAULT_STORAGE_DIRNAME


def generate_note_id() -> str:
    return uuid4().hex[:16]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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
    "ENV_STORAGE_HOME",
    "GLOBAL_SCOPE",
    "MemoryStore",
    "NOTE_SOURCE_KIND",
    "PROJECT_SCOPE",
    "generate_note_id",
    "resolve_storage_root",
    "utc_now",
]
