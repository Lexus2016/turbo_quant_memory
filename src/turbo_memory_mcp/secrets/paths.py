"""Path-isolation helpers for the secrets vault.

The vault lives at ``<storage_root>/projects/<project_id>/secrets/``. Nothing
on the ingestion / lint / retrieval path is allowed to traverse that
subtree, so every public walker calls :func:`is_inside_secrets_storage`
before reading anything from disk.
"""

from __future__ import annotations

from pathlib import Path


def is_inside_secrets_storage(path: Path | str, storage_root: Path | str) -> bool:
    """Return True if ``path`` resolves into a secrets vault subdirectory.

    Matches ``<storage_root>/projects/<project_id>/secrets/`` and anything
    underneath. Tolerant of non-existent paths and symlink chains; on
    resolution failure returns ``False`` so the caller's normal path
    handling proceeds (the boundary guards are belt-and-suspenders, not
    the sole defense).
    """
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
        root = Path(storage_root).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    projects_dir = root / "projects"
    try:
        rel = resolved.relative_to(projects_dir)
    except ValueError:
        return False
    parts = rel.parts
    # parts[0] is the project_id; parts[1] is the immediate subdir name.
    # Match both the secrets/ dir itself and any file beneath it.
    return len(parts) >= 2 and parts[1] == "secrets"


__all__ = ["is_inside_secrets_storage"]
