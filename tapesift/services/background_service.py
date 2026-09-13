"""Central gate for all background media work.

Playback is the highest-priority system in TapeSift. Background jobs
(proxy encodes, play detection, thumbnails) share one machine with the
video decoder, and past regressions all had the same shape: a new feature
added its own background FFmpeg, each individually "polite", with nothing
stopping several from running at once or from running *while the user is
scrubbing*.

Low process priority is not enough on its own - it helps with CPU but not
with disk or memory bandwidth, and Windows' idle class is not absolute.

So every background FFmpeg process registers here, and this module can:

  * report whether a heavy job is already running (callers should not
    start a second one), and
  * genuinely SUSPEND every background process while the transport is
    active, resuming them once playback has been idle for a moment.

Suspension uses NtSuspendProcess/NtResumeProcess, the Windows equivalent
of SIGSTOP: the process stops consuming any CPU or I/O at all, then picks
up exactly where it left off.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading

log = logging.getLogger(__name__)

_PROCESS_SUSPEND_RESUME = 0x0800

_lock = threading.RLock()
_pids: set[int] = set()
_suspended = False
_transport_active = False


def _nt_call(pid: int, suspend: bool) -> bool:
    """Suspend or resume one process. Windows only; no-op elsewhere."""
    if os.name != "nt":
        return False
    kernel32 = ctypes.windll.kernel32
    ntdll = ctypes.windll.ntdll
    handle = kernel32.OpenProcess(_PROCESS_SUSPEND_RESUME, False, pid)
    if not handle:
        return False
    try:
        fn = ntdll.NtSuspendProcess if suspend else ntdll.NtResumeProcess
        return fn(handle) == 0
    finally:
        kernel32.CloseHandle(handle)


def register(pid: int) -> None:
    """Track a background FFmpeg process so it can be paused for playback."""
    with _lock:
        _pids.add(pid)
        # Started while the user is already scrubbing? Pause it immediately.
        if _transport_active:
            _nt_call(pid, True)


def unregister(pid: int) -> None:
    with _lock:
        _pids.discard(pid)


def active_job_count() -> int:
    with _lock:
        return len(_pids)


def is_busy() -> bool:
    """True when heavy background work is already in flight."""
    return active_job_count() > 0


def set_transport_active(active: bool) -> None:
    """Called by the player. Suspends background work during playback."""
    global _transport_active, _suspended
    with _lock:
        _transport_active = active
        if active and not _suspended:
            for pid in list(_pids):
                _nt_call(pid, True)
            _suspended = True
            if _pids:
                log.debug("Suspended %d background job(s) for transport",
                          len(_pids))
        elif not active and _suspended:
            for pid in list(_pids):
                _nt_call(pid, False)
            _suspended = False
            if _pids:
                log.debug("Resumed %d background job(s)", len(_pids))


def resume_all() -> None:
    """Failsafe: never leave a job suspended (e.g. on shutdown)."""
    set_transport_active(False)
