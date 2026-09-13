"""Durability and integrity contracts for immutable export queue jobs."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from tapesift.core.exceptions import ExportCancelledError, ExportError
from tapesift.database.connection import open_project_db
from tapesift.database.repositories import (
    ExportJobRepository,
    ExportJobRepositoryError,
)
from tapesift.database.voiceover_repository import VoiceoverRepository
from tapesift.models.clip import Clip
from tapesift.models.composition_plan import (
    LockedTemplateIdentity,
    PixelSize,
    build_composition_plan,
)
from tapesift.models.export_job import ExportJob, JobStatus, JobType
from tapesift.models.export_job_snapshot import (
    ExportJobSnapshot,
    ExportJobSnapshotValidationError,
    SourceMediaIdentity,
)
from tapesift.models.export_package import (
    CompositorInput,
    ExportPackageSnapshot,
    ExportPackageValidationError,
    ExportStyle,
    ExportTemplateSnapshot,
)
from tapesift.models.export_settings import SOCIAL_1080P, SOURCE_QUALITY
from tapesift.models.presentation_track import (
    PresentationEvent,
    PresentationEventKind,
    PresentationEventTrack,
)
from tapesift.models.signature_template import (
    ImageAssetSnapshot,
    SignatureIdentity,
    SignatureTemplate,
)
from tapesift.models.voiceover import VoiceoverTake
from tapesift.services.export_job_queue_service import ExportJobQueueService
from tapesift.services import ffmpeg_service
from tapesift.services.project_service import ProjectSession
from tapesift.workers.export_worker import ExportWorker


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _png(color: str) -> bytes:
    image = QImage(12, 12, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    payload = QByteArray()
    buffer = QBuffer(payload)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return bytes(payload)


def _wav(frames: int = 480, sample_rate: int = 48_000) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00\x00" * frames)
    return target.getvalue()


def _clean_snapshot(
        clip: Clip,
        *,
        source: str,
        accurate: bool = False,
        source_identity: SourceMediaIdentity | None = None,
) -> ExportJobSnapshot:
    package = ExportPackageSnapshot(
        style=ExportStyle.CLEAN,
        technical_preset=SOURCE_QUALITY.name,
        accurate_cut=accurate,
    )
    plan = build_composition_plan(package, PixelSize(1920, 1080))
    return ExportJobSnapshot.capture(
        package=package,
        composition_plan=plan,
        source_video_path=source,
        source_identity=source_identity,
        clips=[clip],
    )


def _job(clip: Clip, snapshot: ExportJobSnapshot, *, job_id: str = "job-1") -> ExportJob:
    return ExportJob(
        id=job_id,
        job_type=JobType.CLIP,
        display_name="Play 1",
        output_path="D:/exports/play-1.mp4",
        project_id=clip.project_id,
        clip_id=clip.id,
        preset_name=snapshot.package.technical_preset,
        snapshot=snapshot,
    )


def _project(tmp_path: Path) -> tuple[ProjectSession, Clip]:
    source = tmp_path / "source-media.bin"
    source.write_bytes(b"immutable queued source media")
    session = ProjectSession.create("Queue", tmp_path, tmp_path / "out")
    session.project.source_video_path = str(source)
    session.project.source_metadata.width = 1920
    session.project.source_metadata.height = 1080
    clip = session.add_clip(Clip(start_ms=1_000, end_ms=4_000))
    session.save()
    return session, clip


def _signature_package_and_plan(
        take_id: str,
        *,
        source: str = "D:/film/game.mp4",
        source_size: PixelSize = PixelSize(1920, 1080),
) -> tuple[ExportPackageSnapshot, object, bytes, bytes]:
    profile_bytes = _png("#884422")
    wordmark_bytes = _png("#39E07A")
    template = SignatureTemplate(
        template_id="analyst-green",
        name="Analyst Green",
        identity=SignatureIdentity(
            display_text="Nick Marshall",
            username_text="@NMarshall_FB",
            wordmark_text="TAPESIFT",
            profile_photo=ImageAssetSnapshot.capture(profile_bytes),
            wordmark_logo=ImageAssetSnapshot.capture(wordmark_bytes),
        ),
    )
    template_snapshot = ExportTemplateSnapshot(
        template_id=template.template_id,
        revision=3,
        payload_json=template.to_json(),
    )
    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=SOCIAL_1080P.name,
        accurate_cut=True,
        template=template_snapshot,
        compositor_inputs=(
            CompositorInput("source_video", source),
            CompositorInput("ink_event_track", f"take:{take_id}:ink"),
            CompositorInput("voiceover_audio", f"take:{take_id}:audio"),
            CompositorInput(
                "presentation_event_track", f"take:{take_id}:events"),
            CompositorInput("play_call_situation", "3rd & 7"),
            CompositorInput("play_call_concept", "Mesh"),
            CompositorInput("play_call_result", "+18"),
        ),
        include_ink=True,
        include_voiceover=True,
        include_play_call=True,
    )
    identity = LockedTemplateIdentity.capture(template_snapshot, template)
    plan = build_composition_plan(
        package,
        source_size,
        template_identity=identity,
    )
    return package, plan, profile_bytes, wordmark_bytes


def _selected_take(
        session: ProjectSession,
        clip: Clip,
        *,
        frame_count: int = 480,
        marks: list[dict] | None = None,
) -> VoiceoverTake:
    events = [PresentationEvent.create(
        audio_frame=0,
        sequence=0,
        kind=PresentationEventKind.SOURCE_POSITION,
        payload={"source_position_ms": clip.start_ms},
    )]
    if marks is not None:
        events.append(PresentationEvent.create(
            audio_frame=0,
            sequence=1,
            kind=PresentationEventKind.TELESTRATION_SNAPSHOT,
            payload={"marks": marks},
        ))
    track = PresentationEventTrack(
        sample_rate=48_000,
        events=tuple(events),
    )
    take = VoiceoverTake(
        id="take-1",
        project_id=session.project.id,
        clip_id=clip.id,
        audio_wav=_wav(frame_count),
        sample_rate=48_000,
        channels=1,
        frame_count=frame_count,
        duration_ms=frame_count * 1000 // 48_000,
        source_anchor_ms=clip.start_ms,
        waveform=tuple(
            0.25 + (index % 4) * 0.1
            for index in range((frame_count + 479) // 480)
        ),
        presentation_track=track,
        selected=True,
    )
    return VoiceoverRepository(session.conn).save(take)


def _bundled(name: str) -> str:
    value = ffmpeg_service.find_executable(name)
    if not value:
        executable = f"{name}.exe" if os.name == "nt" else name
        installed = Path("D:/TapeSift/vendor/ffmpeg") / executable
        value = str(installed) if installed.is_file() else ""
    if not value:
        pytest.skip(f"Bundled {name} is unavailable")
    return value


def _source_video(tmp_path: Path) -> Path:
    output = tmp_path / "source.mkv"
    completed = subprocess.run(
        [
            _bundled("ffmpeg"),
            "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=#214C2F:s=32x18:r=30:d=1",
            "-c:v", "ffv1", str(output),
        ],
        capture_output=True,
        creationflags=ffmpeg_service.CREATE_NO_WINDOW,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace")
    return output


def test_export_package_json_is_canonical_and_rejects_unknown_fields():
    package = ExportPackageSnapshot(
        style=ExportStyle.CLEAN,
        technical_preset=SOURCE_QUALITY.name,
        accurate_cut=False,
    )

    encoded = package.to_json()

    assert ExportPackageSnapshot.from_json(encoded) == package
    assert ExportPackageSnapshot.from_json(encoded).to_json() == encoded
    damaged = json.loads(encoded)
    damaged["package"]["slate_title"] = "invented"
    with pytest.raises(ExportPackageValidationError, match="invalid fields"):
        ExportPackageSnapshot.from_json(json.dumps(damaged))


def test_direct_snapshot_capture_rejects_take_different_from_locked_inputs(
        tmp_path):
    session, clip = _project(tmp_path)
    selected = _selected_take(session, clip)
    package, plan, _profile, _wordmark = _signature_package_and_plan(
        "take-a", source=session.project.source_video_path)
    wrong_take = replace(selected, id="take-b")

    with pytest.raises(
            ExportJobSnapshotValidationError, match="frozen Voiceover take"):
        ExportJobSnapshot.capture(
            package=package,
            composition_plan=plan,
            source_video_path=session.project.source_video_path,
            clips=[clip],
            selected_voiceover=wrong_take,
            ink_event_track=[],
        )


def test_voiceover_ink_input_must_bind_to_same_take_as_audio_and_events(
        tmp_path):
    session, clip = _project(tmp_path)
    take = _selected_take(session, clip)
    package, plan, _profile, _wordmark = _signature_package_and_plan(
        take.id, source=session.project.source_video_path)
    package = replace(
        package,
        compositor_inputs=tuple(
            CompositorInput(item.name, f"clip:{clip.id}:ink")
            if item.name == "ink_event_track" else item
            for item in package.compositor_inputs
        ),
    )
    plan = build_composition_plan(
        package,
        plan.source_film.source_size,
        template_identity=plan.template_identity,
    )

    with pytest.raises(
            ExportJobSnapshotValidationError, match="Voiceover.*ink.*take"):
        ExportJobSnapshot.capture(
            package=package,
            composition_plan=plan,
            source_video_path=session.project.source_video_path,
            clips=[clip],
            selected_voiceover=take,
            ink_event_track=[],
        )


def test_voiceover_track_requires_effective_frame_zero_source_anchor(tmp_path):
    session, clip = _project(tmp_path)
    take = _selected_take(session, clip)
    package, plan, _profile, _wordmark = _signature_package_and_plan(
        take.id, source=session.project.source_video_path)
    wrong_last = PresentationEventTrack(
        sample_rate=48_000,
        events=(
            PresentationEvent.create(
                audio_frame=0,
                sequence=0,
                kind=PresentationEventKind.SOURCE_POSITION,
                payload={"source_position_ms": take.source_anchor_ms},
            ),
            PresentationEvent.create(
                audio_frame=0,
                sequence=1,
                kind=PresentationEventKind.SOURCE_POSITION,
                payload={"source_position_ms": take.source_anchor_ms + 1},
            ),
        ),
    )

    with pytest.raises(
            ExportJobSnapshotValidationError, match="frame-zero.*anchor"):
        ExportJobSnapshot.capture(
            package=package,
            composition_plan=plan,
            source_video_path=session.project.source_video_path,
            clips=[clip],
            selected_voiceover=replace(take, presentation_track=wrong_last),
            ink_event_track=[],
        )

    # Sequence, not tuple construction order, decides the effective update.
    matching_last = PresentationEventTrack(
        sample_rate=48_000,
        events=(
            PresentationEvent.create(
                audio_frame=0,
                sequence=1,
                kind=PresentationEventKind.SOURCE_POSITION,
                payload={"source_position_ms": take.source_anchor_ms},
            ),
            PresentationEvent.create(
                audio_frame=0,
                sequence=0,
                kind=PresentationEventKind.SOURCE_POSITION,
                payload={"source_position_ms": take.source_anchor_ms + 1},
            ),
        ),
    )
    snapshot = ExportJobSnapshot.capture(
        package=package,
        composition_plan=plan,
        source_video_path=session.project.source_video_path,
        clips=[clip],
        selected_voiceover=replace(take, presentation_track=matching_last),
    )
    assert snapshot.voiceover is not None


def test_voiceover_track_requires_source_position_at_audio_frame_zero(tmp_path):
    session, clip = _project(tmp_path)
    take = _selected_take(session, clip)
    package, plan, _profile, _wordmark = _signature_package_and_plan(
        take.id, source=session.project.source_video_path)
    late_track = PresentationEventTrack(
        sample_rate=48_000,
        events=(PresentationEvent.create(
            audio_frame=1,
            sequence=0,
            kind=PresentationEventKind.SOURCE_POSITION,
            payload={"source_position_ms": take.source_anchor_ms},
        ),),
    )

    with pytest.raises(
            ExportJobSnapshotValidationError, match="audio frame 0"):
        ExportJobSnapshot.capture(
            package=package,
            composition_plan=plan,
            source_video_path=session.project.source_video_path,
            clips=[clip],
            selected_voiceover=replace(take, presentation_track=late_track),
            ink_event_track=[],
        )


def test_migration_and_reopen_preserve_a_clean_retry_snapshot(tmp_path):
    session, clip = _project(tmp_path)
    snapshot = _clean_snapshot(
        clip, source=session.project.source_video_path, accurate=False)
    job = _job(clip, snapshot)
    repo = ExportJobRepository(session.conn)
    repo.save_snapshot(job, created_at="2026-08-20T01:02:03+00:00")
    repo.update_status(job.id, JobStatus.FAILED, error_message="encoder failed")
    db_path = session.db_path
    session.conn.close()

    conn = open_project_db(db_path)
    try:
        columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(export_job_snapshots)")
        }
        assert {
            "package_json", "composition_plan_json", "manifest_json",
            "integrity_sha256",
        } <= columns
        retry = ExportJobRepository(conn).load_for_retry(job.id)
    finally:
        conn.close()

    assert retry.status is JobStatus.WAITING
    assert retry.snapshot is not None
    assert retry.snapshot.package.accurate_cut is False
    assert retry.snapshot.package.technical_preset == SOURCE_QUALITY.name
    assert retry.snapshot.clips == snapshot.clips
    assert retry.snapshot.integrity_sha256 == snapshot.integrity_sha256


def test_retry_worker_uses_stored_accuracy_source_and_range(
        tmp_path, monkeypatch):
    session, queued_clip = _project(tmp_path)
    queued_source = Path(session.project.source_video_path)
    snapshot = _clean_snapshot(
        queued_clip, source=str(queued_source), accurate=False)
    job = _job(queued_clip, snapshot)
    ExportJobRepository(session.conn).save_snapshot(job)
    current_project = replace(
        session.project, source_video_path="D:/current/source.mp4")
    current_clip = Clip(
        id=queued_clip.id, project_id=7, start_ms=9_000, end_ms=19_000)
    worker = ExportWorker(
        "ffmpeg", current_project, [job], {current_clip.id: current_clip}, True,
        hardware_encoder="h264_nvenc",
        database_path=session.db_path,
    )
    observed = {}

    def fake_export(
            _ffmpeg, project, clip, _output, preset, accurate,
            _runner, _progress):
        observed.update(
            source=project.source_video_path,
            start_ms=clip.start_ms,
            end_ms=clip.end_ms,
            preset=preset.name,
            hardware_encoder=preset.hardware_encoder,
            accurate=accurate,
        )
        return "fake"

    monkeypatch.setattr(
        "tapesift.workers.export_worker.export_service.export_clip",
        fake_export,
    )

    worker._run_job(job)

    assert observed == {
        "source": str(queued_source),
        "start_ms": 1_000,
        "end_ms": 4_000,
        "preset": SOURCE_QUALITY.name,
        "hardware_encoder": "",
        "accurate": False,
    }


def test_source_identity_is_reused_for_a_batch_without_rehashing(
        tmp_path, monkeypatch):
    session, first_clip = _project(tmp_path)
    second_clip = session.add_clip(Clip(start_ms=5_000, end_ms=8_000))
    source = session.project.source_video_path
    identity = ExportJobQueueService.capture_source_identity(source)

    def unexpected_rehash(_path):
        raise AssertionError("batch source was hashed more than once")

    monkeypatch.setattr(
        "tapesift.models.export_job_snapshot._source_file_identity",
        unexpected_rehash,
    )
    first = _clean_snapshot(
        first_clip, source=source, source_identity=identity)
    second = _clean_snapshot(
        second_clip, source=source, source_identity=identity)

    assert first.source_sha256 == second.source_sha256 == identity.sha256
    assert first.source_file_size == second.source_file_size == identity.file_size


def test_source_hash_capture_cancels_between_8mib_chunks(tmp_path, monkeypatch):
    source = tmp_path / "large-source.bin"
    source.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    cancelled = threading.Event()
    original_open = Path.open

    class CancellingReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.stream.close()

        def read(self, size=-1):
            value = self.stream.read(size)
            if value:
                cancelled.set()
            return value

    def cancelling_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return CancellingReader(stream) if path == source else stream

    monkeypatch.setattr(Path, "open", cancelling_open)
    with pytest.raises(ExportCancelledError, match="cancelled"):
        SourceMediaIdentity.capture(source, cancel_event=cancelled)


def test_same_path_source_replacement_blocks_worker_before_export(
        tmp_path, monkeypatch):
    session, clip = _project(tmp_path)
    source = Path(session.project.source_video_path)
    snapshot = _clean_snapshot(clip, source=str(source))
    job = _job(clip, snapshot)
    called = False

    def should_not_export(*_args, **_kwargs):
        nonlocal called
        called = True
        return "not reached"

    monkeypatch.setattr(
        "tapesift.workers.export_worker.export_service.export_clip",
        should_not_export,
    )
    original = source.read_bytes()
    source.write_bytes(b"X" * len(original))
    ExportJobRepository(session.conn).save_snapshot(job)
    worker = ExportWorker(
        "ffmpeg", session.project, [job], {clip.id: clip}, accurate=True,
        database_path=session.db_path)

    worker._run_job(job)

    assert job.status is JobStatus.FAILED
    assert "changed after" in job.error_message
    assert called is False


def test_worker_verifies_one_shared_source_hash_once_per_batch(
        tmp_path, monkeypatch):
    session, first_clip = _project(tmp_path)
    second_clip = session.add_clip(Clip(start_ms=5_000, end_ms=8_000))
    source = session.project.source_video_path
    identity = ExportJobQueueService.capture_source_identity(source)
    first_snapshot = _clean_snapshot(
        first_clip, source=source, source_identity=identity)
    second_snapshot = _clean_snapshot(
        second_clip, source=source, source_identity=identity)
    first_job = _job(first_clip, first_snapshot, job_id="batch-1")
    second_job = _job(second_clip, second_snapshot, job_id="batch-2")
    calls = 0
    original = ExportJobSnapshot.verify_source_media

    def counted_verify(snapshot, *, cancel_event=None):
        nonlocal calls
        calls += 1
        return original(snapshot, cancel_event=cancel_event)

    monkeypatch.setattr(ExportJobSnapshot, "verify_source_media", counted_verify)
    monkeypatch.setattr(
        "tapesift.workers.export_worker.export_service.export_clip",
        lambda *_args, **_kwargs: "fake",
    )
    repository = ExportJobRepository(session.conn)
    repository.save_snapshot(first_job)
    repository.save_snapshot(second_job)
    worker = ExportWorker(
        "ffmpeg",
        session.project,
        [first_job, second_job],
        {first_clip.id: first_clip, second_clip.id: second_clip},
        accurate=True,
        database_path=session.db_path,
    )

    worker._run_job(first_job)
    worker._run_job(second_job)

    assert calls == 1
    assert first_job.status is JobStatus.COMPLETED
    assert second_job.status is JobStatus.COMPLETED


def test_snapshot_worker_persists_cpu_retry_completion(tmp_path, monkeypatch):
    session, clip = _project(tmp_path)
    snapshot = replace(
        _clean_snapshot(clip, source=session.project.source_video_path),
        hardware_encoder="h264_nvenc",
    )
    job = _job(clip, snapshot, job_id="cpu-retry-state")
    ExportJobRepository(session.conn).save_snapshot(job)
    attempts: list[str] = []

    def export_with_cpu_retry(
            _ffmpeg, _project, _clip, _output, preset, _accurate,
            _runner, _progress):
        attempts.append(preset.hardware_encoder)
        if preset.hardware_encoder:
            raise ExportError("hardware failed", "Retrying with CPU.")
        return "cpu command"

    monkeypatch.setattr(
        "tapesift.workers.export_worker.export_service.export_clip",
        export_with_cpu_retry,
    )
    worker = ExportWorker(
        "ffmpeg",
        session.project,
        [job],
        {clip.id: clip},
        accurate=True,
        database_path=session.db_path,
    )

    worker._run_job(job)

    reopened = open_project_db(session.db_path)
    try:
        stored = ExportJobRepository(reopened).load(job.id)
    finally:
        reopened.close()
    assert attempts == ["h264_nvenc", ""]
    assert stored is not None
    assert stored.job.status is JobStatus.COMPLETED
    assert stored.job.ffmpeg_command == "cpu command"
    assert stored.started_at is not None
    assert stored.completed_at is not None


def test_reopened_queue_lists_metadata_without_materializing_voiceover_blob(
        tmp_path):
    session, clip = _project(tmp_path)
    take = _selected_take(session, clip)
    package, plan, _profile, _wordmark = _signature_package_and_plan(
        take.id, source=session.project.source_video_path)
    job = ExportJob(
        id="metadata-only-signature",
        job_type=JobType.CLIP,
        display_name="Metadata Only Signature",
        output_path="D:/exports/metadata-only-signature.mp4",
        project_id=session.project.id,
        clip_id=clip.id,
        preset_name=SOCIAL_1080P.name,
    )
    ExportJobQueueService(session.conn).enqueue(
        job=job,
        package=package,
        composition_plan=plan,
        source_video_path=session.project.source_video_path,
        clips=[clip],
    )
    database_path = session.db_path
    project_id = session.project.id
    session.conn.close()

    reopened = open_project_db(database_path)

    def deny_staged_payload_reads(
            action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ:
            if table in {
                "export_job_identity_assets",
                "export_job_voiceover_payloads",
            }:
                return sqlite3.SQLITE_DENY
            if table == "export_job_snapshots" and column != "job_id":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    reopened.set_authorizer(deny_staged_payload_reads)
    summaries = ExportJobRepository(reopened).list_summaries(project_id)
    reopened.set_authorizer(None)
    reopened.close()

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.id == job.id
    assert summary.status is JobStatus.WAITING
    assert summary.display_name == job.display_name
    assert summary.clip_id == clip.id
    assert summary.clip_ids == ()
    assert summary.preset_name == SOCIAL_1080P.name


def test_cancel_queued_persists_without_worker_ever_running(tmp_path):
    session, clip = _project(tmp_path)
    snapshot = _clean_snapshot(
        clip, source=session.project.source_video_path)
    job = _job(clip, snapshot, job_id="cancel-before-worker-run")
    ExportJobRepository(session.conn).save_snapshot(job)
    database_path = session.db_path
    project = session.project
    session.conn.close()

    worker = ExportWorker(
        "unused-ffmpeg",
        project,
        [job],
        {},
        accurate=False,
        database_path=database_path,
    )
    assert worker.cancel_queued(job.id) is True
    assert worker.cancel_queued(job.id) is False

    reopened = open_project_db(database_path)
    persisted = ExportJobRepository(reopened).load(job.id)
    reopened.close()
    assert persisted is not None
    assert persisted.job.status is JobStatus.CANCELLED
    assert persisted.started_at is None
    assert persisted.completed_at is not None


def test_snapshot_worker_failure_allows_a_second_durable_retry(
        tmp_path, monkeypatch):
    session, clip = _project(tmp_path)
    snapshot = _clean_snapshot(
        clip, source=session.project.source_video_path)
    job = _job(clip, snapshot, job_id="retry-twice")
    repo = ExportJobRepository(session.conn)
    repo.save_snapshot(job)

    def fail_export(*_args, **_kwargs):
        raise ExportError("render failed", "Render failed.")

    monkeypatch.setattr(
        "tapesift.workers.export_worker.export_service.export_clip",
        fail_export,
    )
    worker = ExportWorker(
        "ffmpeg", session.project, [job], {clip.id: clip}, True,
        database_path=session.db_path,
    )
    worker._run_job(job)
    first_retry = repo.load_for_retry(job.id)
    ExportWorker(
        "ffmpeg", session.project, [first_retry], {clip.id: clip}, True,
        database_path=session.db_path,
    )._run_job(first_retry)

    reopened = open_project_db(session.db_path)
    try:
        failed_again = ExportJobRepository(reopened).load(job.id)
        second_retry = ExportJobRepository(reopened).load_for_retry(job.id)
    finally:
        reopened.close()
    assert failed_again is not None
    assert failed_again.job.status is JobStatus.FAILED
    assert failed_again.completed_at is not None
    assert second_retry.status is JobStatus.WAITING
    assert second_retry.snapshot == snapshot


def test_cancel_current_during_source_verification_persists_cancelled(
        tmp_path, monkeypatch):
    session, clip = _project(tmp_path)
    snapshot = _clean_snapshot(
        clip, source=session.project.source_video_path)
    job = _job(clip, snapshot, job_id="cancel-current-hash")
    ExportJobRepository(session.conn).save_snapshot(job)
    called = False

    def should_not_export(*_args, **_kwargs):
        nonlocal called
        called = True
        return "not reached"

    monkeypatch.setattr(
        "tapesift.workers.export_worker.export_service.export_clip",
        should_not_export,
    )
    worker = ExportWorker(
        "ffmpeg", session.project, [job], {clip.id: clip}, True,
        database_path=session.db_path,
    )
    worker.cancel_current()
    worker._run_job(job)

    stored = ExportJobRepository(session.conn).load(job.id)
    assert called is False
    assert job.status is JobStatus.CANCELLED
    assert stored is not None
    assert stored.job.status is JobStatus.CANCELLED
    assert stored.started_at is not None
    assert stored.completed_at is not None


def test_cancel_all_persists_every_waiting_snapshot_job(tmp_path):
    session, first_clip = _project(tmp_path)
    second_clip = session.add_clip(Clip(start_ms=5_000, end_ms=8_000))
    first = _job(
        first_clip,
        _clean_snapshot(
            first_clip, source=session.project.source_video_path),
        job_id="cancel-all-1",
    )
    second = _job(
        second_clip,
        _clean_snapshot(
            second_clip, source=session.project.source_video_path),
        job_id="cancel-all-2",
    )
    repo = ExportJobRepository(session.conn)
    repo.save_snapshot(first)
    repo.save_snapshot(second)
    worker = ExportWorker(
        "ffmpeg",
        session.project,
        [first, second],
        {first_clip.id: first_clip, second_clip.id: second_clip},
        accurate=True,
        database_path=session.db_path,
    )
    worker.cancel_all()

    database_path = session.db_path
    session.conn.close()
    reopened = open_project_db(database_path)
    reopened_repo = ExportJobRepository(reopened)

    for job in (first, second):
        stored = reopened_repo.load(job.id)
        assert stored is not None
        assert stored.job.status is JobStatus.CANCELLED
        assert stored.started_at is None
        assert stored.completed_at is not None
    reopened.close()


def test_status_updates_preserve_existing_lifecycle_timestamps(tmp_path):
    session, clip = _project(tmp_path)
    snapshot = _clean_snapshot(
        clip, source=session.project.source_video_path)
    job = _job(clip, snapshot)
    repo = ExportJobRepository(session.conn)
    repo.save_snapshot(job)
    repo.update_status(
        job.id,
        JobStatus.PREPARING,
        started_at="2026-08-20T03:00:00+00:00",
    )
    repo.update_status(
        job.id, JobStatus.FAILED, error_message="encoder failed")
    failed = session.conn.execute(
        "SELECT started_at, completed_at FROM export_jobs WHERE id=?",
        (job.id,),
    ).fetchone()
    assert failed["started_at"] == "2026-08-20T03:00:00+00:00"
    assert failed["completed_at"] is None

    repo.update_status(
        job.id,
        JobStatus.COMPLETED,
        completed_at="2026-08-20T03:02:00+00:00",
    )
    completed = session.conn.execute(
        "SELECT started_at, completed_at FROM export_jobs WHERE id=?",
        (job.id,),
    ).fetchone()
    assert completed["started_at"] == "2026-08-20T03:00:00+00:00"
    assert completed["completed_at"] == "2026-08-20T03:02:00+00:00"

    job.status = JobStatus.COMPLETED
    repeated = repo.save_snapshot(job)
    assert repeated.started_at == "2026-08-20T03:00:00+00:00"
    assert repeated.completed_at == "2026-08-20T03:02:00+00:00"


def test_startup_recovery_fails_preparing_and_exporting_once(
        tmp_path, monkeypatch):
    session, first_clip = _project(tmp_path)
    second_clip = session.add_clip(Clip(start_ms=5_000, end_ms=8_000))
    repo = ExportJobRepository(session.conn)
    jobs = (
        (
            _job(
                first_clip,
                _clean_snapshot(
                    first_clip, source=session.project.source_video_path),
                job_id="recover-preparing",
            ),
            JobStatus.PREPARING,
            "2026-08-20T04:00:00+00:00",
        ),
        (
            _job(
                second_clip,
                _clean_snapshot(
                    second_clip, source=session.project.source_video_path),
                job_id="recover-exporting",
            ),
            JobStatus.EXPORTING,
            "2026-08-20T04:01:00+00:00",
        ),
    )
    for job, status, started_at in jobs:
        repo.save_snapshot(job)
        repo.update_status(job.id, status, started_at=started_at)

    database_path = session.db_path
    session.conn.close()
    recovered_at = "2026-08-20T04:10:00+00:00"
    monkeypatch.setattr(
        "tapesift.database.repositories._utc_now", lambda: recovered_at)

    assert ExportJobQueueService.recover_interrupted_in_database(
        database_path) == 2

    reopened = open_project_db(database_path)
    try:
        for job, _status, started_at in jobs:
            row = reopened.execute(
                """SELECT status, error_message, started_at, completed_at
                   FROM export_jobs WHERE id=?""",
                (job.id,),
            ).fetchone()
            assert row["status"] == JobStatus.FAILED.value
            assert row["error_message"] == (
                "TapeSift closed before this export finished.")
            assert row["started_at"] == started_at
            assert row["completed_at"] == recovered_at
    finally:
        reopened.close()

    monkeypatch.setattr(
        "tapesift.database.repositories._utc_now",
        lambda: "2026-08-20T05:00:00+00:00",
    )
    assert ExportJobQueueService.recover_interrupted_in_database(
        database_path) == 0

    reopened = open_project_db(database_path)
    try:
        for job, _status, started_at in jobs:
            row = reopened.execute(
                """SELECT status, error_message, started_at, completed_at
                   FROM export_jobs WHERE id=?""",
                (job.id,),
            ).fetchone()
            assert row["status"] == JobStatus.FAILED.value
            assert row["error_message"] == (
                "TapeSift closed before this export finished.")
            assert row["started_at"] == started_at
            assert row["completed_at"] == recovered_at
    finally:
        reopened.close()


def test_two_connections_retry_exactly_once_after_startup_recovery(
        tmp_path, monkeypatch):
    session, clip = _project(tmp_path)
    snapshot = _clean_snapshot(
        clip, source=session.project.source_video_path)
    job = _job(clip, snapshot, job_id="recover-then-retry")
    repo = ExportJobRepository(session.conn)
    repo.save_snapshot(job)
    repo.update_status(
        job.id,
        JobStatus.EXPORTING,
        started_at="2026-08-20T04:00:00+00:00",
    )
    database_path = session.db_path
    session.conn.close()
    monkeypatch.setattr(
        "tapesift.database.repositories._utc_now",
        lambda: "2026-08-20T04:10:00+00:00",
    )
    assert ExportJobQueueService.recover_interrupted_in_database(
        database_path) == 1
    barrier = threading.Barrier(2)

    def claim() -> str:
        conn = open_project_db(database_path)
        try:
            barrier.wait(timeout=10)
            try:
                ExportJobRepository(conn).load_for_retry(job.id)
            except ExportJobRepositoryError:
                return "rejected"
            return "claimed"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=30) for future in (
            executor.submit(claim), executor.submit(claim)
        )]

    assert sorted(results) == ["claimed", "rejected"]


def test_startup_recovery_leaves_other_and_legacy_rows_untouched(
        tmp_path, monkeypatch):
    session, clip = _project(tmp_path)
    repo = ExportJobRepository(session.conn)
    statuses = (
        JobStatus.WAITING,
        JobStatus.FAILED,
        JobStatus.COMPLETED,
        JobStatus.CANCELLED,
    )
    for index, status in enumerate(statuses):
        snapshot = _clean_snapshot(
            clip, source=session.project.source_video_path)
        job = _job(
            clip, snapshot, job_id=f"preserve-{status.value.lower()}")
        repo.save_snapshot(job)
        repo.update_status(
            job.id,
            status,
            ffmpeg_command=f"keep-command-{index}",
            error_message=f"keep-error-{index}",
            started_at=f"2026-08-20T0{index}:00:00+00:00",
            completed_at=f"2026-08-20T0{index}:30:00+00:00",
        )

    for index, status in enumerate((
            JobStatus.PREPARING, JobStatus.EXPORTING), start=4):
        repo.record(
            f"legacy-{status.value.lower()}",
            session.project.id,
            clip.id,
            JobType.CLIP.value,
            status.value,
            f"D:/exports/legacy-{index}.mp4",
            f"legacy-command-{index}",
            f"legacy-error-{index}",
            f"2026-08-19T0{index}:00:00+00:00",
            f"2026-08-20T0{index}:00:00+00:00",
            None,
        )

    query = (
        "SELECT id, status, ffmpeg_command, error_message, started_at, "
        "completed_at FROM export_jobs ORDER BY id"
    )
    before = [tuple(row) for row in session.conn.execute(query).fetchall()]
    database_path = session.db_path
    session.conn.close()
    monkeypatch.setattr(
        "tapesift.database.repositories._utc_now",
        lambda: "2026-08-20T09:00:00+00:00",
    )

    assert ExportJobQueueService.recover_interrupted_in_database(
        database_path) == 0

    reopened = open_project_db(database_path)
    try:
        after = [tuple(row) for row in reopened.execute(query).fetchall()]
    finally:
        reopened.close()
    assert after == before


def test_retry_claim_is_persisted_and_cannot_be_claimed_twice(tmp_path):
    session, clip = _project(tmp_path)
    snapshot = _clean_snapshot(
        clip, source=session.project.source_video_path)
    job = _job(clip, snapshot)
    repo = ExportJobRepository(session.conn)
    repo.save_snapshot(job)
    repo.update_status(job.id, JobStatus.FAILED, error_message="failed")

    claimed = repo.load_for_retry(job.id)

    assert claimed.status is JobStatus.WAITING
    assert session.conn.execute(
        "SELECT status FROM export_jobs WHERE id=?", (job.id,)
    ).fetchone()["status"] == JobStatus.WAITING.value
    second = open_project_db(session.db_path)
    try:
        with pytest.raises(ExportJobRepositoryError, match="Only a failed"):
            ExportJobRepository(second).load_for_retry(job.id)
    finally:
        second.close()


def test_worker_thread_retry_owns_connection_and_uses_stored_snapshot(tmp_path):
    session, clip = _project(tmp_path)
    snapshot = _clean_snapshot(
        clip, source=session.project.source_video_path)
    job = _job(clip, snapshot)
    repo = ExportJobRepository(session.conn)
    repo.save_snapshot(job)
    repo.update_status(job.id, JobStatus.FAILED, error_message="failed")
    clip.start_ms = 99_000
    clip.end_ms = 100_000

    with ThreadPoolExecutor(max_workers=1) as executor:
        retried = executor.submit(
            ExportJobQueueService.retry_in_database,
            session.db_path,
            job.id,
        ).result(timeout=30)

    assert retried.status is JobStatus.WAITING
    assert retried.snapshot is not None
    assert retried.snapshot.clips[0].start_ms == snapshot.clips[0].start_ms
    assert retried.snapshot.clips[0].end_ms == snapshot.clips[0].end_ms


def test_concurrent_retry_claim_allows_exactly_one_runner(tmp_path):
    session, clip = _project(tmp_path)
    snapshot = _clean_snapshot(
        clip, source=session.project.source_video_path)
    job = _job(clip, snapshot)
    repo = ExportJobRepository(session.conn)
    repo.save_snapshot(job)
    repo.update_status(job.id, JobStatus.FAILED, error_message="failed")
    barrier = threading.Barrier(2)

    def claim() -> str:
        conn = open_project_db(session.db_path)
        try:
            barrier.wait(timeout=10)
            try:
                ExportJobRepository(conn).load_for_retry(job.id)
            except ExportJobRepositoryError:
                return "rejected"
            return "claimed"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=30) for future in (
            executor.submit(claim), executor.submit(claim)
        )]

    assert sorted(results) == ["claimed", "rejected"]


def test_worker_thread_staging_owns_its_database_connection(tmp_path):
    session, clip = _project(tmp_path)
    frozen_clips = ExportJobQueueService.freeze_clips_for_staging([clip])
    package = ExportPackageSnapshot(
        style=ExportStyle.CLEAN,
        technical_preset=SOURCE_QUALITY.name,
    )
    plan = build_composition_plan(package, PixelSize(1920, 1080))
    job = ExportJob(
        id="thread-staged",
        job_type=JobType.CLIP,
        display_name="Thread staged",
        output_path="D:/exports/thread-staged.mp4",
        project_id=session.project.id,
        clip_id=clip.id,
        preset_name=SOURCE_QUALITY.name,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        persisted = executor.submit(
            ExportJobQueueService.enqueue_in_database,
            session.db_path,
            job=job,
            package=package,
            composition_plan=plan,
            source_video_path=session.project.source_video_path,
            clips=frozen_clips,
        ).result(timeout=30)

    assert persisted.job.snapshot is not None
    assert ExportJobRepository(session.conn).load(job.id) is not None


def test_worker_thread_stages_exact_voiceover_blob_off_ui_connection(tmp_path):
    session, clip = _project(tmp_path)
    take = _selected_take(session, clip)
    package, plan, _profile, _wordmark = _signature_package_and_plan(
        take.id, source=session.project.source_video_path)
    job = ExportJob(
        id="thread-voiceover",
        job_type=JobType.CLIP,
        display_name="Thread Voiceover",
        output_path="D:/exports/thread-voiceover.mp4",
        project_id=session.project.id,
        clip_id=clip.id,
        preset_name=SOCIAL_1080P.name,
    )
    frozen_clips = ExportJobQueueService.freeze_clips_for_staging([clip])

    with ThreadPoolExecutor(max_workers=1) as executor:
        persisted = executor.submit(
            ExportJobQueueService.enqueue_in_database,
            session.db_path,
            job=job,
            package=package,
            composition_plan=plan,
            source_video_path=session.project.source_video_path,
            clips=frozen_clips,
        ).result(timeout=30)

    assert persisted.job.snapshot.voiceover.take_id == take.id
    assert persisted.job.snapshot.voiceover.audio_sha256
    assert persisted.job.snapshot.voiceover.waveform == take.waveform


def test_signature_job_stages_identity_voiceover_and_ink_across_reopen(tmp_path):
    session, clip = _project(tmp_path)
    clip.overlays = [{
        "id": "arrow-1",
        "kind": "arrow",
        "ink": "gold",
        "width": 1.0,
        "points": [[0.2, 0.3], [0.7, 0.6]],
    }]
    session.save()
    recorded_marks = [{
        "id": "recorded-circle",
        "kind": "circle",
        "ink": "cyan",
        "width": 1.0,
        "points": [[0.3, 0.3], [0.5, 0.5]],
    }]
    take = _selected_take(session, clip, marks=recorded_marks)
    package, plan, profile_bytes, wordmark_bytes = (
        _signature_package_and_plan(
            take.id, source=session.project.source_video_path))
    job = ExportJob(
        id="signature-job",
        job_type=JobType.CLIP,
        display_name="Signature Play",
        output_path="D:/exports/signature.mp4",
        project_id=session.project.id,
        clip_id=clip.id,
        preset_name=SOCIAL_1080P.name,
    )
    persisted = ExportJobQueueService(session.conn).enqueue(
        job=job,
        package=package,
        composition_plan=plan,
        source_video_path=session.project.source_video_path,
        clips=[clip],
    )
    assert persisted.job.snapshot is not None
    ExportJobRepository(session.conn).update_status(
        job.id, JobStatus.FAILED, error_message="render failed")

    # Mutate/delete the authoring sources after queueing. Retry must retain the
    # recorded take track, never the unrelated current accepted clip ink.
    clip.overlays = []
    VoiceoverRepository(session.conn).delete(take.id)
    session.save()
    db_path = session.db_path
    session.conn.close()

    conn = open_project_db(db_path)
    try:
        retry = ExportJobQueueService(conn).retry(job.id)
    finally:
        conn.close()

    assert retry.snapshot is not None
    assert [asset.data for asset in retry.snapshot.identity_assets] == [
        profile_bytes, wordmark_bytes,
    ]
    assert retry.snapshot.voiceover is not None
    assert retry.snapshot.voiceover.audio_wav == take.audio_wav
    assert retry.snapshot.voiceover.presentation_track_json == (
        take.presentation_track.to_json())
    assert retry.snapshot.voiceover.waveform == take.waveform
    assert retry.snapshot.voiceover.ink_input_value == f"take:{take.id}:ink"
    assert retry.snapshot.ink_event_track_json == ""
    track = PresentationEventTrack.from_json(
        retry.snapshot.voiceover.presentation_track_json)
    recorded = [
        event.payload["marks"]
        for event in track.events
        if event.kind is PresentationEventKind.TELESTRATION_SNAPSHOT
    ]
    assert recorded == [recorded_marks]


def test_async_staging_uses_take_id_frozen_when_export_was_clicked(tmp_path):
    session, clip = _project(tmp_path)
    clicked_take = _selected_take(session, clip)
    package, plan, _profile, _wordmark = _signature_package_and_plan(
        clicked_take.id, source=session.project.source_video_path)
    package = replace(
        package,
        compositor_inputs=tuple(
            CompositorInput(item.name, clicked_take.id)
            if item.name in {"voiceover_audio", "presentation_event_track"}
            else item
            for item in package.compositor_inputs
        ),
    )
    plan = build_composition_plan(
        package,
        plan.source_film.source_size,
        template_identity=plan.template_identity,
    )

    # The user selects another take before the background staging transaction
    # begins. The package's exact take id remains authoritative.
    VoiceoverRepository(session.conn).save(replace(
        clicked_take,
        id="take-2",
        label="Selected later",
        selected=True,
    ))
    assert VoiceoverRepository(session.conn).selected_for_clip(
        session.project.id, clip.id).id == "take-2"
    job = ExportJob(
        id="click-time-take",
        job_type=JobType.CLIP,
        display_name="Click-time take",
        output_path="D:/exports/click-time.mp4",
        project_id=session.project.id,
        clip_id=clip.id,
        preset_name=SOCIAL_1080P.name,
    )

    persisted = ExportJobQueueService(session.conn).enqueue(
        job=job,
        package=package,
        composition_plan=plan,
        source_video_path=session.project.source_video_path,
        clips=[clip],
    )

    assert persisted.job.snapshot.voiceover.take_id == clicked_take.id
    assert persisted.job.snapshot.voiceover.audio_wav == clicked_take.audio_wav


def test_no_voiceover_composited_snapshot_freezes_range_and_static_ink_but_fails_closed(
        tmp_path):
    session, clip = _project(tmp_path)
    clip.overlays = [{
        "id": "static-circle",
        "kind": "circle",
        "ink": "cyan",
        "width": 1.0,
        "points": [[0.25, 0.25], [0.4, 0.4]],
    }]
    session.save()
    base, base_plan, _profile, _wordmark = _signature_package_and_plan(
        "unused-take", source=session.project.source_video_path)
    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=SOCIAL_1080P.name,
        template=base.template,
        compositor_inputs=(
            CompositorInput(
                "source_video", session.project.source_video_path),
            CompositorInput("ink_event_track", f"clip:{clip.id}:ink"),
        ),
        include_ink=True,
    )
    plan = build_composition_plan(
        package,
        base_plan.source_film.source_size,
        template_identity=base_plan.template_identity,
    )
    job = ExportJob(
        id="no-voiceover",
        job_type=JobType.CLIP,
        display_name="No Voiceover",
        output_path="D:/exports/no-voiceover.mp4",
        project_id=session.project.id,
        clip_id=clip.id,
        preset_name=SOCIAL_1080P.name,
    )
    persisted = ExportJobQueueService(session.conn).enqueue(
        job=job,
        package=package,
        composition_plan=plan,
        source_video_path=session.project.source_video_path,
        clips=[clip],
    )

    assert persisted.job.snapshot.voiceover is None
    assert persisted.job.snapshot.clips[0].start_ms == clip.start_ms
    assert json.loads(
        persisted.job.snapshot.ink_event_track_json)[0]["id"] == "static-circle"
    worker = ExportWorker(
        "ffmpeg", session.project, [job], {clip.id: clip}, accurate=True,
        database_path=session.db_path)
    worker._run_job(job)
    assert job.status is JobStatus.FAILED
    assert "no approved soundtrack policy" in job.error_message


def test_composited_retry_worker_uses_only_reopened_snapshot_inputs(
        tmp_path, monkeypatch):
    session, clip = _project(tmp_path)
    clip.overlays = [{
        "id": "arrow-1", "kind": "arrow", "ink": "gold", "width": 1.0,
        "points": [[0.2, 0.3], [0.7, 0.6]],
    }]
    session.save()
    take = _selected_take(session, clip)
    package, plan, _profile, _wordmark = _signature_package_and_plan(
        take.id, source=session.project.source_video_path)
    job = ExportJob(
        id="retry-signature",
        job_type=JobType.CLIP,
        display_name="Stored Signature",
        output_path="D:/exports/stored-signature.mp4",
        project_id=session.project.id,
        clip_id=clip.id,
        preset_name=SOCIAL_1080P.name,
    )
    service = ExportJobQueueService(session.conn)
    service.enqueue(
        job=job,
        package=package,
        composition_plan=plan,
        source_video_path=session.project.source_video_path,
        clips=[clip],
    )
    queued_source = session.project.source_video_path
    service.repository.update_status(job.id, JobStatus.FAILED)

    # Everything a live panel could now point at is different. The retry job
    # is reconstructed from the durable snapshot before it reaches the worker.
    VoiceoverRepository(session.conn).delete(take.id)
    clip.overlays = []
    session.project.source_video_path = "D:/ui/changed-source.mp4"
    current_clip = Clip(
        id=clip.id, project_id=session.project.id,
        start_ms=40_000, end_ms=55_000,
    )
    retry = service.retry(job.id)
    observed = {}

    def fake_composited(
            _ffmpeg, source, output, locked_plan, inputs, preset, **kwargs):
        observed.update(
            source=str(source),
            output=str(output),
            plan_json=locked_plan.to_json(),
            audio=inputs.voiceover_audio,
            events=inputs.presentation_event_track,
            frames=inputs.voiceover_frame_count,
            waveform=inputs.voiceover_waveform,
            assets=tuple((asset.role.value, asset.data) for asset in inputs.assets),
            situation=inputs.play_call_situation,
            concept=inputs.play_call_concept,
            result=inputs.play_call_result,
            preset=preset.name,
            cancel_event=kwargs["cancel_event"],
        )
        return "stored compositor command"

    monkeypatch.setattr(
        "tapesift.workers.export_worker.composited_export_service."
        "export_composited_sequence",
        fake_composited,
    )
    worker = ExportWorker(
        "current-ui-ffmpeg",
        session.project,
        [retry],
        {current_clip.id: current_clip},
        accurate=False,
        hardware_encoder="h264_nvenc",
        database_path=session.db_path,
    )
    worker._run_job(retry)

    assert retry.status is JobStatus.COMPLETED
    assert Path(observed["source"]) == Path(queued_source)
    assert Path(observed["output"]) == Path("D:/exports/stored-signature.mp4")
    assert observed["plan_json"] == plan.to_json()
    assert observed["audio"] == take.audio_wav
    assert observed["events"] == take.presentation_track.to_json().encode("utf-8")
    assert observed["frames"] == take.frame_count
    assert observed["waveform"] == take.waveform
    assert observed["situation"] == b"3rd & 7"
    assert observed["concept"] == b"Mesh"
    assert observed["result"] == b"+18"
    assert observed["preset"] == SOCIAL_1080P.name
    assert observed["cancel_event"] is worker._runner.cancel_event


def test_reopened_signature_job_executes_real_bundled_ffmpeg_runtime(
        tmp_path, monkeypatch):
    source = _source_video(tmp_path)
    session = ProjectSession.create("Runtime", tmp_path, tmp_path / "out")
    session.project.source_video_path = str(source)
    session.project.source_metadata.width = 32
    session.project.source_metadata.height = 18
    clip = session.add_clip(Clip(start_ms=0, end_ms=500))
    clip.overlays = [{
        "id": "arrow-1", "kind": "arrow", "ink": "gold", "width": 1.0,
        "points": [[0.2, 0.3], [0.7, 0.6]],
    }]
    session.save()
    take = _selected_take(session, clip, frame_count=48_000)
    package, plan, _profile, _wordmark = _signature_package_and_plan(
        take.id, source=str(source), source_size=PixelSize(32, 18))
    output = tmp_path / "runtime-signature.mp4"
    job = ExportJob(
        id="runtime-signature",
        job_type=JobType.CLIP,
        display_name="Runtime Signature",
        output_path=str(output),
        project_id=session.project.id,
        clip_id=clip.id,
        preset_name=SOCIAL_1080P.name,
    )
    service = ExportJobQueueService(session.conn)
    service.enqueue(
        job=job,
        package=package,
        composition_plan=plan,
        source_video_path=source,
        clips=[clip],
    )
    service.repository.update_status(job.id, JobStatus.FAILED)
    retry = service.retry(job.id)
    worker = ExportWorker(
        _bundled("ffmpeg"), session.project, [retry], {}, accurate=False,
        database_path=session.db_path)

    def reject_wav_waveform_rescan(*_args, **_kwargs):
        raise AssertionError("stored Voiceover waveform was not used")

    monkeypatch.setattr(
        "tapesift.services.preview_compositor._decode_waveform",
        reject_wav_waveform_rescan,
    )

    worker._run_job(retry)

    assert retry.status is JobStatus.COMPLETED, retry.error_message
    assert output.is_file() and output.stat().st_size > 0
    probe = subprocess.run(
        [
            _bundled("ffprobe"), "-v", "error", "-show_streams",
            "-of", "json", str(output),
        ],
        capture_output=True,
        creationflags=ffmpeg_service.CREATE_NO_WINDOW,
        timeout=30,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1920, 1080)
    assert any(stream["codec_type"] == "audio" for stream in streams)
    session.conn.close()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE export_job_identity_assets SET data=x'89504e47' "
        "WHERE job_id='signature-job' AND role='profile_photo'",
        "UPDATE export_job_voiceover_payloads "
        "SET audio_wav=zeroblob(length(audio_wav)) "
        "WHERE job_id='signature-job'",
        "UPDATE export_job_voiceover_payloads SET waveform_json='[2]' "
        "WHERE job_id='signature-job'",
        "UPDATE export_job_voiceover_payloads SET waveform_json='[]' "
        "WHERE job_id='signature-job'",
        "UPDATE export_job_snapshots SET manifest_json='{\"x\":1}' "
        "WHERE job_id='signature-job'",
        "UPDATE export_job_snapshots SET composition_plan_json="
        "replace(composition_plan_json, '\"style\":\"signature\"', "
        "'\"style\":\"vertical\"') WHERE job_id='signature-job'",
        "UPDATE export_jobs SET output_path='D:/exports/forged.mp4' "
        "WHERE id='signature-job'",
        "UPDATE export_jobs SET settings_json="
        "replace(settings_json, 'Signature Play', 'Forged') "
        "WHERE id='signature-job'",
    ],
)
def test_staged_payload_and_binding_corruption_blocks_retry(tmp_path, statement):
    session, clip = _project(tmp_path)
    clip.overlays = [{
        "id": "arrow-1", "kind": "arrow", "ink": "gold", "width": 1.0,
        "points": [[0.2, 0.3], [0.7, 0.6]],
    }]
    session.save()
    take = _selected_take(session, clip)
    package, plan, _profile, _wordmark = _signature_package_and_plan(
        take.id, source=session.project.source_video_path)
    job = ExportJob(
        id="signature-job",
        job_type=JobType.CLIP,
        display_name="Signature Play",
        output_path="D:/exports/signature.mp4",
        project_id=session.project.id,
        clip_id=clip.id,
        preset_name=SOCIAL_1080P.name,
    )
    ExportJobQueueService(session.conn).enqueue(
        job=job,
        package=package,
        composition_plan=plan,
        source_video_path=session.project.source_video_path,
        clips=[clip],
    )
    ExportJobRepository(session.conn).update_status(job.id, JobStatus.FAILED)
    session.conn.execute(statement)
    session.conn.commit()

    with pytest.raises(ExportJobRepositoryError, match="corrupt immutable inputs"):
        ExportJobRepository(session.conn).load_for_retry(job.id)
    session.conn.close()


def test_existing_job_id_cannot_replace_its_immutable_snapshot(tmp_path):
    session, clip = _project(tmp_path)
    first = _clean_snapshot(
        clip, source=session.project.source_video_path, accurate=False)
    job = _job(clip, first)
    repo = ExportJobRepository(session.conn)
    repo.save_snapshot(job, created_at="2026-08-20T01:02:03+00:00")

    replacement = _clean_snapshot(
        clip, source=session.project.source_video_path, accurate=True)
    job.snapshot = replacement
    with pytest.raises(ValueError, match="immutable"):
        repo.save_snapshot(job)

    stored = repo.load(job.id)
    assert stored is not None
    assert stored.job.snapshot.package.accurate_cut is False
    session.conn.close()


def test_plan_from_another_layer_selection_cannot_share_a_package_snapshot(
        tmp_path):
    source = tmp_path / "plan-source.bin"
    source.write_bytes(b"plan source revision")
    source_path = str(source)
    clip = Clip(id="clip-1", project_id=7, start_ms=1_000, end_ms=4_000)
    original, plan, _profile, _wordmark = _signature_package_and_plan(
        "take-1", source=source_path)
    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=SOCIAL_1080P.name,
        template=original.template,
        compositor_inputs=(
            CompositorInput("source_video", source_path),
        ),
    )

    with pytest.raises(
            ExportJobSnapshotValidationError,
            match="differs from the locked Export Package"):
        clean = _clean_snapshot(clip, source=source_path)
        ExportJobSnapshot(
            package=package,
            composition_plan=plan,
            source_video_path=source_path,
            source_file_size=clean.source_file_size,
            source_sha256=clean.source_sha256,
            # Direct construction isolates the package/plan seam before any
            # staged Voiceover or ink payload is considered.
            clips=clean.clips,
        )


def test_undefined_slate_never_reaches_the_durable_queue(tmp_path):
    session, clip = _project(tmp_path)
    package = ExportPackageSnapshot(
        style=ExportStyle.CLEAN,
        include_slate=True,
    )
    with pytest.raises(ExportPackageValidationError, match="Slate"):
        package.to_json()
    assert session.conn.execute(
        "SELECT count(*) FROM export_job_snapshots").fetchone()[0] == 0
    session.conn.close()
