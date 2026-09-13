"""Clip naming → export filename pipeline.

The core naming rules:
  1. user-provided output filename base wins
  2. otherwise the sanitized clip title
  3. otherwise a generated fallback (never blank)
Duplicates get _2, _3 ... suffixes. Output must always be Windows-safe.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from tapesift.core.config import SEPARATOR_UNDERSCORE
from tapesift.models.clip import Clip
from tapesift.services.timestamp_parser import format_ms_filename

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_BASE_LENGTH = 180


def sanitize_filename_base(name: str, separator_style: str = "hyphen") -> str:
    """Turn arbitrary clip-name text into a safe Windows filename base."""
    if name is None:
        return ""
    sep = "_" if separator_style == SEPARATOR_UNDERSCORE else "-"
    text = _INVALID_CHARS.sub(" ", name)
    text = text.strip()
    text = re.sub(r"\s+", " ", text)          # collapse repeated whitespace
    text = text.replace(" ", sep)
    text = re.sub(rf"{re.escape(sep)}{{2,}}", sep, text)  # collapse repeated separators
    text = text.strip(sep + ". ")
    stem, dot, suffix = text.partition(".")
    if stem.upper() in _RESERVED_NAMES:
        text = f"{stem}{sep}clip{dot}{suffix}"
    return text[:MAX_BASE_LENGTH]


def fallback_name(clip: Clip) -> str:
    """Generated name used when the user gave no clip name. Never blank."""
    number = clip.clip_number or (clip.order_index + 1)
    if clip.central_timestamp_ms is not None:
        return f"Clip_{number:03d}_{format_ms_filename(clip.central_timestamp_ms)}"
    return (
        f"Clip_{number:03d}_{format_ms_filename(clip.start_ms)}"
        f"_to_{format_ms_filename(clip.end_ms)}"
    )


def effective_base(clip: Clip, separator_style: str = "hyphen") -> str:
    """Resolve the filename base by priority: explicit base > title > fallback."""
    if clip.output_filename_base.strip():
        cleaned = sanitize_filename_base(clip.output_filename_base, separator_style)
        if cleaned:
            return cleaned
    if clip.clip_title.strip():
        cleaned = sanitize_filename_base(clip.clip_title, separator_style)
        if cleaned:
            return cleaned
    return fallback_name(clip)


def render_template(template: str, clip: Clip, project_name: str = "",
                    separator_style: str = "hyphen") -> str:
    """Render the filename template for a clip. Unknown variables are left out."""
    values = {
        "project": sanitize_filename_base(project_name, separator_style),
        "clip_number": f"{(clip.clip_number or clip.order_index + 1):03d}",
        "clip_name": effective_base(clip, separator_style),
        "label": sanitize_filename_base(clip.label, separator_style),
        "timestamp": format_ms_filename(
            clip.central_timestamp_ms if clip.central_timestamp_ms is not None else clip.start_ms
        ),
        "start_time": format_ms_filename(clip.start_ms),
        "end_time": format_ms_filename(clip.end_ms),
        "date": _dt.date.today().isoformat(),
        "tags": sanitize_filename_base("-".join(clip.tags), separator_style),
    }

    def _sub(match: re.Match[str]) -> str:
        return values.get(match.group(1), "")

    rendered = re.sub(r"\{(\w+)\}", _sub, template or "{clip_number}_{clip_name}")
    rendered = sanitize_filename_base(rendered, separator_style) or fallback_name(clip)
    return rendered


FOLDER_TOKEN_ALIASES = {
    "player": "player_name",
    "down_and_distance": "down_distance",
}


def folder_template_tokens() -> list[str]:
    """Tokens usable in a folder template, for UI help text."""
    from tapesift.services.detail_service import DETAIL_KEYS
    return DETAIL_KEYS + ["player", "down_and_distance", "label", "tag",
                          "tags", "preset", "project", "date"]


def render_folder_template(template: str, clip: Clip, project_name: str = "",
                           default_preset: str = "",
                           separator_style: str = "hyphen") -> str:
    """Render a metadata folder path like "{player}/{quarter}/{down_distance}".

    Each level is sanitized independently; a missing value becomes
    "Unspecified" so clips never silently land in the wrong folder.
    Returns a relative path string ("" if the template renders empty).
    """
    def value_for(token: str) -> str:
        token = FOLDER_TOKEN_ALIASES.get(token, token)
        if token == "label":
            raw = clip.label
        elif token == "tag":
            raw = clip.tags[0] if clip.tags else ""
        elif token == "tags":
            raw = "-".join(clip.tags)
        elif token == "preset":
            raw = clip.export_preset or default_preset
        elif token == "project":
            raw = project_name
        elif token == "date":
            raw = _dt.date.today().isoformat()
        else:
            raw = clip.details.get(token, "")
            if token == "quarter" and raw and not raw.upper().startswith("Q"):
                raw = f"Q{raw}"  # same normalization as auto-naming
        return sanitize_filename_base(raw, separator_style)

    levels: list[str] = []
    for level in (template or "").replace("\\", "/").split("/"):
        level = level.strip()
        if not level:
            continue
        rendered = re.sub(r"\{(\w+)\}",
                          lambda m: value_for(m.group(1)) or "", level)
        rendered = sanitize_filename_base(rendered, separator_style)
        # A level whose tokens all came up empty gets the explicit bucket.
        if not rendered:
            rendered = "Unspecified"
        levels.append(rendered)
    return "/".join(levels)


def unique_title(name: str, existing_titles: list[str]) -> str:
    """Auto-number a repeated clip name: "Screen left" -> "Screen left 2".

    Comparison is case-insensitive; numbering continues from the highest
    existing suffix so "Name", "Name 2", "Name 3" never collide.
    """
    name = name.strip()
    if not name:
        return name
    lowered = {t.strip().lower() for t in existing_titles}
    if name.lower() not in lowered:
        return name
    n = 2
    while f"{name.lower()} {n}" in lowered:
        n += 1
    return f"{name} {n}"


def unique_path(directory: Path, base: str, extension: str,
                taken: set[str] | None = None) -> Path:
    """Return a path in `directory` that collides with neither disk nor `taken`.

    `taken` lets callers reserve names within a single export batch before
    any files exist. Extension should include the dot (".mp4").
    """
    taken = taken if taken is not None else set()
    candidate = base
    counter = 2
    while True:
        filename = f"{candidate}{extension}"
        path = directory / filename
        if filename.lower() not in taken and not path.exists():
            taken.add(filename.lower())
            return path
        candidate = f"{base}_{counter}"
        counter += 1


def find_duplicate_bases(clips: list[Clip], template: str, project_name: str = "",
                         separator_style: str = "hyphen") -> set[str]:
    """Return rendered filenames that more than one enabled clip would produce."""
    seen: dict[str, int] = {}
    for clip in clips:
        if not clip.enabled:
            continue
        name = render_template(template, clip, project_name, separator_style).lower()
        seen[name] = seen.get(name, 0) + 1
    return {name for name, count in seen.items() if count > 1}
