from copy import deepcopy
from pathlib import Path
import json
import subprocess

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from tapesift.models.clip import Clip
from tapesift.models.project import Project
from tapesift.models.video_metadata import VideoMetadata
from tapesift.models.export_job import JobType, JobStatus
from tapesift.services import cutup_service as service


def sample_clips():
    return [
        Clip(2000, 3000, clip_number=12, clip_title="Big catch", details={
            "player_name": "Receiver", "other_players": "Defender, Receiver", "quarterback": "Quarterback",
            "run_pass": "Pass", "result": "Completion; Reception; Touchdown", "down_distance": "3rd & 8", "ball_on": "OPP 15"}),
        Clip(0, 1000, clip_number=3, clip_title="Pressure", details={"player_name": "Defender",
            "other_players": "Receiver", "run_pass": "Pass", "result": "Incompletion", "action": "Blitz; PBU", "down_distance": "1st & 10"}),
        Clip(4000, 5000, enabled=False, details={"player_name": "Receiver"}),
    ]


def test_player_involvement_filters_and_film_order():
    clips = sample_clips()
    groups = {g.value: g for g in service.catalog(clips, "player")}
    assert groups["Receiver"].clip_ids == [clips[1].id, clips[0].id]
    assert groups["Quarterback"].clip_ids == [clips[0].id]
    primary = {g.value: g for g in service.catalog(clips, "player", primary_only=True)}
    assert primary["Receiver"].clip_ids == [clips[0].id]
    found = service.catalog(clips, "player", {"play_type": "Pass", "down": "3rd down", "result": "Touchdown"})
    assert all(g.clip_ids == [clips[0].id] for g in found)
    assert service.values_for_clip(clips[0], "situation") == ["Third and long (7+)", "Red zone"]
    assert len(service.catalog(clips, "action")) == 2


def test_cutup_plan_preserves_originals_exclusions_numbers_and_existing_outputs(tmp_path):
    clips = sample_clips()
    before = deepcopy(clips)
    project = Project("Home vs Away", game_year="2026", output_folder=str(tmp_path / "exports"))
    groups = service.catalog(clips, "player")
    receiver = next(g for g in groups if g.value == "Receiver")
    receiver.excluded.add(clips[1].id)
    plan = service.plan_cutups(project, clips, groups)
    assert not Path(project.output_folder).exists()
    assert clips == before
    paths = [Path(j.output_path) for j in plan.jobs]
    assert len(set(paths)) == len(paths)
    assert any(p.name.startswith("012_") for p in paths)
    assert any(p.name.startswith("003_") for p in paths)
    receiver_jobs = [j for j in plan.jobs if Path(j.output_path).parent.name == "Receiver"]
    assert len(receiver_jobs) == 2
    assert next(j for j in receiver_jobs if j.job_type == JobType.REEL).clip_ids == [clips[0].id]
    paths[0].parent.mkdir(parents=True)
    paths[0].write_bytes(b"existing")
    updated = service.plan_cutups(project, clips, groups, prepare=True)
    assert updated.jobs[0].output_path != str(paths[0])
    assert paths[0].read_bytes() == b"existing"
    group_first = service.plan_cutups(project, clips, [receiver], layout="group")
    assert "Players/Receiver/2026/Home-vs-Away" in group_first.jobs[0].output_path.replace("\\", "/")
    unknown_year = service.plan_cutups(project, clips, [receiver], year="")
    assert "Year-unknown" in unknown_year.jobs[0].output_path
    receiver.folder_name = "../../CON"
    safe = service.plan_cutups(project, clips, [receiver])
    assert all(Path(j.output_path).resolve().is_relative_to(Path(project.output_folder).resolve()) for j in safe.jobs)


def test_review_checkboxes_names_notes_and_queue_signal(tmp_path):
    from tapesift.ui_v3.cutup_dialog import CutupDialog
    from tapesift.ui_v3.theme import stylesheet
    app = QApplication.instance() or QApplication([])
    old = app.styleSheet()
    app.setStyleSheet(stylesheet())
    clips = sample_clips()
    project = Project("Home offense vs Away defense", game_year="2026", output_folder=str(tmp_path / "exports"))
    dialog = CutupDialog(project, clips)
    try:
        dialog.show()
        QTest.qWait(30)
        index = next(i for i, g in enumerate(dialog.candidates) if g.value == "Receiver")
        dialog.choices.item(index).setSelected(True)
        dialog._add_groups()
        assert dialog.table.rowCount() == 2
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        assert dialog.groups[0].excluded == {clips[1].id}
        assert dialog.build_plan().jobs[0].clip_id == clips[0].id
        assert clips[1].enabled
        queued = []
        dialog.queue_requested.connect(queued.append)
        dialog.queue_button.click()
        assert queued == [dialog]
        assert not Path(project.output_folder).exists()
        dialog.grab().save(str(tmp_path / "cutup-review.png"))
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.setStyleSheet(old)


