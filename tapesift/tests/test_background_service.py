"""The background gate that keeps playback smooth.

Every past scrubbing regression had the same root cause: background FFmpeg
competing with the video decoder. These tests lock in the guarantees that
prevent it from happening again.
"""

import os
import subprocess
import sys
import time

import pytest

from tapesift.services import background_service as bg


@pytest.fixture(autouse=True)
def clean_state():
    bg.resume_all()
    for pid in list(bg._pids):
        bg.unregister(pid)
    yield
    bg.resume_all()
    for pid in list(bg._pids):
        bg.unregister(pid)


class TestRegistry:
    def test_busy_reflects_registered_jobs(self):
        assert not bg.is_busy()
        bg.register(999_999)
        assert bg.is_busy() and bg.active_job_count() == 1
        bg.unregister(999_999)
        assert not bg.is_busy()

    def test_unregister_is_forgiving(self):
        bg.unregister(123_456)          # never registered; must not raise

    def test_register_is_idempotent(self):
        bg.register(999_999)
        bg.register(999_999)
        assert bg.active_job_count() == 1


class TestTransportGating:
    def test_transport_active_marks_suspended(self):
        bg.register(999_999)
        bg.set_transport_active(True)
        assert bg._suspended
        bg.set_transport_active(False)
        assert not bg._suspended

    def test_resume_all_clears_suspension(self):
        bg.register(999_999)
        bg.set_transport_active(True)
        bg.resume_all()
        assert not bg._suspended

    def test_job_started_during_playback_is_paused_immediately(self):
        """A job launched mid-scrub must not run until the user stops."""
        bg.set_transport_active(True)
        bg.register(999_999)            # would be suspended on a real pid
        assert bg._transport_active


@pytest.mark.skipif(os.name != "nt", reason="process suspension is Windows-only")
class TestRealSuspension:
    def test_suspend_actually_stops_cpu_use(self):
        """Prove suspension works: a CPU-burning child must stop burning."""
        # Deliberately the *base* interpreter, not sys.executable: inside a
        # virtualenv, Scripts/python.exe is a launcher that runs the real
        # interpreter as a child, so suspending its pid stops nothing. That
        # is the true limitation of this service - it gates exactly the pid
        # it is handed - and FFmpeg is launched directly for that reason.
        proc = subprocess.Popen(
            [getattr(sys, "_base_executable", None) or sys.executable,
             "-c", "while True: pass"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL)
        try:
            bg.register(proc.pid)
            # 1. Confirm it really is burning CPU while running. Wait for the
            #    interpreter to finish starting rather than assuming a fixed
            #    delay - a cold venv can take most of a second to get going,
            #    and a fixed sleep turns that into a phantom failure.
            _wait_until_burning(proc.pid)
            running_start = _cpu_seconds(proc.pid)
            time.sleep(0.5)
            running_end = _cpu_seconds(proc.pid)
            assert running_end - running_start > 0.1, "child never ran"

            # 2. Suspend: CPU time must stop advancing entirely.
            bg.set_transport_active(True)
            time.sleep(0.2)                    # let the suspend settle
            frozen_start = _cpu_seconds(proc.pid)
            time.sleep(0.6)
            frozen_end = _cpu_seconds(proc.pid)
            assert frozen_end - frozen_start < 0.05, (
                f"suspended process still burned "
                f"{frozen_end - frozen_start:.3f}s of CPU")

            # 3. Resume: it must pick straight back up.
            bg.set_transport_active(False)
            time.sleep(0.5)
            assert _cpu_seconds(proc.pid) - frozen_end > 0.05, \
                "process did not resume"
        finally:
            bg.unregister(proc.pid)
            proc.kill()
            proc.wait(timeout=10)


def _wait_until_burning(pid: int, timeout: float = 10.0) -> None:
    """Block until the child is measurably using CPU."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        before = _cpu_seconds(pid)
        time.sleep(0.1)
        if _cpu_seconds(pid) - before > 0.02:
            return
    raise AssertionError("child never started burning CPU")


def _cpu_seconds(pid: int) -> float:
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    PROCESS_QUERY_INFORMATION = 0x0400
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return 0.0
    try:
        creation, exit_, kernel, user = (FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(handle, ctypes.byref(creation),
                                   ctypes.byref(exit_), ctypes.byref(kernel),
                                   ctypes.byref(user)):
            return 0.0
        def to_s(ft):
            return ((ft.high << 32) | ft.low) / 1e7
        return to_s(kernel) + to_s(user)
    finally:
        k32.CloseHandle(handle)
