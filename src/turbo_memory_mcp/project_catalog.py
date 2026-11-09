"""Local project labels and explicit folder bindings.

The catalog is separate from project manifests: a second folder may use an
existing memory bucket without replacing that bucket's original repository.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .identity import ProjectIdentity, _ensure_safe_id, resolve_project_identity

CATALOG_FILENAME = "project-catalog.json"
CATALOG_VERSION = 1


def _catalog_path(storage_root: Path) -> Path:
    return storage_root / CATALOG_FILENAME


def _read_catalog(storage_root: Path) -> dict[str, Any]:
    path = _catalog_path(storage_root)
    if not path.exists():
        return {"version": CATALOG_VERSION, "labels": {}, "bindings": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Project catalog is unreadable: {path}: {exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != CATALOG_VERSION
        or not isinstance(data.get("labels"), dict)
        or not isinstance(data.get("bindings"), dict)
    ):
        raise ValueError(f"Project catalog has an unsupported or invalid format: {path}")
    for project_id, labels in data["labels"].items():
        if not isinstance(project_id, str) or not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
            raise ValueError(f"Project catalog has invalid labels: {path}")
    for folder, entry in data["bindings"].items():
        if (
            not isinstance(folder, str)
            or not isinstance(entry, dict)
            or not isinstance(entry.get("project_id"), str)
            or not isinstance(entry.get("device"), int)
            or not isinstance(entry.get("inode"), int)
            or (entry.get("remote_source") is not None and not isinstance(entry.get("remote_source"), str))
        ):
            raise ValueError(f"Project catalog has an invalid binding: {path}")
    return data


def _write_catalog(storage_root: Path, data: Mapping[str, Any]) -> None:
    path = _catalog_path(storage_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _project_manifest(storage_root: Path, project_id: str) -> dict[str, Any]:
    safe_id = _ensure_safe_id(project_id, field="project_id")
    path = storage_root / "projects" / safe_id / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"Unknown project_id: {project_id}") from None
    except (OSError, ValueError) as exc:
        raise ValueError(f"Project manifest is unreadable: {path}: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("scope") != "project" or (manifest.get("project_id") or safe_id) != safe_id:
        raise ValueError(f"Project manifest is invalid: {path}")
    return manifest


def list_projects(storage_root: Path) -> list[dict[str, Any]]:
    """List materialized projects, with display labels and folder aliases."""
    catalog = _read_catalog(storage_root)
    projects_root = storage_root / "projects"
    if not projects_root.is_dir():
        return []
    projects: list[dict[str, Any]] = []
    for child in projects_root.iterdir():
        if not child.is_dir() or not (child / "manifest.json").exists():
            continue
        manifest = _project_manifest(storage_root, child.name)
        project_id = child.name
        labels = catalog["labels"].get(project_id, [])
        bindings = sorted(
            path for path, entry in catalog["bindings"].items()
            if isinstance(entry, dict) and entry.get("project_id") == project_id
        )
        projects.append({
            "project_id": project_id,
            "project_name": str(manifest.get("project_name") or project_id),
            "labels": list(labels),
            "project_root": str(manifest.get("project_root") or ""),
            "remote_url": manifest.get("remote_url"),
            "bound_folders": bindings,
        })
    return sorted(projects, key=lambda item: (item["project_name"].casefold(), item["project_id"]))


def set_project_labels(storage_root: Path, project_id: str, labels: Sequence[str]) -> list[str]:
    _project_manifest(storage_root, project_id)
    if isinstance(labels, (str, bytes)) or len(labels) > 20:
        raise ValueError("labels must be a list of at most 20 strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for label in labels:
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40:
            raise ValueError("each label must contain 1-40 characters")
        value = label.strip()
        if value.casefold() not in seen:
            normalized.append(value)
            seen.add(value.casefold())
    catalog = _read_catalog(storage_root)
    if normalized:
        catalog["labels"][project_id] = normalized
    else:
        catalog["labels"].pop(project_id, None)
    _write_catalog(storage_root, catalog)
    return normalized


def resolve_folder(path: str | Path, *, cwd: Path | str | None = None, must_exist: bool = True) -> Path:
    folder = Path(path).expanduser()
    if not folder.is_absolute():
        folder = Path(cwd or Path.cwd()) / folder
    folder = folder.resolve()
    if must_exist and not folder.is_dir():
        raise ValueError(f"Folder does not exist or is not a directory: {folder}")
    return folder


def bind_project(storage_root: Path, project_id: str, path: str | Path, *, cwd: Path | str | None = None) -> Path:
    """Bind one exact folder to an existing bucket; never index it implicitly."""
    _project_manifest(storage_root, project_id)
    folder = resolve_folder(path, cwd=cwd)
    if folder == folder.parent or folder == storage_root or storage_root in folder.parents:
        raise ValueError("Refusing to bind the filesystem or memory storage root")
    stat = folder.stat()
    candidate = resolve_project_identity(cwd=folder, environ={})
    catalog = _read_catalog(storage_root)
    prior = catalog["bindings"].get(str(folder))
    if prior is not None and prior["project_id"] != project_id:
        raise ValueError(
            f"Folder is already bound to project {prior['project_id']}; unbind it before changing projects"
        )
    catalog["bindings"][str(folder)] = {
        "project_id": project_id,
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "remote_source": candidate.identity_source if candidate.identity_kind == "git_remote" else None,
    }
    _write_catalog(storage_root, catalog)
    return folder


def unbind_project(storage_root: Path, path: str | Path, *, cwd: Path | str | None = None) -> bool:
    folder = resolve_folder(path, cwd=cwd, must_exist=False)
    catalog = _read_catalog(storage_root)
    removed = catalog["bindings"].pop(str(folder), None) is not None
    if removed:
        _write_catalog(storage_root, catalog)
    return removed


def bound_identity(
    storage_root: Path, candidate: ProjectIdentity, *, cwd: Path | str | None = None
) -> ProjectIdentity | None:
    """Resolve an exact folder alias, refusing stale or unreadable bindings."""
    if candidate.identity_kind == "override":
        return None
    catalog = _read_catalog(storage_root)
    start = Path(cwd or Path.cwd()).expanduser().resolve()
    for folder in dict.fromkeys((start, candidate.project_root)):
        entry = catalog["bindings"].get(str(folder))
        if entry is None:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("project_id"), str):
            raise TypeError(f"Invalid project binding for {folder}")
        try:
            stat = folder.stat()
        except OSError as exc:
            raise ValueError(f"Bound folder is unavailable: {folder}: {exc}") from exc
        if not folder.is_dir() or stat.st_dev != entry.get("device") or stat.st_ino != entry.get("inode"):
            raise ValueError(f"Bound folder changed since registration: {folder}; rebind it explicitly")
        remote_source = entry.get("remote_source")
        if remote_source is not None and remote_source != candidate.identity_source:
            raise ValueError(f"Bound folder git origin changed: {folder}; rebind it explicitly")
        manifest = _project_manifest(storage_root, entry["project_id"])
        return ProjectIdentity(
            project_id=entry["project_id"],
            project_name=str(manifest.get("project_name") or entry["project_id"]),
            project_root=folder,
            identity_source=str(manifest.get("identity_source") or entry["project_id"]),
            identity_kind="folder_binding",
            remote_url=manifest.get("remote_url"),
        )
    return None


def selected_identity(storage_root: Path, candidate: ProjectIdentity) -> ProjectIdentity:
    """Resolve an existing bucket by ID without using a client folder path.

    Знаходить наявний проєкт за ідентифікатором без прив'язки папки клієнта.
    """
    manifest = _project_manifest(storage_root, candidate.project_id)
    return ProjectIdentity(
        project_id=candidate.project_id,
        project_name=str(manifest.get("project_name") or candidate.project_id),
        project_root=candidate.project_root,
        identity_source=str(manifest.get("identity_source") or candidate.project_id),
        identity_kind="selected_project",
        remote_url=manifest.get("remote_url"),
    )


__all__ = ["bind_project", "bound_identity", "list_projects", "resolve_folder", "selected_identity", "set_project_labels", "unbind_project"]
