"""Process liveness for workspace sessions, robust against PID reuse.

A bare PID check cannot tell a session's process from an unrelated process that
later received the same PID. Comparing the process start time closes that gap.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Final

from turbo_memory_mcp.daemon import _is_pid_alive

LIVENESS_TOLERANCE_S: Final = 1.0
START_TIME_CACHE_S: Final = 60.0

_PS_TIMEOUT_S: Final = 5
_PS_TIME_FORMAT: Final = "%a %b %d %H:%M:%S %Y"
# Seconds between the Windows FILETIME epoch (1601-01-01 UTC) and the Unix epoch.
_WINDOWS_EPOCH_OFFSET_S: Final = 11644473600
_FILETIME_TICKS_PER_S: Final = 10_000_000

_cache_lock = threading.Lock()
_start_time_cache: dict[int, tuple[float, float | None]] = {}


def process_start_time(pid: int) -> float | None:
    """Return the process start time as Unix seconds, or None when unknown."""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        return _windows_start_time(pid)
    if sys.platform == "darwin":
        return _macos_start_time(pid)
    if sys.platform.startswith("linux"):
        return _linux_start_time(pid)
    return None


def is_process_alive(pid: int, start_time: float | None) -> bool:
    """Return True when ``pid`` runs and, if ``start_time`` is given, is the same process.

    An unknown current start time (the lookup failed) falls back to plain PID liveness.
    """
    if not _is_pid_alive(pid):
        _forget_start_time(pid)
        return False
    if start_time is None:
        return True
    current = _cached_start_time(pid)
    if current is None:
        return True
    return abs(current - start_time) <= LIVENESS_TOLERANCE_S


def _cached_start_time(pid: int) -> float | None:
    with _cache_lock:
        entry = _start_time_cache.get(pid)
    if entry is not None and time.monotonic() - entry[0] < START_TIME_CACHE_S:
        return entry[1]
    value = process_start_time(pid)
    now = time.monotonic()
    with _cache_lock:
        for stale_pid, (stamp, _) in list(_start_time_cache.items()):
            if now - stamp >= START_TIME_CACHE_S:
                del _start_time_cache[stale_pid]
        _start_time_cache[pid] = (now, value)
    return value


def _forget_start_time(pid: int) -> None:
    # A pid observed dead may be reused; its cached start time must not vouch for the newcomer.
    with _cache_lock:
        _start_time_cache.pop(pid, None)


def _linux_start_time(pid: int) -> float | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        boot_time = _linux_boot_time()
    except OSError:
        return None
    if boot_time is None:
        return None
    # The command name (field 2) may contain spaces or parentheses; the remaining
    # fields start after its closing parenthesis. Field 22 is index 19 of that rest.
    fields = stat.rsplit(")", 1)[-1].split()
    try:
        ticks = int(fields[19])
    except (IndexError, ValueError):
        return None
    return boot_time + ticks / os.sysconf("SC_CLK_TCK")


def _linux_boot_time() -> float | None:
    try:
        text = Path("/proc/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("btime "):
            try:
                return float(int(line.split()[1]))
            except (IndexError, ValueError):
                return None
    return None


def _macos_start_time(pid: int) -> float | None:
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=_PS_TIMEOUT_S,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = " ".join(result.stdout.split())
    if result.returncode != 0 or not text:
        return None
    try:
        return time.mktime(time.strptime(text, _PS_TIME_FORMAT))
    except (ValueError, OverflowError):
        return None


def _windows_start_time(pid: int) -> float | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        creation, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)
    return ticks / _FILETIME_TICKS_PER_S - _WINDOWS_EPOCH_OFFSET_S
