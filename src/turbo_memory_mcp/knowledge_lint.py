"""Knowledge-base lint checks for Markdown corpora."""

from __future__ import annotations

import os
import sys
import posixpath
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .ingestion import DEFAULT_IGNORED_DIR_NAMES, build_root_id
from .secrets.paths import is_inside_secrets_storage
from .store import MemoryStore, NOTE_TIER_EPISODIC, PROJECT_SCOPE, utc_now

_MAX_ISSUES_LIMIT = 1000
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_TITLE_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
# Keep Unicode letters/digits (any script), collapse everything else — including
# underscore — to a single "-". The old ASCII-only class ([^a-z0-9]+) mapped every
# Cyrillic (UK/RU) title to "" -> "untitled", producing false duplicate-title
# reports and colliding non-ASCII filenames in the wikilink lookup.
_TITLE_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:", "javascript:")
_ROOT_ENTRY_NAMES = {"index.md", "readme.md", "home.md", "start.md"}
# Cosine threshold above which two notes are reported as near-duplicates,
# applied to TITLE+SUMMARY embeddings. Measured on this repository's real
# bilingual EN/UK twin notes: twins score 0.905-0.969 in that space while the
# closest distinct pair (two different release handoffs) reaches 0.877 — the
# 0.90 boundary separates them. Full-content vectors from the retrieval index
# CANNOT drive this check: measured there, cross-lingual twins drop below 0.70
# and mix with unrelated pairs, so the scan embeds short probes directly.
_NEAR_DUPLICATE_COSINE = 0.90
# Probe embedding is O(n) model calls + O(n^2) vector math; cap the scan so a
# huge store cannot stall lint. Above the cap the check is skipped, not partial.
_NEAR_DUPLICATE_SCAN_CAP = 2000
# Notes whose probe text is shorter than this (after stripping separators) are
# excluded from the scan: several truly-empty legacy notes would otherwise all
# embed the same degenerate probe and report each other as duplicates.
_NEAR_DUPLICATE_MIN_PROBE_CHARS = 12


def _near_dup_probe_text(note: Mapping[str, object]) -> str:
    summary = str(note.get("summary") or note.get("content") or "")
    return f"{note.get('title', '')}. {summary[:200]}"


