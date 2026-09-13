"""Queue-time capture and retry loading for immutable Export Packages."""

from __future__ import annotations

import sqlite3
import threading
from copy import deepcopy
from pathlib import Path

from tapesift.database.connection import open_project_db
from tapesift.database.repositories import (
    ExportJobRepository,
    PersistedExportJob,
    PersistedExportJobSummary,
)
from tapesift.database.voiceover_repository import VoiceoverRepository
from tapesift.models.clip import Clip
from tapesift.models.composition_plan import CompositionPlan
from tapesift.models.export_job import ExportJob
from tapesift.models.export_job_snapshot import (
    ExportJobSnapshot,
    ExportJobSnapshotValidationError,
    SourceMediaIdentity,
    frozen_voiceover_take_id,
)
from tapesift.models.export_package import ExportPackageSnapshot
from tapesift.models.export_settings import ReelSettings
from tapesift.models.voiceover import VoiceoverTake


class ExportJobQueueService:
    """Capture mutable authoring state before handing work to a worker."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.repository = ExportJobRepository(conn)

    @staticmethod
    def freeze_clips_for_staging(
            clips: list[Clip] | tuple[Clip, ...]) -> tuple[Clip, ...]:
        """Copy click-time clip ranges and accepted ink before async staging."""

        if not clips or any(not isinstance(clip, Clip) for clip in clips):
            raise ExportJobSnapshotValidationError(
                "Export staging requires one or more TapeSift clips."
            )
        return tuple(deepcopy(clip) for clip in clips)

    def enqueue(
            self,
            *,
            job: ExportJob,
            package: ExportPackageSnapshot,
            composition_plan: CompositionPlan,
            source_video_path: str | Path,
            clips: list[Clip] | tuple[Clip, ...],
            source_identity: SourceMediaIdentity | None = None,
            cancel_event: threading.Event | None = None,
            hardware_encoder: str = "",
            reel_settings: ReelSettings | None = None,
    ) -> PersistedExportJob:
        """Lock mutable inputs and atomically save the durable queue rows.

        A caller enqueueing several clips from one source should capture one
        :class:`SourceMediaIdentity` and pass it to every call. This avoids
        reading a multi-gigabyte source once per clip.
        """

        selected_voiceover = None
        if package.include_voiceover:
            if len(clips) != 1:
                raise ExportJobSnapshotValidationError(
                    "Voiceover Export Package requires exactly one clip."
                )
            frozen_take_id = frozen_voiceover_take_id(package)
            selected_voiceover = VoiceoverRepository(
                self.conn).get(frozen_take_id, include_audio=True)
            if selected_voiceover is None:
                raise ExportJobSnapshotValidationError(
                    "The Voiceover take selected when Export was clicked no "
                    "longer exists."
                )
            if not isinstance(selected_voiceover, VoiceoverTake) \
                    or selected_voiceover.id != frozen_take_id \
                    or selected_voiceover.project_id != job.project_id \
                    or selected_voiceover.clip_id != clips[0].id:
                raise ExportJobSnapshotValidationError(
                    "The frozen Voiceover take belongs to another clip or project."
                )

        ink_event_track = None
        if package.include_ink and not package.include_voiceover:
            if len(clips) != 1:
                raise ExportJobSnapshotValidationError(
                    "Ink Export Package requires exactly one clip."
                )
            # Clip overlays are accepted film-coordinate marks. Copying them
            # here prevents later edits from changing a failed job's retry.
            ink_event_track = clips[0].overlays

        snapshot = ExportJobSnapshot.capture(
            package=package,
            composition_plan=composition_plan,
            source_video_path=str(source_video_path),
            clips=clips,
            source_identity=source_identity,
            selected_voiceover=selected_voiceover,
            ink_event_track=ink_event_track,
            cancel_event=cancel_event,
            hardware_encoder=hardware_encoder,
            reel_settings=reel_settings,
        )
        return self.repository.save_snapshot(job, snapshot)

    @staticmethod
    def capture_source_identity(
            source_video_path: str | Path,
            *,
            cancel_event: threading.Event | None = None,
    ) -> SourceMediaIdentity:
        """Hash a source once for reuse across an off-thread enqueue batch."""

        return SourceMediaIdentity.capture(
            source_video_path, cancel_event=cancel_event)

    @classmethod
    def enqueue_in_database(
            cls,
            database_path: str | Path,
            *,
            job: ExportJob,
            package: ExportPackageSnapshot,
            composition_plan: CompositionPlan,
            source_video_path: str | Path,
            clips: list[Clip] | tuple[Clip, ...],
            source_identity: SourceMediaIdentity | None = None,
            cancel_event: threading.Event | None = None,
            hardware_encoder: str = "",
            reel_settings: ReelSettings | None = None,
    ) -> PersistedExportJob:
        """Open a caller-thread connection and stage without using the UI DB.

        MainWindow must invoke this from a worker thread: selected Voiceover
        audio can be hundreds of megabytes and source hashing is full-file I/O.
        """

        conn = open_project_db(Path(database_path))
        try:
            return cls(conn).enqueue(
                job=job,
                package=package,
                composition_plan=composition_plan,
                source_video_path=source_video_path,
                clips=clips,
                source_identity=source_identity,
                cancel_event=cancel_event,
                hardware_encoder=hardware_encoder,
                reel_settings=reel_settings,
            )
        finally:
            conn.close()

    def retry(self, job_id: str) -> ExportJob:
        """Load a failed job without consulting any current UI control."""

        return self.repository.load_for_retry(job_id)

    def list_summaries(
            self, project_id: int | None = None
    ) -> list[PersistedExportJobSummary]:
        """Restore queue rows without loading Voiceover or identity BLOBs."""

        return self.repository.list_summaries(project_id)

    def recover_interrupted(self) -> int:
        """Reconcile jobs abandoned when the prior TapeSift process closed."""

        return self.repository.recover_interrupted()

    def cancel_waiting(self, job_id: str) -> bool:
        """Persist cancellation even when the worker never reaches the row."""

        return self.repository.cancel_waiting(job_id)

    @classmethod
    def retry_in_database(
            cls, database_path: str | Path, job_id: str) -> ExportJob:
        """Claim and materialize a retry on a caller-owned worker thread.

        A staged Voiceover WAV may be hundreds of megabytes, so MainWindow
        must not call the synchronous ``retry`` method on its GUI thread.
        """

        conn = open_project_db(Path(database_path))
        try:
            return cls(conn).retry(job_id)
        finally:
            conn.close()

    @classmethod
    def cancel_waiting_in_database(
            cls, database_path: str | Path, job_id: str) -> bool:
        """Cancel one durable waiting row on a short-lived connection."""

        conn = open_project_db(Path(database_path))
        try:
            return cls(conn).cancel_waiting(job_id)
        finally:
            conn.close()

    @classmethod
    def recover_interrupted_in_database(
            cls, database_path: str | Path) -> int:
        """Run startup reconciliation on one short-lived connection.

        The project-open bridge must invoke this before restoring summaries or
        starting a new export worker.
        """

        conn = open_project_db(Path(database_path))
        try:
            return cls(conn).recover_interrupted()
        finally:
            conn.close()
