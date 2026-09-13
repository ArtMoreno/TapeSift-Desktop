"""Helpers that turn user input (timestamps, ranges, parsed rows) into Clips."""

from __future__ import annotations

from dataclasses import dataclass

from tapesift.models.clip import Clip
from tapesift.services.bulk_paste_parser import ParsedRow
from tapesift.services.csv_import_service import CsvRow
from tapesift.services.filename_service import sanitize_filename_base
from tapesift.services.timestamp_parser import clamp_range


@dataclass
class ClipDefaults:
    pre_roll_ms: int = 5000
    post_roll_ms: int = 8000
    separator_style: str = "hyphen"


# Pre/post-roll presets: (name, pre_ms, post_ms)
ROLL_PRESETS: list[tuple[str, int, int]] = [
    ("Tight Clip (2s / 4s)", 2000, 4000),
    ("Standard Clip (5s / 8s)", 5000, 8000),
    ("Wide Clip (8s / 12s)", 8000, 12000),
]


def _apply_name(clip: Clip, name: str, separator_style: str) -> None:
    """Clip title = exactly what the user typed; filename base = sanitized copy."""
    name = (name or "").strip()
    if name:
        clip.clip_title = name
        clip.output_filename_base = sanitize_filename_base(name, separator_style)


def clip_from_timestamp(timestamp_ms: int, name: str, duration_ms: int,
                        defaults: ClipDefaults, label: str = "",
                        tags: list[str] | None = None, notes: str = "",
                        pre_roll_ms: int | None = None,
                        post_roll_ms: int | None = None) -> tuple[Clip, bool]:
    """Create a clip around a single timestamp. Returns (clip, was_clamped)."""
    pre = defaults.pre_roll_ms if pre_roll_ms is None else pre_roll_ms
    post = defaults.post_roll_ms if post_roll_ms is None else post_roll_ms
    start, end, clamped = clamp_range(timestamp_ms - pre, timestamp_ms + post, duration_ms)
    clip = Clip(start_ms=start, end_ms=end, central_timestamp_ms=timestamp_ms,
                label=label, tags=list(tags or []), notes=notes)
    _apply_name(clip, name, defaults.separator_style)
    return clip, clamped


def clip_from_range(start_ms: int, end_ms: int, name: str, duration_ms: int,
                    defaults: ClipDefaults, label: str = "",
                    tags: list[str] | None = None, notes: str = "") -> tuple[Clip, bool]:
    """Create a clip from an explicit range. Returns (clip, was_clamped)."""
    start, end, clamped = clamp_range(start_ms, end_ms, duration_ms)
    clip = Clip(start_ms=start, end_ms=end, label=label,
                tags=list(tags or []), notes=notes)
    _apply_name(clip, name, defaults.separator_style)
    return clip, clamped


def clip_from_parsed_row(row: ParsedRow, duration_ms: int,
                         defaults: ClipDefaults) -> tuple[Clip, bool]:
    if row.end_ms is not None:
        return clip_from_range(row.start_ms, row.end_ms, row.name, duration_ms, defaults)
    return clip_from_timestamp(row.timestamp_ms or row.start_ms, row.name,
                               duration_ms, defaults)


def clip_from_csv_row(row: CsvRow, duration_ms: int,
                      defaults: ClipDefaults) -> tuple[Clip, bool]:
    if row.start_ms is not None and row.end_ms is not None:
        clip, clamped = clip_from_range(row.start_ms, row.end_ms, row.name,
                                        duration_ms, defaults, label=row.label,
                                        tags=row.tags, notes=row.notes)
    elif row.start_ms is not None:
        clip, clamped = clip_from_timestamp(row.start_ms, row.name, duration_ms,
                                            defaults, label=row.label,
                                            tags=row.tags, notes=row.notes)
    else:
        clip, clamped = clip_from_timestamp(row.timestamp_ms or 0, row.name,
                                            duration_ms, defaults, label=row.label,
                                            tags=row.tags, notes=row.notes)
    return clip, clamped
