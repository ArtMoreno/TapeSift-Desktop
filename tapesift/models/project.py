"""Project model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from tapesift.core.config import DEFAULT_NAMING_TEMPLATE
from tapesift.models.video_metadata import VideoMetadata


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_game_year(value) -> str:
    year = str(value or "").strip()
    if year and (not year.isascii() or not year.isdigit() or len(year) != 4 or not 1800 <= int(year) <= 2199):
        raise ValueError("Enter a four-digit game year from 1800 to 2199, or leave it unset.")
    return year


@dataclass
class Project:
    name: str
    id: int = 0
    source_video_path: str = ""
    source_duration_ms: int = 0
    source_metadata: VideoMetadata = field(default_factory=VideoMetadata)
    output_folder: str = ""
    #: The team faced in this film. One game = one opponent, so it lives on
    #: the project and every clip inherits it for search and export.
    opponent: str = ""
    #: Explicit game identity; possession and scores belong to individual clips.
    game_team_ids: list[str] = field(default_factory=list)
    naming_template: str = DEFAULT_NAMING_TEMPLATE
    default_preset: str = "source_quality"
    accurate_cut: bool = True
    pre_roll_ms: int = 5000
    post_roll_ms: int = 8000
    #: Film timestamps where Q2, Q3, Q4, OT, 2OT, and later periods begin.
    #: Q1 always begins at zero, so it is not stored.
    quarter_markers_ms: list[int] = field(default_factory=list)
    #: Project-specific display preferences used when the timeline is colored
    #: by Primary Tag. Tags on clips remain untouched.
    tag_styles: dict[str, dict[str, object]] = field(default_factory=dict)
    output_organization: str = "structured"
    db_path: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    #: Analyst-selected defaults for future unlogged clips in this video.
    logging_defaults: dict[str, object] = field(default_factory=dict)
    game_year: str = ""

    def touch(self) -> None:
        self.updated_at = _now()

    @property
    def has_source(self) -> bool:
        return bool(self.source_video_path)
