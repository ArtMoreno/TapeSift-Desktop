"""Presentation-only timeline experiments used by the desktop test builds.

The variants deliberately live outside project data and AppSettings.  A
shortcut chooses one at launch, while every detector, clip, trim, export and
playback path continues to use the same production code.
"""

from __future__ import annotations

from dataclasses import dataclass


COMPACT = "compact"
DUAL = "dual"
FOCUS = "focus"
# The standard Review workspace uses the full-game overview above the
# zoomable detail scrubber. Keeping Compact as the argument-parser default
# made the real desktop app differ from the UI that was reviewed and tested.
DEFAULT = DUAL
VALID = (COMPACT, DUAL, FOCUS)


@dataclass(frozen=True)
class TimelineVariant:
    key: str
    title: str
    description: str


VARIANTS = {
    COMPACT: TimelineVariant(
        COMPACT,
        "Source Timeline",
        "Full-source coverage rail that expands overlaps at editing zoom.",
    ),
    DUAL: TimelineVariant(
        DUAL,
        "Overview + Detail",
        "A full-game navigator above the editable detail timeline.",
    ),
    FOCUS: TimelineVariant(
        FOCUS,
        "Focus Lens",
        "The selected play expands in place while the game stays visible.",
    ),
}


def normalize_timeline_variant(value: str | None) -> str:
    """Return a supported presentation key, defaulting safely."""
    candidate = (value or "").strip().lower().replace("_", "-")
    aliases = {
        "single": COMPACT,
        "single-rail": COMPACT,
        "adaptive": COMPACT,
        "overview": DUAL,
        "overview-detail": DUAL,
        "overview+detail": DUAL,
        "lens": FOCUS,
        "focus-lens": FOCUS,
    }
    candidate = aliases.get(candidate, candidate)
    return candidate if candidate in VALID else DEFAULT


def timeline_variant_from_args(args: list[str]) -> str:
    """Read ``--timeline-variant`` without taking ownership of app parsing."""
    for index, arg in enumerate(args):
        if arg.startswith("--timeline-variant="):
            return normalize_timeline_variant(arg.partition("=")[2])
        if arg == "--timeline-variant" and index + 1 < len(args):
            return normalize_timeline_variant(args[index + 1])
    return DEFAULT
