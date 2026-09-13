"""Repositories mapping models to the project SQLite database."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from tapesift.core.exceptions import DatabaseError
from tapesift.models.clip import Clip, ExportStatus
from tapesift.models.export_job import ExportJob, JobStatus, JobType
from tapesift.models.export_job_snapshot import (
    ExportJobSnapshot,
    ExportJobSnapshotValidationError,
    StagedIdentityAsset,
    StagedVoiceoverPayload,
)
from tapesift.models.project import Project, normalize_game_year
from tapesift.models.video_metadata import VideoMetadata
from tapesift.services.detail_service import refresh_generated_title


_UNSET = object()
_INTERRUPTED_EXPORT_ERROR = "TapeSift closed before this export finished."


class ProjectRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, project: Project, *, commit: bool = True) -> Project:
        project.game_year = normalize_game_year(project.game_year)
        project.touch()
        if project.id:
            self.conn.execute(
                """UPDATE projects SET name=?, source_video_path=?, source_duration_ms=?,
                   source_metadata_json=?, output_folder=?, naming_template=?,
                   default_preset=?, accurate_cut=?, pre_roll_ms=?, post_roll_ms=?,
                   output_organization=?, opponent=?, quarter_markers_json=?,
                   tag_styles_json=?, game_team_ids_json=?, logging_defaults_json=?, game_year=?,
                   updated_at=? WHERE id=?""",
                (
                    project.name, project.source_video_path, project.source_duration_ms,
                    project.source_metadata.to_json(), project.output_folder,
                    project.naming_template, project.default_preset,
                    int(project.accurate_cut), project.pre_roll_ms, project.post_roll_ms,
                    project.output_organization, project.opponent,
                    json.dumps(project.quarter_markers_ms),
                    json.dumps(project.tag_styles),
                    json.dumps(project.game_team_ids),
                    json.dumps(project.logging_defaults),
                    project.game_year,
                    project.updated_at, project.id,
                ),
            )
        else:
            cur = self.conn.execute(
                """INSERT INTO projects (name, source_video_path, source_duration_ms,
                   source_metadata_json, output_folder, naming_template, default_preset,
                   accurate_cut, pre_roll_ms, post_roll_ms, output_organization,
                   opponent, quarter_markers_json, tag_styles_json, game_team_ids_json, logging_defaults_json, game_year, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    project.name, project.source_video_path, project.source_duration_ms,
                    project.source_metadata.to_json(), project.output_folder,
                    project.naming_template, project.default_preset,
                    int(project.accurate_cut), project.pre_roll_ms, project.post_roll_ms,
                    project.output_organization, project.opponent,
                    json.dumps(project.quarter_markers_ms),
                    json.dumps(project.tag_styles),
                    json.dumps(project.game_team_ids),
                    json.dumps(project.logging_defaults),
                    project.game_year,
                    project.created_at, project.updated_at,
                ),
            )
            project.id = cur.lastrowid or 0
        if commit:
            self.conn.commit()
        return project

    def load(self) -> Project | None:
        row = self.conn.execute("SELECT * FROM projects ORDER BY id LIMIT 1").fetchone()
        return self._to_project(row) if row else None

    @staticmethod
    def _to_project(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            source_video_path=row["source_video_path"],
            source_duration_ms=row["source_duration_ms"],
            source_metadata=VideoMetadata.from_json(row["source_metadata_json"]),
            output_folder=row["output_folder"],
            naming_template=row["naming_template"],
            default_preset=row["default_preset"],
            accurate_cut=bool(row["accurate_cut"]),
            pre_roll_ms=row["pre_roll_ms"],
            post_roll_ms=row["post_roll_ms"],
            output_organization=row["output_organization"],
            opponent=row["opponent"] if "opponent" in row.keys() else "",
            game_year=row["game_year"] if "game_year" in row.keys() else "",
            quarter_markers_ms=json.loads(
                row["quarter_markers_json"] or "[]")
            if "quarter_markers_json" in row.keys() else [],
            tag_styles=json.loads(row["tag_styles_json"] or "{}")
            if "tag_styles_json" in row.keys() else {},
            game_team_ids=json.loads(row["game_team_ids_json"] or "[]")
            if "game_team_ids_json" in row.keys() else [],
            logging_defaults=json.loads(row["logging_defaults_json"] or "{}")
            if "logging_defaults_json" in row.keys() else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ClipRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, clip: Clip, *, commit: bool = True) -> Clip:
        refresh_generated_title(clip)
        clip.touch()
        self.conn.execute(
            """INSERT INTO clips (id, project_id, clip_number, order_index, start_ms, end_ms,
               central_timestamp_ms, clip_title, output_filename_base, label, notes,
               tags_json, details_json, analysis_json, overlays_json,
               detection_lineage_json, export_preset,
               include_in_reel, enabled, thumbnail_path, exported_path,
               export_status, created_at, updated_at, source_photo_json, source_photo_png,
               generated_title)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 clip_number=excluded.clip_number, order_index=excluded.order_index,
                 start_ms=excluded.start_ms, end_ms=excluded.end_ms,
                 central_timestamp_ms=excluded.central_timestamp_ms,
                 clip_title=excluded.clip_title,
                 generated_title=excluded.generated_title,
                 output_filename_base=excluded.output_filename_base,
                 label=excluded.label, notes=excluded.notes, tags_json=excluded.tags_json,
                 details_json=excluded.details_json,
                 analysis_json=excluded.analysis_json,
                 overlays_json=excluded.overlays_json,
                 detection_lineage_json=excluded.detection_lineage_json,
                 export_preset=excluded.export_preset,
                 include_in_reel=excluded.include_in_reel, enabled=excluded.enabled,
                 thumbnail_path=excluded.thumbnail_path,
                 source_photo_json=excluded.source_photo_json,
                 source_photo_png=excluded.source_photo_png,
                 exported_path=excluded.exported_path,
                 export_status=excluded.export_status, updated_at=excluded.updated_at""",
            (
                clip.id, clip.project_id, clip.clip_number, clip.order_index,
                clip.start_ms, clip.end_ms, clip.central_timestamp_ms,
                clip.clip_title, clip.output_filename_base, clip.label, clip.notes,
                clip.tags_json(), clip.details_json(),
                clip.analysis_json(), clip.overlays_json(),
                clip.detection_lineage_json(),
                clip.export_preset, int(clip.include_in_reel),
                int(clip.enabled), clip.thumbnail_path, clip.exported_path,
                clip.export_status.value, clip.created_at, clip.updated_at,
                clip.source_photo_json(), clip.source_photo_png,
                clip.generated_title,
            ),
        )
        if commit:
            self.conn.commit()
        return clip

    def save_many(self, clips: list[Clip], *, commit: bool = True) -> None:
        for clip in clips:
            self.save(clip, commit=False)
        if commit:
            self.conn.commit()

    def delete(self, clip_id: str) -> None:
        self.conn.execute("DELETE FROM clips WHERE id=?", (clip_id,))
        self.conn.commit()

    def list_for_project(self, project_id: int) -> list[Clip]:
        rows = self.conn.execute(
            "SELECT * FROM clips WHERE project_id=? ORDER BY order_index", (project_id,)
        ).fetchall()
        return [self._to_clip(r) for r in rows]

    @staticmethod
    def _load_overlays(row: sqlite3.Row) -> list:
        """Load the optional annotation list without risking project open.

        Telestration is an annotation on a play, not the play's identity or
        searchable metadata. A damaged legacy/default value is therefore
        isolated here. Tags, details, analysis and lineage intentionally
        keep their existing strict JSON handling below.
        """
        if "overlays_json" not in row.keys():
            return []
        raw = row["overlays_json"]
        if not raw:
            return []
        try:
            loaded = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            return []
        return loaded if isinstance(loaded, list) else []

    @staticmethod
    def _load_source_photo(row: sqlite3.Row) -> dict:
        if "source_photo_json" not in row.keys():
            return {}
        try:
            value = json.loads(row["source_photo_json"] or "{}")
        except (ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _to_clip(row: sqlite3.Row) -> Clip:
        return Clip(
            id=row["id"],
            project_id=row["project_id"],
            clip_number=row["clip_number"],
            order_index=row["order_index"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            central_timestamp_ms=row["central_timestamp_ms"],
            clip_title=row["clip_title"],
            generated_title=row["generated_title"] if "generated_title" in row.keys() else None,
            output_filename_base=row["output_filename_base"],
            label=row["label"],
            notes=row["notes"],
            tags=json.loads(row["tags_json"] or "[]"),
            details=json.loads(row["details_json"] or "{}"),
            analysis=json.loads(row["analysis_json"] or "{}")
            if "analysis_json" in row.keys() else {},
            # Column has existed since an early migration but was never
            # read or written by anything - telestration is what finally
            # uses it, so old projects open with an empty list.
            overlays=ClipRepository._load_overlays(row),
            source_photo=ClipRepository._load_source_photo(row),
            source_photo_png=bytes(row["source_photo_png"] or b"")
            if "source_photo_png" in row.keys()
            and isinstance(row["source_photo_png"], (bytes, type(None))) else b"",
            detection_lineage=json.loads(
                row["detection_lineage_json"] or "{}")
            if "detection_lineage_json" in row.keys() else {},
            export_preset=row["export_preset"],
            include_in_reel=bool(row["include_in_reel"]),
            enabled=bool(row["enabled"]),
            thumbnail_path=row["thumbnail_path"],
            exported_path=row["exported_path"],
            export_status=ExportStatus(row["export_status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ExportJobRepositoryError(DatabaseError):
    """A durable queue row is corrupt or cannot be retried safely."""


@dataclass(frozen=True, slots=True)
class PersistedExportJob:
    job: ExportJob
    created_at: str
    started_at: str | None
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class PersistedExportJobSummary:
    """Queue-row metadata that never materializes staged assets or audio."""

    id: str
    project_id: int
    clip_id: str
    clip_ids: tuple[str, ...]
    job_type: JobType
    status: JobStatus
    display_name: str
    output_path: str
    preset_name: str
    ffmpeg_command: str
    error_message: str
    created_at: str
    started_at: str | None
    completed_at: str | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_settings_json(job: ExportJob) -> str:
    return json.dumps(
        {
            "clip_ids": list(job.clip_ids),
            "display_name": job.display_name,
            "preset_name": job.preset_name,
            "schema": "tapesift.export-job-runtime",
            "version": 1,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _job_integrity_sha256(
        job: ExportJob, snapshot: ExportJobSnapshot, settings_json: str,
        created_at: str) -> str:
    payload = json.dumps(
        {
            "clip_id": job.clip_id,
            "created_at": created_at,
            "job_id": job.id,
            "job_type": job.job_type.value,
            "output_path": job.output_path,
            "project_id": job.project_id,
            "runtime": json.loads(settings_json),
            "snapshot_sha256": snapshot.integrity_sha256,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ExportJobRepository:
    """Durable queue and history, with write-once render inputs per job id."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(self, job_id: str, project_id: int, clip_id: str, job_type: str,
               status: str, output_path: str, ffmpeg_command: str,
               error_message: str, created_at: str,
               started_at: str | None, completed_at: str | None) -> None:
        self.conn.execute(
            """INSERT INTO export_jobs (id, project_id, clip_id, job_type, status,
               output_path, ffmpeg_command, error_message, created_at, started_at, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                 error_message=excluded.error_message,
                 started_at=excluded.started_at, completed_at=excluded.completed_at""",
            (job_id, project_id, clip_id, job_type, status, output_path,
             ffmpeg_command, error_message, created_at, started_at, completed_at),
        )
        self.conn.commit()

    @staticmethod
    def _validate_job_binding(
            job: ExportJob, snapshot: ExportJobSnapshot) -> None:
        if not isinstance(job, ExportJob):
            raise ValueError("Only ExportJob values can enter the export queue.")
        if not isinstance(snapshot, ExportJobSnapshot):
            raise ValueError("Export jobs require an immutable queue snapshot.")
        if isinstance(job.project_id, bool) or not isinstance(job.project_id, int) \
                or job.project_id <= 0:
            raise ValueError("A durable export job needs a saved project.")
        if not isinstance(job.id, str) or not job.id.strip() \
                or job.id != job.id.strip():
            raise ValueError("Export job id is invalid.")
        if not isinstance(job.display_name, str) or not job.display_name.strip():
            raise ValueError("Export job display name is required.")
        if not isinstance(job.output_path, str) or not job.output_path.strip() \
                or job.output_path != job.output_path.strip():
            raise ValueError("Export job output path is required and canonical.")
        try:
            job_type = JobType(job.job_type)
            JobStatus(job.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("Export job type or status is invalid.") from exc
        if job.preset_name != snapshot.package.technical_preset:
            raise ValueError(
                "Runtime technical preset differs from the queued package."
            )
        source_ids = tuple(clip.clip_id for clip in snapshot.clips)
        if job_type is JobType.CLIP:
            if len(source_ids) != 1 or job.clip_id != source_ids[0] \
                    or job.clip_ids:
                raise ValueError(
                    "Clip export job differs from its queued source range."
                )
        elif job_type is JobType.REEL:
            if job.clip_id or tuple(job.clip_ids) != source_ids:
                raise ValueError(
                    "Reel export job differs from its queued source ranges."
                )
            if snapshot.package.is_composited:
                raise ValueError(
                    "Composited reel behavior is undefined and cannot be queued."
                )
        else:
            raise ValueError(
                "Thumbnail jobs do not use the Export Package queue contract."
            )
        if snapshot.voiceover is not None:
            if job_type is not JobType.CLIP:
                raise ValueError("Voiceover export requires one clip job.")
            if snapshot.voiceover.project_id != job.project_id \
                    or snapshot.voiceover.clip_id != job.clip_id:
                raise ValueError(
                    "Queued Voiceover take belongs to a different project or clip."
                )

    def save_snapshot(
            self,
            job: ExportJob,
            snapshot: ExportJobSnapshot | None = None,
            *,
            created_at: str | None = None,
            started_at: str | None = None,
            completed_at: str | None = None,
    ) -> PersistedExportJob:
        """Persist a new job atomically; existing job inputs are write-once."""

        snapshot = snapshot or job.snapshot
        if snapshot is None:
            raise ValueError("Export job snapshot is required.")
        self._validate_job_binding(job, snapshot)
        settings_json = _runtime_settings_json(job)
        existing = self.conn.execute(
            """SELECT e.project_id, e.clip_id, e.job_type, e.settings_json,
                      e.output_path, e.created_at, e.started_at, e.completed_at,
                      s.integrity_sha256
               FROM export_jobs e
               LEFT JOIN export_job_snapshots s ON s.job_id=e.id
               WHERE e.id=?""",
            (job.id,),
        ).fetchone()
        if existing is not None and created_at is None:
            created_at = existing["created_at"]
        created_at = created_at or _utc_now()
        if not isinstance(created_at, str) or not created_at.strip():
            raise ValueError("Export job creation time is required.")
        integrity = _job_integrity_sha256(
            job, snapshot, settings_json, created_at)
        if existing is not None:
            existing_hash = existing["integrity_sha256"]
            if not existing_hash:
                raise ValueError(
                    "A legacy export-history row cannot be changed into a "
                    "durable queue job."
                )
            if (
                existing["project_id"] != job.project_id
                or existing["clip_id"] != job.clip_id
                or existing["job_type"] != job.job_type.value
                or existing["settings_json"] != settings_json
                or existing["output_path"] != job.output_path
            ):
                raise ValueError(
                    "Existing export job row differs from its immutable binding."
                )
            if existing_hash != integrity:
                raise ValueError(
                    "Queued export inputs are immutable for an existing job id."
                )
            timestamps: dict[str, object] = {}
            if started_at is not None:
                timestamps["started_at"] = started_at
            if completed_at is not None:
                timestamps["completed_at"] = completed_at
            self.update_status(
                job.id,
                job.status,
                ffmpeg_command=job.ffmpeg_command,
                error_message=job.error_message,
                **timestamps,
            )
            job.snapshot = snapshot
            return PersistedExportJob(
                job,
                created_at,
                started_at if started_at is not None else existing["started_at"],
                completed_at if completed_at is not None
                else existing["completed_at"],
            )

        try:
            self.conn.execute(
                """INSERT INTO export_jobs (
                       id, project_id, clip_id, job_type, status, settings_json,
                       output_path, ffmpeg_command, error_message, created_at,
                       started_at, completed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.id, job.project_id, job.clip_id, job.job_type.value,
                    job.status.value, settings_json, job.output_path,
                    job.ffmpeg_command, job.error_message, created_at,
                    started_at, completed_at,
                ),
            )
            self.conn.execute(
                """INSERT INTO export_job_snapshots (
                       job_id, package_json, composition_plan_json,
                       manifest_json, integrity_sha256)
                   VALUES (?,?,?,?,?)""",
                (
                    job.id, snapshot.package_json,
                    snapshot.composition_plan_json, snapshot.manifest_json,
                    integrity,
                ),
            )
            self.conn.executemany(
                """INSERT INTO export_job_identity_assets (
                       job_id, role, sha256, mime_type, width, height, data)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    (
                        job.id, asset.role.value, asset.sha256,
                        asset.mime_type, asset.width, asset.height,
                        sqlite3.Binary(asset.data),
                    )
                    for asset in snapshot.identity_assets
                ],
            )
            voiceover = snapshot.voiceover
            if voiceover is not None:
                self.conn.execute(
                    """INSERT INTO export_job_voiceover_payloads (
                           job_id, take_id, project_id, clip_id, audio_input_value,
                           presentation_input_value, ink_input_value,
                           source_anchor_ms,
                           sample_rate, channels, frame_count, duration_ms,
                           waveform_json,
                           audio_sha256, presentation_sha256, audio_wav,
                           presentation_track_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job.id, voiceover.take_id, voiceover.project_id,
                        voiceover.clip_id,
                        voiceover.audio_input_value,
                        voiceover.presentation_input_value,
                        voiceover.ink_input_value,
                        voiceover.source_anchor_ms, voiceover.sample_rate,
                        voiceover.channels, voiceover.frame_count,
                        voiceover.duration_ms,
                        json.dumps(
                            list(voiceover.waveform),
                            allow_nan=False,
                            separators=(",", ":"),
                        ),
                        voiceover.audio_sha256,
                        voiceover.presentation_sha256,
                        sqlite3.Binary(voiceover.audio_wav),
                        voiceover.presentation_track_json,
                    ),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        job.snapshot = snapshot
        return PersistedExportJob(job, created_at, started_at, completed_at)

    @staticmethod
    def _runtime_settings(raw: object) -> tuple[str, list[str], str]:
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ExportJobRepositoryError(
                "Export job runtime settings are corrupt.",
                "Remove the damaged queue entry and export it again.",
            ) from exc
        if not isinstance(data, dict) or set(data) != {
            "clip_ids", "display_name", "preset_name", "schema", "version",
        } or data.get("schema") != "tapesift.export-job-runtime" \
                or type(data.get("version")) is not int \
                or data.get("version") != 1 \
                or not isinstance(data.get("display_name"), str) \
                or not data["display_name"].strip() \
                or not isinstance(data.get("preset_name"), str) \
                or not data["preset_name"].strip() \
                or not isinstance(data.get("clip_ids"), list) \
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in data["clip_ids"]
                ):
            raise ExportJobRepositoryError(
                "Export job runtime settings are corrupt.",
                "Remove the damaged queue entry and export it again.",
            )
        return (
            data["display_name"],
            list(data["clip_ids"]),
            data["preset_name"],
        )

    def list_summaries(
            self, project_id: int | None = None
    ) -> list[PersistedExportJobSummary]:
        """List durable queue rows without selecting staged render payloads."""

        if project_id is not None and (
            isinstance(project_id, bool)
            or not isinstance(project_id, int)
            or project_id <= 0
        ):
            raise ValueError("Export queue project id is invalid.")
        where = "AND e.project_id=?" if project_id is not None else ""
        parameters = (project_id,) if project_id is not None else ()
        rows = self.conn.execute(
            f"""SELECT e.id, e.project_id, e.clip_id, e.job_type, e.status,
                       e.settings_json, e.output_path, e.ffmpeg_command,
                       e.error_message, e.created_at, e.started_at,
                       e.completed_at
                FROM export_jobs e
                WHERE EXISTS (
                    SELECT 1 FROM export_job_snapshots s WHERE s.job_id=e.id
                ) {where}
                ORDER BY e.created_at, e.id""",
            parameters,
        ).fetchall()
        summaries: list[PersistedExportJobSummary] = []
        try:
            for row in rows:
                display_name, clip_ids, preset_name = self._runtime_settings(
                    row["settings_json"])
                summaries.append(PersistedExportJobSummary(
                    id=row["id"],
                    project_id=row["project_id"],
                    clip_id=row["clip_id"],
                    clip_ids=tuple(clip_ids),
                    job_type=JobType(row["job_type"]),
                    status=JobStatus(row["status"]),
                    display_name=display_name,
                    output_path=row["output_path"],
                    preset_name=preset_name,
                    ffmpeg_command=row["ffmpeg_command"],
                    error_message=row["error_message"],
                    created_at=row["created_at"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ExportJobRepositoryError(
                "Export queue metadata is corrupt.",
                "Remove the damaged queue entry and export it again.",
            ) from exc
        return summaries

    def recover_interrupted(self) -> int:
        """Fail durable jobs left active by a prior process termination."""

        completed_at = _utc_now()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            cursor = self.conn.execute(
                """UPDATE export_jobs
                   SET status=?, error_message=?, completed_at=?
                   WHERE status IN (?, ?)
                     AND EXISTS (
                         SELECT 1 FROM export_job_snapshots s
                         WHERE s.job_id=export_jobs.id
                     )""",
                (
                    JobStatus.FAILED.value,
                    _INTERRUPTED_EXPORT_ERROR,
                    completed_at,
                    JobStatus.PREPARING.value,
                    JobStatus.EXPORTING.value,
                ),
            )
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        return cursor.rowcount

    def cancel_waiting(self, job_id: str) -> bool:
        """Persist queued cancellation without loading the snapshot payload."""

        cursor = self.conn.execute(
            """UPDATE export_jobs
               SET status=?, ffmpeg_command='', error_message='',
                   completed_at=?
               WHERE id=? AND status=?
                 AND EXISTS (
                     SELECT 1 FROM export_job_snapshots s
                     WHERE s.job_id=export_jobs.id
                 )""",
            (
                JobStatus.CANCELLED.value,
                _utc_now(),
                job_id,
                JobStatus.WAITING.value,
            ),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def load(self, job_id: str) -> PersistedExportJob | None:
        row = self.conn.execute(
            """SELECT e.*, s.package_json, s.composition_plan_json,
                      s.manifest_json, s.integrity_sha256
               FROM export_jobs e
               LEFT JOIN export_job_snapshots s ON s.job_id=e.id
               WHERE e.id=?""",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        if not row["package_json"]:
            raise ExportJobRepositoryError(
                "This legacy export-history entry has no retry snapshot.",
                "Queue a new export with the current Export Package controls.",
            )
        try:
            assets = tuple(
                StagedIdentityAsset(
                    role=asset["role"],
                    sha256=asset["sha256"],
                    mime_type=asset["mime_type"],
                    width=asset["width"],
                    height=asset["height"],
                    data=bytes(asset["data"]),
                )
                for asset in self.conn.execute(
                    """SELECT role, sha256, mime_type, width, height, data
                       FROM export_job_identity_assets
                       WHERE job_id=? ORDER BY role""",
                    (job_id,),
                ).fetchall()
            )
            voice_row = self.conn.execute(
                """SELECT * FROM export_job_voiceover_payloads
                   WHERE job_id=?""",
                (job_id,),
            ).fetchone()
            voiceover = (
                StagedVoiceoverPayload(
                    take_id=voice_row["take_id"],
                    project_id=voice_row["project_id"],
                    clip_id=voice_row["clip_id"],
                    audio_input_value=voice_row["audio_input_value"],
                    presentation_input_value=(
                        voice_row["presentation_input_value"]),
                    ink_input_value=voice_row["ink_input_value"],
                    source_anchor_ms=voice_row["source_anchor_ms"],
                    sample_rate=voice_row["sample_rate"],
                    channels=voice_row["channels"],
                    frame_count=voice_row["frame_count"],
                    duration_ms=voice_row["duration_ms"],
                    audio_sha256=voice_row["audio_sha256"],
                    presentation_sha256=voice_row["presentation_sha256"],
                    audio_wav=bytes(voice_row["audio_wav"]),
                    presentation_track_json=(
                        voice_row["presentation_track_json"]),
                    waveform=tuple(json.loads(voice_row["waveform_json"])),
                )
                if voice_row is not None else None
            )
            snapshot = ExportJobSnapshot.from_storage(
                package_json=row["package_json"],
                composition_plan_json=row["composition_plan_json"],
                manifest_json=row["manifest_json"],
                identity_assets=assets,
                voiceover=voiceover,
            )
            display_name, clip_ids, preset_name = self._runtime_settings(
                row["settings_json"])
            job = ExportJob(
                id=row["id"],
                project_id=row["project_id"],
                clip_id=row["clip_id"],
                clip_ids=clip_ids,
                job_type=JobType(row["job_type"]),
                display_name=display_name,
                output_path=row["output_path"],
                preset_name=preset_name,
                status=JobStatus(row["status"]),
                error_message=row["error_message"],
                ffmpeg_command=row["ffmpeg_command"],
                snapshot=snapshot,
            )
            self._validate_job_binding(job, snapshot)
            expected_integrity = _job_integrity_sha256(
                job, snapshot, row["settings_json"], row["created_at"])
            if expected_integrity != row["integrity_sha256"]:
                raise ExportJobSnapshotValidationError(
                    "Export job row failed its integrity check."
                )
        except (
            ExportJobSnapshotValidationError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise ExportJobRepositoryError(
                f"Export job {job_id} has corrupt immutable inputs.",
                "Remove the damaged queue entry and export it again.",
            ) from exc
        return PersistedExportJob(
            job=job,
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    def load_for_retry(self, job_id: str) -> ExportJob:
        """Atomically claim a failed job using only its stored snapshot."""

        try:
            # Claim before loading a potentially large staged WAV. A second
            # connection waits here and then observes rowcount zero, so two
            # retry clicks cannot both materialize and run the same job.
            self.conn.execute("BEGIN IMMEDIATE")
            cursor = self.conn.execute(
                """UPDATE export_jobs
                   SET status=?, ffmpeg_command='', error_message='',
                       started_at=NULL, completed_at=NULL
                   WHERE id=? AND status=?""",
                (JobStatus.WAITING.value, job_id, JobStatus.FAILED.value),
            )
            if cursor.rowcount != 1:
                status_row = self.conn.execute(
                    "SELECT status FROM export_jobs WHERE id=?", (job_id,)
                ).fetchone()
                self.conn.rollback()
                if status_row is None:
                    raise ExportJobRepositoryError(
                        "That export job no longer exists.",
                        "Queue the export again.",
                    )
                raise ExportJobRepositoryError(
                    "Only a failed export job can be retried.",
                    "Wait for the active retry or queue a new export.",
                )
            persisted = self.load(job_id)
            if persisted is None:  # Foreign-key consistency guard.
                raise ExportJobRepositoryError(
                    "That export job no longer exists.",
                    "Queue the export again.",
                )
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        job = persisted.job
        job.status = JobStatus.WAITING
        job.progress = 0.0
        job.elapsed_seconds = 0.0
        job.error_message = ""
        job.ffmpeg_command = ""
        return job

    def update_status(
            self,
            job_id: str,
            status: JobStatus | str,
            *,
            ffmpeg_command: str = "",
            error_message: str = "",
            started_at: str | None | object = _UNSET,
            completed_at: str | None | object = _UNSET,
    ) -> None:
        try:
            resolved_status = JobStatus(status)
        except (TypeError, ValueError) as exc:
            raise ValueError("Export job status is invalid.") from exc
        assignments = ["status=?", "ffmpeg_command=?", "error_message=?"]
        values: list[object] = [
            resolved_status.value, ffmpeg_command, error_message,
        ]
        if started_at is not _UNSET:
            if started_at is not None and (
                not isinstance(started_at, str) or not started_at.strip()
            ):
                raise ValueError("Export job start time is invalid.")
            assignments.append("started_at=?")
            values.append(started_at)
        if completed_at is not _UNSET:
            if completed_at is not None and (
                not isinstance(completed_at, str) or not completed_at.strip()
            ):
                raise ValueError("Export job completion time is invalid.")
            assignments.append("completed_at=?")
            values.append(completed_at)
        values.append(job_id)
        cursor = self.conn.execute(
            f"UPDATE export_jobs SET {', '.join(assignments)} WHERE id=?",
            values,
        )
        if cursor.rowcount != 1:
            self.conn.rollback()
            raise ExportJobRepositoryError(
                "That export job no longer exists.",
                "Refresh the queue and try again.",
            )
        self.conn.commit()
