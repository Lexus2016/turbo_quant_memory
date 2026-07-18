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
    VaultDecryptError
        Vault exists but could not be decrypted with the resolved master key
        (wrong / shadowing env passphrase, or tampered ciphertext). Typed so
        the MCP layer can surface a structured error instead of a bare,
        message-less ``InvalidTag`` (DEFECT A).
"""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform (e.g. Windows)
    fcntl = None
import json
import os
import re
import secrets as _stdlib_secrets
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag

from .crypto import decrypt, encrypt, key_fingerprint
from .keyresolver import (
    KeyResolutionMode,
    MasterKeyUnavailable,
    resolve_master_key,
)

VAULT_FILENAME = "vault.tqv"
META_FILENAME = "meta.json"
LOCK_FILENAME = ".vault.lock"
META_VERSION = 1
VAULT_VERSION = 1

# One-shot guard so the non-POSIX "no vault lock" warning prints once per process.
_warned_missing_flock = False

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_KDF_PARAMS_RECORD = {
    "time_cost": 3,
    "memory_cost_kib": 65536,
    "parallelism": 4,
}


class VaultDecryptError(RuntimeError):
    """Vault exists but could not be decrypted with the resolved master key.

    The message is a multi-line, surfaceable hint that names the most likely
    cause: a ``TQMEMORY_SECRETS_PASSPHRASE`` that does not match the key the
    vault was created with (e.g. vault ``key_mode == 'keyring_existing'`` but
    an env passphrase is set / forwarded from another MCP client).
    """

    def __init__(self, *, resolved_mode: str, vault_key_mode: str) -> None:
        self.resolved_mode = resolved_mode
        self.vault_key_mode = vault_key_mode
        super().__init__(
            "Secrets vault could not be decrypted with the resolved master "
            "key.\n"
            f"  resolved key via : {resolved_mode}\n"
            f"  vault created via: {vault_key_mode}\n"
            "If TQMEMORY_SECRETS_PASSPHRASE is set (or forwarded from another "
            "MCP client), it does NOT match this vault. Unset it so the "
            "keyring key is used, or set the exact passphrase the vault was "
            "created with. NOTE: the env var is a PASSPHRASE (Argon2id input), "
            "not the raw keyring key — pasting the keyring value here derives "
            "a different key and will not decrypt. (A tampered vault file "
            "produces the same error.)"
        )


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
        self.lock_path = self.secrets_dir / LOCK_FILENAME

    @contextmanager
    def _vault_lock(self):
        """Cross-process exclusive lock around a vault read-modify-write (M1).

        The daemon (``set_secret`` RPC) and a standalone ``secret-set`` CLI
        mutate the same ``vault.tqv`` from different processes; without this an
        interleaved read -> modify -> write loses one update. ``flock`` on a
        STABLE lock file (never on ``vault.tqv``, which is replaced via rename,
        so its lock would not survive the swap) serializes the writers.

        POSIX uses advisory ``flock``; non-POSIX platforms (no ``fcntl``)
        degrade to a no-op with a one-time warning. The lock only guards
        writers that take it — all in-process write paths do.
        """
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        if fcntl is None:
            # Non-POSIX platform (e.g. Windows): fcntl.flock is unavailable.
            # The in-process dispatch lock already serializes the daemon's
            # writers; the only path this flock additionally guards is a
            # standalone CLI racing the daemon, which is not a supported
            # Windows deployment. Degrade to a no-op (warn once) rather than
            # crash on import and take down every entry point (F1).
            global _warned_missing_flock
            if not _warned_missing_flock:
                print(
                    "[tqmemory] warning: cross-process vault lock unavailable "
                    "(no fcntl on this platform); concurrent CLI+daemon secret "
                    "writes are not serialized.",
                    file=sys.stderr,
                )
                _warned_missing_flock = True
            yield
            return
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

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

        with self._vault_lock():
            vault_existed = self.vault_path.exists()
            # A brand-new vault gets a fresh random salt (consumed only if the
            # key turns out env-derived); an existing vault keeps its recorded
            # salt (None for a legacy vault -> deterministic key, unchanged).
            salt = (
                self._salt_from_meta()
                if vault_existed
                else _stdlib_secrets.token_bytes(32)
            )
            try:
                # Provisioning a fresh vault is a write path: allow bootstrap so
                # macOS users get zero-setup secrets on first use.
                key, mode = resolve_master_key(
                    self.project_id, allow_bootstrap=True, salt=salt
                )
            except MasterKeyUnavailable:
                self._write_stub_meta_if_missing()
                return

            created = not self.vault_path.exists()
            # Only stamp the fingerprint for a vault we actually created here.
            # provision() never decrypts, so on a pre-existing vault a freshly
            # resolved key cannot be trusted to match its ciphertext — preserve
            # the recorded fingerprint instead of clobbering it (DEFECT B).
            # Persist the random salt only for a NEW env vault (keyring keys
            # ignore salt); _write_meta preserves it for an existing vault.
            new_salt = (
                salt if (created and mode == KeyResolutionMode.ENV) else None
            )
            if created:
                # Meta (salt + fingerprint) BEFORE the vault ciphertext
                # (peer-review Q3 crash-safety; see set()).
                self._write_meta(
                    mode=mode.value, initialized=True, key=key, salt=new_salt
                )
                empty_blob = encrypt(
                    json.dumps(
                        {"version": VAULT_VERSION, "entries": {}}
                    ).encode("utf-8"),
                    key,
                )
                _atomic_write_bytes(self.vault_path, empty_blob)
            else:
                self._write_meta(
                    mode=mode.value, initialized=True, key=None, salt=None
                )

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

    def _write_meta(
        self,
        *,
        mode: str,
        initialized: bool,
        key: bytes | None = None,
        salt: bytes | None = None,
    ) -> None:
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
        # DEFECT B: record a one-way key fingerprint so a later wrong key fails
        # fast. Preserve any prior fingerprint when no key is supplied.
        fingerprint = (
            key_fingerprint(key)
            if key is not None
            else existing.get("key_fingerprint")
        )
        if fingerprint:
            payload["key_fingerprint"] = fingerprint
        # M5: persist a new env vault's random salt; otherwise preserve the
        # recorded one (a legacy vault simply has none -> deterministic key).
        salt_hex = salt.hex() if salt is not None else existing.get("salt")
        if salt_hex:
            payload["salt"] = salt_hex
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

    def _salt_from_meta(self) -> bytes | None:
        """Persisted random Argon2id salt for this vault, or ``None`` for a
        legacy vault (meta without a "salt") so the deterministic key path is
        used. Keyring-backed vaults also have no salt — harmless, since the
        keyring key is not derived from a passphrase."""
        salt_hex = self._read_meta_or_empty().get("salt")
        if not salt_hex:
            return None
        try:
            return bytes.fromhex(salt_hex)
        except (ValueError, TypeError):
            return None

    # ----- vault I/O -----

    def _verify_key_fingerprint(
        self, key: bytes, mode: KeyResolutionMode, meta: dict
    ) -> None:
        """Fast-fail (DEFECT B) when the resolved key cannot match the vault.

        Skipped for legacy vaults whose ``meta.json`` predates the fingerprint
        (those fall back to the decrypt-time check in :meth:`_load`).
        """
        expected = meta.get("key_fingerprint")
        if not expected:
            return
        if key_fingerprint(key) != expected:
            raise VaultDecryptError(
                resolved_mode=mode.value,
                vault_key_mode=meta.get("key_mode", "unknown"),
            )

    def _load(self, key: bytes, mode: KeyResolutionMode) -> dict:
        if not self.vault_path.exists():
            return {"version": VAULT_VERSION, "entries": {}}
        meta = self._read_meta_or_empty()
        self._verify_key_fingerprint(key, mode, meta)
        blob = self.vault_path.read_bytes()
        try:
            plain = decrypt(blob, key)
        except InvalidTag as exc:
            # Wrong key (legacy vault without a fingerprint) or tampered
            # ciphertext — surface a typed, message-bearing error (DEFECT A).
            raise VaultDecryptError(
                resolved_mode=mode.value,
                vault_key_mode=meta.get("key_mode", "unknown"),
            ) from exc
        data = json.loads(plain.decode("utf-8"))
        if not isinstance(data, dict) or "entries" not in data:
            raise ValueError("vault.tqv has unexpected structure")
        return data

    def _save(self, key: bytes, data: dict) -> None:
        blob = encrypt(
            json.dumps(data, ensure_ascii=False).encode("utf-8"), key
        )
        _atomic_write_bytes(self.vault_path, blob)

    # ----- read paths (never bootstrap a new key: DEFECT D) -----

    def get(self, name: str) -> str | None:
        _validate_name(name)
        if not self.vault_path.exists():
            return None
        key, mode = resolve_master_key(
            self.project_id, allow_bootstrap=False, salt=self._salt_from_meta()
        )
        entry = self._load(key, mode)["entries"].get(name)
        return entry["value"] if entry else None

    def list_names(self) -> list[str]:
        if not self.vault_path.exists():
            return []
        key, mode = resolve_master_key(
            self.project_id, allow_bootstrap=False, salt=self._salt_from_meta()
        )
        return sorted(self._load(key, mode)["entries"].keys())

    # ----- write paths (may bootstrap a fresh key on first use) -----

    def set(self, name: str, value: str) -> None:
        _validate_name(name)
        if not isinstance(value, str):
            raise TypeError("secret value must be a string")
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secrets_dir, 0o700)

        with self._vault_lock():
            vault_existed = self.vault_path.exists()
            salt = (
                self._salt_from_meta()
                if vault_existed
                else _stdlib_secrets.token_bytes(32)
            )
            key, mode = resolve_master_key(
                self.project_id, allow_bootstrap=True, salt=salt
            )
            data = self._load(key, mode) if vault_existed else {
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
            # Persist the random salt only when this call created a new env
            # vault; keyring keys ignore salt, and an existing vault keeps its
            # recorded one.
            new_salt = (
                salt
                if (not vault_existed and mode == KeyResolutionMode.ENV)
                else None
            )
            if not vault_existed:
                # New vault: write meta (salt + fingerprint) BEFORE the vault
                # ciphertext (peer-review Q3 crash-safety). A crash between the
                # two atomic writes then leaves a not-yet-created vault, which
                # the next set() recreates cleanly — rather than a vault whose
                # random salt was lost and is therefore undecryptable.
                self._write_meta(
                    mode=mode.value, initialized=True, key=key, salt=new_salt
                )
                self._save(key, data)
            else:
                self._save(key, data)
                self._write_meta(
                    mode=mode.value, initialized=True, key=key, salt=new_salt
                )

    def delete(self, name: str) -> bool:
        _validate_name(name)
        if not self.vault_path.exists():
            return False
        with self._vault_lock():
            # A delete operates on an existing vault — never mint a new key.
            key, mode = resolve_master_key(
                self.project_id, allow_bootstrap=False, salt=self._salt_from_meta()
            )
            data = self._load(key, mode)
            if name not in data["entries"]:
                return False
            del data["entries"][name]
            self._save(key, data)
            self._write_meta(mode=mode.value, initialized=True, key=key)
            return True
