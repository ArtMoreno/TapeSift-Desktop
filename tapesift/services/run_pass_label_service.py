"""Canonical, non-AI run/pass labels for football clips.

The UI stores the analyst's decision in the existing ``run_pass`` detail.
This module is the single interpretation layer used by later dataset and
prediction work, so edge cases do not acquire different meanings in
different screens or scripts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping


RUN = "run"
PASS = "pass"
SPECIAL = "special"
NO_PLAY = "no_play"
CANONICAL_LABELS = (RUN, PASS, SPECIAL, NO_PLAY)

DISPLAY_LABELS = {
    RUN: "Run",
    PASS: "Pass",
    SPECIAL: "Special",
    NO_PLAY: "No Play",
}

_EXPLICIT_LABELS = {
    "run": RUN,
    "pass": PASS,
    "special": SPECIAL,
    "special teams": SPECIAL,
    "no play": NO_PLAY,
    "no-play": NO_PLAY,
    "no_play": NO_PLAY,
    "noplay": NO_PLAY,
}


@dataclass(frozen=True)
class RunPassResolution:
    """One normalized label plus an audit trail for how it was obtained."""

    label: str
    display: str
    source: str
    evidence: tuple[str, ...] = ()
    conflict: bool = False

    @property
    def trainable(self) -> bool:
        """Only an explicit, internally consistent analyst choice is truth."""
        return (
            self.label in CANONICAL_LABELS
            and self.source == "explicit"
            and not self.conflict
        )


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _contains(text: str, phrase: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", text).strip()
    wanted = re.sub(r"[^a-z0-9]+", " ", phrase).strip()
    return bool(re.search(rf"(?:^|\s){re.escape(wanted)}(?:$|\s)", normalized))


def _derived_label(
    details: Mapping[str, object],
    tags: Iterable[object],
) -> tuple[str, tuple[str, ...]]:
    values = [
        details.get("play_type", ""),
        details.get("result", ""),
        details.get("action", ""),
        *tags,
    ]
    evidence = tuple(
        str(value).strip() for value in values if str(value or "").strip()
    )
    text = " | ".join(_clean(value) for value in evidence)
    if not text:
        return "", ()

    # A generic accepted penalty can still contain a real play. Only explicit
    # dead-ball/pre-snap language becomes No Play automatically.
    if any(_contains(text, phrase) for phrase in (
        "no play", "pre snap penalty", "presnap penalty", "false start",
        "delay of game", "encroachment",
    )):
        return NO_PLAY, evidence

    if any(_contains(text, phrase) for phrase in (
        "kneel", "punt", "kickoff", "field goal", "extra point", "pat",
    )):
        return SPECIAL, evidence

    # RPO is a concept, not a run/pass answer. It becomes determinate only
    # when the outcome/decision is also present.
    if _contains(text, "rpo"):
        if any(_contains(text, phrase) for phrase in (
            "handoff", "hand off", "keep", "keeper",
        )):
            return RUN, evidence
        if any(_contains(text, phrase) for phrase in (
            "throw", "screen", "completion", "reception", "incompletion",
            "interception",
        )):
            return PASS, evidence
        return "", evidence

    if any(_contains(text, phrase) for phrase in (
        "screen", "sack", "spike", "completion", "reception",
        "incompletion", "interception",
    )):
        return PASS, evidence

    if any(_contains(text, phrase) for phrase in (
        "qb draw", "draw", "scramble", "qb sneak",
    )):
        return RUN, evidence

    play_type = _clean(details.get("play_type", ""))
    if play_type == "run":
        return RUN, evidence
    if play_type == "pass":
        return PASS, evidence
    return "", evidence


def resolve_run_pass_label(
    details: Mapping[str, object] | None,
    tags: Iterable[object] = (),
) -> RunPassResolution:
    """Normalize an analyst label and flag contradictions without guessing.

    Explicit Run/Pass/Special/No Play always remains the displayed decision.
    A conflicting football rule is surfaced as a conflict and is excluded
    from training exports until an analyst resolves it.
    """
    values = details or {}
    explicit = _EXPLICIT_LABELS.get(_clean(values.get("run_pass", "")), "")
    derived, evidence = _derived_label(values, tags)
    if explicit:
        conflict = bool(derived and derived != explicit)
        return RunPassResolution(
            explicit,
            DISPLAY_LABELS[explicit],
            "explicit",
            evidence,
            conflict,
        )
    if derived:
        return RunPassResolution(
            derived,
            DISPLAY_LABELS[derived],
            "taxonomy",
            evidence,
        )
    return RunPassResolution("", "", "unlabeled", evidence)
