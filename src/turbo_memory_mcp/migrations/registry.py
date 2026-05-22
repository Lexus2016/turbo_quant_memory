"""Migration registry and decorator.

Subsystems map 1:1 to the existing format-version constants in store.py.
Each registered Migration takes a subsystem, the version it upgrades from,
and the version it upgrades to. Upgrades must be linear (v1->v2, v2->v3),
not skipping versions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class Subsystem(str, Enum):
    """One independent migration chain per subsystem.

    Each value maps 1:1 to a format-version constant in store.py and
    drives a manifest the runner updates atomically last. To add a new
    subsystem (e.g. NOTES in Phase 2): (1) extend this enum, (2) bump
    a matching constant in store.py, (3) handle reads in
    runner._read_current_version, and (4) handle writes in
    runner._bump_manifest. Keep linear chains only — see Migration.
    """

    MARKDOWN = "markdown"
    RETRIEVAL = "retrieval"
    USAGE_STATS = "usage_stats"


MigrationFunc = Callable[[object], None]
"""An upgrade function takes a MemoryStore and mutates state in place.

The function must be idempotent: re-running it on partially-migrated state
should converge to the same result without raising. The type is `object`
rather than `MemoryStore` to avoid a circular import at module load time.
"""


@dataclass(frozen=True)
class Migration:
    """A single upgrade step within one subsystem's chain."""

    subsystem: Subsystem
    from_version: int
    to_version: int
    func: MigrationFunc
    description: str = ""

    def __post_init__(self) -> None:
        if self.to_version != self.from_version + 1:
            raise ValueError(
                f"Migration must bump exactly one version "
                f"(got {self.from_version}->{self.to_version})"
            )
        if self.from_version < 1:
            raise ValueError(f"from_version must be >= 1 (got {self.from_version})")


REGISTRY: list[Migration] = []


def migration(
    subsystem: Subsystem,
    *,
    from_version: int,
    to_version: int,
    description: str = "",
) -> Callable[[MigrationFunc], MigrationFunc]:
    """Decorator registering an upgrade function in REGISTRY.

    Example:
        @migration(Subsystem.MARKDOWN, from_version=1, to_version=2,
                   description="add line/byte offsets")
        def upgrade_markdown_v1_to_v2(layout):
            ...
    """

    def decorator(fn: MigrationFunc) -> MigrationFunc:
        desc = description.strip()
        if not desc:
            doc = (fn.__doc__ or "").strip()
            if doc:
                desc = doc.splitlines()[0].strip()
        REGISTRY.append(
            Migration(
                subsystem=subsystem,
                from_version=from_version,
                to_version=to_version,
                func=fn,
                description=desc,
            )
        )
        return fn

    return decorator


def clear_registry() -> None:
    """Test helper: drop all registered migrations."""
    REGISTRY.clear()


def get_chain(subsystem: Subsystem, from_version: int) -> list[Migration]:
    """Return ordered upgrades from from_version up to the latest known.

    Raises ValueError if the chain is broken (missing intermediate version).
    """
    chain: list[Migration] = []
    cursor = from_version
    while True:
        step = _find_step(subsystem, cursor)
        if step is None:
            break
        chain.append(step)
        cursor = step.to_version
    # Sanity: ensure no gap (registry could have v1->v2 and v3->v4 with v2->v3 missing)
    further = [m for m in REGISTRY if m.subsystem is subsystem and m.from_version > cursor]
    if further:
        gap_start = min(m.from_version for m in further)
        raise ValueError(
            f"Migration chain for {subsystem.value} has a gap between "
            f"v{cursor} and v{gap_start}"
        )
    return chain


def latest_version(subsystem: Subsystem) -> int:
    """Highest version the codebase claims for this subsystem.

    Combines the live constant from store.py (the version a fresh write
    will produce) with the highest to_version registered in REGISTRY.
    The maximum is the source of truth — if the in-code constant has
    been bumped but no migration was registered, the version is still
    considered current (no pending upgrades).
    """
    # Avoid a circular import at module load time.
    from .. import store as _store

    constant_map = {
        Subsystem.MARKDOWN: _store.MARKDOWN_FORMAT_VERSION,
        Subsystem.RETRIEVAL: _store.RETRIEVAL_FORMAT_VERSION,
        Subsystem.USAGE_STATS: _store.USAGE_STATS_FORMAT_VERSION,
    }
    base = int(constant_map.get(subsystem, 1))
    registry_max = max(
        (m.to_version for m in REGISTRY if m.subsystem is subsystem),
        default=0,
    )
    return max(base, registry_max)


def _find_step(subsystem: Subsystem, from_version: int) -> Migration | None:
    candidates = [
        m
        for m in REGISTRY
        if m.subsystem is subsystem and m.from_version == from_version
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        raise ValueError(
            f"Duplicate migrations for {subsystem.value} v{from_version}->v{from_version+1}"
        )
    return candidates[0]
