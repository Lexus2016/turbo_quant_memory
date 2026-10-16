"""Value objects describing a shared git working tree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
