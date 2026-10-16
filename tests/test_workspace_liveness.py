from __future__ import annotations

import calendar
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from turbo_memory_mcp.workspace import liveness
from turbo_memory_mcp.workspace.liveness import (
    LIVENESS_TOLERANCE_S,
    is_process_alive,
    process_start_time,
)


@pytest.fixture(autouse=True)
def _clear_start_time_cache() -> Iterator[None]:
    liveness._start_time_cache.clear()
    yield
    liveness._start_time_cache.clear()


def _recording_lookup(values: list[float | None]) -> tuple[list[int], Callable[[int], float | None]]:
    calls: list[int] = []
    queue = list(values)

    def lookup(pid: int) -> float | None:
        calls.append(pid)
        return queue.pop(0)

    return calls, lookup


def test_current_process_is_alive() -> None:
    start = process_start_time(os.getpid())
    assert start is not None
    assert is_process_alive(os.getpid(), start) is True


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


def test_matching_cached_start_skips_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, lookup = _recording_lookup([])
    monkeypatch.setattr(liveness, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(liveness, "process_start_time", lookup)
    liveness._put_cached_start_time(4242, 1000.0)
    assert is_process_alive(4242, 1000.0) is True
    assert calls == []


def test_mismatched_cached_start_refetches_once_and_accepts_newcomer(monkeypatch: pytest.MonkeyPatch) -> None:
    # Cached 1000.0 belongs to the process that held this pid before; the live newcomer started at 2000.0.
    calls, lookup = _recording_lookup([2000.0])
    monkeypatch.setattr(liveness, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(liveness, "process_start_time", lookup)
    liveness._put_cached_start_time(4242, 1000.0)
    assert is_process_alive(4242, 2000.0) is True
    assert calls == [4242]


def test_mismatch_after_refetch_is_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, lookup = _recording_lookup([2000.0])
    monkeypatch.setattr(liveness, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(liveness, "process_start_time", lookup)
    liveness._put_cached_start_time(4242, 1000.0)
    assert is_process_alive(4242, 3000.0) is False
    assert calls == [4242]


def test_failed_lookup_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, lookup = _recording_lookup([None, 5.0])
    monkeypatch.setattr(liveness, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(liveness, "process_start_time", lookup)
    assert is_process_alive(4242, 5.0) is True
    assert 4242 not in liveness._start_time_cache
    assert is_process_alive(4242, 5.0) is True
    assert calls == [4242, 4242]


def test_death_during_lookup_is_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    alive_answers = iter([True, False])
    monkeypatch.setattr(liveness, "_is_pid_alive", lambda pid: next(alive_answers))
    monkeypatch.setattr(liveness, "process_start_time", lambda pid: None)
    assert is_process_alive(4242, 5.0) is False


def test_successful_lookup_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, lookup = _recording_lookup([7.0])
    monkeypatch.setattr(liveness, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(liveness, "process_start_time", lookup)
    assert is_process_alive(4242, 7.0) is True
    assert is_process_alive(4242, 7.0) is True
    assert calls == [4242]


def test_lstart_parses_as_utc_with_english_month_names() -> None:
    assert liveness._parse_lstart("Thu May 11 01:03:52 2017") == float(calendar.timegm((2017, 5, 11, 1, 3, 52)))
    # ps pads single-digit days with a space.
    assert liveness._parse_lstart("Thu May  1 01:03:52 2017\n") == float(calendar.timegm((2017, 5, 1, 1, 3, 52)))
    assert liveness._parse_lstart("") is None
    assert liveness._parse_lstart("Thu Foo 11 01:03:52 2017") is None


def test_macos_lookup_runs_ps_in_c_locale_and_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="Thu May 11 01:03:52 2017\n", stderr="")

    monkeypatch.setattr(liveness.subprocess, "run", fake_run)
    assert liveness._macos_start_time(4242) == float(calendar.timegm((2017, 5, 11, 1, 3, 52)))
    assert seen["env"]["LC_ALL"] == "C"
    assert seen["env"]["TZ"] == "UTC"


def test_macos_lookup_failure_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 5)

    monkeypatch.setattr(liveness.subprocess, "run", fake_run)
    assert liveness._macos_start_time(4242) is None


def _proc_stat_line(pid: int, comm: str, starttime_ticks: int) -> str:
    # Fields 3..52 of /proc/<pid>/stat; field 22 (starttime) is the one under test.
    rest = ["S"] + [str(n) for n in range(4, 53)]
    rest[22 - 3] = str(starttime_ticks)
    return f"{pid} ({comm}) " + " ".join(rest)


def test_linux_start_time_converts_field_22_to_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    # Platform-independent: the /proc read and sysconf are faked, so this runs on every OS.
    read_paths: list[str] = []
    stat_text = _proc_stat_line(4321, "my (odd) name", 987654)

    class FakePath:
        def __init__(self, path: str) -> None:
            read_paths.append(path)

        def read_text(self, **_: object) -> str:
            return stat_text

    monkeypatch.setattr(liveness, "Path", FakePath)
    monkeypatch.setattr(liveness.os, "sysconf", lambda name: 100 if name == "SC_CLK_TCK" else -1, raising=False)
    assert liveness._linux_start_time(4321) == 9876.54
    assert read_paths == ["/proc/4321/stat"]


def test_linux_start_time_failures_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class MissingPath:
        def __init__(self, path: str) -> None:
            pass

        def read_text(self, **_: object) -> str:
            raise FileNotFoundError("/proc/4321/stat")

    monkeypatch.setattr(liveness, "Path", MissingPath)
    assert liveness._linux_start_time(4321) is None

    class TruncatedPath(MissingPath):
        def read_text(self, **_: object) -> str:
            return "4321 (short) S 1"

    monkeypatch.setattr(liveness, "Path", TruncatedPath)
    monkeypatch.setattr(liveness.os, "sysconf", lambda name: 100, raising=False)
    assert liveness._linux_start_time(4321) is None


def test_lstart_calendar_fault_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_timegm(_: object) -> int:
        raise OSError("injected")

    monkeypatch.setattr(liveness.calendar, "timegm", failing_timegm)
    assert liveness._parse_lstart("Thu May 11 01:03:52 2017") is None
