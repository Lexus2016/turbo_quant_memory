"""Value objects describing a shared git working tree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Workspace:
    """A git working tree shared by one or more agent sessions.

    ``root_key`` is the POSIX form of ``root`` and identifies the tree across
    sessions; ``journal_refusal`` explains why the change journal must not run
    for this root, or is ``None`` when it may run.
    """

    root: Path
    root_key: str
    project_id: str
    is_git: bool
    journal_refusal: str | None


SESSION_STARTED = "session_started"
SESSION_ENDED = "session_ended"
WORK_ANNOUNCED = "work_announced"
WORK_FINISHED = "work_finished"
FILE_CHANGED = "file_changed"
FILE_DELETED = "file_deleted"


@dataclass(frozen=True)
class SessionRow:
    """One live agent session registered in a workspace's coordination database."""

    session_id: str
    workspace: str
    client_name: str
    client_version: str
    pid: int
    proc_start: float | None
    started_at: float
    last_activity_at: float
    feature: str | None
    areas: tuple[str, ...]
    details: str | None


@dataclass(frozen=True)
class EventRow:
    """One entry of the append-only workspace event log."""

    event_id: int
    workspace: str
    kind: str
    session_id: str | None
    path: str | None
    detail: dict[str, Any]
    created_at: float


@dataclass(frozen=True)
class LeaseRow:
    """The session that currently owns a workspace's single-writer lease."""

    workspace: str
    session_id: str
    pid: int
    proc_start: float | None
    acquired_at: float