def _scan_near_duplicate_notes(store: MemoryStore) -> list[dict[str, object]]:
    """Note pairs that are semantic twins — most commonly the same note saved
    in two languages (EN + UK), which then crowd each other out of top-k
    retrieval.

    Embeds a short title+summary probe per active note and compares pairwise —
    the space where twins separate cleanly from related-but-distinct notes
    (see the threshold comment). Complements the write-time ``similar_notes``
    hint: the hint prevents new twins at save time, this catches the legacy
    ones already stored. Best-effort: any failure — store read, model load,
    malformed vectors, ragged shapes — degrades to "no findings" rather than
    failing lint.
    """
    try:
        notes = store.list_notes(PROJECT_SCOPE)
        probed = [(note, _near_dup_probe_text(note)) for note in notes]
        probed = [
            (note, probe)
            for note, probe in probed
            if len(probe.strip(" .")) >= _NEAR_DUPLICATE_MIN_PROBE_CHARS
        ]
        if len(probed) < 2 or len(probed) > _NEAR_DUPLICATE_SCAN_CAP:
            return []

        import numpy as np

        from .retrieval_index import build_default_embedder

        vectors = build_default_embedder().encode([probe for _, probe in probed])
        matrix = np.asarray([list(map(float, v)) for v in vectors], dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(probed) or not np.isfinite(matrix).all():
            return []
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        normalized = matrix / norms
        similarity = normalized @ normalized.T

        findings: list[dict[str, object]] = []
        upper_i, upper_j = np.triu_indices(len(probed), k=1)
        for i, j in zip(upper_i.tolist(), upper_j.tolist()):
            cosine = float(similarity[i, j])
            if cosine < _NEAR_DUPLICATE_COSINE:
                continue
            first, second = probed[i][0], probed[j][0]
            findings.append(
                {
                    "note_ids": sorted([str(first["note_id"]), str(second["note_id"])]),
                    "titles": [str(first.get("title", "")), str(second.get("title", ""))],
                    "similarity": round(cosine, 3),
                }
            )
        findings.sort(key=lambda f: f["similarity"], reverse=True)
        return findings
    except Exception:  # noqa: BLE001 — advisory check; lint must never fail on it
        return []


def _scan_stale_episodic_notes(store: MemoryStore) -> list[dict[str, object]]:
    """Episodic (handoff/session) notes older than TQMEMORY_EPISODIC_STALE_DAYS.

    Handoff notes accumulate quickly and are ephemeral, so left unchecked they
    dominate the corpus. This surfaces the stale ones oldest-first (most stale
    first) so the agent can ``deprecate_note`` them. Default threshold 14 days;
    set the env var to 0 to disable. Read-only — never deprecates anything itself.
    """
    try:
        threshold_days = int(float(os.environ.get("TQMEMORY_EPISODIC_STALE_DAYS", "14")))
    except (ValueError, TypeError):
        threshold_days = 14
    if threshold_days <= 0:
        return []
    now = datetime.now(timezone.utc)
    stale: list[dict[str, object]] = []
    for note in store.list_notes(PROJECT_SCOPE):
        tier = str(note.get("tier") or "")
        kind = str(note.get("note_kind") or "")
        # tier is authoritative when present; fall back to the kind for older
        # notes that never persisted a tier field.
        if not (tier == NOTE_TIER_EPISODIC or (not tier and kind == "handoff")):
            continue
        raw = str(note.get("updated_at") or note.get("created_at") or "")
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (now - ts).days
        if age_days >= threshold_days:
            stale.append(
                {
                    "note_id": str(note.get("note_id", "")),
                    "age_days": age_days,
                    "title": str(note.get("title", "")),
                }
            )
    stale.sort(key=lambda entry: -int(entry["age_days"]))
    return stale


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
    # No markdown roots is NOT a failure: TQMemory is commonly used purely as an
    # MCP note store with no indexed files. Markdown checks are skipped in that
    # case; the note-level checks below still run and the status stays "ok".

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

        files = _iter_markdown_files(root_path, storage_root=store.storage_root)
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
            source_text = file_path.read_text(encoding="utf-8", errors="replace")
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

    stale_episodic = _scan_stale_episodic_notes(store)
    for entry in stale_episodic:
        _append_issue(
            issues,
            normalized_limit,
            {
                "kind": "stale_episodic_note",
                "severity": "low",
                "note_id": entry["note_id"],
                "age_days": entry["age_days"],
                "title": entry["title"],
                "message": (
                    f"Episodic note is {entry['age_days']} days old; consider deprecate_note "
                    "to keep the knowledge base lean."
                ),
            },
        )
    stale_episodic_count = len(stale_episodic)

    near_duplicates = _scan_near_duplicate_notes(store)
    for entry in near_duplicates:
        _append_issue(
            issues,
            normalized_limit,
            {
                "kind": "near_duplicate_notes",
                "severity": "medium",
                "note_ids": entry["note_ids"],
                "titles": entry["titles"],
                "similarity": entry["similarity"],
                "message": (
                    f"Two active notes are near-identical (cosine {entry['similarity']}) — "
                    "typically the same note saved in two languages. They crowd each other "
                    "out of top-k retrieval: keep one (prefer English per the save-in-English "
                    "rule) and deprecate_note the other with replacement_note_id."
                ),
            },
        )
    near_duplicate_count = len(near_duplicates)
    total_issue_count = (
        broken_link_count
        + orphan_candidate_count
        + duplicate_title_count
        + stale_episodic_count
        + near_duplicate_count
    )
    return {
        "status": "ok",
        "checked_at": utc_now(),
        "max_issues": normalized_limit,
        "truncated": total_issue_count > len(issues),
        "roots": root_summaries,
        "summary": {
            "root_count": len(root_summaries),
            "markdown_configured": bool(root_summaries),
            "file_count": total_file_count,
            "issue_count": total_issue_count,
            "broken_link_count": broken_link_count,
            "orphan_candidate_count": orphan_candidate_count,
            "duplicate_title_count": duplicate_title_count,
            "stale_episodic_note_count": stale_episodic_count,
            "near_duplicate_note_count": near_duplicate_count,
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
        if is_inside_secrets_storage(resolved_path, store.storage_root):
            raise ValueError(
                f"Refusing to lint a path inside the secrets vault: "
                f"{resolved_path}. The secrets/ subtree is hard-isolated."
            )
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


def _iter_markdown_files(
    root_path: Path, *, storage_root: Path | None = None
) -> list[Path]:
    root_resolved = root_path.resolve()
    files: list[Path] = []
    for file_path in root_path.rglob("*.md"):
        if not file_path.is_file():
            continue
        # Security (S1): skip a .md whose real path escapes the root (symlink),
        # mirroring ingestion so the linter cannot read files outside the tree.
        try:
            resolved = file_path.resolve()
            within = resolved == root_resolved or resolved.is_relative_to(root_resolved)
        except (OSError, ValueError):
            within = False
        if not within:
            print(
                f"[tqmemory] lint skipping .md escaping the root (symlink?): {file_path}",
                file=sys.stderr,
            )
            continue
        if storage_root is not None and is_inside_secrets_storage(
            file_path, storage_root
        ):
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
