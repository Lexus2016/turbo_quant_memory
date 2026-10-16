"""Content-addressed version journal for files in a shared git working tree.

Each change to a text file is stored as a zlib-compressed blob keyed by its SHA-256 under the
project's workspace storage directory, never inside the repository. Version rows live in the
coordination database. ``journal.log`` mirrors every version (with its original ``version_id``)
so the index can be rebuilt after the database is lost, and carries a watermark so evicted ids are
never reused.

Ordering rules that keep the two stores consistent:

- A version's blob and row commit together. Its log line is appended only after that commit, under
  the write lock, so a rolled-back row can never leave a log line behind.
- Eviction commits the deletion first and rewrites the log afterwards, so the log can only be a
  superset of the index. Blobs are swept last, and only when neither store refers to them.
- Restores of one workspace are serialized by a lock file, and the current file is re-hashed right
  before it is replaced.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
import zlib
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NamedTuple

from turbo_memory_mcp.workspace.db import CoordinationDB, CoordinationUnavailable
from turbo_memory_mcp.workspace.models import FILE_CHANGED, FILE_DELETED, VersionRecord, Workspace
from turbo_memory_mcp.workspace.paths import to_relative

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

ENV_JOURNAL_MAX_FILE_BYTES = "TQMEMORY_JOURNAL_MAX_FILE_BYTES"
ENV_JOURNAL_MAX_BYTES = "TQMEMORY_JOURNAL_MAX_BYTES"
ENV_JOURNAL_RETENTION_DAYS = "TQMEMORY_JOURNAL_RETENTION_DAYS"

_LOG = logging.getLogger(__name__)

_BINARY_SCAN_BYTES = 8192
_DAY_SECONDS = 86400.0
_RECENT_SECONDS = _DAY_SECONDS
_GIT_TIMEOUT_SECONDS = 10
_LOCK_RETRY_SECONDS = 0.05
_ZLIB_LEVEL = 6
_LOG_NAME = "journal.log"
_BLOB_SUFFIX = ".z"
_TEMP_SUFFIX = ".tmp"
_LOCK_SUFFIX = ".lock"
_KIND_WATERMARK = "watermark"
_MAX_SNAPSHOT_PASSES = 3
_RECORDABLE_ATTRIBUTIONS = frozenset({"inferred", "unknown"})
_ALL_ATTRIBUTIONS = frozenset({"inferred", "unknown", "restore"})
_KINDS = frozenset({"content", "deletion"})
_ROW_KEYS = (
    "version_id",
    "workspace",
    "path",
    "kind",
    "sha256",
    "size",
    "observed_at",
    "attribution",
    "session_id",
)
_LOG_KEYS = (
    "workspace",
    "version_id",
    "path",
    "kind",
    "sha256",
    "size",
    "observed_at",
    "attribution",
    "session_id",
)
_VERSION_SELECT = "SELECT version_id, path, kind, sha256, size, observed_at, attribution, session_id FROM versions"
_INSERT_VERSION = (
    "INSERT INTO versions (workspace, path, kind, sha256, size, observed_at, attribution, session_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_VERSION_WITH_ID = (
    "INSERT INTO versions (version_id, workspace, path, kind, sha256, size, observed_at, attribution, session_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_EVENT = "INSERT INTO events (workspace, kind, session_id, path, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)"


@dataclass(frozen=True)
class JournalLimits:
    """Size and age caps for the journal; defaults match the documented ``TQMEMORY_JOURNAL_*`` values."""

    max_file_bytes: int = 1_048_576
    max_total_bytes: int = 268_435_456
    retention_days: int = 14

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> JournalLimits:
        """Read the three ``TQMEMORY_JOURNAL_*`` variables; a malformed value raises ``ValueError`` naming it."""

        env = os.environ if environ is None else environ
        defaults = cls()
        return cls(
            max_file_bytes=_positive_int(env, ENV_JOURNAL_MAX_FILE_BYTES, defaults.max_file_bytes),
            max_total_bytes=_positive_int(env, ENV_JOURNAL_MAX_BYTES, defaults.max_total_bytes),
            retention_days=_positive_int(env, ENV_JOURNAL_RETENTION_DAYS, defaults.retention_days),
        )


class _EvictRow(NamedTuple):
    version_id: int
    workspace: str
    path: str
    kind: str
    sha256: str | None
    observed_at: float


class Journal:
    """Versions of the files in one workspace, stored under ``blobs_root`` and indexed in ``db``.

    ``over_cap`` is set by :meth:`evict` and reports whether the size cap is still exceeded
    after eviction, which happens when every remaining candidate is protected as recent.
    """

    def __init__(self, workspace: Workspace, db: CoordinationDB, blobs_root: Path, limits: JournalLimits) -> None:
        self.workspace = workspace
        self.over_cap = False
        self._db = db
        self._limits = limits
        self._blobs_root = blobs_root
        self._log_path = blobs_root.parent / _LOG_NAME

    # Recording ----------------------------------------------------------------

    def record(
        self,
        rel_path: str,
        *,
        attribution: Literal["inferred", "unknown"] = "unknown",
        session_id: str | None = None,
        now: float | None = None,
        feature: str | None = None,
    ) -> VersionRecord | None:
        """Journal the current content of ``rel_path``; ``None`` when it is skipped.

        Skipped: the file is missing, larger than the per-file limit, binary, or identical to the
        newest content version of the path.
        """

        _check_recordable(attribution)
        rel = self._relative(rel_path)
        observed_at = _now(now)
        content = self._read_for_journal(rel)
        if content is None:
            return None
        digest = hashlib.sha256(content).hexdigest()
        with _transaction(self._db) as conn:
            latest = _latest(conn, self.workspace.root_key, rel)
            if latest is not None and latest.kind == "content" and latest.sha256 == digest:
                return None
            record = self._insert(
                conn,
                rel,
                kind="content",
                content=content,
                digest=digest,
                size=len(content),
                attribution=attribution,
                session_id=session_id,
                observed_at=observed_at,
                feature=feature,
            )
        self._append_log(record)
        return record

    def record_deletion(
        self,
        rel_path: str,
        *,
        attribution: Literal["inferred", "unknown"] = "unknown",
        session_id: str | None = None,
        now: float | None = None,
        feature: str | None = None,
    ) -> VersionRecord | None:
        """Journal that ``rel_path`` was deleted; ``None`` when the newest version is already a deletion."""

        _check_recordable(attribution)
        rel = self._relative(rel_path)
        observed_at = _now(now)
        with _transaction(self._db) as conn:
            latest = _latest(conn, self.workspace.root_key, rel)
            if latest is not None and latest.kind == "deletion":
                return None
            record = self._insert(
                conn,
                rel,
                kind="deletion",
                content=None,
                digest=None,
                size=0,
                attribution=attribution,
                session_id=session_id,
                observed_at=observed_at,
                feature=feature,
            )
        self._append_log(record)
        return record

    # Reading ------------------------------------------------------------------

    def history(self, rel_path: str, limit: int = 20) -> list[VersionRecord]:
        """Versions of ``rel_path`` in this workspace, newest first."""

        if limit < 1:
            raise ValueError("limit must be at least 1")
        rel = self._relative(rel_path)
        with _reading(self._db) as conn:
            rows = conn.execute(
                f"{_VERSION_SELECT} WHERE workspace = ? AND path = ? ORDER BY version_id DESC LIMIT ?",
                (self.workspace.root_key, rel, limit),
            ).fetchall()
        return [_version_record(row) for row in rows]

    def read_version(self, version_id: int) -> bytes:
        """Return the content of a content version.

        Raises ``ValueError`` for an unknown id, a deletion, or a blob that is missing or fails its checksum.
        """

        with _reading(self._db) as conn:
            row = conn.execute("SELECT kind, sha256 FROM versions WHERE version_id = ?", (version_id,)).fetchone()
        if row is None:
            raise ValueError(f"no version {version_id}")
        kind, digest = row
        if kind != "content" or digest is None:
            raise ValueError(f"version {version_id} is a deletion and has no content")
        return self._read_blob(digest, version_id)

    # Restore ------------------------------------------------------------------

    def restore(self, rel_path: str, version: int | Literal["HEAD"]) -> dict[str, Any]:
        """Write ``version`` (a version id, or ``"HEAD"`` for the committed blob) back to ``rel_path``.

        Nothing is written unless the target content is read successfully first. Under a per-workspace lock the
        destination is snapshotted with attribution ``"restore"`` until a re-read after the snapshot matches it
        (at most three passes), then replaced, so the returned undo id holds the content that was overwritten.
        A destination too large to snapshot, or one that never settles, is refused without writing.
        """

        rel = self._relative(rel_path)
        if version == "HEAD":
            target = self._head_content(rel)
        elif isinstance(version, int) and not isinstance(version, bool):
            self._check_restorable(version, rel)
            target = self.read_version(version)
        else:
            raise ValueError("version must be a version id or 'HEAD'")

        dest = self.workspace.root / rel
        if dest.is_dir():
            raise ValueError(f"{rel} is a directory")
        with self._restore_lock():
            snapshot, current = self._snapshot_until_stable(rel, dest)
            old = b"" if current is None else current
            mode = stat.S_IMODE(dest.stat().st_mode) if dest.is_file() else None
            dest.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(dest, target, mode=mode)
        added, removed = _line_diff(old, target)
        return {
            "status": "ok",
            "path": rel,
            "restored_version": version,
            "previous_version_id": snapshot.version_id if snapshot is not None else None,
            "diff": {"added": added, "removed": removed},
        }

    def _snapshot_until_stable(self, rel: str, dest: Path) -> tuple[VersionRecord | None, bytes | None]:
        """Snapshot ``dest`` until a re-read after the snapshot equals it; return the undo snapshot and that content.

        An absent destination needs no snapshot. Raises ``ValueError`` when it still changes after the last pass.
        """

        snapshot: VersionRecord | None = None
        current: bytes | None = None
        for _ in range(_MAX_SNAPSHOT_PASSES):
            if not dest.is_file():
                return snapshot, current
            observed = self._current_bytes(dest, rel)
            snapshot = self._snapshot(rel, observed)
            current = observed
            if dest.is_file() and _digest(self._current_bytes(dest, rel)) == _digest(observed):
                return snapshot, current
        raise ValueError(f"{rel} kept changing while it was being snapshotted; nothing was overwritten")

    # Retention and recovery ---------------------------------------------------

    def evict(self, now: float | None = None) -> int:
        """Drop versions past the retention age, then the oldest unprotected content until under the size cap.

        The newest content version of every path changed in the last 24 hours is never dropped for size.
        The deletion commits first; then the log is regenerated from the index and unreferenced blobs are swept.
        Returns the number of versions removed.
        """

        observed_now = _now(now)
        cutoff = observed_now - self._limits.retention_days * _DAY_SECONDS
        with _transaction(self._db) as conn:
            rows = [
                _EvictRow(*row)
                for row in conn.execute(
                    "SELECT version_id, workspace, path, kind, sha256, observed_at FROM versions ORDER BY version_id"
                ).fetchall()
            ]
            if not rows:
                # An empty index means the log and blobs are the only copy of the history; leave them for a rebuild.
                return 0
            expired = {row.version_id for row in rows if row.observed_at < cutoff}
            live = [row for row in rows if row.version_id not in expired]
            protected = _protected_versions(live, observed_now)
            refs = Counter(row.sha256 for row in live if row.kind == "content" and row.sha256)
            sizes = {digest: self._blob_size(digest) for digest in refs}
            total = sum(sizes.values())
            cap = self._limits.max_total_bytes
            for row in sorted(live, key=lambda item: (item.observed_at, item.version_id)):
                if total <= cap:
                    break
                if row.kind != "content" or row.sha256 is None or row.version_id in protected:
                    continue
                expired.add(row.version_id)
                refs[row.sha256] -= 1
                if refs[row.sha256] == 0:
                    del refs[row.sha256]
                    total -= sizes.pop(row.sha256)
            self.over_cap = total > cap
            if expired:
                conn.executemany("DELETE FROM versions WHERE version_id = ?", [(item,) for item in sorted(expired)])
        self._maintain_after_commit()
        return len(expired)

    def rebuild_index_if_empty(self) -> int:
        """Re-insert versions from ``journal.log`` when the index is empty; return how many were inserted.

        Versions keep their logged ``version_id``, so an old id still names the same content. A version id seen
        twice (a regenerated line plus a late append) is inserted once. Content entries whose blob is gone are
        skipped; deletion entries are always re-inserted. The id sequence is raised to the highest id ever
        logged, so evicted ids are not handed out again.
        """

        with _transaction(self._db) as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM versions").fetchone()
            if count or not self._log_path.is_file():
                return 0
            inserted = 0
            top = 0
            seen: set[int] = set()
            for entry in _read_log(self._log_path):
                top = max(top, entry["version_id"])
                if entry["kind"] == _KIND_WATERMARK or entry["version_id"] in seen:
                    continue
                seen.add(entry["version_id"])
                digest = entry["sha256"]
                if entry["kind"] == "content" and not _blob_path(self._blobs_root, digest).is_file():
                    continue
                conn.execute(
                    _INSERT_VERSION_WITH_ID,
                    (
                        entry["version_id"],
                        entry["workspace"],
                        entry["path"],
                        entry["kind"],
                        digest,
                        entry["size"],
                        entry["observed_at"],
                        entry["attribution"],
                        entry["session_id"],
                    ),
                )
                inserted += 1
            _bump_sequence(conn, top)
            return inserted

    # Internals ----------------------------------------------------------------

    def _relative(self, path: str | Path) -> str:
        rel = to_relative(self.workspace, path)
        if rel == ".":
            raise ValueError("path must name a file inside the workspace")
        # casefold: on case-insensitive filesystems ".GIT" and "sub/.Git" alias the git directory.
        if any(part.casefold() == ".git" for part in rel.split("/")):
            raise ValueError("path is inside .git")
        return rel

    def _read_for_journal(self, rel: str) -> bytes | None:
        path = self.workspace.root / rel
        if not path.is_file():
            return None
        try:
            if path.stat().st_size > self._limits.max_file_bytes:
                return None
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        if len(data) > self._limits.max_file_bytes or b"\0" in data[:_BINARY_SCAN_BYTES]:
            return None
        return data

    def _current_bytes(self, dest: Path, rel: str) -> bytes:
        if dest.stat().st_size > self._limits.max_file_bytes:
            raise ValueError(f"current {rel} exceeds the journal size limit; it was not overwritten")
        return dest.read_bytes()

    def _snapshot(self, rel: str, content: bytes) -> VersionRecord:
        with _transaction(self._db) as conn:
            record = self._insert(
                conn,
                rel,
                kind="content",
                content=content,
                digest=_digest(content),
                size=len(content),
                attribution="restore",
                session_id=None,
                observed_at=_now(None),
                feature=None,
            )
        self._append_log(record)
        return record

    def _check_restorable(self, version_id: int, rel: str) -> None:
        with _reading(self._db) as conn:
            row = conn.execute(
                "SELECT workspace, path, kind FROM versions WHERE version_id = ?", (version_id,)
            ).fetchone()
        if row is None or row[0] != self.workspace.root_key:
            raise ValueError(f"no version {version_id} in this workspace")
        if row[1] != rel:
            raise ValueError(f"version {version_id} belongs to a different path")
        if row[2] != "content":
            raise ValueError(f"version {version_id} is a deletion and cannot be restored")

    def _head_content(self, rel: str) -> bytes:
        # One call: a tree, a missing path or a submodule makes cat-file fail, so no separate type check is needed.
        try:
            result = subprocess.run(
                ["git", "cat-file", "blob", f"HEAD:{rel}"],
                cwd=self.workspace.root,
                check=False,
                capture_output=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"cannot read HEAD for {rel}: {exc}") from None
        if result.returncode != 0:
            raise ValueError(f"{rel} is not a committed file in HEAD")
        return result.stdout

    @contextmanager
    def _restore_lock(self) -> Iterator[None]:
        """Hold a per-workspace lock file (flock, or LK_LOCK on Windows) for the whole restore."""

        key = hashlib.sha256(self.workspace.root_key.encode("utf-8")).hexdigest()[:16]
        lock_path = self._blobs_root.parent / f"restore-{key}{_LOCK_SUFFIX}"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _acquire(fd)
            try:
                yield
            finally:
                _release(fd)
        finally:
            os.close(fd)

    def _read_blob(self, digest: str, version_id: int) -> bytes:
        try:
            data = zlib.decompress(_blob_path(self._blobs_root, digest).read_bytes())
        except FileNotFoundError:
            raise ValueError(f"blob for version {version_id} is missing") from None
        except zlib.error:
            raise ValueError(f"blob for version {version_id} is corrupt") from None
        if _digest(data) != digest:
            raise ValueError(f"blob for version {version_id} failed its checksum")
        return data

    def _blob_size(self, digest: str) -> int:
        try:
            return _blob_path(self._blobs_root, digest).stat().st_size
        except FileNotFoundError:
            return 0

    def _maintain_after_commit(self) -> None:
        """Regenerate the log from the index, then sweep unreferenced blobs, in one write transaction.

        While the index is alive it is authoritative, so the log is regenerated from its rows instead of being
        filtered. A write that committed meanwhile is included; its own append may land after this and duplicate
        a line, which rebuild dedupes.
        """

        with _transaction(self._db) as conn:
            referenced = {
                row[0] for row in conn.execute("SELECT DISTINCT sha256 FROM versions WHERE sha256 IS NOT NULL")
            }
            if self._log_path.is_file():
                referenced.update(entry["sha256"] for entry in _read_log(self._log_path) if entry["kind"] == "content")
            self._regenerate_log(conn)
            self._sweep_unreferenced_blobs(referenced)

    def _sweep_unreferenced_blobs(self, referenced: set[str]) -> None:
        """Delete blobs whose digest is not in ``referenced``.

        A blob that cannot be unlinked (for example, open elsewhere on Windows) is left for a later sweep.
        """

        if not self._blobs_root.is_dir():
            return
        for shard in self._blobs_root.iterdir():
            if not shard.is_dir():
                continue
            for entry in list(shard.iterdir()):
                if entry.suffix == _BLOB_SUFFIX and entry.stem in referenced:
                    continue
                if entry.suffix not in (_BLOB_SUFFIX, _TEMP_SUFFIX):
                    continue
                try:
                    entry.unlink()
                except OSError:
                    continue

    def _regenerate_log(self, conn: sqlite3.Connection) -> None:
        """Write ``journal.log`` from the index rows, followed by a watermark of the highest id ever assigned."""

        old_text = self._log_path.read_text(encoding="utf-8") if self._log_path.is_file() else ""
        top = _id_high_water(conn)
        for line in old_text.splitlines():
            entry = _parse_log_line(line)
            if entry is not None:
                top = max(top, entry["version_id"])
        rows = conn.execute(
            "SELECT version_id, workspace, path, kind, sha256, size, observed_at, attribution, session_id "
            "FROM versions ORDER BY version_id"
        ).fetchall()
        parts = [_json_line(dict(zip(_ROW_KEYS, row, strict=True))) for row in rows]
        if top > 0:
            parts.append(_json_line({"kind": _KIND_WATERMARK, "version_id": top}))
        new_text = "".join(parts)
        if new_text != old_text:
            _atomic_write(self._log_path, new_text.encode("utf-8"))

    def _insert(
        self,
        conn: sqlite3.Connection,
        rel: str,
        *,
        kind: Literal["content", "deletion"],
        content: bytes | None,
        digest: str | None,
        size: int,
        attribution: Literal["inferred", "unknown", "restore"],
        session_id: str | None,
        observed_at: float,
        feature: str | None,
    ) -> VersionRecord:
        """Store the blob, insert the version row and its event. Must run inside ``_transaction``.

        The log line is not written here; callers append it after the transaction commits.
        """

        workspace = self.workspace.root_key
        if content is not None and digest is not None:
            _write_blob(self._blobs_root, digest, content)
        cursor = conn.execute(
            _INSERT_VERSION, (workspace, rel, kind, digest, size, observed_at, attribution, session_id)
        )
        version_id = cursor.lastrowid
        if version_id is None:  # pragma: no cover - sqlite always reports the rowid of an INSERT
            raise CoordinationUnavailable("version insert returned no row id")
        detail: dict[str, Any] = {"attribution": attribution, "session_id": session_id, "version_id": version_id}
        if feature is not None:
            detail["feature"] = feature
        conn.execute(
            _INSERT_EVENT,
            (
                workspace,
                FILE_CHANGED if kind == "content" else FILE_DELETED,
                session_id,
                rel,
                json.dumps(detail, sort_keys=True),
                observed_at,
            ),
        )
        return VersionRecord(
            version_id=version_id,
            path=rel,
            kind=kind,
            sha256=digest,
            size=size,
            observed_at=observed_at,
            attribution=attribution,
            session_id=session_id,
        )

    def _append_log(self, record: VersionRecord) -> None:
        """Append the record's log line under the write lock.

        The row is already committed, so a failed append is logged rather than raised; the next regeneration
        writes the line.
        """

        line = _json_line(_log_entry(self.workspace.root_key, record))
        try:
            with _transaction(self._db):
                _append_line(self._log_path, line)
        except (OSError, CoordinationUnavailable) as exc:
            _LOG.warning(
                "version %s is not in journal.log yet (%s); the next eviction will write it", record.version_id, exc
            )


# Module helpers -----------------------------------------------------------------


def _id_high_water(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'versions'").fetchone()
    return 0 if row is None else int(row[0])


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}") from None
    if value < 1:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    return value


def _check_recordable(attribution: str) -> None:
    if attribution not in _RECORDABLE_ATTRIBUTIONS:
        raise ValueError(f"attribution must be one of {sorted(_RECORDABLE_ATTRIBUTIONS)}, got {attribution!r}")


def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _transaction(db: CoordinationDB) -> Iterator[sqlite3.Connection]:
    """A write transaction under the handle's lock; ``BEGIN IMMEDIATE`` also serializes writers across processes."""

    with db.lock:
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            try:
                yield db.conn
                db.conn.execute("COMMIT")
            except BaseException:
                if db.conn.in_transaction:
                    db.conn.execute("ROLLBACK")
                raise
        except sqlite3.Error as exc:
            raise CoordinationUnavailable(str(exc)) from exc


