"""Persistent usage and savings telemetry kept outside memory scopes."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from .store import MemoryStore, USAGE_STATS_FORMAT_VERSION, utc_now

INPUT_COST_ENV = "TQMEMORY_INPUT_COST_PER_1M_TOKENS_USD"
TOKEN_MILESTONES = (1_000, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000)
SEARCH_MILESTONES = (10, 25, 50, 100, 250, 500, 1_000)


def load_usage_stats(store: MemoryStore) -> dict[str, Any]:
    payload = store.read_usage_stats()
    if not payload:
        return _new_usage_payload()

    format_version = int(payload.get("format_version", 0))
    if format_version != USAGE_STATS_FORMAT_VERSION:
        payload = _migrate_usage_stats(payload, from_version=format_version)
        store.write_usage_stats(payload)
    payload.setdefault("totals", _empty_counter())
    payload.setdefault("projects", {})
    return payload


def build_usage_snapshot(
    store: MemoryStore,
    *,
    project_id: str,
    project_name: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    stats = load_usage_stats(store)
    total_counter = _normalized_counter(stats["totals"])
    project_counter = _normalized_counter(
        stats["projects"].get(project_id, {"project_id": project_id, "project_name": project_name})
    )
    cost_basis = _resolve_cost_basis(environ)
    payload: dict[str, Any] = {
        "format_version": USAGE_STATS_FORMAT_VERSION,
        "measurement_basis": "raw_source_content_vs_compact_context",
        "totals": _serialize_counter(total_counter, cost_basis),
        "current_project": _serialize_counter(project_counter, cost_basis),
        "pricing_basis": cost_basis,
        "headline": _build_headline(total_counter, cost_basis),
    }
    return payload


def record_semantic_search_usage(
    store: MemoryStore,
    *,
    project_id: str,
    project_name: str,
    response_payload: Mapping[str, Any],
    raw_source_bytes: int,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    stats = load_usage_stats(store)
    total_counter = _get_project_counter(stats, None, None)
    project_counter = _get_project_counter(stats, project_id, project_name)

    compact_context_bytes = _estimate_compact_context_bytes(response_payload)
    items = response_payload.get("items", [])
    estimated_bytes_saved = max(int(raw_source_bytes) - compact_context_bytes, 0)
    estimated_tokens_saved = int(math.ceil(estimated_bytes_saved / 4)) if estimated_bytes_saved else 0

    for counter in (total_counter, project_counter):
        counter["search_calls"] += 1
        counter["compact_items_served"] += len(items)
        counter["raw_source_bytes"] += int(raw_source_bytes)
        counter["compact_context_bytes"] += compact_context_bytes
        counter["estimated_bytes_saved"] += estimated_bytes_saved
        counter["estimated_input_tokens_saved"] += estimated_tokens_saved

    stats["updated_at"] = utc_now()
    store.write_usage_stats(stats)

    cost_basis = _resolve_cost_basis(environ)
    return _maybe_emit_milestone(total_counter, cost_basis)


def record_hydration_usage(
    store: MemoryStore,
    *,
    project_id: str,
    project_name: str,
    response_payload: Mapping[str, Any],
) -> None:
    stats = load_usage_stats(store)
    total_counter = _get_project_counter(stats, None, None)
    project_counter = _get_project_counter(stats, project_id, project_name)
    hydrated_context_bytes = _estimate_hydrated_context_bytes(response_payload)

    for counter in (total_counter, project_counter):
        counter["hydrate_calls"] += 1
        counter["hydrated_items_served"] += 1
        counter["hydrated_context_bytes"] += hydrated_context_bytes

    stats["updated_at"] = utc_now()
    store.write_usage_stats(stats)


def _empty_counter() -> dict[str, Any]:
    return {
        "search_calls": 0,
        "hydrate_calls": 0,
        "compact_items_served": 0,
        "hydrated_items_served": 0,
        "raw_source_bytes": 0,
        "compact_context_bytes": 0,
        "hydrated_context_bytes": 0,
        "estimated_bytes_saved": 0,
        "estimated_input_tokens_saved": 0,
        "last_announced_token_milestone": 0,
        "last_announced_search_milestone": 0,
    }


def _new_usage_payload() -> dict[str, Any]:
    timestamp = utc_now()
    return {
        "format_version": USAGE_STATS_FORMAT_VERSION,
        "created_at": timestamp,
        "updated_at": timestamp,
        "totals": _empty_counter(),
        "projects": {},
    }


def _migrate_usage_stats(payload: Mapping[str, Any], *, from_version: int) -> dict[str, Any]:
    migrated = _new_usage_payload()
    migrated["created_at"] = str(payload.get("created_at") or migrated["created_at"])
    migrated["updated_at"] = utc_now()

    if from_version <= 0:
        return migrated

    migrated["totals"] = _migrate_counter(payload.get("totals", {}))
    for project_id, counter in dict(payload.get("projects", {})).items():
        migrated_counter = _migrate_counter(counter)
        migrated_counter["project_id"] = str(project_id)
        if counter and counter.get("project_name"):
            migrated_counter["project_name"] = str(counter["project_name"])
        migrated["projects"][str(project_id)] = migrated_counter
    return migrated


def _migrate_counter(counter: Any) -> dict[str, Any]:
    source = dict(counter) if isinstance(counter, Mapping) else {}
    migrated = _empty_counter()
    migrated["search_calls"] = int(source.get("search_calls", 0))
    migrated["hydrate_calls"] = int(source.get("hydrate_calls", 0))
    migrated["compact_items_served"] = int(source.get("compact_items_served", 0))
    migrated["hydrated_items_served"] = int(source.get("hydrated_items_served", 0))
    migrated["last_announced_search_milestone"] = int(source.get("last_announced_search_milestone", 0))
    return migrated


def _normalized_counter(counter: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _empty_counter()
    normalized.update({key: counter.get(key, value) for key, value in normalized.items()})
    if "compact_context_bytes" not in counter and "compact_response_bytes" in counter:
        normalized["compact_context_bytes"] = counter.get("compact_response_bytes", 0)
    if "hydrated_context_bytes" not in counter and "hydrated_response_bytes" in counter:
        normalized["hydrated_context_bytes"] = counter.get("hydrated_response_bytes", 0)
    if counter.get("project_id"):
        normalized["project_id"] = counter["project_id"]
    if counter.get("project_name"):
        normalized["project_name"] = counter["project_name"]
    return normalized


def _get_project_counter(stats: dict[str, Any], project_id: str | None, project_name: str | None) -> dict[str, Any]:
    if project_id is None:
        stats["totals"] = _normalized_counter(stats.get("totals", {}))
        return stats["totals"]

    projects = stats.setdefault("projects", {})
    counter = _normalized_counter(projects.get(project_id, {}))
    counter["project_id"] = project_id
    counter["project_name"] = project_name or counter.get("project_name") or project_id
    projects[project_id] = counter
    return counter


def _serialize_counter(counter: Mapping[str, Any], cost_basis: dict[str, Any] | None) -> dict[str, Any]:
    payload = {
        "search_calls": int(counter["search_calls"]),
        "hydrate_calls": int(counter["hydrate_calls"]),
        "compact_items_served": int(counter["compact_items_served"]),
        "hydrated_items_served": int(counter["hydrated_items_served"]),
        "raw_source_bytes": int(counter["raw_source_bytes"]),
        "compact_context_bytes": int(counter["compact_context_bytes"]),
        "compact_response_bytes": int(counter["compact_context_bytes"]),
        "hydrated_context_bytes": int(counter["hydrated_context_bytes"]),
        "hydrated_response_bytes": int(counter["hydrated_context_bytes"]),
        "estimated_bytes_saved": int(counter["estimated_bytes_saved"]),
        "estimated_input_tokens_saved": int(counter["estimated_input_tokens_saved"]),
    }
    if counter.get("project_id"):
        payload["project_id"] = counter["project_id"]
    if counter.get("project_name"):
        payload["project_name"] = counter["project_name"]
    if cost_basis and cost_basis.get("input_cost_per_1m_tokens_usd") is not None:
        payload["estimated_input_cost_saved_usd"] = round(
            (payload["estimated_input_tokens_saved"] / 1_000_000) * float(cost_basis["input_cost_per_1m_tokens_usd"]),
            6,
        )
    return payload


def _resolve_cost_basis(environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    if environ is None:
        return None
    raw_value = environ.get(INPUT_COST_ENV)
    if raw_value is None or not raw_value.strip():
        return None
    return {
        "input_cost_per_1m_tokens_usd": float(raw_value),
        "source": f"env:{INPUT_COST_ENV}",
    }


def _build_headline(counter: Mapping[str, Any], cost_basis: dict[str, Any] | None) -> str:
    tokens_saved = int(counter["estimated_input_tokens_saved"])
    searches = int(counter["search_calls"])
    if not searches:
        return "No savings recorded yet."
    if not tokens_saved:
        return (
            f"Tracked {searches:,} retrievals so far. "
            "Estimated savings stay at 0 until compact context beats the raw source payload."
        )
    if cost_basis and cost_basis.get("input_cost_per_1m_tokens_usd") is not None:
        saved_usd = (tokens_saved / 1_000_000) * float(cost_basis["input_cost_per_1m_tokens_usd"])
        return (
            f"Estimated savings so far: {tokens_saved:,} input tokens across {searches:,} retrievals "
            f"(~${saved_usd:,.4f} using {cost_basis['source']})."
        )
    return f"Estimated savings so far: {tokens_saved:,} input tokens across {searches:,} retrievals."


def _maybe_emit_milestone(counter: dict[str, Any], cost_basis: dict[str, Any] | None) -> dict[str, Any] | None:
    tokens_saved = int(counter["estimated_input_tokens_saved"])
    searches = int(counter["search_calls"])

    token_milestone = max(
        (value for value in TOKEN_MILESTONES if value <= tokens_saved),
        default=0,
    )
    if token_milestone > int(counter["last_announced_token_milestone"]):
        counter["last_announced_token_milestone"] = token_milestone
        return {
            "kind": "tokens_saved",
            "headline": _build_headline(counter, cost_basis),
            "milestone": token_milestone,
        }

    search_milestone = max(
        (value for value in SEARCH_MILESTONES if value <= searches),
        default=0,
    )
    if search_milestone > int(counter["last_announced_search_milestone"]):
        counter["last_announced_search_milestone"] = search_milestone
        return {
            "kind": "retrievals",
            "headline": _build_headline(counter, cost_basis),
            "milestone": search_milestone,
        }
    return None


def _estimate_compact_context_bytes(response_payload: Mapping[str, Any]) -> int:
    fragments: list[str] = []
    for item in response_payload.get("items", []):
        if not isinstance(item, Mapping):
            continue
        fragments.extend(_iter_compact_fragments(item))
    return _sum_unique_text_bytes(fragments)


def _estimate_hydrated_context_bytes(response_payload: Mapping[str, Any]) -> int:
    fragments: list[str] = []
    item = response_payload.get("item")
    if isinstance(item, Mapping):
        fragments.extend(_iter_hydrated_fragments(item))
    for neighbor in response_payload.get("neighbors_before", []):
        if isinstance(neighbor, Mapping):
            fragments.extend(_iter_hydrated_fragments(neighbor))
    for neighbor in response_payload.get("neighbors_after", []):
        if isinstance(neighbor, Mapping):
            fragments.extend(_iter_hydrated_fragments(neighbor))
    return _sum_unique_text_bytes(fragments)


def _iter_compact_fragments(item: Mapping[str, Any]) -> list[str]:
    fragments: list[str] = []
    summary = str(item.get("compressed_summary", "")).strip()
    if summary:
        fragments.append(summary)
    for point in item.get("key_points", []):
        normalized_point = str(point).strip()
        if normalized_point:
            fragments.append(normalized_point)
    return fragments


def _iter_hydrated_fragments(item: Mapping[str, Any]) -> list[str]:
    content = str(item.get("content", "")).strip()
    return [content] if content else []


def _sum_unique_text_bytes(fragments: Iterable[str]) -> int:
    seen: set[str] = set()
    total = 0
    for fragment in fragments:
        normalized = _normalize_fragment(fragment)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        total += len(normalized.encode("utf-8"))
    return total


def _normalize_fragment(fragment: str) -> str:
    return " ".join(fragment.split())


__all__ = [
    "INPUT_COST_ENV",
    "build_usage_snapshot",
    "load_usage_stats",
    "record_hydration_usage",
    "record_semantic_search_usage",
]
