"""Conservative, read-only prioritization of possible-missed source ranges.

The auditor does not modify detector output and does not claim that a range is
or is not a play.  It ranks the review queue using structural evidence already
captured by autodetect so the editor can inspect the most play-shaped gaps
first without hiding weaker ones.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Any


AUDIT_VERSION = "1.0"
CHECK_FIRST = "check_first"
REVIEW = "review"
LOW_SIGNAL = "low_signal"

AUDIT_LABELS = {
    CHECK_FIRST: "CHECK FIRST",
    REVIEW: "REVIEW",
    LOW_SIGNAL: "LOW SIGNAL",
}

AUDIT_RANKS = {
    CHECK_FIRST: 0,
    REVIEW: 2,
    LOW_SIGNAL: 3,
}

_CANDIDATE_KINDS = frozenset({"play", "review"})
_DEFAULT_MIN_PLAY_MS = 4_000
_DEFAULT_MAX_PLAY_MS = 120_000


@dataclass(frozen=True)
class GapAudit:
    """One explainable queue ranking; ``score`` is not a probability."""

    segment_index: int
    level: str
    label: str
    rank: int
    score: int
    reasons: tuple[str, ...]
    version: str = AUDIT_VERSION

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["reasons"] = list(self.reasons)
        return result


def _positive_ms(value: Any, fallback: int) -> int:
    try:
        converted = round(float(value) * 1000)
    except (TypeError, ValueError):
        return fallback
    return converted if converted > 0 else fallback


def _profile_values(captured: dict[str, Any]) -> tuple[int, int, int]:
    """Return detector minimum, maximum, and film median in milliseconds."""
    parameters = captured.get("parameters", {})
    if not isinstance(parameters, dict):
        parameters = {}
    minimum_ms = _positive_ms(
        parameters.get("min_play_s"), _DEFAULT_MIN_PLAY_MS)
    maximum_ms = _positive_ms(
        parameters.get("max_play_s"), _DEFAULT_MAX_PLAY_MS)
    maximum_ms = max(minimum_ms, maximum_ms)

    result = captured.get("result", {})
    if not isinstance(result, dict):
        return minimum_ms, maximum_ms, 0
    summary = result.get("summary", {})
    profile = summary.get("profile", {}) \
        if isinstance(summary, dict) else {}
    if not isinstance(profile, dict):
        profile = {}
    try:
        median_ms = max(0, int(profile.get("median_play_ms", 0)))
    except (TypeError, ValueError):
        median_ms = 0
    if median_ms:
        return minimum_ms, maximum_ms, median_ms

    durations: list[int] = []
    plays = result.get("plays", [])
    if isinstance(plays, list):
        for play in plays:
            if not isinstance(play, dict):
                continue
            try:
                duration = int(play["end_ms"]) - int(play["start_ms"])
            except (KeyError, TypeError, ValueError):
                continue
            if duration > 0:
                durations.append(duration)
    inferred = round(median(durations)) if durations else 0
    return minimum_ms, maximum_ms, inferred


def _seconds(milliseconds: int) -> str:
    return f"{max(0, milliseconds) / 1000:.1f}s"


def audit_possible_missed(
    captured: dict[str, Any],
) -> dict[int, GapAudit]:
    """Rank every possible-missed interval while retaining all of them.

    Strong structural evidence is deliberately narrow:

    - a duration near the film's own median play length;
    - duration inside the user's detector limits;
    - adjacency to explicit black separators;
    - placement between detector candidate ranges.

    Source-edge ranges, very short flashes, and over-long ranges are lowered in
    the queue, never removed or automatically resolved.
    """
    result = captured.get("result", {})
    if not isinstance(result, dict):
        return {}
    coverage = result.get("coverage", [])
    if not isinstance(coverage, list):
        return {}

    minimum_ms, maximum_ms, median_ms = _profile_values(captured)
    audits: dict[int, GapAudit] = {}
    last_index = len(coverage) - 1
    for index, segment in enumerate(coverage):
        if not isinstance(segment, dict) \
                or segment.get("kind") != "possible_missed":
            continue
        try:
            start_ms = int(segment["start_ms"])
            end_ms = int(segment["end_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        duration_ms = end_ms - start_ms
        if duration_ms <= 0:
            continue

        score = 0
        evidence: list[str] = []
        cautions: list[str] = []

        if duration_ms < minimum_ms:
            score -= 2
            cautions.append(
                f"shorter than the {_seconds(minimum_ms)} detector minimum")
        else:
            score += 2
            evidence.append(
                f"long enough for the {_seconds(minimum_ms)} detector minimum")

        if duration_ms > maximum_ms:
            score -= 4
            cautions.append(
                f"longer than the {_seconds(maximum_ms)} configured maximum")
        elif median_ms > 0:
            ratio = duration_ms / median_ms
            if 0.55 <= ratio <= 1.60:
                score += 3
                evidence.append(
                    f"{_seconds(duration_ms)} is near this film's "
                    f"{_seconds(median_ms)} median play")
            elif 0.30 <= ratio <= 2.10:
                score += 1
                evidence.append(
                    f"duration is within the film's broad play range")
            else:
                cautions.append(
                    f"duration is unlike the film's "
                    f"{_seconds(median_ms)} median play")
        elif minimum_ms <= duration_ms <= maximum_ms:
            score += 1
            evidence.append("duration is inside the configured play range")

        previous = coverage[index - 1] if index > 0 else None
        following = coverage[index + 1] if index < last_index else None
        previous_kind = previous.get("kind") \
            if isinstance(previous, dict) else ""
        following_kind = following.get("kind") \
            if isinstance(following, dict) else ""
        separator_count = int(previous_kind == "separator") \
            + int(following_kind == "separator")
        if separator_count == 2:
            score += 4
            evidence.append("bounded by verified black separators")
        elif separator_count == 1:
            score += 2
            evidence.append("touches a verified black separator")
        elif previous_kind in _CANDIDATE_KINDS \
                and following_kind in _CANDIDATE_KINDS:
            score += 1
            evidence.append("sits between two detector candidate ranges")

        if index in {0, last_index}:
            score -= 1
            cautions.append("touches the beginning or end of the source")

        if score >= 7:
            level = CHECK_FIRST
            reasons = evidence + cautions
        elif score >= 3:
            level = REVIEW
            reasons = evidence + cautions
        else:
            level = LOW_SIGNAL
            reasons = cautions + evidence
        if not reasons:
            reasons = [
                "unclaimed source footage with no strong structural match"]
        audits[index] = GapAudit(
            segment_index=index,
            level=level,
            label=AUDIT_LABELS[level],
            rank=AUDIT_RANKS[level],
            score=score,
            reasons=tuple(reasons),
        )
    return audits
