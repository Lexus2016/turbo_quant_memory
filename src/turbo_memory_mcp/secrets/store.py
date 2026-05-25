"""On-disk per-project secrets store.

Stores secrets encrypted in
``<storage_root>/projects/<project_id>/secrets/vault.tqv``. Metadata sits
next to the vault in ``meta.json``.

This module owns ``secrets/`` and its contents only. The parent
``<storage_root>/projects/<project_id>/`` directory is managed by
``MemoryStore`` (notes / markdown / index).

Public surface:
    SecretsStore(storage_root, project_id)
        Bind to one project.
    .provision()
        Idempotent: dir + meta + empty vault if master key resolvable;
        dir + stub meta with ``vault_initialized: false`` otherwise (so
        migrations stay unconditionally green and the vault is initialized
        on first successful ``set``).
    .set(name, value)
        Store or overwrite a secret (string value only).
    .get(name) -> str | None
    .list_names() -> list[str]
        Sorted names, never values.
    .delete(name) -> bool
        ``True`` if an entry was removed.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .crypto import decrypt, encrypt
from .keyresolver import (
    MasterKeyUnavailable,
    resolve_master_key,
)

VAULT_FILENAME = "vault.tqv"
META_FILENAME = "meta.json"
META_VERSION = 1
VAULT_VERSION = 1

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_KDF_PARAMS_RECORD = {
    "time_cost": 3,
    "memory_cost_kib": 65536,
    "parallelism": 4,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"secret name {name!r} must match [A-Za-z0-9_.-]{{1,128}}"
        )


def _atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    fd, tmp = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _atomic_write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


class SecretsStore:
    def __init__(self, storage_root: Path | str, project_id: str) -> None:
        if not project_id:
            raise ValueError("project_id must be non-empty")
        self.storage_root = Path(storage_root)
        self.project_id = project_id
        self.secrets_dir = (
            self.storage_root / "projects" / project_id / "secrets"
        )
        self.vault_path = self.secrets_dir / VAULT_FILENAME
        self.meta_path = self.secrets_dir / META_FILENAME

    # ----- provisioning -----

    def provision(self) -> None:
        """Idempotently create the secrets directory and initial vault.

        If the master key is unavailable (e.g. headless install without env
        passphrase yet), the directory and a stub ``meta.json`` are still
        created so the migration step stays unconditionally green; the
        ``vault.tqv`` file is materialized on the first successful ``set``.
        """
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secrets_dir, 0o700)

        try:
            key, mode = resolve_master_key(self.project_id)
        except MasterKeyUnavailable:
            self._write_stub_meta_if_missing()
            return

        if not self.vault_path.exists():
            empty_blob = encrypt(
                json.dumps(
                    {"version": VAULT_VERSION, "entries": {}}
                ).encode("utf-8"),
                key,
            )
            _atomic_write_bytes(self.vault_path, empty_blob)

        self._write_meta(mode=mode.value, initialized=True)

    def _write_stub_meta_if_missing(self) -> None:
        if self.meta_path.exists():
            return
        payload = {
            "version": META_VERSION,
            "kdf": "argon2id",
            "kdf_params": _KDF_PARAMS_RECORD,
            "key_mode": "unavailable",
            "vault_initialized": False,
            "created_at": _utc_now_iso(),
        }
        _atomic_write_text(
            self.meta_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    def _write_meta(self, *, mode: str, initialized: bool) -> None:
        existing = self._read_meta_or_empty()
        payload = {
            "version": META_VERSION,
            "kdf": "argon2id",
            "kdf_params": _KDF_PARAMS_RECORD,
            "key_mode": mode,
            "vault_initialized": initialized,
            "created_at": existing.get("created_at", _utc_now_iso()),
            "updated_at": _utc_now_iso(),
        }
        _atomic_write_text(
            self.meta_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    def _read_meta_or_empty(self) -> dict:
        if not self.meta_path.exists():
            return {}
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    # ----- vault I/O -----

    def _load(self, key: bytes) -> dict:
        if not self.vault_path.exists():
            return {"version": VAULT_VERSION, "entries": {}}
        blob = self.vault_path.read_bytes()
        plain = decrypt(blob, key)
        data = json.loads(plain.decode("utf-8"))
        if not isinstance(data, dict) or "entries" not in data:
            raise ValueError("vault.tqv has unexpected structure")
        return data

    def _save(self, key: bytes, data: dict) -> None:
        blob = encrypt(
            json.dumps(data, ensure_ascii=False).encode("utf-8"), key
        )
        _atomic_write_bytes(self.vault_path, blob)

    # ----- read paths -----

    def get(self, name: str) -> str | None:
        _validate_name(name)
        if not self.vault_path.exists():
            return None
        key, _ = resolve_master_key(self.project_id)
        entry = self._load(key)["entries"].get(name)
        return entry["value"] if entry else None

    def list_names(self) -> list[str]:
        if not self.vault_path.exists():
            return []
        key, _ = resolve_master_key(self.project_id)
        return sorted(self._load(key)["entries"].keys())

    # ----- write paths -----

    def set(self, name: str, value: str) -> None:
        _validate_name(name)
        if not isinstance(value, str):
            raise TypeError("secret value must be a string")
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secrets_dir, 0o700)

        key, mode = resolve_master_key(self.project_id)
        data = self._load(key) if self.vault_path.exists() else {
            "version": VAULT_VERSION,
            "entries": {},
        }
        now = _utc_now_iso()
        entry = data["entries"].get(name)
        if entry:
            entry["value"] = value
            entry["updated_at"] = now
        else:
            data["entries"][name] = {
                "value": value,
                "created_at": now,
                "updated_at": now,
            }
        self._save(key, data)
        self._write_meta(mode=mode.value, initialized=True)

    def delete(self, name: str) -> bool:
        _validate_name(name)
        if not self.vault_path.exists():
            return False
        key, mode = resolve_master_key(self.project_id)
        data = self._load(key)
        if name not in data["entries"]:
            return False
        del data["entries"][name]
        self._save(key, data)
        self._write_meta(mode=mode.value, initialized=True)
        return True
