"""Knowledge-base lint checks for Markdown corpora."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Mapping, Sequence

from .ingestion import DEFAULT_IGNORED_DIR_NAMES, build_root_id
from .store import MemoryStore, utc_now

_MAX_ISSUES_LIMIT = 1000
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_TITLE_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_TITLE_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:", "javascript:")
_ROOT_ENTRY_NAMES = {"index.md", "readme.md", "home.md", "start.md"}


def lint_knowledge_base(
    store: MemoryStore,
    paths: Sequence[str] | None = None,
    *,
    max_issues: int = 200,
    cwd: Path | str | None = None,
) -> dict[str, object]:
    """Scan Markdown roots and report structural knowledge-base issues."""

    normalized_limit = max(1, min(int(max_issues), _MAX_ISSUES_LIMIT))
    base_dir = Path(cwd or store.project.project_root).expanduser().resolve()
    roots = _resolve_roots(store, paths, base_dir=base_dir)
    if not roots:
        raise ValueError(
            "lint_knowledge_base requires at least one root path or registered Markdown root. "
            "Run index_paths(...) first or pass paths=[...]."
        )

    issues: list[dict[str, object]] = []
    broken_link_count = 0
    orphan_candidate_count = 0
    duplicate_title_count = 0
    total_file_count = 0
    root_summaries: list[dict[str, object]] = []

    for root in roots:
        root_id = str(root["root_id"])
        root_path = Path(str(root["path"])).expanduser().resolve()
        if not root_path.exists() or not root_path.is_dir():
            raise FileNotFoundError(f"Markdown root does not exist: {root_path}")

        files = _iter_markdown_files(root_path)
        total_file_count += len(files)
        root_summaries.append({"root_id": root_id, "path": str(root_path), "file_count": len(files)})

        source_paths = [path.relative_to(root_path).as_posix() for path in files]
        source_set = set(source_paths)
        wikilink_lookup = _build_wikilink_lookup(source_paths)
        inbound_counts = {source: 0 for source in source_paths}
        outbound_counts = {source: 0 for source in source_paths}
        title_index: dict[str, list[str]] = {}
        seen_broken_links: set[tuple[str, str]] = set()

        for file_path in files:
            source_path = file_path.relative_to(root_path).as_posix()
            source_text = file_path.read_text(encoding="utf-8")
            title = _extract_title(source_text, fallback=file_path.stem)
            title_key = _normalize_title(title)
            title_index.setdefault(title_key, []).append(source_path)

            targets = _extract_internal_targets(
                source_text,
                source_path=source_path,
                wikilink_lookup=wikilink_lookup,
            )
            for target in targets:
                outbound_counts[source_path] += 1
                if target in source_set:
                    inbound_counts[target] += 1
                    continue

                broken_link_count += 1
                broken_identity = (source_path, target)
                if broken_identity in seen_broken_links:
                    continue
                seen_broken_links.add(broken_identity)
                _append_issue(
                    issues,
                    normalized_limit,
                    {
                        "kind": "broken_link",
                        "severity": "medium",
                        "root_id": root_id,
                        "root_path": str(root_path),
                        "source_path": source_path,
                        "target_path": target,
                        "message": f"Internal link target not found: {target}",
                    },
                )

        for source_path in source_paths:
            if source_path.lower() in _ROOT_ENTRY_NAMES:
                continue
            if inbound_counts[source_path] > 0 or outbound_counts[source_path] > 0:
                continue
            orphan_candidate_count += 1
            _append_issue(
                issues,
                normalized_limit,
                {
                    "kind": "orphan_candidate",
                    "severity": "low",
                    "root_id": root_id,
                    "root_path": str(root_path),
                    "source_path": source_path,
                    "message": "File has no inbound or outbound internal Markdown links.",
                },
            )

        for normalized_title, title_paths in sorted(title_index.items()):
            if len(title_paths) <= 1:
                continue
            duplicate_title_count += 1
            _append_issue(
                issues,
                normalized_limit,
                {
                    "kind": "duplicate_title",
                    "severity": "low",
                    "root_id": root_id,
                    "root_path": str(root_path),
                    "title_key": normalized_title,
                    "source_paths": sorted(title_paths),
                    "message": f"Duplicate title key appears in {len(title_paths)} files.",
                },
            )

    total_issue_count = broken_link_count + orphan_candidate_count + duplicate_title_count
    return {
        "status": "ok",
        "checked_at": utc_now(),
        "max_issues": normalized_limit,
        "truncated": total_issue_count > len(issues),
        "roots": root_summaries,
        "summary": {
            "root_count": len(root_summaries),
            "file_count": total_file_count,
            "issue_count": total_issue_count,
            "broken_link_count": broken_link_count,
            "orphan_candidate_count": orphan_candidate_count,
            "duplicate_title_count": duplicate_title_count,
        },
        "issues": issues,
    }


def _resolve_roots(
    store: MemoryStore,
    paths: Sequence[str] | None,
    *,
    base_dir: Path,
) -> list[dict[str, object]]:
    if not paths:
        return [
            {"root_id": str(root["root_id"]), "path": str(root["path"])}
            for root in sorted(store.list_markdown_roots(), key=lambda root: str(root["path"]))
        ]

    roots: list[dict[str, object]] = []
    seen_paths: set[Path] = set()
    for raw_path in paths:
        resolved_path = _resolve_input_path(raw_path, base_dir=base_dir)
        if resolved_path in seen_paths:
            continue
        seen_paths.add(resolved_path)
        roots.append({"root_id": build_root_id(resolved_path), "path": str(resolved_path)})
    return roots


def _resolve_input_path(raw_path: str, *, base_dir: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        return (base_dir / candidate).resolve()
    return candidate.resolve()


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


def _extract_title(text: str, *, fallback: str) -> str:
    match = _TITLE_RE.search(text)
    if not match:
        return fallback.strip() or "untitled"
    title = match.group(1).strip()
    return title or (fallback.strip() or "untitled")


def _normalize_title(title: str) -> str:
    normalized = _TITLE_NORMALIZE_RE.sub("-", title.lower()).strip("-")
    return normalized or "untitled"


def _build_wikilink_lookup(source_paths: Sequence[str]) -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = {}
    for source_path in source_paths:
        stem_key = _normalize_title(Path(source_path).stem)
        lookup.setdefault(stem_key, []).append(source_path)

    for key in list(lookup):
        lookup[key] = sorted(set(lookup[key]))
    return lookup


def _extract_internal_targets(
    text: str,
    *,
    source_path: str,
    wikilink_lookup: Mapping[str, Sequence[str]],
) -> list[str]:
    targets: list[str] = []
    for raw_target in _MARKDOWN_LINK_RE.findall(text):
        target = _resolve_internal_target(raw_target, source_path=source_path)
        if target is not None:
            targets.append(target)

    for raw_target in _WIKI_LINK_RE.findall(text):
        target_body = raw_target.split("|", maxsplit=1)[0].strip()
        target = _resolve_wikilink_target(target_body, source_path=source_path, lookup=wikilink_lookup)
        if target is not None:
            targets.append(target)
    return targets


def _resolve_wikilink_target(
    raw_target: str,
    *,
    source_path: str,
    lookup: Mapping[str, Sequence[str]],
) -> str | None:
    target = raw_target.strip()
    if not target:
        return None

    looks_like_path = "/" in target or target.endswith(".md")
    if looks_like_path:
        return _resolve_internal_target(target, source_path=source_path)

    key = _normalize_title(target)
    candidates = sorted(str(candidate) for candidate in lookup.get(key, ()))
    if candidates:
        return candidates[0]

    return _resolve_internal_target(target, source_path=source_path)


def _resolve_internal_target(raw_target: str, *, source_path: str) -> str | None:
    target = raw_target.strip().strip("<>").strip()
    if not target:
        return None
    lower = target.lower()
    if lower.startswith(_EXTERNAL_PREFIXES) or lower.startswith("#"):
        return None

    target = target.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0].strip()
    if not target:
        return None

    source_parent = posixpath.dirname(source_path)
    if target.startswith("/"):
        combined = target.lstrip("/")
    elif source_parent:
        combined = posixpath.join(source_parent, target)
    else:
        combined = target
    normalized = posixpath.normpath(combined)
    if not normalized or normalized in {".", ".."} or normalized.startswith("../"):
        return None

    suffix = Path(normalized).suffix.lower()
    if suffix and suffix != ".md":
        return None
    if not suffix:
        normalized = f"{normalized}.md"
    return normalized


def _append_issue(
    bucket: list[dict[str, object]],
    max_issues: int,
    issue: dict[str, object],
) -> None:
    if len(bucket) < max_issues:
        bucket.append(issue)


__all__ = ["lint_knowledge_base"]
