"""Shared play-tag readout: colors and projections for tagged plays.

These lived in the retired Tag Map widget. The attribute grid still
projects the same fields onto the timeline axis, so the vocabulary -
what counts as Run/Pass/Screen/RPO, how an outcome reads, the data
green that stays clear of the brand green - keeps one home here.
"""

from __future__ import annotations

import re

from tapesift.models.clip import Clip
from tapesift.services.football_vocab import lookup

# Brand green ($green) means "primary action" - Save + Next, the wordmark,
# the jog accent. Data uses a softer green so a RUN marker and a LOGGED chip
# stop reading as the same thing. Same hue family, clearly lower energy.
DATA_GREEN = "#57c98a"

PLAY_TYPE_COLORS = {
    "run": DATA_GREEN,
    "pass": "#4da3ff",
    "screen": "#9567d8",
    "rpo": "#36d6c0",
    "special": "#9567d8",
    "no_play": "#89939b",
}
OUTCOME_COLORS = {
    "penalty": "#d9b43b",
    "score": "#e779b8",
    "turnover": "#ff5d73",
    "negative": "#f59e0b",
    "first_down": DATA_GREEN,
    "complete": "#3f7cc0",
    "stop": "#667169",
    "special": "#9567d8",
    "other": "#526158",
}


def outcome_key(value: object) -> str:
    """Palette key for one stored result spelling.

    OUTCOME_COLORS is keyed by legend category, not by spelling, so every
    lookup has to go through the roll-up first. An unseen spelling lands on
    "other" rather than being guessed into a category.
    """
    from tapesift.services.football_vocab import category_for

    return category_for("result", _clean(value)).casefold().replace(" ", "_")


def _clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _play_type(details: dict[str, str]) -> tuple[str, str] | None:
    """Return the most specific play family without collapsing Screen/RPO."""
    concept = lookup("play_type", details.get("play_type"))
    family = lookup("run_pass", details.get("run_pass"))
    # A retained concept cannot make a nullified/special play look like an
    # ordinary offensive snap. The saved family is explicit in these cases.
    if family and family.canonical == "No Play":
        return "no_play", "NO PLAY"
    if family and family.canonical == "Special":
        return "special", "SPECIAL"
    if concept and concept.canonical == "Screen":
        return "screen", "SCREEN"
    if concept and concept.canonical == "RPO":
        return "rpo", "RPO"
    if family and family.canonical == "Run":
        return "run", "RUN"
    if family and family.canonical == "Pass":
        return "pass", "PASS"
    return None


def _short_outcome(value: str) -> tuple[str, str]:
    text = _clean(value)
    folded = text.casefold()
    if re.search(r"\bpenalt(?:y|ies)\b", folded):
        return "penalty", "PENALTY"
    if re.search(r"\b(touchdown|pass td|rush(?:ing)? td|receiving td|td)\b",
                 folded):
        return "touchdown", "TD"
    if re.search(r"\b(interception|intercepted|pick six|pick-6|int)\b",
                 folded):
        return "interception", "INT"
    if re.search(r"\bsack(?:ed)?\b", folded):
        return "sack", "SACK"
    if re.search(r"\b(pass break(?:up)?|broken up|pbu)\b", folded):
        return "pbu", "PBU"
    if re.search(r"\bfumble\b", folded):
        return "fumble", "FUMBLE"
    if re.search(r"\bno gain\b", folded):
        return "no_gain", "NO GAIN"
    gain = re.search(r"\bgain(?: of)?\s+(-?\d+)\b", folded)
    if gain:
        return "gain", f"GAIN {gain.group(1)}"
    if re.search(r"\bfirst down\b", folded):
        return "first_down", "1ST DOWN"
    if re.search(r"\bincompletion|incomplete\b", folded):
        return "incompletion", "INCOMP"
    if re.search(r"\breception\b", folded):
        return "reception", "REC"
    if re.search(r"\bcompletion|complete\b", folded):
        return "completion", "COMP"
    if re.search(r"\btackle\b", folded):
        return "tackle", "TACKLE"
    return "other", text.upper()[:18]


def _outcome(clip: Clip) -> tuple[str, str] | None:
    """Project the saved outcome without reviving stale derived text.

    Result is the analyst-owned source of truth. Titles and tags can retain a
    value copied from an earlier save (for example, Touchdown after TD was
    deselected), so they are only a fallback for older clips that have no
    structured Result field.
    """
    from tapesift.services.football_vocab import category_for
    from tapesift.services.result_service import primary_result

    result = _clean(clip.details.get("result"))
    if result:
        category = primary_result(result) or category_for("result", result)
        key = category.casefold().replace(" ", "_")  # same rule as outcome_key
        if key == "first_down":
            return key, "1ST DOWN"
        return key, category.upper()
    legacy_text = " ".join((
        clip.clip_title, clip.label, " ".join(clip.tags),
    ))
    if not legacy_text.strip():
        return None
    key, label = _short_outcome(legacy_text)
    if key == "other":
        return None
    # The legacy matcher owns the text recognition; the palette still owns
    # categories. Keep the familiar face without using a spelling as a color key.
    category_key = key if key in OUTCOME_COLORS else outcome_key(key.replace("_", " "))
    return category_key, label


_NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def _surname(value: str) -> str:
    """Family name, skipping generational suffixes.

    Taking the last word turned "Mark Fletcher Jr." into "JR.", which is not
    a name and collides with every other Jr. in the squad.
    """
    parts = _clean(value).split()
    while len(parts) > 1 and parts[-1].casefold() in _NAME_SUFFIXES:
        parts.pop()
    return parts[-1].upper() if parts else ""
