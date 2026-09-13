"""Crash reporting for TapeSift (local-first, no telemetry).

Two layers:

1. Native (Windows): installs a ``SetUnhandledExceptionFilter`` handler that
   writes a minidump via ``dbghelp.MiniDumpWriteDump``. This is the ONLY way to
   catch the kind of crash seen in the field (#3: a native ``0xC0000409``
   ``/GS`` stack-buffer-overrun in the Qt/FFmpeg/WMF media layer). Python's
   ``sys.excepthook`` never fires for those - the process just dies in C++.

2. Python (cross-platform): ``sys.excepthook`` catches uncaught Python
   exceptions and writes a readable report next to the minidump.

Both write to ``paths.crash_dir()`` as ``TapeSift-<timestamp>.{dmp,txt}``.
The ``.txt`` is human-readable; the ``.dmp`` is machine-readable and can be
fed to ``scripts/analyze_minidump.py`` (or WinDbg). Nothing is sent anywhere
- TapeSift is local-first, so the user carries the report file to you.

The handler is intentionally minimal: from inside the native filter we only
write a file. We do NOT touch other Python objects, the Qt event loop, or
logging, because the process is already corrupted.
"""

from __future__ import annotations

import ctypes
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from tapesift import __version__
from tapesift.core import paths

# --- module state -----------------------------------------------------------
_installed = False
_lock = threading.Lock()
#: The native filter, kept alive for the life of the process.
#:
#: ctypes does not retain callbacks it hands to C. Without a reference here
#: the WINFUNCTYPE trampoline built in ``_install_native_windows`` is freed
#: the moment that function returns, and ``SetUnhandledExceptionFilter`` is
#: left pointing at released memory - so the one handler written to diagnose
#: native crashes could not run, and a real crash jumped to a dangling
#: pointer instead, corrupting the very dump it was supposed to produce.
_native_filter = None

_WINDOWS = sys.platform == "win32"

# dbghelp / win32 constants
_MINIDUMP_NORMAL = 0x00000000
_GENERIC_WRITE = 0x40000000
_CREATE_ALWAYS = 2
_FILE_ATTRIBUTE_NORMAL = 0x80
_FILE_SHARE_READ = 1
_INVALID_HANDLE = ctypes.c_void_p(-1)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _write_text_report(reason: str, detail: str = "", stamp: str = "") -> Path:
    """Write a readable crash report; returns its path.

    ``stamp`` lets the native path name the .txt and its .dmp from one
    timestamp. Taking a fresh one here meant the pair could straddle a
    second boundary and land with names that do not match, which is a poor
    way to find a dump.
    """
    d = paths.crash_dir()
    txt = d / f"TapeSift-{stamp or _stamp()}.txt"
    lines = [
        "TapeSift crash report",
        f"version:   {__version__}",
        f"platform:  {platform.system()} {platform.release()} "
        f"({platform.machine()})",
        f"python:    {platform.python_version()}",
        f"time:      {datetime.now().isoformat(timespec='seconds')}",
        f"kind:      {reason}",
        "",
    ]
    if detail:
        lines.append(detail)
        lines.append("")
    lines.append(
        "This file is local-only. Send it (and the matching .dmp if present) "
        "to the developer to help diagnose the crash.")
    try:
        txt.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass
    return txt


# --- native Windows handler -------------------------------------------------
def _install_native_windows() -> bool:
    """Install a Windows unhandled-exception filter (win32 only).

    Returns True if the filter was installed. The filter writes a minidump
    plus a matching .txt report. It returns EXCEPTION_EXECUTE_HANDLER so the
    process terminates cleanly after the dump is written.
    """
    if not _WINDOWS:
        return False
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        dbghelp = ctypes.windll.dbghelp     # type: ignore[attr-defined]
    except AttributeError:
        return False

    class MINIDUMP_EXCEPTION_INFORMATION(ctypes.Structure):
        # minidumpapiset.h uses pack(4), including the pointer on Win64.
        _pack_ = 4
        _fields_ = [
            ("ThreadId", ctypes.c_uint32),
            ("ExceptionPointers", ctypes.c_void_p),
            ("ClientPointers", ctypes.c_uint32),
        ]

    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetCurrentThread.argtypes = []
    kernel32.GetCurrentThread.restype = ctypes.c_void_p
    kernel32.GetCurrentProcessId.argtypes = []
    kernel32.GetCurrentProcessId.restype = ctypes.c_uint32

    # Returns the previous filter - a pointer, so it needs declaring on
    # Win64 like every other call here, or ctypes truncates it to 32 bits.
    kernel32.SetUnhandledExceptionFilter.restype = ctypes.c_void_p

    dbghelp.MiniDumpWriteDump.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    dbghelp.MiniDumpWriteDump.restype = ctypes.c_int

    @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
    def _filter(exception_pointers):
        try:
            # One timestamp for both files so the .txt names its own .dmp.
            stamp = _stamp()
            _write_text_report(
                "native", "Unhandled native exception; minidump written.",
                stamp)
            dmp_path = str(paths.crash_dir() / f"TapeSift-{stamp}.dmp")
            hfile = kernel32.CreateFileW(
                dmp_path, _GENERIC_WRITE, _FILE_SHARE_READ, None,
                _CREATE_ALWAYS, _FILE_ATTRIBUTE_NORMAL, None)
            if hfile and hfile != _INVALID_HANDLE.value:
                info = MINIDUMP_EXCEPTION_INFORMATION()
                info.ThreadId = kernel32.GetCurrentThreadId()
                info.ExceptionPointers = exception_pointers
                info.ClientPointers = 0
                dbghelp.MiniDumpWriteDump(
                    kernel32.GetCurrentProcess(),
                    kernel32.GetCurrentProcessId(),
                    hfile, _MINIDUMP_NORMAL, ctypes.byref(info), None, None)
                kernel32.CloseHandle(hfile)
        except Exception:
            pass
        # EXCEPTION_EXECUTE_HANDLER: let the process terminate cleanly.
        return 1

    global _native_filter
    try:
        # Assign before installing: once the filter is live, Windows can call
        # it, and it must already be owned by something that outlives us.
        _native_filter = _filter
        kernel32.SetUnhandledExceptionFilter(_filter)
        return True
    except Exception:
        _native_filter = None
        return False


# --- Python handler ---------------------------------------------------------
def _install_python_handler() -> bool:
    def _hook(exc_type, exc_value, exc_tb):
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            _write_text_report("python", detail)
        except Exception:
            pass
        # Preserve the default behavior (print to stderr).
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    return True


# --- public API -------------------------------------------------------------
def install_crash_reporter() -> None:
    """Install both crash handlers. Idempotent and safe to call once at boot."""
    global _installed
    with _lock:
        if _installed:
            return
        _install_native_windows()
        _install_python_handler()
        _installed = True


def pending_reports() -> list[Path]:
    """Return unread crash .txt reports (most recent first)."""
    d = paths.crash_dir()
    seen = d / ".seen"
    read: set[str] = set()
    if seen.exists():
        try:
            read = {line.strip() for line in seen.read_text().splitlines()
                    if line.strip()}
        except OSError:
            read = set()
    reports = sorted(d.glob("TapeSift-*.txt"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return [p for p in reports if p.name not in read]


def mark_reports_seen(reports: list[Path]) -> None:
    seen = paths.crash_dir() / ".seen"
    try:
        existing = seen.read_text().splitlines() if seen.exists() else []
        names = {p.name for p in reports}
        merged = [n for n in existing if n not in names] + list(names)
        seen.write_text("\n".join(merged), encoding="utf-8")
    except OSError:
        pass
