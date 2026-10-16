from __future__ import annotations

import os
import subprocess
import sys

from turbo_memory_mcp.workspace.liveness import (
    LIVENESS_TOLERANCE_S,
    is_process_alive,
    process_start_time,
)


def test_current_process_is_alive() -> None:
    assert is_process_alive(os.getpid(), process_start_time(os.getpid())) is True


def test_exited_process_is_dead() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert is_process_alive(proc.pid, None) is False


def test_reused_pid_is_dead() -> None:
    start = process_start_time(os.getpid())
    assert start is not None
    assert is_process_alive(os.getpid(), start - 3600) is False


def test_start_time_is_stable() -> None:
    first = process_start_time(os.getpid())
    second = process_start_time(os.getpid())
    assert first is not None
    assert second is not None
    assert abs(first - second) <= LIVENESS_TOLERANCE_S


def test_start_time_within_tolerance_is_alive() -> None:
    start = process_start_time(os.getpid())
    assert start is not None
    assert is_process_alive(os.getpid(), start + LIVENESS_TOLERANCE_S / 2) is True
    assert is_process_alive(os.getpid(), start + LIVENESS_TOLERANCE_S * 5) is False


def test_unknown_start_time_trusts_pid_liveness() -> None:
    assert is_process_alive(os.getpid(), None) is True
    assert is_process_alive(0, None) is False
