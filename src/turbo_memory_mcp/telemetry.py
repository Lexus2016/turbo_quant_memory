"""Persistent usage and savings telemetry kept outside memory scopes."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

from .store import MemoryStore, USAGE_STATS_FORMAT_VERSION, utc_now

INPUT_COST_ENV = "TQMEMORY_INPUT_COST_PER_1M_TOKENS_USD"
TOKEN_MILESTONES = (1_000, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000)
SEARCH_MILESTONES = (10, 25, 50, 100, 250, 500, 1_000)


def load_usage_stats(store: MemoryStore) -> dict[str, Any]:
    payload = store.read_usage_stats()
    if not payload or int(payload.get("format_version", 0)) != USAGE_STATS_FORMAT_VERSION:
        return {
            "format_version": USAGE_STATS_FORMAT_VERSION,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "totals": _empty_counter(),
            "projects": {},
        }
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

    compact_response_bytes = len(json.dumps(response_payload, ensure_ascii=False))
    items = response_payload.get("items", [])
    estimated_bytes_saved = max(int(raw_source_bytes) - compact_response_bytes, 0)
    estimated_tokens_saved = int(math.ceil(estimated_bytes_saved / 4)) if estimated_bytes_saved else 0

    for counter in (total_counter, project_counter):
        counter["search_calls"] += 1
        counter["compact_items_served"] += len(items)
        counter["raw_source_bytes"] += int(raw_source_bytes)
        counter["compact_response_bytes"] += compact_response_bytes
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
    hydrated_response_bytes = len(json.dumps(response_payload, ensure_ascii=False))

    for counter in (total_counter, project_counter):
        counter["hydrate_calls"] += 1
        counter["hydrated_items_served"] += 1
        counter["hydrated_response_bytes"] += hydrated_response_bytes

    stats["updated_at"] = utc_now()
    store.write_usage_stats(stats)


def _empty_counter() -> dict[str, Any]:
    return {
        "search_calls": 0,
        "hydrate_calls": 0,
        "compact_items_served": 0,
        "hydrated_items_served": 0,
        "raw_source_bytes": 0,
        "compact_response_bytes": 0,
        "hydrated_response_bytes": 0,
        "estimated_bytes_saved": 0,
        "estimated_input_tokens_saved": 0,
        "last_announced_token_milestone": 0,
        "last_announced_search_milestone": 0,
    }


def _normalized_counter(counter: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _empty_counter()
    normalized.update({key: counter.get(key, value) for key, value in normalized.items()})
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
        "compact_response_bytes": int(counter["compact_response_bytes"]),
        "hydrated_response_bytes": int(counter["hydrated_response_bytes"]),
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
    if not tokens_saved and not searches:
        return "No savings recorded yet."
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


__all__ = [
    "INPUT_COST_ENV",
    "build_usage_snapshot",
    "load_usage_stats",
    "record_hydration_usage",
    "record_semantic_search_usage",
]
