"""Pure helpers for announced work, overlap and writer attribution.

Nothing here touches the database: callers pass rows and events in and get plain values back.
Attribution is an inference from announced areas, so every writer hint says so rather than
stating a writer as fact.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from turbo_memory_mcp.workspace.models import FILE_CHANGED, FILE_DELETED, EventRow, SessionRow

STATUS_RECENT_WINDOW_S: Final = 7200.0
CHANGE_KINDS: Final = (FILE_CHANGED, FILE_DELETED)
CHANGE_SCAN_LIMIT: Final = 200
RECENT_CHANGES_LIMIT: Final = 20
_GLOB_CHARS: Final = "*?["
_UNKNOWN_CLIENT: Final = "unknown"

COVERAGE_TEXT: Final = (
    "Versions are kept for files that change while two or more agent sessions share this working tree. "
    "Writes are not blocked. Agents that do not call tqmemory do not see this status."
)


def area_matches(path: str, area: str) -> bool:
    """True when ``path`` is ``area`` itself, lies under it, or matches it as a glob."""

    if any(char in area for char in _GLOB_CHARS):
        return fnmatch.fnmatchcase(path, area)
    return path == area or path.startswith(area.rstrip("/") + "/")


def areas_overlap(first: str, second: str) -> bool:
    """True when either area contains the other, so neither is only a sibling of the other."""

    return area_matches(first, second) or area_matches(second, first)


def shared_areas(mine: Iterable[str], theirs: Sequence[str]) -> list[str]:
    """The areas of ``mine`` that overlap any of ``theirs``, in their original order."""

    return [area for area in mine if any(areas_overlap(area, other) for other in theirs)]


def infer_attribution(path: str, sessions: Sequence[SessionRow]) -> tuple[str, str | None]:
    """Attribute ``path`` to a session only when exactly one session announced an area covering it."""

    matches = [session for session in sessions if any(area_matches(path, area) for area in session.areas)]
    if len(matches) == 1:
        return ("inferred", matches[0].session_id)
    return ("unknown", None)


def writer_hint(attribution: str, session: SessionRow | None) -> str:
    """Human-readable attribution. An inferred writer is described as an area, never as the author."""

    if attribution == "inferred" and session is not None:
        return f"inside {_client_label(session)}'s announced area ({session.feature or 'unnamed work'})"
    if attribution == "restore":
        return "restored by an agent"
    return "writer unknown"


def overlap_views(mine: Sequence[str], peers: Iterable[SessionRow]) -> list[dict[str, Any]]:
    """Peers whose announced areas overlap ``mine``, with the overlapping areas of ``mine``."""

    views: list[dict[str, Any]] = []
    for peer in peers:
        shared = shared_areas(mine, peer.areas)
        if shared:
            views.append(
                {
                    "session_id": peer.session_id,
                    "client": peer.client_name,
                    "feature": peer.feature,
                    "shared_areas": shared,
                }
            )
    return views


def session_view(row: SessionRow) -> dict[str, Any]:
    return {
        "client": row.client_name,
        "feature": row.feature,
        "areas": list(row.areas),
        "started_at": iso_time(row.started_at),
        "last_activity_at": iso_time(row.last_activity_at),
    }


def change_views(
    events: Iterable[EventRow],
    sessions_by_id: Mapping[str, SessionRow],
    areas: Sequence[str] | None = None,
    limit: int = RECENT_CHANGES_LIMIT,
) -> list[dict[str, Any]]:
    """Newest-first file changes, optionally limited to paths under ``areas``, capped at ``limit``."""

    views: list[dict[str, Any]] = []
    for event in events:
        if event.kind not in CHANGE_KINDS or event.path is None:
            continue
        if areas is not None and not any(area_matches(event.path, area) for area in areas):
            continue
        attribution = str(event.detail.get("attribution", "unknown"))
        inferred_id = event.detail.get("session_id")
        session = sessions_by_id.get(inferred_id) if isinstance(inferred_id, str) else None
        views.append(
            {
                "path": event.path,
                "when": iso_time(event.created_at),
                "attribution": attribution,
                "writer_hint": writer_hint(attribution, session),
            }
        )
        if len(views) >= limit:
            break
    return views


def iso_time(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, tz=UTC).isoformat(timespec="seconds")


def _client_label(session: SessionRow) -> str:
    return "another session" if session.client_name == _UNKNOWN_CLIENT else session.client_name