def test_real_media_grouped_individuals_and_reels_use_durable_queue(tmp_path):
    from tapesift.services.project_service import ProjectSession
    from tapesift.services.export_job_queue_service import ExportJobQueueService
    from tapesift.models.export_package import ExportPackageSnapshot, ExportStyle
    from tapesift.models.composition_plan import PixelSize, build_composition_plan
    from tapesift.workers.export_worker import ExportWorker
    ffmpeg = Path(__file__).resolve().parents[2] / "vendor/ffmpeg/ffmpeg.exe"
    if not ffmpeg.exists():
        pytest.skip("Bundled FFmpeg is required for real media acceptance")
    source = tmp_path / "source.mp4"
    subprocess.run([str(ffmpeg), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                    "-t", "4", "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(source)], check=True, timeout=30)
    app = QApplication.instance() or QApplication([])
    session = ProjectSession.create("Cutup media QA", tmp_path / "project", tmp_path / "exports")
    try:
        session.project.source_video_path = str(source)
        session.project.source_duration_ms = 4000
        session.project.source_metadata = VideoMetadata(width=320, height=180, duration_ms=4000, frame_rate=30, file_size_bytes=source.stat().st_size)
        session.project.game_year = "2026"
        clips = sample_clips()[:2]
        for clip in clips:
            session.add_clip(clip)
        session.save()
        groups = service.catalog(session.clips, "player")
        plan = service.plan_cutups(session.project, session.clips, groups, prepare=True)
        queue = ExportJobQueueService(session.conn)
        identity = queue.capture_source_identity(source)
        jobs = []
        for job in plan.jobs:
            package = ExportPackageSnapshot(style=ExportStyle.CLEAN, technical_preset=job.preset_name, accurate_cut=True)
            ids = [job.clip_id] if job.clip_id else job.clip_ids
            persisted = queue.enqueue(job=job, package=package, composition_plan=build_composition_plan(package, PixelSize(320, 180)),
                          source_video_path=source, clips=[session.get_clip(cid) for cid in ids], source_identity=identity)
            jobs.append(persisted.job)
        worker = ExportWorker(str(ffmpeg), session.project, jobs, {}, True, database_path=session.db_path)
        worker.run()
        assert all(j.status == JobStatus.COMPLETED for j in jobs), [(j.display_name, j.error_message) for j in jobs]
        for job in jobs:
            result = subprocess.run([str(ffmpeg.with_name("ffprobe.exe")), "-v", "error", "-show_entries", "format=duration",
                "-of", "json", job.output_path], capture_output=True, text=True, check=True, timeout=15)
            duration = float(json.loads(result.stdout)["format"]["duration"])
            assert abs(duration - (len(job.clip_ids) if job.job_type == JobType.REEL else 1)) < 0.2
        assert len(jobs) == 8
    finally:
        session.close()


def test_export_page_opens_review_and_hands_selected_plan_to_existing_launcher(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QWidget
    from tapesift.ui_v3.main_window import MainWindowV3
    from tapesift.ui_v3.export_surface import ExportPageV3
    from tapesift.ui_v3.cutup_dialog import CutupDialog
    app = QApplication.instance() or QApplication([])
    clips = sample_clips()
    source = tmp_path / "source.bin"
    source.write_bytes(b"identity only")
    owner = QWidget()
    owner.session = SimpleNamespace(project=Project("QA", source_video_path=str(source), output_folder=str(tmp_path / "exports")),
                                    clips=clips, get_clip=lambda cid: next((c for c in clips if c.id == cid), None))
    owner.clip_editor = SimpleNamespace(_clip=None)
    owner._v3_export_can_start = lambda: True
    owner._try_save = lambda: True
    owner.export_panel = SimpleNamespace(set_export_style=lambda *_: None)
    page = ExportPageV3(owner)
    owner._v3_review = SimpleNamespace(export_page=page)
    stages, plans = [], []
    owner._set_workspace_stage = stages.append
    owner._sync_v3_export_state = lambda: None
    def launch(plan, accurate, *, quick):
        plans.append(plan)
        owner._export_staging_worker = object()
        assert accurate and not quick
    owner._launch_export_plan = launch
    def review(dialog):
        dialog.choices.item(0).setSelected(True)
        dialog._add_groups()
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        dialog.queue_button.click()
        assert dialog.result() == dialog.DialogCode.Accepted
        return dialog.result()
    monkeypatch.setattr(CutupDialog, "exec", review)
    page.cutups_requested.connect(lambda: MainWindowV3._open_cutup_builder(owner))
    try:
        page.cutups_button.click()
        assert len(plans) == 1 and len(plans[0].jobs) == 2
        assert plans[0].jobs[0].clip_id == clips[0].id
        assert stages == ["export"] and page.title.text() == "Cutup export"
    finally:
        owner.close()
        owner.deleteLater()
