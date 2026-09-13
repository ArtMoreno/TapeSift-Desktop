"""Export job model for the export queue."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tapesift.models.export_job_snapshot import ExportJobSnapshot


class JobType(str, Enum):
    CLIP = "clip"
    REEL = "reel"
    THUMBNAIL = "thumbnail"


class JobStatus(str, Enum):
    WAITING = "Waiting"
    PREPARING = "Preparing"
    EXPORTING = "Exporting"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass
class ExportJob:
    job_type: JobType
    display_name: str
    output_path: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    project_id: int = 0
    clip_id: str = ""
    clip_ids: list[str] = field(default_factory=list)  # for reels
    preset_name: str = ""
    status: JobStatus = JobStatus.WAITING
    progress: float = 0.0  # 0..100
    elapsed_seconds: float = 0.0
    error_message: str = ""
    ffmpeg_command: str = ""
    # Set once when the job enters the durable queue.  The frozen snapshot is
    # what retry and the worker consume; current panel settings are not a
    # fallback for snapshotted jobs.
    snapshot: "ExportJobSnapshot | None" = field(default=None, repr=False)
