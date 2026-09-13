"""Selected-clip Quick Export wiring for Iteration 2."""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread, Signal
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.services import recovery_service
from tapesift.services.project_service import ProjectSession
from tapesift.ui_core.clip_editor import ClipEditor
from tapesift.ui_v2 import main_window as v2_main_window
from tapesift.ui_v2.main_window import MainWindowV2


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    for name in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def test_inspector_quick_export_button_emits_current_clip(qapp):
    editor = ClipEditor(AppSettings())
    clip = Clip(start_ms=1_000, end_ms=5_000, clip_title="Play 001")
    emitted = []
    editor.quick_export_requested.connect(emitted.append)

    editor.set_clip(clip)
    assert editor.quick_export_btn.text() == "Export Clip"
    editor.quick_export_btn.click()
    assert emitted == [clip.id]

    editor.set_clip(None)
    assert editor.quick_export_btn.isEnabled() is False


def test_quick_export_uses_only_selected_clip(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    settings = AppSettings()
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "out")
    monkeypatch.setattr(settings, "save", lambda *a, **k: None)
    recovery_service.mark_closed()
    window = MainWindowV2(settings)

    session = ProjectSession.create("Vs Duke", tmp_path, tmp_path / "out")
    first = Clip(
        start_ms=1_000, end_ms=6_000, clip_title="Play 001",
        export_preset="source_quality")
    second = Clip(
        start_ms=9_000, end_ms=15_000, clip_title="Play 002",
        export_preset="social_1080p")
    session.add_clip(first)
    session.add_clip(second)
    session.project.accurate_cut = False
    session.save()
    window._activate_session(session)
    # Activation syncs the current app export defaults into the project.
    window.session.project.accurate_cut = False

    calls = []
    monkeypatch.setattr(
        window, "_start_export",
        lambda *args, **kwargs: calls.append((args, kwargs)))

    window._select_row(1)
    window._quick_export_selected()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("individual", "social_1080p", False)
    assert kwargs["quick"] is True
    assert kwargs["clips"] == [second]

    second.enabled = False
    window._quick_export_selected()
    assert len(calls) == 1
    assert "excluded from export" in window.statusBar().currentMessage()

    window._close_project()
    window.close()


def test_export_stays_busy_until_worker_thread_has_exited(
        qapp, tmp_path, monkeypatch):
    """The next export must not open in the all_finished/isRunning race."""

    class GateExportWorker(QThread):
        job_started = Signal(str)
        job_progress = Signal(str, float, float)
        job_completed = Signal(str, str)
        job_failed = Signal(str, str)
        job_cancelled = Signal(str)
        all_finished = Signal()

        def __init__(self, _ffmpeg, _project, jobs, _clips_by_id, _accurate,
                     **kwargs):
            super().__init__(kwargs.get("parent"))
            self.jobs = jobs
            self.entered = threading.Event()
            self.release = threading.Event()

        def run(self):
            self.all_finished.emit()
            self.entered.set()
            self.release.wait(5)

        def cancel_current(self):
            self.release.set()

        def cancel_all(self):
            self.release.set()

        def cancel_queued(self, _job_id):
            return False

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    settings = AppSettings()
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "out")
    monkeypatch.setattr(settings, "save", lambda *a, **k: None)
    monkeypatch.setattr(v2_main_window, "ExportWorker", GateExportWorker)
    recovery_service.mark_closed()
    window = MainWindowV2(settings)

    source = tmp_path / "source.mp4"
    source.write_bytes(b"not decoded by the gated worker")
    session = ProjectSession.create("Vs Duke", tmp_path, tmp_path / "out")
    session.project.source_video_path = str(source)
    session.project.source_metadata.width = 320
    session.project.source_metadata.height = 180
    clip = Clip(start_ms=1_000, end_ms=6_000, clip_title="Play 001")
    session.add_clip(clip)
    session.save()
    window._activate_session(session)
    deadline = time.monotonic() + 5
    while not window._export_restore_complete \
            and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)
    assert window._export_restore_complete

    window._start_export(
        "individual", "social_1080p", True, clips=[clip], quick=True)
    # Clean exports now freeze and persist their inputs before launching.
    deadline = time.monotonic() + 5
    while window.export_worker is None and time.monotonic() < deadline:
        QTest.qWait(10)
    worker = window.export_worker
    assert worker is not None
    assert worker.entered.wait(2)
    qapp.processEvents()

    # all_finished has fired, but the QThread is deliberately still alive.
    assert worker.isRunning()
    assert window.export_worker is worker
    assert window.export_panel._running is True

    finished = QSignalSpy(worker.finished)
    worker.release.set()
    assert finished.wait(2_000)
    qapp.processEvents()

    assert window.export_worker is None
    assert window.export_panel._running is False

    window._close_project()
    window.close()
