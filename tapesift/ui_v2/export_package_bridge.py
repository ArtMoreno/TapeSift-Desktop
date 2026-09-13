"""Asynchronous boundaries for the immutable Export Package UI.

Nothing in this module owns playback.  Selected-take preview resolves the
recorded Voiceover clock into one source PTS and one recorded ink snapshot,
then asks the production FFmpeg loader and compositor for that exact frame.
Queue staging, retry, recovery, and cancellation each open SQLite only on the
worker thread that performs the operation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from tapesift.core.exceptions import ExportCancelledError
from tapesift.database.connection import open_project_db
from tapesift.models.clip import Clip
from tapesift.models.composition_plan import CompositionPlan
from tapesift.models.export_job import ExportJob, JobStatus
from tapesift.models.export_package import ExportPackageSnapshot
from tapesift.models.export_settings import ReelSettings
from tapesift.services.composited_export_service import (
    RandomAccessFFmpegFrameLoader,
)
from tapesift.services.export_job_queue_service import ExportJobQueueService
from tapesift.services.presentation_replay import PresentationReplay
from tapesift.services.preview_compositor import (
    CompositionAssetPayload,
    CompositionFrameCompositor,
    CompositionFramePayloads,
)


def request_export_worker_cancel_all(worker) -> None:
    """Signal cancellation without running durable SQLite work on the GUI.

    ``ExportWorker.cancel_all`` intentionally offers synchronous durability for
    non-GUI callers.  The window uses this adapter, then dispatches each WAITING
    row through :class:`ExportQueuedCancelThread`.
    """

    worker._cancel_all = True
    worker.cancel_current()


@dataclass(frozen=True, slots=True)
class SelectedTakePreviewRequest:
    """All immutable, non-playback inputs for one selected-take preview."""

    request_id: int
    database_path: Path
    ffmpeg_path: str
    ffprobe_path: str
    source_video_path: Path
    project_id: int
    clip_id: str
    take_id: str
    plan: CompositionPlan
    assets: tuple[CompositionAssetPayload, ...] = ()
    include_ink: bool = True
    play_call_situation: bytes | None = None
    play_call_concept: bytes | None = None
    play_call_result: bytes | None = None


@dataclass(frozen=True, slots=True)
class SelectedTakePreviewResult:
    request_id: int
    take_id: str
    audio_frame: int
    source_position_ms: int
    marks_sha256: str
    image: QImage


class SelectedTakePreviewThread(QThread):
    """Decode and compose one real selected-take frame away from the GUI."""

    preview_ready = Signal(object)  # SelectedTakePreviewResult
    preview_failed = Signal(int, str)  # request id, actionable text
    preview_cancelled = Signal(int)

    def __init__(
            self, request: SelectedTakePreviewRequest, parent=None) -> None:
        super().__init__(parent)
        self.request = request
        self._cancel_event = threading.Event()
        self._loader: RandomAccessFFmpegFrameLoader | None = None
        self._loader_lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._connection_lock = threading.RLock()

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._loader_lock:
            loader = self._loader
        if loader is not None:
            loader.cancel()
        with self._connection_lock:
            connection = self._connection
        if connection is not None:
            try:
                connection.interrupt()
            except sqlite3.Error:
                pass

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def run(self) -> None:
        request = self.request
        loader: RandomAccessFFmpegFrameLoader | None = None
        try:
            self._raise_if_cancelled()
            connection = open_project_db(request.database_path)
            with self._connection_lock:
                self._connection = connection
            try:
                # Deliberately omit audio_wav. Preview needs the recorded event
                # clock and persisted 100 Hz envelope, never the audio BLOB.
                row = connection.execute(
                    """SELECT project_id, clip_id, frame_count, waveform_json,
                              presentation_track_json
                       FROM voiceover_takes WHERE id=?""",
                    (request.take_id,),
                ).fetchone()
            finally:
                with self._connection_lock:
                    self._connection = None
                connection.close()
            self._raise_if_cancelled()
            if row is None:
                raise ValueError(
                    "The Voiceover take selected for preview no longer exists.")
            if int(row["project_id"]) != request.project_id \
                    or str(row["clip_id"]) != request.clip_id:
                raise ValueError(
                    "The selected Voiceover take belongs to another clip or project.")
            frame_count = int(row["frame_count"])
            if frame_count <= 0:
                raise ValueError("The selected Voiceover take has no audio frames.")
            raw_track = str(row["presentation_track_json"] or "")
            if not raw_track:
                raise ValueError(
                    "The selected Voiceover take has no presentation event track.")
            try:
                decoded_waveform = json.loads(str(row["waveform_json"] or "[]"))
                waveform = tuple(float(value) for value in decoded_waveform)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "The selected Voiceover take has an invalid stored waveform.") \
                    from exc

            replay = PresentationReplay.from_json_bytes(
                raw_track.encode("utf-8"),
                voiceover_frame_count=frame_count,
                fps=30,
            )
            state = replay.frame(0)
            self._raise_if_cancelled()
            loader = RandomAccessFFmpegFrameLoader(
                request.ffmpeg_path,
                request.source_video_path,
                ffprobe_path=request.ffprobe_path or None,
                cancel_event=self._cancel_event,
                cache_entries=1,
            )
            with self._loader_lock:
                self._loader = loader
            source_frame = loader.load(state.source_position_ms)
            self._raise_if_cancelled()

            payloads = CompositionFramePayloads(
                source_video=source_frame,
                assets=request.assets,
                ink_event_track=state.marks_json if request.include_ink else None,
                voiceover_waveform=waveform,
                voiceover_waveform_rate_hz=100,
                play_call_situation=request.play_call_situation,
                play_call_concept=request.play_call_concept,
                play_call_result=request.play_call_result,
                timeline_position=state.audio_frame,
                timeline_duration=frame_count,
            )
            image = CompositionFrameCompositor().render(
                request.plan, payloads).copy()
            self._raise_if_cancelled()
            self.preview_ready.emit(SelectedTakePreviewResult(
                request_id=request.request_id,
                take_id=request.take_id,
                audio_frame=state.audio_frame,
                source_position_ms=state.source_position_ms,
                marks_sha256=hashlib.sha256(state.marks_json).hexdigest(),
                image=image,
            ))
        except ExportCancelledError:
            self.preview_cancelled.emit(request.request_id)
        except sqlite3.OperationalError as exc:
            if self._cancel_event.is_set():
                self.preview_cancelled.emit(request.request_id)
            else:
                self.preview_failed.emit(request.request_id, str(exc))
        except Exception as exc:
            if self._cancel_event.is_set():
                self.preview_cancelled.emit(request.request_id)
            else:
                self.preview_failed.emit(request.request_id, str(exc))
        finally:
            with self._loader_lock:
                self._loader = None
            if loader is not None:
                loader.clear()

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise ExportCancelledError("Selected-take preview was cancelled.")


@dataclass(frozen=True, slots=True)
class ExportStagingRequest:
    database_path: Path
    job: ExportJob
    package: ExportPackageSnapshot
    composition_plan: CompositionPlan
    source_video_path: Path
    clips: tuple[Clip, ...]
    hardware_encoder: str = ""
    reel_settings: ReelSettings | None = None


class ExportStagingThread(QThread):
    """Hash, materialize the chosen take, and enqueue atomically off the GUI."""

    staged = Signal(object)  # PersistedExportJob
    staging_failed = Signal(str)
    staging_cancelled = Signal()

    def __init__(self, request: ExportStagingRequest, parent=None, *,
                 remaining_requests: tuple[ExportStagingRequest, ...] = ()) -> None:
        super().__init__(parent)
        self.request = request
        self.requests = (request, *remaining_requests)
        if any(r.database_path != request.database_path
               or r.source_video_path != request.source_video_path
               for r in self.requests):
            raise ValueError("An export batch must share one project and source.")
        self._cancel_event = threading.Event()
        self._connection: sqlite3.Connection | None = None
        self._connection_lock = threading.RLock()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._connection_lock:
            connection = self._connection
        if connection is not None:
            try:
                connection.interrupt()
            except sqlite3.Error:
                pass

    def run(self) -> None:
        try:
            source_identity = ExportJobQueueService.capture_source_identity(
                self.request.source_video_path,
                cancel_event=self._cancel_event,
            )
            if self._cancel_event.is_set():
                raise ExportCancelledError("Export staging was cancelled.")
            connection = open_project_db(self.request.database_path)
            with self._connection_lock:
                self._connection = connection
            try:
                service = ExportJobQueueService(connection)
                persisted_batch = []
                try:
                    for request in self.requests:
                        if self._cancel_event.is_set():
                            raise ExportCancelledError("Export staging was cancelled.")
                        persisted_batch.append(service.enqueue(
                            job=replace(request.job, status=JobStatus.WAITING),
                            package=request.package,
                            composition_plan=request.composition_plan,
                            source_video_path=request.source_video_path,
                            clips=request.clips,
                            source_identity=source_identity,
                            cancel_event=self._cancel_event,
                            hardware_encoder=request.hardware_encoder,
                            reel_settings=request.reel_settings,
                        ))
                    if self._cancel_event.is_set():
                        raise ExportCancelledError("Export staging was cancelled.")
                except Exception as exc:
                    cleanup_errors = []
                    for persisted in persisted_batch:
                        try:
                            if not service.cancel_waiting(persisted.job.id):
                                cleanup_errors.append(persisted.job.id)
                        except Exception as cleanup:
                            cleanup_errors.append(f"{persisted.job.id}: {cleanup}")
                    if cleanup_errors:
                        self.staging_failed.emit(
                            f"{exc}; could not cancel queued jobs: "
                            + "; ".join(cleanup_errors))
                        return
                    raise
            finally:
                with self._connection_lock:
                    self._connection = None
                connection.close()
            self.staged.emit(persisted_batch[0] if len(self.requests) == 1
                             else tuple(persisted_batch))
        except ExportCancelledError:
            self.staging_cancelled.emit()
        except sqlite3.OperationalError as exc:
            if self._cancel_event.is_set():
                self.staging_cancelled.emit()
            else:
                self.staging_failed.emit(str(exc))
        except Exception as exc:
            if self._cancel_event.is_set():
                self.staging_cancelled.emit()
            else:
                self.staging_failed.emit(str(exc))


class ExportQueueRestoreThread(QThread):
    """Recover and list durable metadata without selecting staged BLOBs."""

    summaries_ready = Signal(object)
    restore_failed = Signal(int, str)

    def __init__(
            self, database_path: Path, project_id: int, generation: int,
            parent=None) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.project_id = project_id
        self.generation = generation
        self._connection: sqlite3.Connection | None = None
        self._connection_lock = threading.RLock()

    def cancel(self) -> None:
        self.requestInterruption()
        with self._connection_lock:
            connection = self._connection
        if connection is not None:
            try:
                connection.interrupt()
            except sqlite3.Error:
                pass

    def run(self) -> None:
        try:
            # Recovery is a separate short-lived worker-owned transaction and
            # must precede both summary reads and any new ExportWorker start.
            ExportJobQueueService.recover_interrupted_in_database(
                self.database_path)
            if self.isInterruptionRequested():
                return
            connection = open_project_db(self.database_path)
            with self._connection_lock:
                self._connection = connection
            try:
                summaries = tuple(
                    ExportJobQueueService(connection).list_summaries(
                        self.project_id))
            finally:
                with self._connection_lock:
                    self._connection = None
                connection.close()
            if not self.isInterruptionRequested():
                self.summaries_ready.emit((self.generation, summaries))
        except sqlite3.OperationalError as exc:
            if not self.isInterruptionRequested():
                self.restore_failed.emit(self.generation, str(exc))
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.restore_failed.emit(self.generation, str(exc))


class ExportRetryThread(QThread):
    """Atomically claim and materialize one failed snapshot away from Qt."""

    retry_ready = Signal(object)  # ExportJob
    retry_failed = Signal(str)

    def __init__(self, database_path: Path, job_id: str, parent=None) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.job_id = job_id
        self._cancel_event = threading.Event()
        self._connection: sqlite3.Connection | None = None
        self._connection_lock = threading.RLock()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def cancel(self) -> None:
        self._cancel_event.set()
        self.requestInterruption()
        with self._connection_lock:
            connection = self._connection
        if connection is not None:
            try:
                connection.interrupt()
            except sqlite3.Error:
                pass

    def run(self) -> None:
        try:
            if self.cancel_requested:
                return
            connection = open_project_db(self.database_path)
            with self._connection_lock:
                self._connection = connection
            try:
                service = ExportJobQueueService(connection)
                job = service.retry(self.job_id)
                if self.cancel_requested:
                    try:
                        if not service.cancel_waiting(job.id):
                            raise RuntimeError("The claimed export is no longer waiting.")
                        job.status = JobStatus.CANCELLED
                    except Exception as exc:
                        self.retry_failed.emit(f"Retry cancelled, but queued cleanup failed: {exc}")
                        return
            finally:
                with self._connection_lock:
                    self._connection = None
                connection.close()
            self.retry_ready.emit(job)
        except sqlite3.OperationalError as exc:
            if not self.cancel_requested:
                self.retry_failed.emit(str(exc))
        except Exception as exc:
            if not self.cancel_requested:
                user_text = getattr(exc, "user_text", None)
                self.retry_failed.emit(
                    user_text() if callable(user_text) else str(exc))


class ExportQueuedCancelThread(QThread):
    """Persist one waiting cancellation without opening SQLite on the GUI."""

    cancel_finished = Signal(str, bool)
    cancel_failed = Signal(str, str)

    def __init__(
            self, database_path: Path, job_id: str, *,
            active_cancel: Callable[[str], bool] | None = None,
            parent=None) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.job_id = job_id
        self.active_cancel = active_cancel

    def run(self) -> None:
        try:
            if self.active_cancel is not None:
                cancelled = bool(self.active_cancel(self.job_id))
            else:
                connection = open_project_db(self.database_path)
                try:
                    cancelled = ExportJobQueueService(
                        connection).cancel_waiting(self.job_id)
                finally:
                    connection.close()
            self.cancel_finished.emit(self.job_id, cancelled)
        except Exception as exc:
            self.cancel_failed.emit(self.job_id, str(exc))

