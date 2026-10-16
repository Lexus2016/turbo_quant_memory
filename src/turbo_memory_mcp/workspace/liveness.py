"""Process liveness for workspace sessions, robust against PID reuse.

A bare PID check cannot tell a session's process from an unrelated process that
later received the same PID. Comparing the process start time closes that gap.
"""

from __future__ import annotations

import calendar
import ctypes
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
_MONTHS: Final = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
# Seconds between the Windows FILETIME epoch (1601-01-01 UTC) and the Unix epoch.
_WINDOWS_EPOCH_OFFSET_S: Final = 11644473600
_FILETIME_TICKS_PER_S: Final = 10_000_000

_cache_lock = threading.Lock()
# Successful lookups only, as pid -> (monotonic timestamp, start time). Failures are never cached.
_start_time_cache: dict[int, tuple[float, float]] = {}


def process_start_time(pid: int) -> float | None:
    """Return the start time of ``pid`` in seconds, or None when it cannot be determined.

    The clock is platform-specific, so values are comparable only between calls on the
    same host. Linux returns seconds since boot (not an epoch) so the value does not
    move when the wall clock is stepped. macOS and Windows return Unix epoch seconds.
    """
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

    A cached start time that disagrees with ``start_time`` may belong to an earlier
    process that held this pid, so it is re-fetched once without the cache before
    returning False. When the lookup fails, plain PID liveness decides.
    """
    if not _is_pid_alive(pid):
        _forget_start_time(pid)
        return False
    if start_time is None:
        return True
    cached = _get_cached_start_time(pid)
    if cached is not None and _same_start(cached, start_time):
        return True
    current = process_start_time(pid)
    if current is None:
        # The lookup failed, or the process exited while it ran.
        return _is_pid_alive(pid)
    _put_cached_start_time(pid, current)
    return _same_start(current, start_time)


def _same_start(current: float, expected: float) -> bool:
    return abs(current - expected) <= LIVENESS_TOLERANCE_S


def _get_cached_start_time(pid: int) -> float | None:
    with _cache_lock:
        entry = _start_time_cache.get(pid)
    if entry is None or time.monotonic() - entry[0] >= START_TIME_CACHE_S:
        return None
    return entry[1]


def _put_cached_start_time(pid: int, value: float) -> None:
    now = time.monotonic()
    with _cache_lock:
        for stale_pid, (stamp, _) in list(_start_time_cache.items()):
            if now - stamp >= START_TIME_CACHE_S:
                del _start_time_cache[stale_pid]
        _start_time_cache[pid] = (now, value)


def _forget_start_time(pid: int) -> None:
    # A pid observed dead may be reused; its cached start time must not vouch for the newcomer.
    with _cache_lock:
        _start_time_cache.pop(pid, None)


def _linux_start_time(pid: int) -> float | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        ticks_per_s = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        return None
    if ticks_per_s <= 0:
        return None
    # The command name (field 2) may contain spaces or parentheses; the remaining
    # fields start after its closing parenthesis. Field 22 is index 19 of that rest.
    fields = stat.rsplit(")", 1)[-1].split()
    try:
        ticks = int(fields[19])
    except (IndexError, ValueError):
        return None
    return ticks / ticks_per_s


def _macos_start_time(pid: int) -> float | None:
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=_PS_TIMEOUT_S,
            # C locale keeps the month names English; UTC keeps the repeated autumn hour unambiguous.
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return _parse_lstart(result.stdout)


def _parse_lstart(text: str) -> float | None:
    # ps lstart reads "Thu May 11 01:03:52 2017" (weekday, month, day, time, year). The month
    # is looked up in a fixed English table rather than with strptime, which follows LC_TIME.
    parts = text.split()
    if len(parts) != 5:
        return None
    _weekday, month_name, day, clock, year = parts
    month = _MONTHS.get(month_name)
    if month is None:
        return None
    try:
        hour, minute, second = (int(field) for field in clock.split(":"))
        return float(calendar.timegm((int(year), month, int(day), hour, minute, second)))
    except (ValueError, OverflowError):
        return None


def _windows_start_time(pid: int) -> float | None:
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    try:
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
    except (OSError, ValueError, OverflowError, ctypes.ArgumentError):
        # ctypes.ArgumentError: a pid outside the DWORD range.
        return None
    return ticks / _FILETIME_TICKS_PER_S - _WINDOWS_EPOCH_OFFSET_S
