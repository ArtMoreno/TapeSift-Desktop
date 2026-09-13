"""Background workers: owned until their thread exits, and joined at close.

Qt aborts the process when a running QThread is destroyed. Every worker here
is parented to the main window, so "still running when the window goes" is
not a leak - it is a crash, and it was reachable by dropping a large file on
the start screen and closing straight away.
"""

from __future__ import annotations

import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.services import recovery_service
from tapesift.services.project_service import ProjectSession
from tapesift.ui_core import main_window_workflow
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
from tapesift.ui_v2.main_window import MainWindowV2


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeWorker:
    def __init__(self, running: bool = True):
        self.running = running
        self.cancelled = False
        self.stopped = False
        self.deleted = False
        self._cancel_all = False
        self.jobs: tuple = ()

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True

    def cancel_current(self):
        self.cancelled = True

    def stop(self):
        self.stopped = True

    def deleteLater(self):
        self.deleted = True


class ProxyHarness:
    _stop_proxy_worker = MainWindowWorkflow._stop_proxy_worker
    _proxy_worker_finished = MainWindowWorkflow._proxy_worker_finished

    def __init__(self, worker):
        self.proxy_worker = worker
        self.session = None
        self._proxy_retry_timer = Mock()


class ThumbnailHarness:
    _stop_thumbnails = MainWindowWorkflow._stop_thumbnails
    _thumbnail_worker_finished = MainWindowWorkflow._thumbnail_worker_finished
    _thumbnail_ready = MainWindowWorkflow._thumbnail_ready

    def __init__(self, worker):
        self.thumb_worker = worker
        self._pending_thumbnail_clips = None
        self.session = None


def test_proxy_reference_is_kept_while_cancelled_worker_is_running():
    worker = FakeWorker()
    owner = ProxyHarness(worker)

    owner._stop_proxy_worker()

    assert worker.cancelled is True
    assert owner.proxy_worker is worker
    worker.running = False
    owner._proxy_worker_finished(worker)
    assert owner.proxy_worker is None
    assert worker.deleted is True


def test_thumbnail_reference_is_kept_while_worker_is_stopping():
    worker = FakeWorker()
    owner = ThumbnailHarness(worker)

    owner._stop_thumbnails()

    assert worker.stopped is True
    assert owner.thumb_worker is worker
    worker.running = False
    owner._thumbnail_worker_finished(worker)
    assert owner.thumb_worker is None
    assert worker.deleted is True


def test_thumbnail_completion_does_not_mark_user_work_dirty(tmp_path):
    session = ProjectSession.create("Game", tmp_path, tmp_path / "out")
    clip = session.add_clip(Clip(start_ms=1_000, end_ms=5_000))
    owner = ThumbnailHarness(None)
    owner.session = session

    owner._thumbnail_ready(clip.id, str(tmp_path / "thumb.jpg"))

    assert clip.thumbnail_path == str(tmp_path / "thumb.jpg")
    assert session.dirty is False
    db_path = session.db_path
    session.close()
    reopened = ProjectSession.open(db_path)
    assert reopened.get_clip(clip.id).thumbnail_path == str(
        tmp_path / "thumb.jpg")
    reopened.close()


