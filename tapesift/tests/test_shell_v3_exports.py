"""Export UI and queue integrity; real media acceptance lives with native QA."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from tapesift.models.clip import Clip
from tapesift.models.export_job import ExportJob, JobStatus, JobType
from tapesift.models.export_package import ExportPackageSnapshot, ExportStyle
from tapesift.models.composition_plan import PixelSize, build_composition_plan
from tapesift.services.project_service import ProjectSession
from tapesift.services.export_job_queue_service import ExportJobQueueService
from tapesift.services import export_service, filename_service
from tapesift.ui_v2.export_package_bridge import ExportStagingRequest, ExportStagingThread, ExportRetryThread
from tapesift.ui_v3.export_surface import ExportPageV3


def requests_for(tmp_path):
    session = ProjectSession.create("Export QA", tmp_path, tmp_path / "output")
    source = tmp_path / "identity-only.bin"
    source.write_bytes(b"local source identity, not a decodable movie")
    session.project.source_video_path = str(source)
    session.project.source_metadata.width = 320
    session.project.source_metadata.height = 180
    session.clips = [Clip(project_id=session.project.id, clip_title=f"Play {i}",
                          start_ms=1000*i, end_ms=1000*i+500) for i in range(1, 4)]
    session.save()
    frozen = ExportJobQueueService.freeze_clips_for_staging(session.clips)
    package = ExportPackageSnapshot(style=ExportStyle.CLEAN, accurate_cut=False)
    descriptor = build_composition_plan(package, PixelSize(320, 180))
    jobs = [ExportJob(job_type=JobType.CLIP, display_name="Clip QA", project_id=session.project.id, clip_id=frozen[0].id,
                      output_path=str(tmp_path / "clip.mp4"), status=JobStatus.PREPARING, preset_name=package.technical_preset),
            ExportJob(project_id=session.project.id, job_type=JobType.REEL, display_name="Reel QA",
                      clip_ids=[c.id for c in reversed(frozen)],
                      output_path=str(tmp_path / "reel.mp4"), status=JobStatus.PREPARING, preset_name=package.technical_preset)]
    requests = tuple(ExportStagingRequest(Path(session.db_path), job, package, descriptor, source,
                     (frozen[0],) if job.clip_id else tuple(reversed(frozen))) for job in jobs)
    return session, requests


def test_clean_batch_freezes_order_ranges_and_retries_from_reopened_database(tmp_path):
    session, requests = requests_for(tmp_path)
    original_bounds = [(c.id, c.start_ms, c.end_ms) for c in requests[1].clips]
    session.clips[0].start_ms = 42000
    worker = ExportStagingThread(requests[0], remaining_requests=requests[1:])
    staged, failed = [], []
    worker.staged.connect(staged.append)
    worker.staging_failed.connect(failed.append)
    worker.run()
    assert not failed and len(staged) == 1
    clip, reel = [p.job for p in staged[0]]
    assert clip.status is reel.status is JobStatus.WAITING
    assert [(c.clip_id, c.start_ms, c.end_ms) for c in reel.snapshot.clips] == original_bounds
    assert clip.snapshot.package.accurate_cut is False
    assert requests[0].job.status is JobStatus.PREPARING
    session.conn.execute("UPDATE export_jobs SET status=? WHERE id=?", (JobStatus.FAILED.value, reel.id))
    session.conn.commit()
    retry = ExportJobQueueService.retry_in_database(Path(session.db_path), reel.id)
    assert retry.snapshot == reel.snapshot and retry.output_path == reel.output_path
    session.close()


def test_failed_second_staging_cancels_committed_first_and_reports_cleanup_failure(tmp_path, monkeypatch):
    session, requests = requests_for(tmp_path)
    original = ExportJobQueueService.enqueue
    calls = []
    def enqueue(service, **kwargs):
        calls.append(kwargs["job"].id)
        if len(calls) == 2:
            raise OSError("second staging failed")
        return original(service, **kwargs)
    monkeypatch.setattr(ExportJobQueueService, "enqueue", enqueue)
    worker = ExportStagingThread(requests[0], remaining_requests=requests[1:])
    ready, errors = [], []
    worker.staged.connect(ready.append);worker.staging_failed.connect(errors.append)
    worker.run()
    assert not ready and errors == ["second staging failed"]
    row = session.conn.execute("SELECT status FROM export_jobs WHERE id=?", (requests[0].job.id,)).fetchone()
    assert row[0] == JobStatus.CANCELLED.value
    # A later cleanup failure must be visible, never misreported as cancellation.
    calls.clear()
    requests = tuple(replace(r, job=replace(r.job, id=r.job.id+"again")) for r in requests)
    monkeypatch.setattr(ExportJobQueueService, "cancel_waiting", lambda *_: False)
    worker = ExportStagingThread(requests[0], remaining_requests=requests[1:]);errors=[]
    worker.staging_failed.connect(errors.append);worker.run()
    assert errors and "could not cancel queued jobs" in errors[0]
    session.close()


def test_retry_cancel_after_claim_does_not_leave_a_waiting_job(tmp_path, monkeypatch):
    session, requests = requests_for(tmp_path)
    worker = ExportStagingThread(requests[0]);worker.run()
    jid = requests[0].job.id
    session.conn.execute("UPDATE export_jobs SET status=? WHERE id=?", (JobStatus.FAILED.value, jid));session.conn.commit()
    worker = ExportRetryThread(Path(session.db_path), jid)
    original = ExportJobQueueService.retry
    def retry(service, job_id):
        job = original(service, job_id);worker.cancel();return job
    monkeypatch.setattr(ExportJobQueueService, "retry", retry)
    ready, errors = [], []
    worker.retry_ready.connect(ready.append);worker.retry_failed.connect(errors.append);worker.run()
    assert not errors and len(ready) == 1 and ready[0].status is JobStatus.CANCELLED
    assert session.conn.execute("SELECT status FROM export_jobs WHERE id=?", (jid,)).fetchone()[0] == JobStatus.CANCELLED.value
    session.close()


def test_preview_and_real_planner_share_names_reel_membership_and_safe_folders(tmp_path):
    session, requests = requests_for(tmp_path)
    session.project.output_folder = str(tmp_path / "not-created")
    session.project.output_organization = "by_label"
    session.clips[0].label = "../../CON.txt"
    session.clips[1].enabled = False
    session.clips[2].include_in_reel = False
    plan = export_service.plan_export(session.project, session.clips, "both", prepare=False)
    assert not Path(session.project.output_folder).exists()
    assert len(plan.jobs) == 3 and plan.jobs[-1].clip_ids == [session.clips[0].id]
    root = Path(session.project.output_folder).resolve()
    for job in plan.jobs:Path(job.output_path).resolve().relative_to(root)
    real = export_service.plan_export(session.project, session.clips, "both")
    assert [j.output_path for j in real.jobs] == [j.output_path for j in plan.jobs]
    assert filename_service.sanitize_filename_base("CON.txt") == "CON-clip.txt"
    # Simulate an inner junction resolving beyond the root at the common owner.
    candidate = export_service.core_paths.project_output_structure(root)["reels"]
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        return tmp_path / "outside" if path == candidate else original(path, *args, **kwargs)
    from tapesift.core.exceptions import OutputFolderError
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "resolve", resolve)
        with pytest.raises(OutputFolderError):export_service.plan_export(session.project, session.clips, "reel", prepare=False)
    session.close()


def test_package_exposes_every_row_and_keeps_order_reel_flags_and_busy_guard():
    app = QApplication.instance() or QApplication([])
    page = ExportPageV3(QWidget());page.resize(1000, 428);page.show()
    clips = [Clip(clip_title=f"Play {i}", start_ms=i*1000, end_ms=i*1000+500,
                  include_in_reel=i != 7) for i in range(8)]
    page.configure_package(clips, destination="D:/output")
    setup = page.package_setup;QTest.qWait(5)
    assert len(setup.play_rows) == 8
    page.setFixedHeight(248);QTest.qWait(5)
    assert setup.rows_scroll.verticalScrollBar().maximum() > 0
    assert "7 included plays" in setup.summary.text()
    last = setup.play_rows[-1];last.setFocus()
    QTest.keyClick(last, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert setup._clips[-2] is clips[-1]
    before = [c.id for c in setup._clips]
    setup.set_configuration_enabled(False);setup._move_clip(clips[-1].id, -1)
    assert [c.id for c in setup._clips] == before
    page.close();page.deleteLater()


def test_queue_reports_mixed_outcomes_and_targets_real_outputs(tmp_path):
    app = QApplication.instance() or QApplication([])
    page = ExportPageV3(QWidget());page.show();page.mark_started()
    output = tmp_path / "made.mp4";output.write_bytes(b"exists")
    jobs = [ExportJob(job_type=JobType.CLIP, display_name="Queue QA", output_path=str(output), status=JobStatus.COMPLETED),
            ExportJob(job_type=JobType.CLIP, display_name="Queue QA", output_path=str(tmp_path/"bad.mp4"), status=JobStatus.FAILED, error_message="Read failure"),
            ExportJob(job_type=JobType.CLIP, display_name="Queue QA", output_path="", status=JobStatus.CANCELLED), ExportJob(job_type=JobType.CLIP, display_name="Queue QA", output_path="", status=JobStatus.WAITING)]
    page.sync_jobs(jobs, running=False)
    queue = page.clip_setup.queue
    assert queue.complete_state.text() == "PARTIAL"
    assert "1 failed" in queue.complete_title.text() and "1 cancelled" in queue.complete_title.text()
    assert queue.complete_detail.text() == "Read failure"
    assert queue._completed_output_path == str(output)
    page._populate_queue_menu();menus=[a.menu() for a in page.queue_menu.actions()]
    retried=[];cancelled=[]
    page.retry_requested.connect(retried.append);page.queued_cancel_requested.connect(cancelled.append)
    menus[1].actions()[-1].trigger();menus[3].actions()[0].trigger()
    assert retried == [jobs[1].id] and cancelled == [jobs[3].id]
    page.sync_jobs([], running=False)
    assert queue.complete_title.text() == "No completed exports" and not queue.open_button.isVisible()
    page.close();page.deleteLater()


def test_export_callbacks_reject_an_old_database_and_cancel_only_its_waiting_job(tmp_path, monkeypatch):
    from types import MethodType
    from tapesift.ui_v2 import main_window as module
    from tapesift.ui_v2.main_window import MainWindowV2
    old_db, current_db = tmp_path / "old.tapesift", tmp_path / "current.tapesift"
    old = SimpleNamespace(request=SimpleNamespace(database_path=old_db))
    current = SimpleNamespace(request=SimpleNamespace(database_path=current_db))
    events, cancelled = [], []
    class Signal:
        def connect(self, fn): pass
    class Cleanup:
        def __init__(self, database_path, job_id, parent):
            self.database_path=database_path;self.job_id=job_id
            self.cancel_failed=Signal();self.finished=Signal()
        def start(self):cancelled.append((self.database_path,self.job_id))
    monkeypatch.setattr(module,"ExportQueuedCancelThread",Cleanup)
    window=SimpleNamespace(session=SimpleNamespace(db_path=current_db,project=SimpleNamespace(id=1)),
        _export_staging_worker=current,_export_bridge_generation=2,_export_cancel_workers=set(),
        _composited_export_staged=lambda payload:events.append(("ready",payload)),
        _composited_staging_failed=lambda text:events.append(("failed",text)),
        _composited_staging_cancelled=lambda:events.append(("cancelled",)),
        statusBar=lambda:SimpleNamespace(showMessage=lambda *a:None))
    window._export_input_is_current=MethodType(MainWindowV2._export_input_is_current,window)
    payload=SimpleNamespace(job=ExportJob(JobType.CLIP,"Old","old.mp4",project_id=1))
    MainWindowV2._export_input_ready(window,payload,old,"_export_staging_worker",1)
    MainWindowV2._export_input_failed(window,"old failure",old,"_export_staging_worker",1)
    MainWindowV2._export_input_failed(window,None,old,"_export_staging_worker",1)
    assert events == [] and cancelled == [(old_db,payload.job.id)]
    MainWindowV2._export_input_failed(window,"current failure",current,"_export_staging_worker",2)
    assert events == [("failed","current failure")]


def test_cancel_between_ready_emission_and_delivery_prevents_both_launch_routes(tmp_path):
    from tapesift.ui_v2.main_window import MainWindowV2
    jobs=[];cancelled=[];launched=[]
    job=ExportJob(JobType.CLIP,"QA","out.mp4",project_id=1)
    panel=SimpleNamespace(upsert_job=jobs.append,set_bridge_readiness_error=lambda text:None)
    window=SimpleNamespace(session=SimpleNamespace(project=SimpleNamespace(id=1)),export_panel=panel,
        _export_staging_worker=SimpleNamespace(cancel_requested=True),
        _export_retry_worker=SimpleNamespace(cancel_requested=True),
        _remove_queued_export=cancelled.append,_launch_snapshot_export_worker=launched.append)
    MainWindowV2._composited_export_staged(window,SimpleNamespace(job=job))
    MainWindowV2._snapshot_retry_ready(window,job)
    assert launched == [] and cancelled == [job.id,job.id] and jobs == [job,job]
