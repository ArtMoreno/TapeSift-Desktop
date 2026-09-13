"""Clip model - the core unit of work in TapeSift."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum



class ExportStatus(str, Enum):
    NOT_EXPORTED = "not_exported"
    WAITING = "waiting"
    PREPARING = "preparing"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Clip:
    start_ms: int
    end_ms: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    project_id: int = 0
    clip_number: int = 0
    order_index: int = 0
    central_timestamp_ms: int | None = None
    clip_title: str = ""
    output_filename_base: str = ""
    label: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    details: dict[str, str] = field(default_factory=dict)
    # Private machine-generated analysis such as a predicted snap bookmark.
    # This stays separate from analyst-entered details and from immutable
    # automatic-detection provenance.
    analysis: dict = field(default_factory=dict)
    # Private machine-readable provenance for clips created by automatic
    # detection. This is separate from user metadata so it never appears in
    # search, tags, notes, or exported clip names.
    detection_lineage: dict = field(default_factory=dict)
    # Marks drawn on the play, in film coordinates - see
    # models/telestration.py. Analyst-authored, so deliberately not in
    # `analysis` (machine-generated) or `details` (searchable metadata).
    overlays: list = field(default_factory=list)
    # Analyst-selected evidence, independent of thumbnails and machine analysis.
    source_photo: dict = field(default_factory=dict)
    source_photo_png: bytes = field(default=b"", repr=False)
    export_preset: str = ""  # empty = use project default
    include_in_reel: bool = True
    enabled: bool = True
    export_status: ExportStatus = ExportStatus.NOT_EXPORTED
    exported_path: str = ""
    thumbnail_path: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    # Last automatic title; a different title belongs to the analyst.
    generated_title: str | None = None

    @property
    def uses_auto_name(self) -> bool:
        return not self.output_filename_base.strip() and (
            not self.clip_title.strip()
            or self.generated_title == self.clip_title)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def touch(self) -> None:
        self.updated_at = _now()

    def tags_json(self) -> str:
        return json.dumps(self.tags)

    def details_json(self) -> str:
        return json.dumps(self.details)

    def overlays_json(self) -> str:
        return json.dumps(self.overlays)

    def analysis_json(self) -> str:
        return json.dumps(self.analysis)

    def source_photo_json(self) -> str:
        return json.dumps(self.source_photo)

    def detection_lineage_json(self) -> str:
        return json.dumps(self.detection_lineage)

    def copy_as_new(self) -> "Clip":
        """Duplicate this clip with a fresh identity and reset export state."""
        dup = Clip(
            start_ms=self.start_ms,
            end_ms=self.end_ms,
            project_id=self.project_id,
            central_timestamp_ms=self.central_timestamp_ms,
            clip_title=self.clip_title,
            generated_title=self.generated_title,
            output_filename_base=self.output_filename_base,
            label=self.label,
            tags=list(self.tags),
            notes=self.notes,
            details=dict(self.details),
            analysis=json.loads(json.dumps(self.analysis)),
            detection_lineage=json.loads(json.dumps(self.detection_lineage)),
            export_preset=self.export_preset,
            include_in_reel=self.include_in_reel,
            enabled=self.enabled,
        )
        if self.source_photo:
            dup.source_photo = json.loads(self.source_photo_json())
            dup.source_photo["clip_id"] = dup.id
            dup.source_photo_png = self.source_photo_png
        return dup
