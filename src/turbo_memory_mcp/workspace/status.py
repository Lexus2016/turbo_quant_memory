"""Pure helpers for announced work, overlap and writer attribution.

Nothing here touches the database: callers pass rows and events in, or a page fetcher, and get plain
values back. Attribution is an inference from announced areas, so every writer hint says so rather
than stating a writer as fact.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from turbo_memory_mcp.workspace.models import FILE_CHANGED, FILE_DELETED, EventRow, SessionRow

STATUS_RECENT_WINDOW_S: Final = 7200.0
CHANGE_KINDS: Final = (FILE_CHANGED, FILE_DELETED)
CHANGE_PAGE_SIZE: Final = 200
# Hard bound on events scanned for one response, so a noisy workspace cannot make a read unbounded.
CHANGE_SCAN_LIMIT: Final = 5000
RECENT_CHANGES_LIMIT: Final = 20
_GLOB_CHARS: Final = "*?["
_WHOLE_TREE: Final = "."

COVERAGE_TEXT: Final = (
    "Versions are kept for files that change while two or more agent sessions share this working tree. "
    "Writes are not blocked. Agents that do not call tqmemory do not see this status."
)


def area_matches(path: str, area: str) -> bool:
    """True when ``path`` is ``area`` itself, lies under it, or matches it as a glob. ``"."`` matches every path."""

    if area == _WHOLE_TREE:
        return True
    if _is_glob(area):
        return fnmatch.fnmatchcase(path, area)
    return path == area or path.startswith(area.rstrip("/") + "/")


def areas_overlap(first: str, second: str) -> bool:
    """Symmetric and conservative: true when either area matches the other, or, for a glob, when the
    literal prefixes before the first glob character are prefix-related (so ``src/*.py`` and ``src/r*``
    overlap, while the plain siblings ``src/a`` and ``src/ab`` do not)."""

    if area_matches(first, second) or area_matches(second, first):
        return True
    if not (_is_glob(first) or _is_glob(second)):
        return False
    prefix_a, prefix_b = _literal_prefix(first), _literal_prefix(second)
    return prefix_a.startswith(prefix_b) or prefix_b.startswith(prefix_a)


def shared_areas(mine: Iterable[str], theirs: Sequence[str]) -> list[str]:
    """For each overlapping pair of areas, the more specific one; duplicates dropped, first occurrence kept."""

    shared: list[str] = []
    for area in mine:
        for other in theirs:
            if not areas_overlap(area, other):
                continue
            specific = _more_specific(area, other)
            if specific not in shared:
                shared.append(specific)
    return shared


def infer_attribution(path: str, sessions: Sequence[SessionRow]) -> tuple[str, str | None]:
    """Attribute ``path`` to a session only when exactly one session announced an area covering it."""

    matches = [session for session in sessions if any(area_matches(path, area) for area in session.areas)]
    if len(matches) == 1:
        return ("inferred", matches[0].session_id)
    return ("unknown", None)


def writer_hint(attribution: str, session: SessionRow | None, feature: str | None = None) -> str:
    """Human-readable attribution. An inferred writer is described as an announced area, never as the author.

    ``feature`` is the feature recorded with the event when there is one; otherwise the session's current
    feature is named. An empty feature drops the parenthetical.
    """

    if attribution == "inferred" and session is not None:
        text = f"inside {session.client_name}'s announced area"
        named = feature or session.feature
        return f"{text} ({named})" if named else text
    if attribution == "restore":
        return "restored by an agent"
    return "writer unknown"


def overlap_views(mine: Sequence[str], peers: Iterable[SessionRow]) -> list[dict[str, Any]]:
    """Peers whose announced areas overlap ``mine``, with the shared areas."""

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


def collect_changes(
    fetch: Callable[[int, int], Sequence[EventRow]],
    *,
    upper_id: int,
    since: float,
    areas: Sequence[str] | None,
    limit: int = RECENT_CHANGES_LIMIT,
    scan_limit: int = CHANGE_SCAN_LIMIT,
    page_size: int = CHANGE_PAGE_SIZE,
) -> list[EventRow]:
    """Newest-first file-change events under ``areas`` (all paths when None), read page by page.

    ``fetch(upper_id, before_id)`` returns up to ``page_size`` change events with
    ``before_id > event_id`` and ``event_id <= upper_id``, newest first. Collection stops at ``limit``
    matches, at the first event older than ``since``, or after ``scan_limit`` scanned events.
    """

    matches: list[EventRow] = []
    scanned = 0
    before = upper_id + 1
    while scanned < scan_limit:
        page = fetch(upper_id, before)
        if not page:
            break
        scanned += len(page)
        for event in page:
            if event.created_at < since:
                return matches
            if event.path is not None and (areas is None or any(area_matches(event.path, a) for a in areas)):
                matches.append(event)
                if len(matches) >= limit:
                    return matches
        if len(page) < page_size:
            break
        before = page[-1].event_id
    return matches


def change_views(events: Iterable[EventRow], sessions_by_id: Mapping[str, SessionRow]) -> list[dict[str, Any]]:
    """Presentation rows for change events, with writer hints built from their recorded attribution."""

    views: list[dict[str, Any]] = []
    for event in events:
        attribution = str(event.detail.get("attribution", "unknown"))
        inferred_id = event.detail.get("session_id")
        session = sessions_by_id.get(inferred_id) if isinstance(inferred_id, str) else None
        recorded_feature = event.detail.get("feature")
        feature = recorded_feature if isinstance(recorded_feature, str) else None
        views.append(
            {
                "path": event.path,
                "when": iso_time(event.created_at),
                "attribution": attribution,
                "writer_hint": writer_hint(attribution, session, feature),
            }
        )
    return views


def iso_time(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, tz=UTC).isoformat(timespec="seconds")


def _is_glob(area: str) -> bool:
    return any(char in area for char in _GLOB_CHARS)


def _literal_prefix(area: str) -> str:
    """The part of ``area`` before its first glob character (the whole area when it has none)."""

    for index, char in enumerate(area):
        if char in _GLOB_CHARS:
            return area[:index]
    return area


def _more_specific(first: str, second: str) -> str:
    """The area that lies inside the other; for glob areas that are not nested, the longer literal prefix."""

    first_in_second = area_matches(first, second)
    second_in_first = area_matches(second, first)
    if first_in_second and not second_in_first:
        return first
    if second_in_first and not first_in_second:
        return second
    return first if len(_literal_prefix(first)) >= len(_literal_prefix(second)) else second