class FakeSignal:
    def __init__(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def connect(self, _slot):
        pass


class JoinableWorker(FakeWorker):
    """A worker whose wait() succeeds or times out on demand."""

    def __init__(self, *, stops: bool):
        super().__init__(running=True)
        self.stops = stops
        self.waited_ms = None
        self.unparented = False
        self.finished_ok = FakeSignal()
        self.failed = FakeSignal()
        self.finished = FakeSignal()

    def wait(self, ms):
        self.waited_ms = ms
        return self.stops

    def setParent(self, parent):
        self.unparented = parent is None


class JoinHarness:
    _join_worker = MainWindowWorkflow._join_worker


class TestJoiningWorkersAtClose:
    def test_a_worker_that_stops_in_time_is_not_detached(self):
        worker = JoinableWorker(stops=True)
        before = list(main_window_workflow._detached_workers)

        JoinHarness()._join_worker(worker)

        assert worker.stopped is True
        assert worker.waited_ms == 3000
        assert worker.unparented is False
        assert main_window_workflow._detached_workers == before

    def test_result_signals_are_dropped_before_waiting(self):
        # A probe landing after this point would be delivered into a window
        # that is already tearing itself down.
        worker = JoinableWorker(stops=True)

        JoinHarness()._join_worker(worker)

        assert worker.finished_ok.connected is False
        assert worker.failed.connected is False

    def test_a_worker_that_will_not_stop_is_detached_not_destroyed(self):
        worker = JoinableWorker(stops=False)
        try:
            JoinHarness()._join_worker(worker, ms=10)
            # Unparented so Qt cannot destroy it with the window, and held
            # so Python cannot collect the wrapper while C++ still runs.
            assert worker.unparented is True
            assert worker in main_window_workflow._detached_workers
        finally:
            main_window_workflow._forget_detached(worker)

    def test_a_finished_worker_stops_being_held(self):
        worker = JoinableWorker(stops=False)
        JoinHarness()._join_worker(worker, ms=10)
        assert worker in main_window_workflow._detached_workers

        main_window_workflow._forget_detached(worker)

        assert worker not in main_window_workflow._detached_workers
        # Idempotent: the finished signal can arrive after a manual sweep.
        main_window_workflow._forget_detached(worker)


class TestDecliningToCancelAnExportKeepsTheWindowOpen:
    """Answering "no" used to close the window anyway.

    `closeEvent` called `_close_project`, ignored its early return, and
    accepted the event - so the export prompt's "no" was collected and
    discarded, along with the session save, the library index flush and the
    recovery marker that a real close performs.
    """

    @pytest.fixture
    def window(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        settings = AppSettings(onboarding_seen=True, recent_projects=[])
        settings.default_project_folder = str(tmp_path)
        settings.default_output_folder = str(tmp_path / "out")
        monkeypatch.setattr(settings, "save", lambda *a, **k: None)
        recovery_service.mark_closed()
        window = MainWindowV2(settings)
        session = ProjectSession.create("Vs Duke", tmp_path, tmp_path / "out")
        window._activate_session(session)
        yield window
        window.export_worker = None

    def _answer(self, monkeypatch, button):
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: button))

    def test_no_keeps_the_project_open_and_refuses_the_close(
            self, window, monkeypatch):
        window.export_worker = FakeWorker(running=True)
        self._answer(monkeypatch, QMessageBox.StandardButton.No)

        assert window._close_project() is False
        assert window.session is not None
        assert window.close() is False
        assert window.isVisible() or window.session is not None

    def test_yes_cancels_the_export_and_closes(self, window, monkeypatch):
        worker = FakeWorker(running=True)
        worker.wait = lambda ms: True
        worker.finished = FakeSignal()
        window.export_worker = worker
        self._answer(monkeypatch, QMessageBox.StandardButton.Yes)

        assert window._close_project() is True
        assert worker.cancelled is True
        assert window.session is None

    def test_a_plain_close_still_reports_success(self, window):
        assert window._close_project() is True
        assert window.session is None

    def test_declining_while_switching_projects_leaks_nothing(
            self, window, monkeypatch, tmp_path):
        """Same bug in the other direction: opening a second project.

        _activate_session called _close_project, ignored the answer, and
        overwrote self.session anyway - leaving the first project open,
        unsaved and unreferenced, its file handle held for the life of the
        process.
        """
        first = window.session
        window.export_worker = FakeWorker(running=True)
        self._answer(monkeypatch, QMessageBox.StandardButton.No)
        incoming = ProjectSession.create("Vs Pitt", tmp_path, tmp_path / "out")

        assert window._activate_session(incoming) is False

        # The project on screen is still the original one...
        assert window.session is first
        # ...and the one we refused is closed, not orphaned.
        with pytest.raises(Exception):
            incoming.conn.execute("SELECT 1")
