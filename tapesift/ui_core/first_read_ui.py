"""Presentation rules for First Read inside the analyst workflow.

The model's answer is advisory data.  These helpers deliberately read it
without mutating the clip, and keep the analyst's ``run_pass`` decision as
the only way a First Read becomes resolved.
"""

from __future__ import annotations

from dataclasses import dataclass

from tapesift.models.clip import Clip
from tapesift.services.first_read_service import PASS, RUN, load


@dataclass(frozen=True)
class FirstReadPresentation:
    """One compact, non-editable state for Play Details."""

    state: str
    message: str
    chip_text: str = ""


def presentation_for(clip: Clip | None) -> FirstReadPresentation | None:
    """Translate stored analysis into one of the three standard UI states."""
    if clip is None:
        return None
    read = load(clip.analysis_json())
    if read is None:
        return None
    if read.error:
        return FirstReadPresentation(
            state="frames_failed",
            message="Couldn't read frames.",
        )
    if read.agreement and read.label in {RUN, PASS}:
        return FirstReadPresentation(
            state="suggested",
            chip_text=read.label.upper(),
            message="Suggestion only - press R or P to confirm.",
        )
    return FirstReadPresentation(
        state="disagreed",
        message="The two views disagree.",
    )


def needs_first_read_call(clip: Clip) -> bool:
    """True until the analyst has made an explicit run/pass call.

    Even an agreed suggestion remains unresolved: displaying RUN is not the
    same act as the analyst pressing R.  A disagreement and a frame failure
    are unresolved for the same reason, but retain distinct presentation.
    """
    return (
        presentation_for(clip) is not None
        and not clip.details.get("run_pass", "").strip()
    )


def needs_logging(clip: Clip) -> bool:
    """Preserve TapeSift's old definition and add unresolved First Reads."""
    return not clip.details or needs_first_read_call(clip)


def unresolved_count(clips) -> int:
    """Count First Reads still waiting for the analyst's R or P."""
    return sum(needs_first_read_call(clip) for clip in clips)
