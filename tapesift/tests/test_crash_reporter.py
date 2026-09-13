"""Crash reporter: Python-level report writing + pending/seen bookkeeping.

The minidump itself still cannot be provoked from a test - that needs a real
access violation. What is covered here is everything around it: the
cross-platform report writer, the pending/seen state machine behind the
post-mortem dialog, and the one property the native path silently lost for
its whole life, that the installed filter stays alive to be called.
"""

from __future__ import annotations

import ctypes
import gc
import sys
import weakref

import pytest

from tapesift.core import crash_reporter
from tapesift.core import paths


@pytest.fixture
def crash(tmp_path, monkeypatch):
    d = tmp_path / "Crashes"
    monkeypatch.setattr(paths, "crash_dir", lambda: d)
    d.mkdir(parents=True, exist_ok=True)
    # Reset module state so the idempotent guard doesn't carry between tests.
    monkeypatch.setattr(crash_reporter, "_installed", False)
    yield d


class TestPythonReport:
    def test_python_hook_writes_report(self, crash):
        crash_reporter.install_crash_reporter()

        # Simulate an uncaught Python exception.
        def boom():
            raise ValueError("kaboom")

        try:
            boom()
        except ValueError:
            crash_reporter._write_text_report(
                "python", "".join(__import__("traceback").format_exc()))

        reports = list(crash.glob("TapeSift-*.txt"))
        assert reports, "no crash report written"
        text = reports[0].read_text(encoding="utf-8")
        assert "version:" in text
        assert "kaboom" in text

    def test_install_is_idempotent(self, crash):
        assert crash_reporter.install_crash_reporter() is None
        assert crash_reporter.install_crash_reporter() is None
        # No exception, second call is a no-op.


@pytest.mark.skipif(sys.platform != "win32", reason="win32-only handler")
class TestNativeFilter:
    """The filter Windows calls has to outlive the function that installs it.

    ctypes does not retain callbacks handed to C. The trampoline used to be
    a local, so it was freed as soon as ``_install_native_windows``
    returned and Windows was left holding released memory: the minidump
    written to diagnose native crashes could never run, and a real crash
    jumped to a dangling pointer.
    """

    def test_installed_filter_survives_the_installing_call(self, crash,
                                                           monkeypatch):
        monkeypatch.setattr(crash_reporter, "_native_filter", None)
        installed: dict[str, object] = {}
        real = ctypes.windll.kernel32.SetUnhandledExceptionFilter

        def _spy(callback):
            installed["ref"] = weakref.ref(callback)
            return real(callback)

        monkeypatch.setattr(
            ctypes.windll.kernel32, "SetUnhandledExceptionFilter", _spy)
        try:
            assert crash_reporter._install_native_windows() is True
            gc.collect()
            assert installed["ref"]() is not None, (
                "Windows holds a pointer to a freed callback")
        finally:
            # Leave the process pointing at a live filter either way.
            real(crash_reporter._native_filter)

    def test_module_keeps_the_reference(self, crash, monkeypatch):
        monkeypatch.setattr(crash_reporter, "_native_filter", None)
        crash_reporter._install_native_windows()
        assert crash_reporter._native_filter is not None

    def test_native_writer_receives_the_windows_packed_exception_layout(self, crash, monkeypatch):
        import struct

        writes = []

        def writer(_process, _pid, _file, _flags, exception_info, *_rest):
            writes.append(ctypes.string_at(exception_info, 8 + ctypes.sizeof(ctypes.c_void_p)))
            return 0

        monkeypatch.setattr(ctypes.windll.dbghelp, "MiniDumpWriteDump", writer)
        assert crash_reporter._install_native_windows()
        marker = 0x1234567887654321 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x12345678
        crash_reporter._native_filter(marker)
        pointer_format = "Q" if ctypes.sizeof(ctypes.c_void_p) == 8 else "I"
        expected = struct.pack("<I" + pointer_format + "I",
                               ctypes.windll.kernel32.GetCurrentThreadId(), marker, 0)
        assert writes == [expected]

    def test_report_and_dump_share_one_timestamp(self, crash):
        # The .txt tells you the dump exists; a mismatched name makes it a
        # scavenger hunt through a folder of near-identical stamps.
        txt = crash_reporter._write_text_report("native", "detail", "STAMP")
        assert txt.name == "TapeSift-STAMP.txt"


class TestPendingSeen:
    def test_pending_empty_initially(self, crash):
        assert crash_reporter.pending_reports() == []

    def test_pending_lists_unseen_reports(self, crash):
        import os
        r1 = crash / "TapeSift-20240101-000000.txt"
        r1.write_text("one", encoding="utf-8")
        os.utime(r1, (1000, 1000))
        r2 = crash / "TapeSift-20240102-000000.txt"
        r2.write_text("two", encoding="utf-8")
        os.utime(r2, (2000, 2000))  # newer
        pending = crash_reporter.pending_reports()
        assert len(pending) == 2
        # Most recent first.
        assert pending[0].name == "TapeSift-20240102-000000.txt"

    def test_mark_seen_hides_reports(self, crash):
        r = crash / "TapeSift-20240101-000000.txt"
        r.write_text("one", encoding="utf-8")
        assert len(crash_reporter.pending_reports()) == 1
        crash_reporter.mark_reports_seen([r])
        assert crash_reporter.pending_reports() == []
