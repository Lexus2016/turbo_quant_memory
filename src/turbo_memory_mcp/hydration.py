"""Explicit hydration orchestration for bounded Phase 5 context recovery."""

from __future__ import annotations

from typing import Any

from .contracts import (
    HYDRATE_MODES,
    build_hydrated_markdown_item_payload,
    build_hydrated_note_item_payload,
    build_hydration_payload,
)
from .store import GLOBAL_SCOPE, MARKDOWN_SOURCE_KIND, MemoryStore, PROJECT_SCOPE

HYDRATE_WINDOWS = {
    "default": {"before": 1, "after": 1},
    "related": {"before": 2, "after": 2},
}


def hydrate(
    store: MemoryStore,
    item_id: str,
    *,
    scope: str,
    mode: str = "default",
) -> dict[str, object]:
    resolved_item_id = item_id.strip()
    if not resolved_item_id:
        raise ValueError("hydrate requires a non-empty item_id.")

    resolved_scope = scope.strip().lower()
    if resolved_scope not in {PROJECT_SCOPE, GLOBAL_SCOPE}:
        raise ValueError(f"hydrate only supports '{PROJECT_SCOPE}' or '{GLOBAL_SCOPE}' item scopes.")

    resolved_mode = mode.strip().lower()
    if resolved_mode not in HYDRATE_WINDOWS:
        supported = ", ".join(HYDRATE_MODES)
        raise ValueError(f"Unsupported hydrate mode: {mode}. Expected one of: {supported}.")

    if resolved_scope == GLOBAL_SCOPE:
        return _hydrate_note(
            store.read_global_note(resolved_item_id),
            source_path=str(store.global_note_path(resolved_item_id)),
            mode=resolved_mode,
        )

    item = store.resolve_project_item(resolved_item_id)
    if item["source_kind"] == MARKDOWN_SOURCE_KIND:
        return _hydrate_markdown(store, resolved_item_id, mode=resolved_mode)
    return _hydrate_note(
        item,
        source_path=str(store.note_source_path(item)),
        mode=resolved_mode,
    )


def _hydrate_markdown(
    store: MemoryStore,
    block_id: str,
    *,
    mode: str,
) -> dict[str, object]:
    window = HYDRATE_WINDOWS[mode]
    neighborhood = store.read_markdown_neighborhood(
        block_id,
        before=window["before"],
        after=window["after"],
    )
    item = build_hydrated_markdown_item_payload(
        neighborhood["item"],
        project_name=store.project.project_name,
    )
    neighbors_before = [
        build_hydrated_markdown_item_payload(block, project_name=store.project.project_name)
        for block in neighborhood["neighbors_before"]
    ]
    neighbors_after = [
        build_hydrated_markdown_item_payload(block, project_name=store.project.project_name)
        for block in neighborhood["neighbors_after"]
    ]
    return build_hydration_payload(
        mode=mode,
        item=item,
        neighbors_before=neighbors_before,
        neighbors_after=neighbors_after,
        neighbor_window=neighborhood["neighbor_window"],
    )


def _hydrate_note(
    note: dict[str, Any],
    *,
    source_path: str,
    mode: str,
) -> dict[str, object]:
    item = build_hydrated_note_item_payload(note, source_path=source_path)
    return build_hydration_payload(
        mode=mode,
        item=item,
        neighbors_before=[],
        neighbors_after=[],
        neighbor_window={"before": 0, "after": 0},
    )


__all__ = [
    "HYDRATE_WINDOWS",
    "hydrate",
]
