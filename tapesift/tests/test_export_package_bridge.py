from pathlib import Path
from types import SimpleNamespace

from tapesift.models.export_job import ExportJob, JobStatus, JobType
from tapesift.ui_v2 import export_package_bridge as bridge
from tapesift.ui_v2.export_package_bridge import (
    ExportQueueRestoreThread,
    ExportStagingThread,
)
from tapesift.ui_v2.main_window import MainWindowV2


def test_stale_preview_result_is_rejected_after_options_change():
    accepted = []
    panel = SimpleNamespace(
        composited_preview_generation=2,
        set_composited_preview_result=lambda *args, **kwargs:
        accepted.append((args, kwargs)),
    )
    window = SimpleNamespace(
        _export_preview_request_id=8,
        _export_preview_panel_generation=1,
        export_panel=panel,
    )
    result = SimpleNamespace(
        request_id=8,
        image=object(),
        take_id="take-1",
        audio_frame=0,
        source_position_ms=1_000,
        marks_sha256="abc",
    )

    MainWindowV2._composited_preview_ready(window, result)

    assert accepted == []


def test_restore_filters_metadata_to_the_open_project(monkeypatch):
    seen = []

    class _Connection:
        def close(self):
            pass

        def interrupt(self):
            pass

    class _Service:
        @classmethod
        def recover_interrupted_in_database(cls, _path):
            return 0

        def __init__(self, _connection):
            pass

        def list_summaries(self, project_id=None):
            seen.append(project_id)
            return ["only-this-project"]

    monkeypatch.setattr(bridge, "open_project_db", lambda _path: _Connection())
    monkeypatch.setattr(bridge, "ExportJobQueueService", _Service)
    payloads = []
    worker = ExportQueueRestoreThread(Path("project.tapesift"), 17, 4)
    worker.summaries_ready.connect(payloads.append)

    worker.run()

    assert seen == [17]
    assert payloads == [(4, ("only-this-project",))]


def test_cancel_after_enqueue_commit_persists_waiting_cancellation(
        monkeypatch):
    cancelled_ids = []
    staged = []
    cancelled = []

    class _Connection:
        def close(self):
            pass

        def interrupt(self):
            pass

    thread_holder = {}

    class _Service:
        @classmethod
        def capture_source_identity(cls, _path, *, cancel_event):
            return object()

        def __init__(self, _connection):
            pass

        def enqueue(self, **_kwargs):
            thread_holder["worker"]._cancel_event.set()
            return SimpleNamespace(job=SimpleNamespace(id="job-1"))

        def cancel_waiting(self, job_id):
            cancelled_ids.append(job_id)
            return True

    monkeypatch.setattr(bridge, "open_project_db", lambda _path: _Connection())
    monkeypatch.setattr(bridge, "ExportJobQueueService", _Service)
    request = SimpleNamespace(
        database_path=Path("project.tapesift"),
        source_video_path=Path("source.mp4"),
        job=ExportJob(JobType.CLIP, "Clip QA", "clip.mp4", status=JobStatus.PREPARING),
        package=object(),
        composition_plan=object(),
        clips=(),
        hardware_encoder="",
        reel_settings=None,
    )
    worker = ExportStagingThread(request)
    thread_holder["worker"] = worker
    worker.staged.connect(staged.append)
    worker.staging_cancelled.connect(lambda: cancelled.append(True))

    worker.run()

    assert cancelled_ids == ["job-1"]
    assert staged == []
    assert cancelled == [True]
    assert request.job.status is JobStatus.PREPARING
