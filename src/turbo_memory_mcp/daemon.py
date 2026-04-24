"""Singleton daemon transport for turbo-memory-mcp.

Ensures only one process on the machine holds the sentence-transformers model
and LanceDB handles. Additional MCP-client launches become thin stdio<->socket
proxies that forward tool calls to the primary process.

Cross-platform:
- Unix / macOS: AF_UNIX socket at ~/.turbo-quant-memory/.daemon.sock (0600 perms)
- Windows:     AF_PIPE named pipe (\\\\.\\pipe\\tqmemory-<user>-<pid>)

Lockfile stores: pid, protocol_version, server_version, endpoint address,
authkey (base64). Path: ~/.turbo-quant-memory/.daemon.lock (0600 perms).

Escape hatch: TQMEMORY_DAEMON_DISABLE=1 falls back to self-contained mode
(previous behavior: each process owns its own model + LanceDB handles).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any, Callable, Mapping

from . import __version__
from .store import resolve_storage_root

DAEMON_PROTOCOL_VERSION = "1.0"
ENV_DAEMON_DISABLE = "TQMEMORY_DAEMON_DISABLE"
LOCKFILE_NAME = ".daemon.lock"
UNIX_SOCKET_NAME = ".daemon.sock"
AUTHKEY_BYTES = 32
CONNECT_TIMEOUT_SECONDS = 5.0
RPC_TIMEOUT_SECONDS = 120.0
LISTENER_BACKLOG = 32

MESSAGE_CALL = "call"
MESSAGE_OK = "ok"
MESSAGE_ERROR = "error"
MESSAGE_HELLO = "hello"
MESSAGE_HELLO_ACK = "hello_ack"


def daemon_is_disabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(ENV_DAEMON_DISABLE, "")).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class DaemonEndpoint:
    address: str
    family: str
    authkey: bytes
    pid: int
    server_version: str
    protocol_version: str

    def to_lockfile(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "family": self.family,
            "authkey_b64": base64.b64encode(self.authkey).decode("ascii"),
            "pid": self.pid,
            "server_version": self.server_version,
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_lockfile(cls, payload: Mapping[str, Any]) -> "DaemonEndpoint":
        return cls(
            address=str(payload["address"]),
            family=str(payload["family"]),
            authkey=base64.b64decode(str(payload["authkey_b64"])),
            pid=int(payload["pid"]),
            server_version=str(payload["server_version"]),
            protocol_version=str(payload["protocol_version"]),
        )


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            import ctypes
        except ImportError:
            return False
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return bool(ok) and exit_code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _resolve_storage_dir(environ: Mapping[str, str] | None = None) -> Path:
    storage_root = resolve_storage_root(environ)
    storage_root.mkdir(parents=True, exist_ok=True)
    return storage_root


def lockfile_path(environ: Mapping[str, str] | None = None) -> Path:
    return _resolve_storage_dir(environ) / LOCKFILE_NAME


def _unix_socket_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return a short AF_UNIX path that never exceeds the ~104-char limit.

    AF_UNIX paths are bounded by ``sizeof(sockaddr_un.sun_path)`` (108 on Linux,
    104 on macOS). Long storage roots (nested worktrees, pytest tmp_path) break
    this easily, so we route the socket through the system tempdir with a
    deterministic short discriminator derived from uid + storage_root.

    The lockfile (which points at this path) still lives inside storage_root so
    proxies can discover the endpoint without guessing the tempdir layout.
    """

    storage = resolve_storage_root(environ)
    try:
        uid_part = str(os.getuid())
    except AttributeError:  # Windows: os.getuid missing, but path is unused there.
        uid_part = os.environ.get("USERNAME", "anon")
    discriminator = hashlib.sha1(
        f"{uid_part}:{storage}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]
    tmp_dir = Path(tempfile.gettempdir())
    return tmp_dir / f"tqm-{discriminator}.sock"


def _windows_pipe_name() -> str:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "anon"
    safe_user = "".join(ch for ch in user if ch.isalnum() or ch in {"-", "_"}) or "anon"
    return rf"\\.\pipe\tqmemory-{safe_user}-{os.getpid()}"


def _read_lockfile(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _try_claim_lockfile(path: Path, endpoint: DaemonEndpoint) -> bool:
    """Atomically create the lockfile. Returns True if claim succeeded."""
    encoded = json.dumps(endpoint.to_lockfile()).encode("utf-8")
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)
    if platform.system() != "Windows":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return True


def _make_authkey() -> bytes:
    return secrets.token_bytes(AUTHKEY_BYTES)


def make_primary_endpoint(
    *,
    authkey: bytes | None = None,
    environ: Mapping[str, str] | None = None,
) -> DaemonEndpoint:
    key = authkey if authkey is not None else _make_authkey()
    if platform.system() == "Windows":
        return DaemonEndpoint(
            address=_windows_pipe_name(),
            family="AF_PIPE",
            authkey=key,
            pid=os.getpid(),
            server_version=__version__,
            protocol_version=DAEMON_PROTOCOL_VERSION,
        )
    socket_path = _unix_socket_path(environ)
    if socket_path.exists():
        try:
            socket_path.unlink()
        except OSError:
            pass
    return DaemonEndpoint(
        address=str(socket_path),
        family="AF_UNIX",
        authkey=key,
        pid=os.getpid(),
        server_version=__version__,
        protocol_version=DAEMON_PROTOCOL_VERSION,
    )


def maybe_existing_endpoint(
    path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> DaemonEndpoint | None:
    resolved_path = path or lockfile_path(environ)
    payload = _read_lockfile(resolved_path)
    if payload is None:
        return None
    try:
        endpoint = DaemonEndpoint.from_lockfile(payload)
    except (KeyError, ValueError, TypeError):
        return None
    if endpoint.protocol_version != DAEMON_PROTOCOL_VERSION:
        return None
    if not _is_pid_alive(endpoint.pid):
        return None
    return endpoint


def _send(conn: Any, payload: Mapping[str, Any]) -> None:
    conn.send(dict(payload))


def _recv(conn: Any, timeout: float) -> Any:
    if not conn.poll(timeout):
        raise TimeoutError("daemon connection timeout")
    return conn.recv()


_REBUILDABLE_ERRORS: dict[str, type[BaseException]] = {
    "ValueError": ValueError,
    "KeyError": KeyError,
    "FileNotFoundError": FileNotFoundError,
    "RuntimeError": RuntimeError,
    "TypeError": TypeError,
    "OSError": OSError,
}


def _reconstruct_error(error_type: str, message: str) -> BaseException:
    cls = _REBUILDABLE_ERRORS.get(error_type, RuntimeError)
    return cls(message)


class DaemonClient:
    """Thin RPC client used by proxy processes to reach the primary."""

    def __init__(self, endpoint: DaemonEndpoint) -> None:
        self._endpoint = endpoint
        self._lock = threading.Lock()
        self._conn: Any = None

    @property
    def endpoint(self) -> DaemonEndpoint:
        return self._endpoint

    def _connect(self) -> Any:
        conn = Client(self._endpoint.address, authkey=self._endpoint.authkey)
        try:
            _send(
                conn,
                {
                    "kind": MESSAGE_HELLO,
                    "client_version": __version__,
                    "protocol_version": DAEMON_PROTOCOL_VERSION,
                },
            )
            reply = _recv(conn, CONNECT_TIMEOUT_SECONDS)
            if not isinstance(reply, Mapping) or reply.get("kind") != MESSAGE_HELLO_ACK:
                raise ConnectionError(f"Unexpected hello reply: {reply!r}")
            if str(reply.get("protocol_version")) != DAEMON_PROTOCOL_VERSION:
                raise ConnectionError(
                    f"Protocol mismatch: daemon={reply.get('protocol_version')} "
                    f"client={DAEMON_PROTOCOL_VERSION}"
                )
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            raise
        return conn

    def _ensure_conn(self) -> Any:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def ping(self) -> None:
        with self._lock:
            self._ensure_conn()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def call(self, tool: str, kwargs: Mapping[str, Any]) -> Any:
        payload = {"kind": MESSAGE_CALL, "tool": tool, "kwargs": dict(kwargs)}
        with self._lock:
            conn = self._ensure_conn()
            try:
                _send(conn, payload)
                reply = _recv(conn, RPC_TIMEOUT_SECONDS)
            except (BrokenPipeError, ConnectionError, EOFError, OSError, TimeoutError):
                self._conn = None
                conn = self._ensure_conn()
                _send(conn, payload)
                reply = _recv(conn, RPC_TIMEOUT_SECONDS)
        if not isinstance(reply, Mapping):
            raise RuntimeError(f"Unexpected reply shape: {reply!r}")
        kind = reply.get("kind")
        if kind == MESSAGE_OK:
            return reply.get("result")
        if kind == MESSAGE_ERROR:
            raise _reconstruct_error(
                str(reply.get("error_type", "RuntimeError")),
                str(reply.get("message", "daemon error")),
            )
        raise RuntimeError(f"Unknown reply kind: {kind!r}")


class DaemonListener:
    """Accept proxy RPC calls in a background thread and dispatch via handler.

    The handler receives (tool_name, kwargs) and must return a JSON-serializable
    payload or raise an exception. All handler invocations are serialized under
    a single RLock so MemoryStore / LanceDB access stays single-writer.
    """

    def __init__(
        self,
        endpoint: DaemonEndpoint,
        handler: Callable[[str, Mapping[str, Any]], Any],
        *,
        dispatch_lock: threading.RLock | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._handler = handler
        self._dispatch_lock = dispatch_lock or threading.RLock()
        self._listener: Listener | None = None
        self._accept_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def endpoint(self) -> DaemonEndpoint:
        return self._endpoint

    @property
    def dispatch_lock(self) -> threading.RLock:
        return self._dispatch_lock

    def start(self) -> None:
        self._listener = Listener(
            self._endpoint.address,
            family=self._endpoint.family,
            authkey=self._endpoint.authkey,
            backlog=LISTENER_BACKLOG,
        )
        if self._endpoint.family == "AF_UNIX":
            try:
                os.chmod(self._endpoint.address, 0o600)
            except OSError:
                pass
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="tqmemory-daemon-accept",
            daemon=True,
        )
        self._accept_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass

    def _accept_loop(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while not self._stop_event.is_set():
            try:
                conn = listener.accept()
            except (OSError, EOFError):
                return
            except Exception:
                # Untrusted authkey, malformed handshake: swallow and keep serving.
                continue
            worker = threading.Thread(
                target=self._serve_conn,
                args=(conn,),
                name="tqmemory-daemon-worker",
                daemon=True,
            )
            worker.start()

    def _serve_conn(self, conn: Any) -> None:
        try:
            try:
                hello = _recv(conn, CONNECT_TIMEOUT_SECONDS)
            except (TimeoutError, EOFError, OSError):
                return
            if not isinstance(hello, Mapping) or hello.get("kind") != MESSAGE_HELLO:
                return
            _send(
                conn,
                {
                    "kind": MESSAGE_HELLO_ACK,
                    "server_version": self._endpoint.server_version,
                    "protocol_version": self._endpoint.protocol_version,
                },
            )
            while not self._stop_event.is_set():
                try:
                    if not conn.poll(1.0):
                        continue
                    msg = conn.recv()
                except (EOFError, OSError):
                    return
                if not isinstance(msg, Mapping) or msg.get("kind") != MESSAGE_CALL:
                    continue
                tool = str(msg.get("tool", ""))
                kwargs_raw = msg.get("kwargs") or {}
                kwargs: Mapping[str, Any] = kwargs_raw if isinstance(kwargs_raw, Mapping) else {}
                with self._dispatch_lock:
                    try:
                        result = self._handler(tool, kwargs)
                        reply: dict[str, Any] = {"kind": MESSAGE_OK, "result": result}
                    except Exception as exc:
                        reply = {
                            "kind": MESSAGE_ERROR,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                try:
                    _send(conn, reply)
                except (OSError, BrokenPipeError):
                    return
        finally:
            try:
                conn.close()
            except Exception:
                pass


@dataclass(frozen=True)
class BootstrapResult:
    role: str  # "primary" | "proxy" | "standalone"
    endpoint: DaemonEndpoint | None
    client: DaemonClient | None


def acquire_daemon_role(
    *,
    environ: Mapping[str, str] | None = None,
    max_retries: int = 5,
    retry_sleep_seconds: float = 0.1,
) -> BootstrapResult:
    """Decide whether this process should be primary, proxy, or standalone.

    Flow:
    1. If TQMEMORY_DAEMON_DISABLE set -> standalone (no lockfile, legacy behavior).
    2. If live lockfile points to a reachable primary -> proxy.
    3. Else try atomic claim (O_EXCL); on success -> primary.
    4. On contention, retry (another process may have won or left stale lock).
    5. After max_retries, give up and go standalone (safe fallback).
    """

    if daemon_is_disabled(environ):
        return BootstrapResult(role="standalone", endpoint=None, client=None)

    lock_path = lockfile_path(environ)
    for _ in range(max_retries):
        existing = maybe_existing_endpoint(lock_path, environ=environ)
        if existing is not None:
            try:
                client = DaemonClient(existing)
                client.ping()
                return BootstrapResult(role="proxy", endpoint=existing, client=client)
            except Exception:
                # Endpoint advertised but unreachable; treat as stale.
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
        elif lock_path.exists():
            # Lockfile present but owner is dead / payload unreadable / protocol
            # mismatch. Remove it so the next claim can succeed.
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

        new_endpoint = make_primary_endpoint(environ=environ)
        if _try_claim_lockfile(lock_path, new_endpoint):
            return BootstrapResult(role="primary", endpoint=new_endpoint, client=None)

        # Another process wrote the lockfile between our check and claim; wait and retry.
        time.sleep(retry_sleep_seconds)

    return BootstrapResult(role="standalone", endpoint=None, client=None)


def release_daemon_lock(
    endpoint: DaemonEndpoint | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Release the lockfile if we still own it (best-effort, idempotent)."""

    path = lockfile_path(environ)
    payload = _read_lockfile(path)
    if payload is None:
        return
    try:
        existing = DaemonEndpoint.from_lockfile(payload)
    except (KeyError, ValueError, TypeError):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    if endpoint is not None and existing.pid != endpoint.pid:
        return
    if endpoint is None and existing.pid != os.getpid():
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    if existing.family == "AF_UNIX":
        try:
            Path(existing.address).unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "AUTHKEY_BYTES",
    "BootstrapResult",
    "DAEMON_PROTOCOL_VERSION",
    "DaemonClient",
    "DaemonEndpoint",
    "DaemonListener",
    "ENV_DAEMON_DISABLE",
    "LOCKFILE_NAME",
    "MESSAGE_CALL",
    "MESSAGE_ERROR",
    "MESSAGE_HELLO",
    "MESSAGE_HELLO_ACK",
    "MESSAGE_OK",
    "UNIX_SOCKET_NAME",
    "acquire_daemon_role",
    "daemon_is_disabled",
    "lockfile_path",
    "make_primary_endpoint",
    "maybe_existing_endpoint",
    "release_daemon_lock",
]