@contextmanager
def _reading(db: CoordinationDB) -> Iterator[sqlite3.Connection]:
    with db.lock:
        try:
            yield db.conn
        except sqlite3.Error as exc:
            raise CoordinationUnavailable(str(exc)) from exc


def _bump_sequence(conn: sqlite3.Connection, value: int) -> None:
    """Raise the AUTOINCREMENT counter of ``versions`` to at least ``value``."""

    conn.execute(
        "INSERT INTO sqlite_sequence (name, seq) SELECT 'versions', 0 "
        "WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'versions')"
    )
    conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'versions'", (value,))


def _latest(conn: sqlite3.Connection, workspace: str, rel: str) -> VersionRecord | None:
    row = conn.execute(
        f"{_VERSION_SELECT} WHERE workspace = ? AND path = ? ORDER BY version_id DESC LIMIT 1",
        (workspace, rel),
    ).fetchone()
    return None if row is None else _version_record(row)


def _version_record(row: tuple[Any, ...]) -> VersionRecord:
    return VersionRecord(
        version_id=int(row[0]),
        path=str(row[1]),
        kind=row[2],
        sha256=row[3],
        size=int(row[4]),
        observed_at=float(row[5]),
        attribution=row[6],
        session_id=row[7],
    )


def _protected_versions(rows: list[_EvictRow], now: float) -> set[int]:
    """Newest content version of every path with any version observed in the last 24 hours."""

    recent = {(row.workspace, row.path) for row in rows if row.observed_at >= now - _RECENT_SECONDS}
    newest: dict[tuple[str, str], int] = {}
    for row in rows:  # rows arrive in ascending version_id order, so the last content row wins
        if row.kind == "content":
            newest[(row.workspace, row.path)] = row.version_id
    return {newest[key] for key in recent if key in newest}


