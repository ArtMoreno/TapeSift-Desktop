"""Project-level display preferences for clip tags.

Tags remain plain clip metadata. These helpers only decide how a tag is
presented when the timeline is colored by Primary Tag.
"""

from __future__ import annotations

import re
from copy import deepcopy


DEFAULT_STYLE = {
    "color": "",
    "category": "",
    "primary": True,
    "show_on_timeline": True,
}


def canonical_tag_key(value: str) -> str:
    """Stable, case-insensitive key for a tag or display category."""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def _valid_color(value: object) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else ""


def style_for_tag(
        tag: str, styles: dict[str, dict[str, object]] | None,
) -> dict[str, object]:
    """Return a normalized style without mutating stored project data."""
    raw = (styles or {}).get(canonical_tag_key(tag), {})
    return {
        "color": _valid_color(raw.get("color")),
        "category": " ".join(str(raw.get("category", "")).split()),
        "primary": bool(raw.get("primary", True)),
        "show_on_timeline": bool(raw.get("show_on_timeline", True)),
    }


def normalized_styles(
        styles: dict[str, dict[str, object]] | None,
) -> dict[str, dict[str, object]]:
    """Drop invalid/empty keys and make serialized project preferences safe."""
    cleaned: dict[str, dict[str, object]] = {}
    for key, value in (styles or {}).items():
        normalized_key = canonical_tag_key(str(key))
        if not normalized_key or not isinstance(value, dict):
            continue
        cleaned[normalized_key] = style_for_tag(normalized_key, {normalized_key: value})
    return cleaned


def primary_timeline_tag(
        tags: list[str], styles: dict[str, dict[str, object]] | None,
) -> tuple[str, str, str]:
    """First visible primary tag, with an optional category and custom color.

    A tag is primary by default so existing projects retain their current
    first-tag behavior until the analyst opts into a primary/secondary setup.
    """
    for tag in tags:
        label = " ".join(tag.strip().split())
        if not label:
            continue
        style = style_for_tag(label, styles)
        if not style["primary"] or not style["show_on_timeline"]:
            continue
        display = str(style["category"]) or label
        return canonical_tag_key(display), display, str(style["color"])
    return "", "Unlabelled", ""


def copy_styles(styles: dict[str, dict[str, object]] | None) -> dict[str, dict[str, object]]:
    """Defensive copy for dialog working state."""
    return deepcopy(normalized_styles(styles))
