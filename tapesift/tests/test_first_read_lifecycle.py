"""First Read commits and teardown, without constructing a multimedia window."""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QDialog

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.services import first_read_batch, first_read_service as fr
from tapesift.services.project_service import ProjectSession
from tapesift.ui_core import main_window_workflow as workflow
from tapesift.ui_core.clip_editor import ClipEditor
from tapesift.workers.first_read_worker import FirstReadWorker


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class Owner(QObject):
    _first_read = workflow.MainWindowWorkflow._first_read
    _store_first_read = workflow.MainWindowWorkflow._store_first_read
    _first_read_finished = workflow.MainWindowWorkflow._first_read_finished
    _first_read_thread_finished = workflow.MainWindowWorkflow._first_read_thread_finished
    _stop_background_workers = workflow.MainWindowWorkflow._stop_background_workers

    def __init__(self, session, clip):
        super().__init__()
        self.session = session
        self.settings = AppSettings(onboarding_seen=True)
        self.messages = []
        self.clip_list = SimpleNamespace(
            update_row=lambda *_: None, selected_clip_ids=lambda: [clip.id])
        self.clip_editor = ClipEditor(self.settings)
        self.clip_editor.set_clip(clip)
        self._first_read_dialog = SimpleNamespace(hide=lambda: None)
        self._first_read_worker = FirstReadWorker(
            [clip], ffmpeg_path="unused", source=Path("film.mp4"),
            scratch_dir=Path("."), api_key="unused", parent=self)
        self._first_read_context = (
            session, session.project.source_video_path,
            {clip.id: (clip.start_ms, clip.end_ms)})
        self._first_read_worker.result_ready.connect(self._store_first_read)
        self._first_read_worker.progressed.connect(lambda _progress: None)
        self._first_read_worker.finished_batch.connect(self._first_read_finished)
        self._first_read_worker.finished.connect(self._first_read_thread_finished)

    def statusBar(self):
        return SimpleNamespace(showMessage=lambda *args: self.messages.append(args))

    def _update_review_label(self):
        pass

    def _require_session(self):
        return self.session is not None

    def _stop_thumbnails(self):
        pass

    def _stop_proxy_worker(self):
        pass

    def _stop_snap_prediction_worker(self):
        pass

    def _join_worker(self, worker):
        workflow.MainWindowWorkflow._join_worker(self, worker, ms=200)


@pytest.fixture
def owned(qapp, tmp_path):
    session = ProjectSession.create("Game", tmp_path, tmp_path / "out")
    session.project.source_video_path = "film.mp4"
    clip = session.add_clip(Clip(start_ms=0, end_ms=10_000))
    owner = Owner(session, clip)
    yield owner, session, clip
    session.close()


def suggestion():
    return fr.store('{"snap_prediction":{"onset_ms":100}}',
                    fr.FirstRead(label="run", agreement=True, views=("run", "run")))


def test_result_merges_current_analysis_and_preserves_editor_draft(owned):
    owner, session, clip = owned
    clip.analysis = {"snap_prediction": {"onset_ms":2000}, "other": {"keep": True}}
    clip.details["run_pass"] = "Pass"
    owner.clip_editor.title_edit.setText("Unapplied analyst draft")
    worker = owner._first_read_worker
    worker.result_ready.emit(clip.id, suggestion())
    worker.finished_batch.emit(first_read_batch.BatchSummary(total=1, suggested=1))
    assert clip.analysis["snap_prediction"] == {"onset_ms":2000}
    assert clip.analysis["other"] == {"keep":True}
    assert fr.load(clip.analysis_json()).label == "run"
    assert clip.details["run_pass"] == "Pass"
    assert owner.clip_editor.title_edit.text() == "Unapplied analyst draft"
    assert owner._first_read_worker is worker  # Summary is earlier than thread exit.
    session.save()
    reopened = ProjectSession.open(session.db_path)
    try:
        assert reopened.get_clip(clip.id).analysis == clip.analysis
    finally:
        reopened.close()


def test_forward_slash_source_starts_after_confirmation(owned, tmp_path, monkeypatch):
    owner, session, _clip = owned
    source = tmp_path / "film.mp4"
    source.write_bytes(b"unused")
    session.project.source_video_path = source.as_posix()
    owner.settings.first_read_enabled = True
    owner.settings.first_read_api_key = "unused"
    owner._first_read_worker = None
    monkeypatch.setattr(workflow, "FirstReadConfirm", lambda *_a, **_k:
                        SimpleNamespace(exec=lambda: QDialog.DialogCode.Accepted))
    monkeypatch.setattr(workflow, "FirstReadDialog", lambda *_a, **_k:
                        SimpleNamespace(show_progress=lambda _p: None,
                                        finish=lambda _s: None, show=lambda: None,
                                        stop_requested=SimpleNamespace(connect=lambda _f: None)))
    started = []
    monkeypatch.setattr(FirstReadWorker, "start", lambda worker: started.append(worker))
    owner._first_read()
    assert len(started) == 1


def test_equivalent_source_path_spelling_accepts_result(owned):
    owner, session, clip = owned
    session.project.source_video_path = "D:/film/source.mp4"
    owner._first_read_context = (
        session, str(Path(session.project.source_video_path)),
        {clip.id: (clip.start_ms, clip.end_ms)})
    owner._first_read_worker.result_ready.emit(clip.id, suggestion())
    assert fr.load(clip.analysis_json()).label == "run"


@pytest.mark.parametrize("change", ["copied_session", "source", "range", "read_only", "removed", "old_worker"])
def test_late_result_cannot_write_changed_context(owned, change):
    owner, session, clip = owned
    worker = owner._first_read_worker
    if change == "copied_session":
        # Copied projects retain both project and clip IDs.
        owner.session = SimpleNamespace(project=session.project, clips=[clip])
    elif change == "source":
        session.project.source_video_path = "relinked.mp4"
    elif change == "range":
        clip.end_ms += 1000
    elif change == "read_only":
        session.read_only = True
    elif change == "removed":
        session.clips.clear()
    elif change == "old_worker":
        owner._first_read_worker = object()
    worker.result_ready.emit(clip.id, suggestion())
    assert clip.analysis == {}
    session.read_only = False


@pytest.mark.parametrize("cooperative", [True, False])
def test_shutdown_stops_or_detaches_worker_and_rejects_queued_result(
        owned, qapp, monkeypatch, cooperative):
    owner, _session, clip = owned
    worker = owner._first_read_worker
    ready, release = threading.Event(), threading.Event()

    def blocked(*_args, save, should_cancel, **_kwargs):
        save(clip.id, suggestion())  # Queued while the GUI waits below.
        ready.set()
        while not release.wait(0.005):
            if cooperative and should_cancel():
                break
        return first_read_batch.BatchSummary(cancelled=True)

    monkeypatch.setattr(first_read_batch, "run", blocked)
    worker.start()
    try:
        assert ready.wait(2)
        owner._stop_background_workers()
        assert worker._stop is True
        assert owner._first_read_worker is None
        assert owner._first_read_context is None
        if not cooperative:
            assert worker.parent() is None
            assert worker in workflow._detached_workers
        qapp.processEvents()
        assert clip.analysis == {}
        assert owner.messages == []
    finally:
        release.set()
        worker.wait(2000)
        qapp.processEvents()
        assert worker not in workflow._detached_workers