def _blob_path(blobs_root: Path, digest: str) -> Path:
    return blobs_root / digest[:2] / f"{digest}{_BLOB_SUFFIX}"


def _write_blob(blobs_root: Path, digest: str, content: bytes) -> None:
    path = _blob_path(blobs_root, digest)
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, zlib.compress(content, _ZLIB_LEVEL))


def _atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Write ``data`` to a temp file beside ``path`` and move it into place with ``os.replace``.

    ``mode`` (permission bits) is applied to the temp file before the replace, so the destination keeps its mode.
    """

    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}{_TEMP_SUFFIX}")
    try:
        temp.write_bytes(data)
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _append_line(log_path: Path, line: str) -> None:
    """Append one line. A torn trailing fragment is terminated first, so it stays an isolated invalid line."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    needs_break = False
    if log_path.is_file() and log_path.stat().st_size > 0:
        with log_path.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            needs_break = handle.read(1) != b"\n"
    with log_path.open("ab") as handle:
        if needs_break:
            handle.write(b"\n")
        handle.write(line.encode("utf-8"))
        handle.flush()


def _json_line(obj: Mapping[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True) + "\n"


def _log_entry(workspace: str, record: VersionRecord) -> dict[str, Any]:
    return {
        "workspace": workspace,
        "version_id": record.version_id,
        "path": record.path,
        "kind": record.kind,
        "sha256": record.sha256,
        "size": record.size,
        "observed_at": record.observed_at,
        "attribution": record.attribution,
        "session_id": record.session_id,
    }


def _parse_log_line(line: str) -> dict[str, Any] | None:
    """Return a well-formed version entry or watermark, or ``None`` for a torn or foreign line."""

    try:
        entry = json.loads(line)
    except ValueError:
        return None
    if not isinstance(entry, dict) or type(entry.get("version_id")) is not int:
        return None
    if entry.get("kind") == _KIND_WATERMARK:
        return entry
    if any(key not in entry for key in _LOG_KEYS):
        return None
    if entry["kind"] not in _KINDS or entry["attribution"] not in _ALL_ATTRIBUTIONS:
        return None
    if entry["kind"] == "content" and not isinstance(entry["sha256"], str):
        return None
    return entry


def _read_log(log_path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        entry = _parse_log_line(line)
        if entry is not None:
            entries.append(entry)
    return entries


def _acquire(fd: int) -> None:
    if sys.platform == "win32":
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(_LOCK_RETRY_SECONDS)
    fcntl.flock(fd, fcntl.LOCK_EX)


def _release(fd: int) -> None:
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass  # closing the descriptor releases the lock anyway


def _line_diff(old: bytes, new: bytes) -> tuple[int, int]:
    """Return ``(added, removed)`` line counts from ``old`` to ``new``."""

    old_lines = old.decode("utf-8", errors="replace").splitlines()
    new_lines = new.decode("utf-8", errors="replace").splitlines()
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed
