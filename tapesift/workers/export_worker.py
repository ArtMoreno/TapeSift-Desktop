"""Export queue worker: processes ExportJobs on a QThread, one at a time."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from tapesift.core.exceptions import (
    TapeSiftError, ExportCancelledError, ExportError, HardwareEncoderError,
)
from tapesift.database.connection import open_project_db
from tapesift.database.repositories import ExportJobRepository
from tapesift.models.export_job import ExportJob, JobStatus, JobType
from tapesift.models.export_job_snapshot import ExportJobSnapshotValidationError
from tapesift.models.export_settings import ReelSettings, get_preset
from tapesift.models.project import Project
from tapesift.services import composited_export_service, export_service
from tapesift.services.presentation_sequence_renderer import (
    CompositionSequenceInputs,
)
from tapesift.services.preview_compositor import CompositionAssetPayload

log = logging.getLogger(__name__)


class ExportWorker(QThread):
    """Runs a list of export jobs sequentially off the UI thread.

    Signals carry job id + data; the queue panel owns the ExportJob objects.
    """

    job_started = Signal(str)                 # job_id
    job_progress = Signal(str, float, float)  # job_id, percent, elapsed_seconds
    job_completed = Signal(str, str)          # job_id, output_path
    job_failed = Signal(str, str)             # job_id, error message
    job_cancelled = Signal(str)               # job_id
    all_finished = Signal()

    def __init__(self, ffmpeg_path: str, project: Project, jobs: list[ExportJob],
                 clips_by_id: dict, accurate: bool,
                 reel_settings: ReelSettings | None = None,
                 hardware_encoder: str = "", parent=None,
                 database_path: str | Path | None = None) -> None:
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.project = project
        self.jobs = jobs
        self.clips_by_id = clips_by_id
        self.accurate = accurate
        self.reel_settings = reel_settings
        self.hardware_encoder = hardware_encoder
        self.database_path = (
            Path(database_path) if database_path is not None else None)
        if any(job.snapshot is not None for job in jobs) \
                and self.database_path is None:
            raise ValueError(
                "Snapshot export workers require the project database path."
            )
        self._runner = export_service.FFmpegRunner()
        self._cancel_all = False
        self._state_connection = None
        self._state_repository: ExportJobRepository | None = None
        self._verified_source_revisions: dict[
            tuple[str, int, str], tuple[int, int, int, int]
        ] = {}

    def cancel_current(self) -> None:
        self._runner.cancel()

    def cancel_all(self) -> None:
        self._cancel_all = True
        self._runner.cancel()
        for job in self.jobs:
            if job.status == JobStatus.WAITING:
                self.cancel_queued(job.id)

    def cancel_queued(self, job_id: str) -> bool:
        """Cancel a not-yet-started output before the worker reaches it.

        Snapshot jobs are claimed through a short-lived connection so callers
        never touch the worker thread's connection and a restart observes the
        cancellation even if ``run`` was never entered.
        """
        for job in self.jobs:
            if job.id == job_id and job.status == JobStatus.WAITING:
                if job.snapshot is not None:
                    connection = open_project_db(self.database_path)
                    try:
                        cancelled = ExportJobRepository(
                            connection).cancel_waiting(job.id)
                    finally:
                        connection.close()
                    if not cancelled:
                        return False
                job.status = JobStatus.CANCELLED
                return True
        return False

    def run(self) -> None:
        self._open_state_repository()
        try:
            for job in self.jobs:
                if job.status == JobStatus.CANCELLED or self._cancel_all:
                    self._mark_cancelled(job)
                    continue
                self._runner = export_service.FFmpegRunner()
                self._run_job(job)
        finally:
            self._close_state_repository()
            self.all_finished.emit()

    def _open_state_repository(self) -> None:
        if self._state_repository is not None or self.database_path is None:
            return
        self._state_connection = open_project_db(self.database_path)
        self._state_repository = ExportJobRepository(self._state_connection)

    def _close_state_repository(self) -> None:
        connection = self._state_connection
        self._state_repository = None
        self._state_connection = None
        if connection is not None:
            connection.close()

    def _persist_status(
            self,
            job: ExportJob,
            status: JobStatus,
            *,
            started_at: str | None = None,
            completed_at: str | None = None,
            include_started: bool = False,
            include_completed: bool = False,
    ) -> None:
        if job.snapshot is None:
            return
        if self._state_repository is None:
            raise RuntimeError(
                "Snapshot export state requires a worker-owned database."
            )
        timestamps: dict[str, str | None] = {}
        if include_started:
            timestamps["started_at"] = started_at
        if include_completed:
            timestamps["completed_at"] = completed_at
        self._state_repository.update_status(
            job.id,
            status,
            ffmpeg_command=job.ffmpeg_command,
            error_message=job.error_message,
            **timestamps,
        )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _mark_cancelled(self, job: ExportJob) -> None:
        job.status = JobStatus.CANCELLED
        self._persist_status(
            job,
            JobStatus.CANCELLED,
            completed_at=self._utc_now(),
            include_completed=True,
        )
        self.job_cancelled.emit(job.id)

    def _run_job(self, job: ExportJob) -> None:
        owns_repository = False
        if job.snapshot is not None and self._state_repository is None:
            self._open_state_repository()
            owns_repository = True
        try:
            self._run_job_with_repository(job)
        finally:
            if owns_repository:
                self._close_state_repository()

    def _run_job_with_repository(self, job: ExportJob) -> None:
        started = time.monotonic()
        # Publish the authoritative state before the queued UI signal can be
        # delivered. This closes the small handoff window where a job already
        # entering FFmpeg could still look removable to the main thread.
        job.status = JobStatus.EXPORTING
        self._persist_status(
            job,
            JobStatus.EXPORTING,
            started_at=self._utc_now(),
            completed_at=None,
            include_started=True,
            include_completed=True,
        )
        self.job_started.emit(job.id)

        def on_progress(pct: float) -> None:
            self.job_progress.emit(job.id, pct, time.monotonic() - started)

        preset_name = (
            job.snapshot.package.technical_preset
            if job.snapshot is not None else job.preset_name
        )
        hardware_encoder = (
            job.snapshot.hardware_encoder
            if job.snapshot is not None else self.hardware_encoder
        )
        preset = get_preset(preset_name)
        if hardware_encoder and not preset.stream_copy:
            preset = preset.with_hardware(hardware_encoder)
        try:
            self._execute(job, preset, on_progress)
            job.status = JobStatus.COMPLETED
            self._persist_status(
                job,
                JobStatus.COMPLETED,
                completed_at=self._utc_now(),
                include_completed=True,
            )
            self.job_completed.emit(job.id, job.output_path)
        except ExportCancelledError:
            self._mark_cancelled(job)
        except TapeSiftError as exc:
            if hardware_encoder and not preset.stream_copy:
                # Hardware encoder failed - retry once on CPU.
                log.warning("Hardware encode failed (%s); retrying with CPU", exc.message)
                try:
                    self._execute(job, get_preset(preset_name), on_progress)
                    job.status = JobStatus.COMPLETED
                    self._persist_status(
                        job,
                        JobStatus.COMPLETED,
                        completed_at=self._utc_now(),
                        include_completed=True,
                    )
                    self.job_completed.emit(job.id, job.output_path)
                    return
                except ExportCancelledError:
                    self._mark_cancelled(job)
                    return
                except TapeSiftError as cpu_exc:
                    exc = cpu_exc
            job.status = JobStatus.FAILED
            job.error_message = exc.user_text()
            self._persist_status(
                job,
                JobStatus.FAILED,
                completed_at=self._utc_now(),
                include_completed=True,
            )
            log.error("Export job %s failed: %s", job.display_name, exc.message)
            self.job_failed.emit(job.id, exc.user_text())
        except Exception as exc:  # defensive: never kill the queue thread
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            self._persist_status(
                job,
                JobStatus.FAILED,
                completed_at=self._utc_now(),
                include_completed=True,
            )
            log.exception("Unexpected export failure for %s", job.display_name)
            self.job_failed.emit(job.id, f"Unexpected error: {exc}")

    def _execute(self, job: ExportJob, preset, on_progress) -> None:
        snapshot = job.snapshot
        project = self.project
        clips_by_id = self.clips_by_id
        accurate = self.accurate
        reel_settings = self.reel_settings
        if snapshot is not None:
            try:
                source_path = Path(snapshot.source_video_path)
                stat = source_path.stat()
                stat_revision = (
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    stat.st_ino,
                )
                source_key = (
                    snapshot.source_video_path,
                    snapshot.source_file_size,
                    snapshot.source_sha256,
                )
                if self._verified_source_revisions.get(
                        source_key) != stat_revision:
                    snapshot.verify_source_media(
                        cancel_event=self._runner.cancel_event)
                    verified = source_path.stat()
                    self._verified_source_revisions[source_key] = (
                        verified.st_size,
                        verified.st_mtime_ns,
                        verified.st_ctime_ns,
                        verified.st_ino,
                    )
            except OSError as exc:
                raise ExportError(
                    f"Queued source video cannot be read: "
                    f"{snapshot.source_video_path}.",
                    "Relink the source and queue a new export.",
                ) from exc
            except ExportJobSnapshotValidationError as exc:
                raise ExportError(
                    str(exc),
                    "Restore that exact source revision or queue a new export.",
                ) from exc
            if snapshot.package.is_composited:
                voiceover = snapshot.voiceover
                if voiceover is None:
                    raise ExportError(
                        "Composited export without Voiceover has no approved "
                        "soundtrack policy.",
                        "Enable and select a Voiceover take for Signature or "
                        "Vertical export.",
                    )
                def text_input(name: str) -> bytes | None:
                    value = snapshot.compositor_input_value(name)
                    return value.encode("utf-8") if value else None

                inputs = CompositionSequenceInputs(
                    voiceover_audio=voiceover.audio_wav,
                    presentation_event_track=(
                        voiceover.presentation_track_json.encode("utf-8")),
                    voiceover_frame_count=voiceover.frame_count,
                    voiceover_waveform=voiceover.waveform,
                    assets=tuple(
                        CompositionAssetPayload(asset.role, asset.data)
                        for asset in snapshot.identity_assets
                    ),
                    play_call_situation=text_input("play_call_situation"),
                    play_call_concept=text_input("play_call_concept"),
                    play_call_result=text_input("play_call_result"),
                )
                job.ffmpeg_command = (
                    composited_export_service.export_composited_sequence(
                        self.ffmpeg_path,
                        Path(snapshot.source_video_path),
                        Path(job.output_path),
                        snapshot.composition_plan,
                        inputs,
                        preset,
                        cancel_event=self._runner.cancel_event,
                        on_progress=on_progress,
                    )
                )
                return
            project = replace(
                self.project,
                source_video_path=snapshot.source_video_path,
            )
            clips_by_id = {
                clip.clip_id: clip.to_clip() for clip in snapshot.clips
            }
            accurate = snapshot.package.accurate_cut
            reel_settings = snapshot.reel_settings.to_reel_settings()
        if job.job_type == JobType.CLIP:
            clip = clips_by_id[job.clip_id]
            job.ffmpeg_command = export_service.export_clip(
                self.ffmpeg_path, project, clip, Path(job.output_path),
                preset, accurate, self._runner, on_progress)
        else:
            clips = [clips_by_id[cid] for cid in job.clip_ids
                     if cid in clips_by_id]
            job.ffmpeg_command = export_service.export_reel(
                self.ffmpeg_path, project, clips, Path(job.output_path),
                preset, accurate, self._runner, reel_settings, on_progress)
