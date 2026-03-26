"""Project-scoped Markdown ingestion orchestration for Phase 3."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from .contracts import INDEX_MODES, build_indexing_payload
from .markdown_parser import build_block_id, parse_markdown_blocks
from .store import MemoryStore, sha256_path, sha256_text, utc_now

_SLUG_RE = re.compile(r"[^a-z0-9]+")
DEFAULT_IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".omc",
        ".planning",
        ".pytest_cache",
        ".ruff_cache",
        ".serena",
        ".mypy_cache",
        ".venv",
        "__pycache__",
        "benchmarks",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)


def index_paths(
    store: MemoryStore,
    paths: Sequence[str] | None = None,
    *,
    mode: str = "incremental",
    cwd: Path | str | None = None,
) -> dict[str, object]:
    """Register Markdown roots and index them into the current project store."""

    resolved_mode = mode.strip().lower()
    if resolved_mode not in INDEX_MODES:
        raise ValueError(f"Unsupported indexing mode: {mode}")

    base_dir = Path(cwd or store.project.project_root).expanduser().resolve()
    registered_roots = _resolve_roots(store, paths, base_dir=base_dir)
    if not registered_roots:
        raise ValueError("index_paths requires at least one Markdown root path.")

    indexed_files = 0
    changed_files = 0
    skipped_files = 0
    deleted_files = 0

    for root_record in registered_roots:
        root_id = str(root_record["root_id"])
        root_path = Path(root_record["path"]).expanduser().resolve()
        if not root_path.exists() or not root_path.is_dir():
            raise FileNotFoundError(f"Markdown root does not exist: {root_path}")

        existing_manifests = {
            str(manifest["source_path"]): manifest
            for manifest in store.list_markdown_file_manifests(root_id=root_id)
        }
        seen_source_paths: set[str] = set()

        for file_path in _iter_markdown_files(root_path):
            indexed_files += 1
            source_path = file_path.relative_to(root_path).as_posix()
            seen_source_paths.add(source_path)
            existing_manifest = existing_manifests.get(source_path)

            stat = file_path.stat()
            size = int(stat.st_size)
            mtime_ns = int(stat.st_mtime_ns)

            if (
                resolved_mode == "incremental"
                and existing_manifest is not None
                and int(existing_manifest["size"]) == size
                and int(existing_manifest["mtime_ns"]) == mtime_ns
            ):
                skipped_files += 1
                continue

            source_text = file_path.read_text(encoding="utf-8")
            source_checksum = sha256_text(source_text)
            file_key = build_file_key(root_id, source_path)
            indexed_at = utc_now()

            if (
                resolved_mode == "incremental"
                and existing_manifest is not None
                and str(existing_manifest["source_checksum"]) == source_checksum
            ):
                store.write_markdown_file_manifest(
                    {
                        "root_id": root_id,
                        "source_path": source_path,
                        "file_key": file_key,
                        "size": size,
                        "mtime_ns": mtime_ns,
                        "source_checksum": source_checksum,
                        "block_ids": existing_manifest.get("block_ids", []),
                        "indexed_at": indexed_at,
                    }
                )
                skipped_files += 1
                continue

            parsed_blocks = parse_markdown_blocks(source_text)
            block_records = [
                {
                    "block_id": build_block_id(root_id, source_path, block.heading_path, block.chunk_index),
                    "root_id": root_id,
                    "source_path": source_path,
                    "heading_path": list(block.heading_path),
                    "chunk_index": block.chunk_index,
                    "content_raw": block.content_raw,
                    "block_checksum": block.block_checksum,
                    "source_checksum": source_checksum,
                    "updated_at": indexed_at,
                }
                for block in parsed_blocks
            ]
            block_ids = store.replace_blocks_for_file(root_id, source_path, block_records)
            store.write_markdown_file_manifest(
                {
                    "root_id": root_id,
                    "source_path": source_path,
                    "file_key": file_key,
                    "size": size,
                    "mtime_ns": mtime_ns,
                    "source_checksum": source_checksum,
                    "block_ids": block_ids,
                    "indexed_at": indexed_at,
                }
            )
            changed_files += 1

        deleted_source_paths = sorted(set(existing_manifests).difference(seen_source_paths))
        for source_path in deleted_source_paths:
            manifest = existing_manifests[source_path]
            store.delete_blocks_for_file(root_id, source_path)
            store.delete_markdown_file_manifest(str(manifest["file_key"]))
            deleted_files += 1

    block_count = sum(len(store.list_markdown_blocks(root_id=str(root["root_id"]))) for root in registered_roots)
    return build_indexing_payload(
        mode=resolved_mode,
        registered_roots=[
            {"root_id": str(root["root_id"]), "path": str(root["path"])}
            for root in registered_roots
        ],
        indexed_files=indexed_files,
        changed_files=changed_files,
        skipped_files=skipped_files,
        deleted_files=deleted_files,
        block_count=block_count,
    )


def build_root_id(root_path: str | Path) -> str:
    return f"mdroot-{sha256_path(root_path)[:16]}"


def build_file_key(root_id: str, source_path: str) -> str:
    normalized_source_path = Path(source_path).as_posix().lstrip("./")
    readable = _slugify(Path(normalized_source_path).with_suffix("").as_posix().replace("/", "-"))
    return f"{readable}-{sha256_text(f'{root_id}|{normalized_source_path}')[:10]}"


def _resolve_roots(store: MemoryStore, paths: Sequence[str] | None, *, base_dir: Path) -> list[dict[str, object]]:
    existing_by_root_id = {str(root["root_id"]): root for root in store.list_markdown_roots()}
    if not paths:
        return [existing_by_root_id[root_id] for root_id in sorted(existing_by_root_id)]

    registered_roots: list[dict[str, object]] = []
    seen_paths: set[Path] = set()
    for raw_path in paths:
        resolved_path = _resolve_input_path(raw_path, base_dir=base_dir)
        if resolved_path in seen_paths:
            continue
        seen_paths.add(resolved_path)
        root_id = build_root_id(resolved_path)
        existing = existing_by_root_id.get(root_id)
        root_record = store.write_markdown_root(
            {
                "root_id": root_id,
                "path": str(resolved_path),
                "path_hash": sha256_path(resolved_path),
                "registered_at": existing.get("registered_at", utc_now()) if existing else utc_now(),
                "updated_at": utc_now(),
            }
        )
        registered_roots.append(root_record)
    return registered_roots


def _resolve_input_path(raw_path: str, *, base_dir: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "markdown"


def _iter_markdown_files(root_path: Path) -> list[Path]:
    files: list[Path] = []
    for file_path in root_path.rglob("*.md"):
        if not file_path.is_file():
            continue
        relative_parts = file_path.relative_to(root_path).parts[:-1]
        if any(part in DEFAULT_IGNORED_DIR_NAMES for part in relative_parts):
            continue
        files.append(file_path)
    return sorted(files)


__all__ = [
    "DEFAULT_IGNORED_DIR_NAMES",
    "build_file_key",
    "build_root_id",
    "index_paths",
]
