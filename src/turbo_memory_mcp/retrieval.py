"""Semantic retrieval orchestration for Phase 4."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from .contracts import build_search_payload, build_semantic_item_payload
from .retrieval_index import RetrievalIndex
from .store import GLOBAL_SCOPE, MARKDOWN_SOURCE_KIND, MemoryStore, NOTE_SOURCE_KIND, PROJECT_SCOPE

HYBRID_PROJECT_BIAS = 0.15
MARKDOWN_KIND_BONUS = 0.02
MAX_SEMANTIC_LIMIT = 20
AMBIGUOUS_SCORE_DELTA = 0.03
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)")


def semantic_search(
    store: MemoryStore,
    query: str,
    *,
    scope: str,
    limit: int,
) -> dict[str, object]:
    query_text = query.strip()
    if not query_text:
        raise ValueError("semantic_search requires a non-empty query.")

    resolved_scope = scope.strip().lower()
    if resolved_scope not in {PROJECT_SCOPE, GLOBAL_SCOPE, "hybrid"}:
        raise ValueError(f"Unsupported query scope: {scope}")

    normalized_limit = max(1, min(int(limit), MAX_SEMANTIC_LIMIT))
    index = RetrievalIndex(store)

    if resolved_scope == PROJECT_SCOPE:
        ranked = _query_scope(index, store, PROJECT_SCOPE, query_text, normalized_limit)
    elif resolved_scope == GLOBAL_SCOPE:
        ranked = _query_scope(index, store, GLOBAL_SCOPE, query_text, normalized_limit)
    else:
        ranked = _rank_candidates(
            [
                *_query_scope(index, store, PROJECT_SCOPE, query_text, max(5, normalized_limit * 2), hybrid=True),
                *_query_scope(index, store, GLOBAL_SCOPE, query_text, max(5, normalized_limit * 2), hybrid=True),
            ]
        )

    selected = ranked[:normalized_limit]
    overall_state, warning = _resolve_overall_state(selected)
    items = [
        build_semantic_item_payload(
            _decorate_candidate(
                candidate,
                store=store,
                query=query_text,
                overall_state=overall_state if idx < 2 and overall_state == "ambiguous" else None,
            )
        )
        for idx, candidate in enumerate(selected)
    ]
    return build_search_payload(
        query=query_text,
        scope=resolved_scope,
        items=items,
        confidence_state=overall_state,
        warning=warning,
    )


def sync_project_retrieval(store: MemoryStore) -> list[dict[str, Any]]:
    return RetrievalIndex(store).sync_project()


def sync_global_retrieval(store: MemoryStore) -> list[dict[str, Any]]:
    return RetrievalIndex(store).sync_global()


def _query_scope(
    index: RetrievalIndex,
    store: MemoryStore,
    scope: str,
    query: str,
    limit: int,
    *,
    hybrid: bool = False,
) -> list[dict[str, Any]]:
    _ensure_scope_synced(index, store, scope)
    rows = index.search(query, scope, limit=max(limit, 5))
    if not rows:
        rows = _lexical_fallback_rows(index, scope, query, limit=max(limit, 5))
    candidates: list[dict[str, Any]] = []
    for row in rows:
        base_score = _distance_to_score(float(row.get("_distance", 1.0)))
        lexical_bonus = _lexical_bonus(row, query)
        project_bias = HYBRID_PROJECT_BIAS if hybrid and scope == PROJECT_SCOPE else 0.0
        kind_bonus = MARKDOWN_KIND_BONUS if row.get("source_kind") == MARKDOWN_SOURCE_KIND else 0.0
        score = min(base_score + lexical_bonus, 1.0)
        effective_score = min(score + project_bias + kind_bonus, 1.0)
        candidates.append(
            {
                **row,
                "score": round(score, 3),
                "confidence": round(score, 3),
                "effective_score": effective_score,
                "scope_priority": 0 if scope == PROJECT_SCOPE else 1,
                "source_priority": 0 if row.get("source_kind") == MARKDOWN_SOURCE_KIND else 1,
                "updated_epoch": _updated_epoch(str(row["updated_at"])),
                "item_identity": str(row["item_id"]),
            }
        )
    return _rank_candidates(candidates)


def _ensure_scope_synced(index: RetrievalIndex, store: MemoryStore, scope: str) -> None:
    if scope == PROJECT_SCOPE:
        if index.count_rows(PROJECT_SCOPE) == 0 and (
            store.list_markdown_blocks(project_id=store.project.project_id) or store.list_notes(PROJECT_SCOPE)
        ):
            index.sync_project()
        return

    if index.count_rows(GLOBAL_SCOPE) == 0 and store.list_notes(GLOBAL_SCOPE):
        index.sync_global()


def _rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates.sort(
        key=lambda item: (
            -round(float(item["effective_score"]), 2),
            item["scope_priority"],
            item["source_priority"],
            -item["updated_epoch"],
            item["item_identity"],
        )
    )
    return candidates


def _decorate_candidate(
    candidate: Mapping[str, Any],
    *,
    store: MemoryStore,
    query: str,
    overall_state: str | None,
) -> dict[str, Any]:
    confidence_state = overall_state or _confidence_state(float(candidate["confidence"]))
    warning = None
    if confidence_state == "ambiguous":
        warning = "Top results are close; hydrate before acting."
    elif confidence_state == "low":
        warning = "Low-confidence retrieval; refine the query or hydrate before acting."

    payload: dict[str, Any] = {
        "scope": candidate["scope"],
        "project_id": candidate["project_id"],
        "project_name": candidate["project_name"],
        "source_kind": candidate["source_kind"],
        "item_id": candidate["item_id"],
        "source_path": candidate["source_path"],
        "title": candidate["title"],
        "heading_path": list(candidate.get("heading_path", [])),
        "updated_at": candidate["updated_at"],
        "score": candidate["score"],
        "confidence": candidate["confidence"],
        "confidence_state": confidence_state,
        "compressed_summary": _build_compressed_summary(str(candidate["title"]), str(candidate["content_summary_seed"])),
        "key_points": _extract_key_points(str(candidate["content_summary_seed"]), query),
        "can_hydrate": True,
    }
    if candidate.get("block_id"):
        payload["block_id"] = candidate["block_id"]
    if warning is not None:
        payload["warning"] = warning

    if candidate["source_kind"] == NOTE_SOURCE_KIND:
        note = store.read_note(str(candidate["note_id"]), str(candidate["scope"]))
        payload["note_kind"] = note["note_kind"]
        payload["note_status"] = note["note_status"]
        if note.get("promoted_from"):
            payload["promoted_from"] = dict(note["promoted_from"])

    return payload


def _resolve_overall_state(candidates: list[Mapping[str, Any]]) -> tuple[str, str | None]:
    if not candidates:
        return "low", "No relevant memory results found."

    if len(candidates) > 1:
        first = float(candidates[0]["effective_score"])
        second = float(candidates[1]["effective_score"])
        if abs(first - second) <= AMBIGUOUS_SCORE_DELTA or round(first, 2) == round(second, 2):
            return "ambiguous", "Top results are close; hydrate before acting."

    state = _confidence_state(float(candidates[0]["confidence"]))
    if state == "low":
        return state, "Low-confidence retrieval; refine the query or hydrate before acting."
    return state, None


def _confidence_state(score: float) -> str:
    if score >= 0.82:
        return "high"
    if score >= 0.62:
        return "medium"
    return "low"


def _distance_to_score(distance: float) -> float:
    return max(0.0, 1.0 - min(distance, 1.0))


def _lexical_fallback_rows(
    index: RetrievalIndex,
    scope: str,
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    ranked: list[tuple[float, float, dict[str, Any]]] = []
    for row in index.list_rows(scope):
        lexical_score = _lexical_bonus(row, query)
        if lexical_score <= 0.0:
            continue
        ranked.append((lexical_score, _updated_epoch(str(row["updated_at"])), {**row, "_distance": 1.0}))

    ranked.sort(key=lambda item: (-item[0], -item[1], str(item[2]["item_id"])))
    return [row for _, _, row in ranked[:limit]]


def _lexical_bonus(candidate: Mapping[str, Any], query: str) -> float:
    query_terms = tuple(dict.fromkeys(_tokenize(query)))
    if not query_terms:
        return 0.0

    title = str(candidate.get("title", ""))
    tags = " ".join(str(tag) for tag in candidate.get("tags", []))
    content_search = str(candidate.get("content_search", ""))
    full_text = f"{title} {tags} {content_search}".casefold()
    title_tokens = set(_tokenize(title.casefold()))
    tag_tokens = set(_tokenize(tags.casefold()))
    full_tokens = set(_tokenize(full_text))

    overlap = sum(1 for term in query_terms if term in full_tokens)
    title_overlap = sum(1 for term in query_terms if term in title_tokens)
    tag_overlap = sum(1 for term in query_terms if term in tag_tokens)
    phrase_bonus = 0.08 if query.casefold() in full_text else 0.0
    denominator = max(len(query_terms), 1)
    return min(
        (overlap / denominator) * 0.16
        + (title_overlap / denominator) * 0.08
        + (tag_overlap / denominator) * 0.04
        + phrase_bonus,
        0.24,
    )


def _build_compressed_summary(title: str, raw_text: str, limit: int = 220) -> str:
    text = _normalize_text(raw_text)
    if not text:
        return title

    summary = text if text.lower().startswith(title.lower()) else f"{title}: {text}" if title else text
    if len(summary) <= limit:
        return summary
    return summary[: limit - 1].rstrip() + "…"


def _extract_key_points(raw_text: str, query: str, *, max_points: int = 3) -> list[str]:
    candidates: list[str] = []
    for line in raw_text.splitlines():
        cleaned = _clean_candidate_line(line)
        if cleaned:
            candidates.extend(_split_candidate_segments(cleaned))

    if not candidates:
        normalized = _normalize_text(raw_text)
        candidates = _split_candidate_segments(normalized)

    query_terms = set(_tokenize(query))
    scored: list[tuple[int, int, int, str]] = []
    seen: set[str] = set()
    for idx, candidate in enumerate(candidates):
        lowered = candidate.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        overlap = sum(1 for term in query_terms if term in lowered)
        scored.append((-overlap, idx, len(candidate), candidate))

    scored.sort()
    return [item[3] for item in scored[:max_points]]


def _clean_candidate_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("```"):
        return None
    stripped = stripped.lstrip("#").strip()
    stripped = _LIST_PREFIX_RE.sub("", stripped)
    stripped = _normalize_text(stripped)
    if len(stripped) < 6:
        return None
    return stripped


def _split_candidate_segments(text: str) -> list[str]:
    segments = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", text) if segment.strip()]
    return segments or [text.strip()]


def _normalize_text(value: str) -> str:
    cleaned = _LINK_RE.sub(r"\1", value)
    cleaned = cleaned.replace("`", "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _updated_epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _tokenize(value: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(value)]


__all__ = [
    "HYBRID_PROJECT_BIAS",
    "MARKDOWN_KIND_BONUS",
    "MAX_SEMANTIC_LIMIT",
    "semantic_search",
    "sync_global_retrieval",
    "sync_project_retrieval",
]
