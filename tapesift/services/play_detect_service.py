"""Automatic play detection for cut-up game film.

Cut-up film is assembled one play at a time, and the assembly leaves a
boundary between every play. Measured across real All-22 film, the boundary
is one of three things - and which one varies by film:

  * a short black gap        (defense cuts:  [wide][black][tight][black])
  * a short graphic card     (offense cuts:  [play][card ~3s][play])
  * a plain hard cut         (no black, no card)

Rather than special-case each, the detector splits the film on whatever
boundary signal exists and then applies one rule that covers all of them:

    short spans are separators, long spans are play content.

Consecutive long spans (i.e. the same play shown from several camera
angles) are grouped into a single play.
"""

from __future__ import annotations

import logging
import re
import statistics
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tapesift.core.exceptions import TapeSiftError
from tapesift.services import background_service, ffmpeg_service

log = logging.getLogger(__name__)

# A span shorter than this is a separator (black gap, card, bumper), not a play.
# Measured across 14 All-22 films the longest separator seen was 4.54s, so 5.0
# holds everywhere - but the margin is thin, which is why the ceiling below is
# derived per film rather than fixed.
DEFAULT_SEPARATOR_MAX_S = 5.0
# Absolute sanity bounds only. These are not the classifier - see
# FilmProfile - they just rule out runs no film could call a play.
DEFAULT_MIN_PLAY_S = 4.0
DEFAULT_MAX_PLAY_S = 90.0
# What one camera angle of one play can physically contain: a second or two of
# scoreboard/graphic, a few seconds pre-snap, 4-7s of live football (up to ~15
# on a long completion with run after catch), and a short tail. Measured on
# film with a known play count: 21.3s per angle. 30 leaves generous headroom.
MAX_SECONDS_PER_ANGLE = 30.0
# Above this, a reported "angle" is too long to be one angle - the detector is
# almost certainly missing the boundary between two of them.
SUSPECT_SECONDS_PER_ANGLE = 26.0
# Fewer confident runs than this and the film cannot describe itself; fall
# back to the fixed bounds rather than calibrate from noise.
MIN_RUNS_TO_PROFILE = 8
# A mixed-signal film can contain one or two incidental black flashes that
# are unrelated to its actual edit grammar.  The primary selector historically
# treated any black interval as authoritative and then ignored every scene
# cut, which can collapse an otherwise well-structured film into one giant
# unclassified range.  A scene fallback is allowed only when the black-derived
# structure cannot calibrate at all and the already-captured scene cuts describe
# a large, strongly consistent population.  Outputs remain review-only.
MIXED_SCENE_FALLBACK_MIN_PROFILED_RUNS = 24
MIXED_SCENE_FALLBACK_MIN_MODAL_SHARE = 0.80
MIXED_SCENE_FALLBACK_REVIEW_REASON = (
    "recovered from a mixed-signal film whose sparse black gaps could not "
    "describe the film; check the scene-based boundaries")
# In this fallback, a half-second scene span can be the camera change inside
# one play rather than a boundary between plays.  Join it only when the film
# repeats that topology and both surrounding pieces together look like one
# normal play.  The intentionally local rule leaves short scorebug fragments
# and isolated coincidences untouched.
MIXED_SCENE_PAIR_MIN_GAP_MS = 450
MIXED_SCENE_PAIR_MAX_GAP_MS = 550
# FFmpeg can report one half-second camera transition as three contiguous
# scene spans when a couple of frames inside the transition also cross the
# scene threshold.  The frozen Miami review contains one human-confirmed pair
# with the exact 201 ms + 33 ms + 267 ms shape.  Keep this exception local to
# the already fail-closed mixed-signal repair: the general scene-pairing path
# deliberately continues to treat three-part micro transitions as ambiguous.
MIXED_SCENE_PAIR_FRAGMENTED_MICRO_PARTS = 3
MIXED_SCENE_PAIR_FRAGMENT_OUTER_MIN_MS = 150
MIXED_SCENE_PAIR_FRAGMENT_OUTER_MAX_MS = 300
MIXED_SCENE_PAIR_FRAGMENT_MIDDLE_MAX_MS = 50
MIXED_SCENE_PAIR_OUTER_CARD_MIN_MS = 2_000
MIXED_SCENE_PAIR_OUTER_CARD_MAX_MS = 3_100
MIXED_SCENE_PAIR_MIN_PAIRS = 8
MIXED_SCENE_PAIR_MIN_ELIGIBLE_SHARE = 0.75
MIXED_SCENE_PAIR_MIN_INPUT_SHARE = 0.10
MIXED_SCENE_PAIR_MAX_INPUT_SHARE = 0.25
MIXED_SCENE_PAIR_MIN_MEDIAN_MS = 30_000
MIXED_SCENE_PAIR_MAX_MEDIAN_MS = 45_000
MIXED_SCENE_PAIR_MIN_DURATION_FACTOR = 0.75
MIXED_SCENE_PAIR_MAX_DURATION_FACTOR = 1.50
MIXED_SCENE_PAIR_MIN_COMPONENT_FACTOR = 0.20
MIXED_SCENE_PAIR_MAX_COMPONENT_FACTOR = 0.80
MIXED_SCENE_PAIR_REVIEW_REASON = (
    "paired across a sub-second camera change recovered from mixed-signal "
    "film; check both angles and boundaries")
# How a clip that needs checking announces itself once it exists. The prefix
# is for the eye scanning the list; the tag is what filters and bulk edits
# work on, and what makes clearing them afterwards a single action.
REVIEW_PREFIX = "[REVIEW]"
REVIEW_TAG = "needs-review"
# Coverage Guardian records the detector's disposition of the entire source,
# including footage that did not become a clip candidate.  These values are
# persisted verbatim, so keep them stable across future detector revisions.
COVERAGE_SCHEMA_VERSION = "1.0"
COVERAGE_PLAY = "play"
COVERAGE_REVIEW = "review"
COVERAGE_POSSIBLE_MISSED = "possible_missed"
COVERAGE_SEPARATOR = "separator"
COVERAGE_KINDS = (
    COVERAGE_PLAY,
    COVERAGE_REVIEW,
    COVERAGE_POSSIBLE_MISSED,
    COVERAGE_SEPARATOR,
)
# Scene-change sensitivity; only used when a film has no black/card boundaries.
DEFAULT_SCENE_THRESHOLD = 0.35
# Scene-only film sometimes contains useful camera transitions below the
# shipping threshold. They are never trusted globally. A second pass may use
# them only to partition footage the primary detector already left for review.
WEAK_SCENE_THRESHOLD = 0.15
WEAK_SCENE_CLUSTER_GAP_MS = 3_500
WEAK_SCENE_MIN_SCORE = 0.20
WEAK_SCENE_MIN_DURATION_FACTOR = 0.75
WEAK_SCENE_MAX_DURATION_FACTOR = 1.25
WEAK_SCENE_AMBIGUITY_FACTOR = 0.10
# A few scene-only producers use a very short score-card flash between the
# two angles of one play and a longer card between logical plays. The normal
# five-second separator rule cannot distinguish those two populations. This
# repair is intentionally narrow and runs only after the primary and weak
# recovery passes have produced an otherwise clean result.
SCENE_PAIR_MIN_PLAYS = 24
SCENE_PAIR_MIN_MODAL_SHARE = 0.90
SCENE_PAIR_MAX_MEDIAN_PLAY_MS = 20_000
SCENE_PAIR_MICRO_MIN_MS = 250
SCENE_PAIR_MICRO_MAX_MS = 1_500
SCENE_PAIR_CARD_MIN_MS = 2_000
SCENE_PAIR_CARD_MAX_MS = 6_000
SCENE_PAIR_MICRO_COMPONENT_MAX_MS = 750
SCENE_PAIR_MIN_GAPS_PER_MODE = 8
SCENE_PAIR_MIN_ISOLATED_SHARE = 0.75
SCENE_PAIR_MIN_OUTPUT_SHARE = 0.70
SCENE_PAIR_MIN_GAP_RATIO = 3.0
SCENE_PAIR_MIN_MODE_SEPARATION_MS = 1_500
SCENE_PAIR_SPAN_TOLERANCE_MS = 2
SCENE_PAIR_REVIEW_REASON = (
    "paired across a short in-play card in scene-only film; "
    "check the boundaries")
# A separate, fail-closed fallback covers one known catastrophic topology:
# black-bounded film with many content spans, but no short content card, so
# the primary rule merges the entire film into one uncalibrated review block.
# It is only useful when the black gaps themselves form two clean alternating
# modes: a very short gap between the two angles of one snap, followed by a
# longer gap before the next snap.  The strict gates are deliberate.  Without
# the two modes, individual spans are observationally ambiguous.
BLACK_PAIR_FALLBACK_MIN_PAIRS = 24
BLACK_PAIR_SHORT_MIN_MS = 50
BLACK_PAIR_SHORT_MAX_MS = 500
BLACK_PAIR_LONG_MIN_MS = 2_000
BLACK_PAIR_LONG_MAX_MS = 5_000
BLACK_PAIR_ENDPOINT_TOLERANCE_MS = 2
BLACK_PAIR_MIN_MODE_RATIO = 3.0
BLACK_PAIR_MIN_MODE_SEPARATION_MS = 1_500
BLACK_PAIR_MAX_EXTRA_SCENE_SHARE = 0.05
BLACK_PAIR_REVIEW_REASON = (
    "paired from an uncalibrated alternating black-gap pattern; "
    "check both angles and boundaries")

_BLACK_RE = re.compile(r"black_start:([\d.]+) black_end:([\d.]+)")
_PTS_RE = re.compile(r"pts_time:([\d.]+)")
_SCENE_SCORE_RE = re.compile(r"lavfi\.scene_score=([\d.eE+-]+)")


@dataclass
class DetectedPlay:
    """One play: a run of camera angles between two separators."""
    start_ms: int
    end_ms: int
    angle_count: int = 1
    # Start of each camera angle within the play, in ms (absolute).
    angle_starts: list[int] = field(default_factory=list)
    # True when this clip came out of a split we are not certain about, or
    # is film we could not classify. Carried through to the created clip so
    # it can be found and checked instead of being lost among good ones.
    needs_review: bool = False
    review_reason: str = ""

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class UnclassifiedSegment:
    """Film the detector could not call a play - kept, never discarded.

    Everything outside the play bounds used to be deleted silently, which is
    how whole films came back empty with no error. Footage the detector does
    not understand is still the user's footage: it is surfaced for review so
    it can be split, kept, or dismissed deliberately.
    """
    start_ms: int
    end_ms: int
    angle_count: int
    reason: str
    # Angle boundaries inside the segment: where a split would land.
    split_points_ms: list[int] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class FilmProfile:
    """What this film's own structure says a play looks like.

    Films come from different producers: some separate plays with black, some
    with a graphic card, some with a plain cut; some show two camera angles
    per play, some one. A constant that fits one film misclassifies another,
    so the numbers are measured from the film in hand.
    """
    modal_angles: int
    modal_share: float          # how firmly the modal count is held (0-1)
    median_play_ms: int
    max_play_ms: int
    calibrated: bool            # False => too little signal, using defaults

    @property
    def seconds_per_angle(self) -> float:
        if not self.modal_angles:
            return 0.0
        return self.median_play_ms / 1000 / self.modal_angles

    @property
    def angle_count_suspect(self) -> bool:
        """One 'angle' lasting longer than a whole play means the detector is
        seeing two angles as one."""
        return self.seconds_per_angle > SUSPECT_SECONDS_PER_ANGLE


@dataclass(frozen=True)
class CoverageSegment:
    """One half-open interval in the detector's full-source coverage ledger.

    Candidate references let later UI phases jump from the neutral source rail
    to the exact play or review row without changing the detector result.
    Separator and possible-missed intervals intentionally have no candidate.
    """

    start_ms: int
    end_ms: int
    kind: str
    reason: str = ""
    candidate_kind: str = ""
    candidate_index: int | None = None

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class DetectionResult:
    plays: list[DetectedPlay]
    signal: str            # "black" | "scene" | "mixed"
    spans_found: int
    separators_found: int
    duration_ms: int
    unclassified: list[UnclassifiedSegment] = field(default_factory=list)
    profile: FilmProfile | None = None
    coverage_segments: list[CoverageSegment] = field(default_factory=list)

    @property
    def coverage_pct(self) -> float:
        if not self.duration_ms:
            return 0.0
        covered = sum(p.duration_ms for p in self.plays)
        return 100.0 * covered / self.duration_ms

    @property
    def unclassified_ms(self) -> int:
        return sum(s.duration_ms for s in self.unclassified)

    @property
    def coverage_by_kind(self) -> dict[str, int]:
        totals = {kind: 0 for kind in COVERAGE_KINDS}
        for segment in self.coverage_segments:
            if segment.kind in totals:
                totals[segment.kind] += segment.duration_ms
        return totals

    @property
    def accounted_ms(self) -> int:
        return sum(segment.duration_ms for segment in self.coverage_segments)

    @property
    def unaccounted_ms(self) -> int:
        return max(0, self.duration_ms - self.accounted_ms)

    @property
    def accounted_pct(self) -> float:
        if not self.duration_ms:
            return 0.0
        return 100.0 * self.accounted_ms / self.duration_ms

    @property
    def possible_missed_ms(self) -> int:
        return self.coverage_by_kind[COVERAGE_POSSIBLE_MISSED]

    @property
    def separator_ms(self) -> int:
        return self.coverage_by_kind[COVERAGE_SEPARATOR]

    @property
    def review_ms(self) -> int:
        return self.coverage_by_kind[COVERAGE_REVIEW]


def validate_coverage_ledger(
    segments: list[CoverageSegment],
    duration_ms: int,
) -> None:
    """Raise when a ledger is not an exact, ordered source partition."""
    duration_ms = max(0, int(duration_ms))
    if duration_ms == 0:
        if segments:
            raise ValueError("zero-duration source has coverage segments")
        return

    cursor = 0
    for segment in segments:
        if segment.kind not in COVERAGE_KINDS:
            raise ValueError(f"unknown coverage kind: {segment.kind}")
        if segment.start_ms != cursor:
            raise ValueError(
                f"coverage is not contiguous at {cursor}ms "
                f"(next starts at {segment.start_ms}ms)")
        if segment.end_ms <= segment.start_ms:
            raise ValueError("coverage segment has no duration")
        if segment.end_ms > duration_ms:
            raise ValueError("coverage segment exceeds source duration")
        cursor = segment.end_ms
    if cursor != duration_ms:
        raise ValueError(
            f"coverage stops at {cursor}ms before source end {duration_ms}ms")


def build_coverage_ledger(
    duration_ms: int,
    plays: list[DetectedPlay],
    unclassified: list[UnclassifiedSegment],
    black_intervals: list[tuple[float, float]],
) -> list[CoverageSegment]:
    """Partition every source millisecond without changing play decisions.

    Final play/review candidates take precedence because their exported ranges
    deliberately include camera transitions.  Explicit FFmpeg black intervals
    outside candidates are verified separators.  Every other unclaimed source
    interval remains visible as possible-missed footage instead of silently
    disappearing.
    """
    duration_ms = max(0, int(duration_ms))
    if duration_ms == 0:
        return []

    def clipped(start_ms: int, end_ms: int) -> tuple[int, int] | None:
        start_ms = max(0, min(duration_ms, int(start_ms)))
        end_ms = max(0, min(duration_ms, int(end_ms)))
        return (start_ms, end_ms) if end_ms > start_ms else None

    black_ms: list[tuple[int, int]] = []
    for start_s, end_s in black_intervals:
        interval = clipped(round(start_s * 1000), round(end_s * 1000))
        if interval is not None:
            black_ms.append(interval)

    # priority, start, end, kind, reason, candidate kind, candidate index
    claims: list[tuple[int, int, int, str, str, str, int]] = []
    for index, play in enumerate(plays):
        interval = clipped(play.start_ms, play.end_ms)
        if interval is None:
            continue
        kind = COVERAGE_REVIEW if play.needs_review else COVERAGE_PLAY
        reason = play.review_reason if play.needs_review else ""
        claims.append((
            2 if play.needs_review else 1,
            interval[0],
            interval[1],
            kind,
            reason,
            "play",
            index,
        ))
    for index, segment in enumerate(unclassified):
        interval = clipped(segment.start_ms, segment.end_ms)
        if interval is None:
            continue
        claims.append((
            3,
            interval[0],
            interval[1],
            COVERAGE_REVIEW,
            segment.reason,
            "unclassified",
            index,
        ))

    edges = {0, duration_ms}
    for _priority, start_ms, end_ms, *_rest in claims:
        edges.update((start_ms, end_ms))
    for start_ms, end_ms in black_ms:
        edges.update((start_ms, end_ms))
    ordered_edges = sorted(edges)

    ledger: list[CoverageSegment] = []
    for start_ms, end_ms in zip(ordered_edges, ordered_edges[1:]):
        active = [
            claim
            for claim in claims
            if claim[1] < end_ms and claim[2] > start_ms
        ]
        if active:
            # Review beats confidence if detector outputs ever overlap.  The
            # stable tie-break makes capture files deterministic.
            active.sort(key=lambda item: (-item[0], item[5], item[6]))
            _priority, _start, _end, kind, reason, candidate_kind, index = \
                active[0]
            current = CoverageSegment(
                start_ms=start_ms,
                end_ms=end_ms,
                kind=kind,
                reason=reason,
                candidate_kind=candidate_kind,
                candidate_index=index,
            )
        elif any(
            black_start <= start_ms and black_end >= end_ms
            for black_start, black_end in black_ms
        ):
            current = CoverageSegment(
                start_ms=start_ms,
                end_ms=end_ms,
                kind=COVERAGE_SEPARATOR,
                reason="explicit black interval",
            )
        else:
            current = CoverageSegment(
                start_ms=start_ms,
                end_ms=end_ms,
                kind=COVERAGE_POSSIBLE_MISSED,
                reason="not included in a detected or review candidate",
            )

        previous = ledger[-1] if ledger else None
        if (
            previous is not None
            and previous.end_ms == current.start_ms
            and previous.kind == current.kind
            and previous.reason == current.reason
            and previous.candidate_kind == current.candidate_kind
            and previous.candidate_index == current.candidate_index
        ):
            ledger[-1] = CoverageSegment(
                start_ms=previous.start_ms,
                end_ms=current.end_ms,
                kind=previous.kind,
                reason=previous.reason,
                candidate_kind=previous.candidate_kind,
                candidate_index=previous.candidate_index,
            )
        else:
            ledger.append(current)

    validate_coverage_ledger(ledger, duration_ms)
    return ledger


def _run_ffmpeg_analysis(ffmpeg_path: str, source: Path,
                         scene_threshold: float,
                         process_started: Callable[[subprocess.Popen], None]
                         | None = None) -> tuple[str, str]:
    """One decode pass reporting both black intervals and scene cuts."""
    cmd = [
        ffmpeg_path, "-nostdin", "-hide_banner", "-i", str(source),
        "-vf", (f"select='gt(scene,{scene_threshold})',metadata=print:file=-,"
                f"blackdetect=d=0.05:pix_th=0.10"),
        "-an", "-f", "null", "-",
    ]
    log.info("Play detection: %s", ffmpeg_service.command_to_display_string(cmd))
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
            creationflags=ffmpeg_service.CREATE_NO_WINDOW |
            ffmpeg_service.IDLE_PRIORITY_CLASS,
        )
    except OSError as exc:
        raise TapeSiftError(
            f"Could not analyse the video for plays: {exc}",
            "Check the FFmpeg path in Settings > FFmpeg.")
    if process_started is not None:
        process_started(proc)
    # Registered so playback can suspend this scan while the user scrubs.
    background_service.register(proc.pid)
    try:
        stdout, stderr = proc.communicate(timeout=3600)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TapeSiftError("Analysing the video for plays timed out.")
    finally:
        background_service.unregister(proc.pid)
    if proc.returncode:
        raise TapeSiftError(
            "FFmpeg did not finish analysing the video for plays "
            f"(exit code {proc.returncode}).",
            "Try again. If it keeps failing, check the video and the FFmpeg "
            "path in Settings > FFmpeg.",
        )
    return stdout or "", stderr or ""


def _parse_scene_events(output: str) -> list[tuple[int, float]]:
    """Pair FFmpeg scene timestamps with their score metadata."""
    events: list[tuple[int, float]] = []
    pending_ms: int | None = None
    for line in output.splitlines():
        timestamp = _PTS_RE.search(line)
        if timestamp:
            pending_ms = round(float(timestamp.group(1)) * 1000)
        score = _SCENE_SCORE_RE.search(line)
        if score and pending_ms is not None:
            events.append((pending_ms, float(score.group(1))))
            pending_ms = None
    return events


def spans_from_boundaries(duration_s: float, black: list[tuple[float, float]],
                          cuts: list[float]) -> tuple[list[tuple[float, float]], str]:
    """Content spans between boundaries, plus which signal produced them.

    Black gaps are preferred when present: they are unambiguous. Scene cuts
    are the fallback for films assembled with hard cuts (or with graphic
    cards, whose entry/exit register as cuts).
    """
    if black:
        spans, prev = [], 0.0
        for start, end in black:
            if start > prev:
                spans.append((prev, start))
            prev = end
        if duration_s > prev:
            spans.append((prev, duration_s))
        return spans, ("mixed" if cuts else "black")
    if cuts:
        edges = [0.0] + sorted(cuts) + [duration_s]
        return [(a, b) for a, b in zip(edges, edges[1:]) if b > a], "scene"
    return [(0.0, duration_s)], "none"


def group_spans_into_plays(
    spans: list[tuple[float, float]],
    separator_max_s: float = DEFAULT_SEPARATOR_MAX_S,
    min_play_s: float = DEFAULT_MIN_PLAY_S,
    max_play_s: float = DEFAULT_MAX_PLAY_S,
) -> list[DetectedPlay]:
    """Apply the core rule: short spans separate, long spans are the play."""
    plays: list[DetectedPlay] = []
    run: list[tuple[float, float]] = []

    def flush() -> None:
        if not run:
            return
        start, end = run[0][0], run[-1][1]
        if min_play_s <= (end - start) <= max_play_s:
            plays.append(DetectedPlay(
                start_ms=int(start * 1000), end_ms=int(end * 1000),
                angle_count=len(run),
                angle_starts=[int(s * 1000) for s, _ in run]))

    for start, end in spans:
        if (end - start) < separator_max_s:
            flush()
            run = []
        else:
            run.append((start, end))
    flush()
    return plays


def _profiled_runs(runs: list[DetectedPlay]) -> list[DetectedPlay]:
    """Return runs whose durations are plausible enough to profile."""
    return [
        run for run in runs
        if DEFAULT_MIN_PLAY_S * 1000 <= run.duration_ms
        <= DEFAULT_MAX_PLAY_S * 1000
    ]


def profile_film(runs: list[DetectedPlay]) -> FilmProfile:
    """Measure what a normal play looks like in this particular film."""
    plausible = _profiled_runs(runs)
    if len(plausible) < MIN_RUNS_TO_PROFILE:
        return FilmProfile(modal_angles=1, modal_share=0.0,
                           median_play_ms=0,
                           max_play_ms=int(DEFAULT_MAX_PLAY_S * 1000),
                           calibrated=False)

    counts = Counter(r.angle_count for r in plausible)
    modal_angles, modal_n = counts.most_common(1)[0]
    typical = [r for r in plausible if r.angle_count == modal_angles]
    median_play = int(statistics.median([r.duration_ms for r in typical]))

    # The ceiling follows the clip's structure rather than a flat number: a
    # play is its angles, and an angle has a physical maximum. A film that
    # genuinely carries three angles gets a proportionally higher ceiling.
    structural_max = modal_angles * MAX_SECONDS_PER_ANGLE * 1000
    # Never below what this film's own plays actually do.
    observed_max = median_play * 1.6
    return FilmProfile(
        modal_angles=modal_angles,
        modal_share=modal_n / len(plausible),
        median_play_ms=median_play,
        max_play_ms=int(max(structural_max, observed_max)),
        calibrated=True)


def _try_mixed_scene_fallback(
    duration_s: float,
    black: list[tuple[float, float]],
    cuts: list[float],
    primary_profile: FilmProfile,
    *,
    separator_max_s: float,
) -> tuple[
    list[tuple[float, float]],
    list[DetectedPlay],
    FilmProfile,
] | None:
    """Use scene structure when incidental black gaps collapse the film.

    Both signals come from the primary decode.  This is not a lower-threshold
    recovery and adds no analysis pass.  It only corrects the all-or-nothing
    signal choice for a mixed film where black-derived runs cannot calibrate,
    while scene-derived runs form a large and strongly held population.
    """
    if not black or not cuts or primary_profile.calibrated:
        return None

    scene_spans, _scene_signal = spans_from_boundaries(
        duration_s, [], cuts)
    scene_runs = group_spans_into_plays(
        scene_spans,
        separator_max_s,
        min_play_s=0.0,
        max_play_s=float("inf"),
    )
    profiled_scene_runs = _profiled_runs(scene_runs)
    if (
        len(profiled_scene_runs)
        < MIXED_SCENE_FALLBACK_MIN_PROFILED_RUNS
    ):
        return None
    scene_profile = profile_film(profiled_scene_runs)
    if (
        not scene_profile.calibrated
        or scene_profile.modal_share < MIXED_SCENE_FALLBACK_MIN_MODAL_SHARE
    ):
        return None
    return scene_spans, scene_runs, scene_profile


def classify_runs(runs: list[DetectedPlay], profile: FilmProfile
                  ) -> tuple[list[DetectedPlay], list[UnclassifiedSegment]]:
    """Split runs into plays and review segments. Nothing is thrown away."""
    floor_ms = int(DEFAULT_MIN_PLAY_S * 1000)
    ceiling_ms = profile.max_play_ms
    plays: list[DetectedPlay] = []
    review: list[UnclassifiedSegment] = []

    for run in runs:
        if floor_ms <= run.duration_ms <= ceiling_ms:
            plays.append(run)
            continue

        pieces = _confident_split(run, profile, floor_ms, ceiling_ms)
        if pieces:
            # A clean multiple of this film's angle pattern, cut on boundaries
            # the detector actually found, with every piece a plausible play.
            # Splitting is not a guess here, so do it rather than ask.
            plays.extend(pieces)
            continue

        pieces, leftovers = _aggressive_split(
            run, profile, floor_ms, ceiling_ms)
        if pieces or leftovers:
            # Long enough that it certainly holds several plays. A rough split
            # into play-sized pieces beats one unusable block - a two-minute
            # clip has to be cut by hand either way, and this at least puts
            # the cuts on real camera changes. Any piece outside the learned
            # play bounds stays visible as unclassified footage. Flagged, not
            # trusted.
            plays.extend(pieces)
            review.extend(leftovers)
            continue

        if run.duration_ms < floor_ms:
            reason = (f"{run.duration_ms / 1000:.1f}s - too short to be a play")
            splits: list[int] = []
        elif profile.angle_count_suspect:
            # The angle count this film reports is too long to be one angle,
            # so splitting on it would cut plays in half. Flag, do not guess.
            splits = []
            reason = (f"{run.duration_ms / 1000:.0f}s - needs splitting by "
                      f"hand (camera changes in this film are unreliable)")
        else:
            splits = _angle_group_splits(run, profile.modal_angles)
            if splits:
                reason = (f"{run.duration_ms / 1000:.0f}s across "
                          f"{run.angle_count} camera segments - about "
                          f"{len(splits) + 1} plays "
                          f"(this film usually shows {profile.modal_angles} "
                          f"angle{'s' if profile.modal_angles != 1 else ''} "
                          f"per play)")
            else:
                reason = (f"{run.duration_ms / 1000:.0f}s - longer than a play "
                          f"can be, with no camera change to split on")
        review.append(UnclassifiedSegment(
            start_ms=run.start_ms, end_ms=run.end_ms,
            angle_count=run.angle_count, reason=reason,
            split_points_ms=splits))
    return plays, review


def _confident_split(run: DetectedPlay, profile: FilmProfile,
                     floor_ms: int, ceiling_ms: int) -> list[DetectedPlay]:
    """Split an over-long run into plays, but only when it is not a guess.

    Every condition here exists because breaking one turns a merged play into
    two half-plays, which is worse than leaving it for review:
      - the film must have described itself confidently
      - its angle count must be trustworthy (see angle_count_suspect)
      - the run must hold a whole number of that film's angle pattern
      - and every resulting piece must itself look like a play
    """
    if not profile.calibrated or profile.angle_count_suspect:
        return []
    if profile.modal_share < 0.8 or profile.modal_angles < 1:
        return []
    if run.angle_count < profile.modal_angles * 2:
        return []
    if run.angle_count % profile.modal_angles:
        return []
    if len(run.angle_starts) < run.angle_count:
        return []

    bounds = run.angle_starts + [run.end_ms]
    pieces: list[DetectedPlay] = []
    for i in range(0, run.angle_count, profile.modal_angles):
        start = bounds[i]
        end = bounds[min(i + profile.modal_angles, run.angle_count)]
        if not floor_ms <= (end - start) <= ceiling_ms:
            return []
        pieces.append(DetectedPlay(
            start_ms=start, end_ms=end, angle_count=profile.modal_angles,
            angle_starts=bounds[i:i + profile.modal_angles]))
    return pieces


def _aggressive_split(run: DetectedPlay, profile: FilmProfile,
                      floor_ms: int, ceiling_ms: int
                      ) -> tuple[list[DetectedPlay],
                                 list[UnclassifiedSegment]]:
    """Cut a clearly-multi-play run into play-sized pieces.

    _confident_split only fires when the run is a whole number of the film's
    angle pattern. That leaves the worst cases untouched: a 157-second run of
    7 angles, or Pittsburgh's four-minute blocks. Those are certainly several
    plays, and handing back one enormous clip is the least useful answer -
    it has to be cut by hand either way.

    So above a clear threshold we group angles greedily towards the film's
    own median play length, cutting on camera changes the detector actually
    found rather than on arbitrary times. Every piece is flagged for review:
    this is a rough answer offered in place of a useless one, not a claim to
    have got it right. A final remainder outside the learned play bounds is
    kept as unclassified footage. It must never be emitted as a play.
    """
    if not profile.calibrated or not profile.median_play_ms:
        return [], []
    # Only for runs that cannot be one play by any reading.
    if run.duration_ms < max(ceiling_ms * 2, profile.median_play_ms * 3):
        return [], []
    if run.angle_count < 2 or len(run.angle_starts) < run.angle_count:
        return [], []

    target = profile.median_play_ms
    bounds = run.angle_starts + [run.end_ms]
    partitions: list[tuple[int, int, list[int]]] = []
    start_index = 0
    for index in range(1, run.angle_count + 1):
        span = bounds[index] - bounds[start_index]
        last = index == run.angle_count
        # Close the piece once adding another angle would overshoot the
        # typical play by more than it undershoots now.
        next_span = (bounds[index + 1] - bounds[start_index]
                     if index < run.angle_count else None)
        overshoots = next_span is not None and (
            abs(next_span - target) > abs(span - target)
            or next_span > ceiling_ms)
        if last or (span >= floor_ms and overshoots):
            partitions.append((
                bounds[start_index],
                bounds[index],
                bounds[start_index:index],
            ))
            start_index = index
    # A single partition means nothing was actually divided.
    if len(partitions) <= 1:
        return [], []

    pieces: list[DetectedPlay] = []
    leftovers: list[UnclassifiedSegment] = []
    for start_ms, end_ms, angle_starts in partitions:
        duration_ms = end_ms - start_ms
        if floor_ms <= duration_ms <= ceiling_ms:
            pieces.append(DetectedPlay(
                start_ms=start_ms,
                end_ms=end_ms,
                angle_count=len(angle_starts),
                angle_starts=angle_starts,
                needs_review=True,
                review_reason=(
                    f"cut from a {run.duration_ms / 1000:.0f}s block that held "
                    f"several plays - check the boundaries")))
            continue

        if duration_ms < floor_ms:
            reason = (
                f"{duration_ms / 1000:.1f}s remainder from a "
                f"{run.duration_ms / 1000:.0f}s block - too short to be a play")
        else:
            reason = (
                f"{duration_ms / 1000:.0f}s remainder from a "
                f"{run.duration_ms / 1000:.0f}s block - longer than the "
                f"{ceiling_ms / 1000:.0f}s play ceiling")
        leftovers.append(UnclassifiedSegment(
            start_ms=start_ms,
            end_ms=end_ms,
            angle_count=max(1, len(angle_starts)),
            reason=reason,
            split_points_ms=angle_starts[1:],
        ))
    return pieces, leftovers


def _angle_group_splits(run: DetectedPlay, modal_angles: int) -> list[int]:
    """Where to cut a run that holds several plays' worth of camera angles."""
    if modal_angles < 1 or run.angle_count < modal_angles * 2:
        return []
    if len(run.angle_starts) < run.angle_count:
        return []
    return [run.angle_starts[i]
            for i in range(modal_angles, run.angle_count, modal_angles)]


def _black_pair_gap_kind(duration_ms: int) -> str:
    if BLACK_PAIR_SHORT_MIN_MS <= duration_ms <= BLACK_PAIR_SHORT_MAX_MS:
        return "short"
    if BLACK_PAIR_LONG_MIN_MS <= duration_ms <= BLACK_PAIR_LONG_MAX_MS:
        return "long"
    return "ambiguous"


def _black_endpoints_have_scene_support(
    black_ms: list[tuple[int, int]],
    cuts_ms: list[int],
) -> bool:
    """Require scene evidence for every black edge and very little else."""
    if not black_ms or not cuts_ms:
        return False
    endpoints = [point for interval in black_ms for point in interval]
    if any(
        not any(
            abs(cut_ms - endpoint_ms) <= BLACK_PAIR_ENDPOINT_TOLERANCE_MS
            for cut_ms in cuts_ms
        )
        for endpoint_ms in endpoints
    ):
        return False
    unmatched_cuts = sum(
        1
        for cut_ms in cuts_ms
        if not any(
            abs(cut_ms - endpoint_ms) <= BLACK_PAIR_ENDPOINT_TOLERANCE_MS
            for endpoint_ms in endpoints
        )
    )
    return unmatched_cuts <= max(
        2,
        int(len(endpoints) * BLACK_PAIR_MAX_EXTRA_SCENE_SHARE),
    )


def _recover_bimodal_black_angle_pairs(
    spans: list[tuple[float, float]],
    black: list[tuple[float, float]],
    cuts: list[float],
    *,
    floor_ms: int,
    max_play_ms: int,
    duration_ms: int,
) -> tuple[list[DetectedPlay], list[UnclassifiedSegment]] | None:
    """Pair two angles only when alternating black-gap modes prove the shape.

    This helper intentionally recognizes one narrow topology.  The short
    black gap joins wide and tight views of the same snap; the following long
    black gap separates snaps.  Any ambiguity returns ``None`` rather than a
    partial answer.
    """
    if len(spans) % 2 or len(spans) // 2 < BLACK_PAIR_FALLBACK_MIN_PAIRS:
        return None
    if len(black) != len(spans) - 1:
        return None

    span_ms = [
        (int(start * 1000), int(end * 1000))
        for start, end in spans
    ]
    black_ms = [
        (round(start * 1000), round(end * 1000))
        for start, end in black
    ]
    cuts_ms = sorted({round(cut * 1000) for cut in cuts})
    if any(
        start_ms < 0 or end_ms <= start_ms or end_ms > duration_ms
        for start_ms, end_ms in span_ms
    ):
        return None
    if abs(span_ms[0][0]) > BLACK_PAIR_ENDPOINT_TOLERANCE_MS \
            or abs(span_ms[-1][1] - duration_ms) \
            > BLACK_PAIR_ENDPOINT_TOLERANCE_MS:
        return None
    span_ms[0] = (0, span_ms[0][1])
    span_ms[-1] = (span_ms[-1][0], duration_ms)

    for index, ((left_start, left_end), (right_start, _right_end)) \
            in enumerate(zip(span_ms, span_ms[1:])):
        black_start, black_end = black_ms[index]
        if abs(left_end - black_start) > BLACK_PAIR_ENDPOINT_TOLERANCE_MS:
            return None
        if abs(right_start - black_end) > BLACK_PAIR_ENDPOINT_TOLERANCE_MS:
            return None
        if black_end <= black_start:
            return None

    gap_kinds = [
        _black_pair_gap_kind(end_ms - start_ms)
        for start_ms, end_ms in black_ms
    ]
    if "ambiguous" in gap_kinds:
        return None
    expected = [
        "short" if index % 2 == 0 else "long"
        for index in range(len(gap_kinds))
    ]
    if gap_kinds != expected:
        return None
    short_gaps = [
        end_ms - start_ms
        for (start_ms, end_ms), kind in zip(black_ms, gap_kinds)
        if kind == "short"
    ]
    long_gaps = [
        end_ms - start_ms
        for (start_ms, end_ms), kind in zip(black_ms, gap_kinds)
        if kind == "long"
    ]
    if len(short_gaps) != len(long_gaps) + 1:
        return None
    if len(long_gaps) < BLACK_PAIR_FALLBACK_MIN_PAIRS - 1:
        return None
    median_short = statistics.median(short_gaps)
    median_long = statistics.median(long_gaps)
    if median_long < median_short * BLACK_PAIR_MIN_MODE_RATIO:
        return None
    if median_long - median_short < BLACK_PAIR_MIN_MODE_SEPARATION_MS:
        return None
    if not _black_endpoints_have_scene_support(black_ms, cuts_ms):
        return None

    component_ceiling_ms = int(MAX_SECONDS_PER_ANGLE * 1000)
    pair_ceiling_ms = min(
        max_play_ms,
        component_ceiling_ms * 2,
    )
    recovered: list[DetectedPlay] = []
    remaining: list[UnclassifiedSegment] = []
    pair_count = len(span_ms) // 2
    for pair_index in range(pair_count):
        left = span_ms[pair_index * 2]
        right = span_ms[pair_index * 2 + 1]
        start_ms = left[0]
        angle_start_ms = right[0]
        end_ms = right[1]
        left_ok = floor_ms <= left[1] - left[0] <= component_ceiling_ms
        right_ok = floor_ms <= right[1] - right[0] <= component_ceiling_ms
        pair_ok = floor_ms <= end_ms - start_ms <= pair_ceiling_ms
        if left_ok and right_ok and pair_ok:
            recovered.append(DetectedPlay(
                start_ms=start_ms,
                end_ms=end_ms,
                angle_count=2,
                angle_starts=[start_ms, angle_start_ms],
                needs_review=True,
                review_reason=BLACK_PAIR_REVIEW_REASON,
            ))
            continue

        # The known catastrophic shape may end in a frozen/still tail.  Keep
        # the whole final two-span region visible instead of trimming a guess.
        if pair_index != pair_count - 1 or end_ms - start_ms <= pair_ceiling_ms:
            return None
        remaining.append(UnclassifiedSegment(
            start_ms=start_ms,
            end_ms=end_ms,
            angle_count=2,
            reason=(
                f"{(end_ms - start_ms) / 1000:.0f}s terminal block after "
                "alternating black-gap pairing - split or trim by hand"
            ),
            split_points_ms=[angle_start_ms],
        ))

    if len(recovered) < BLACK_PAIR_FALLBACK_MIN_PAIRS:
        return None
    segments = sorted(
        [(item.start_ms, item.end_ms) for item in recovered]
        + [(item.start_ms, item.end_ms) for item in remaining]
    )
    if any(start_ms < 0 or end_ms <= start_ms or end_ms > duration_ms
           for start_ms, end_ms in segments):
        return None
    if any(
        right_start < left_end
        for (_left_start, left_end), (right_start, _right_end)
        in zip(segments, segments[1:])
    ):
        return None
    return recovered, remaining


def _try_bimodal_black_angle_fallback(
    plays: list[DetectedPlay],
    review: list[UnclassifiedSegment],
    runs: list[DetectedPlay],
    spans: list[tuple[float, float]],
    black: list[tuple[float, float]],
    cuts: list[float],
    profile: FilmProfile,
    *,
    signal: str,
    scene_threshold: float,
    separator_max_s: float,
    min_play_s: float,
    max_play_s: float,
    duration_ms: int,
) -> tuple[list[DetectedPlay], list[UnclassifiedSegment]]:
    """Try the guarded black-gap repair, preserving the primary result.

    This remains review-only.  It does not recalibrate the film or turn its
    own proposals into confidence evidence.
    """
    original_plays = plays
    original_review = review
    try:
        if signal != "mixed":
            return original_plays, original_review
        if scene_threshold != DEFAULT_SCENE_THRESHOLD \
                or separator_max_s != DEFAULT_SEPARATOR_MAX_S \
                or min_play_s != DEFAULT_MIN_PLAY_S \
                or max_play_s != DEFAULT_MAX_PLAY_S:
            return original_plays, original_review
        if plays or len(review) != 1 or len(runs) != 1:
            return original_plays, original_review
        if profile.calibrated or profile.modal_angles != 1 \
                or profile.median_play_ms != 0:
            return original_plays, original_review
        if not black or not cuts:
            return original_plays, original_review
        if any((end - start) < separator_max_s for start, end in spans):
            return original_plays, original_review

        source_run = runs[0]
        source_review = review[0]
        expected_starts = [int(start * 1000) for start, _end in spans]
        expected_end = int(spans[-1][1] * 1000) if spans else 0
        if len(spans) < BLACK_PAIR_FALLBACK_MIN_PAIRS * 2:
            return original_plays, original_review
        if abs(source_run.start_ms) > BLACK_PAIR_ENDPOINT_TOLERANCE_MS \
                or abs(source_run.end_ms - duration_ms) \
                > BLACK_PAIR_ENDPOINT_TOLERANCE_MS:
            return original_plays, original_review
        if source_run.angle_count != len(spans) \
                or source_run.angle_starts != expected_starts \
                or source_run.end_ms != expected_end:
            return original_plays, original_review
        if source_review.start_ms != source_run.start_ms \
                or source_review.end_ms != source_run.end_ms \
                or source_review.angle_count != source_run.angle_count \
                or source_review.split_points_ms != expected_starts[1:]:
            return original_plays, original_review

        candidate = _recover_bimodal_black_angle_pairs(
            spans,
            black,
            cuts,
            floor_ms=int(min_play_s * 1000),
            max_play_ms=int(max_play_s * 1000),
            duration_ms=duration_ms,
        )
        if candidate is None:
            return original_plays, original_review
        recovered, remaining = candidate
    except Exception:
        log.exception(
            "Unexpected bimodal black-gap fallback failure; "
            "keeping primary result")
        return original_plays, original_review

    log.info(
        "Bimodal black-gap fallback: paired %d review play(s); "
        "%d terminal range(s) remain unclassified",
        len(recovered), len(remaining))
    return recovered, remaining


def _scene_gap_kind(duration_ms: int) -> str:
    if SCENE_PAIR_MICRO_MIN_MS <= duration_ms <= SCENE_PAIR_MICRO_MAX_MS:
        return "micro"
    if SCENE_PAIR_CARD_MIN_MS <= duration_ms <= SCENE_PAIR_CARD_MAX_MS:
        return "card"
    return "ambiguous"


def _verified_scene_gap_kind(
    start_ms: int,
    end_ms: int,
    raw_spans_ms: list[tuple[int, int]],
) -> str:
    """Classify a discarded gap only when raw spans account for all of it."""
    duration_ms = end_ms - start_ms
    kind = _scene_gap_kind(duration_ms)
    if duration_ms <= 0 or kind == "ambiguous":
        return "ambiguous"
    inside = sorted(
        (raw_start, raw_end)
        for raw_start, raw_end in raw_spans_ms
        if raw_start >= start_ms - SCENE_PAIR_SPAN_TOLERANCE_MS
        and raw_end <= end_ms + SCENE_PAIR_SPAN_TOLERANCE_MS
        and raw_end > start_ms
        and raw_start < end_ms
    )
    if not inside:
        return "ambiguous"
    cursor = start_ms
    for raw_start, raw_end in inside:
        if abs(raw_start - cursor) > SCENE_PAIR_SPAN_TOLERANCE_MS:
            return "ambiguous"
        if raw_end <= raw_start \
                or raw_end - raw_start >= DEFAULT_SEPARATOR_MAX_S * 1000:
            return "ambiguous"
        cursor = raw_end
    if abs(cursor - end_ms) > SCENE_PAIR_SPAN_TOLERANCE_MS:
        return "ambiguous"
    # A micro transition may be one scene span or two brief graphic flashes.
    # Anything more complicated remains ambiguous. A long between-play card
    # may contain several flashes, but it is only used as an outer boundary
    # and is never swallowed.
    if kind == "micro":
        if len(inside) > 2:
            return "ambiguous"
        if len(inside) == 2 and any(
                raw_end - raw_start > SCENE_PAIR_MICRO_COMPONENT_MAX_MS
                for raw_start, raw_end in inside):
            return "ambiguous"
    return kind


def _mixed_scene_micro_gap_is_verified(
    start_ms: int,
    end_ms: int,
    raw_spans_ms: list[tuple[int, int]],
) -> bool:
    """Verify the mixed-fallback camera transition without widening it.

    The normal case is one raw half-second span.  One frozen, human-reviewed
    Miami transition is split into a large + one-frame + large topology by
    FFmpeg (201 ms + 33 ms + 267 ms).  Accept that representation only when
    the pieces cover the same strict 450-550 ms gap exactly, the middle flash
    is at most 50 ms, and both outer pieces are 150-300 ms.  Two-part,
    equal-part, and four-plus-part transitions retain the previous abstention.
    """
    gap_ms = end_ms - start_ms
    if not MIXED_SCENE_PAIR_MIN_GAP_MS <= gap_ms <= \
            MIXED_SCENE_PAIR_MAX_GAP_MS:
        return False

    inside = [
        (raw_start, raw_end)
        for raw_start, raw_end in raw_spans_ms
        if raw_start >= start_ms - SCENE_PAIR_SPAN_TOLERANCE_MS
        and raw_end <= end_ms + SCENE_PAIR_SPAN_TOLERANCE_MS
        and raw_end > start_ms
        and raw_start < end_ms
    ]
    if len(inside) == 1:
        raw_start, raw_end = inside[0]
        return (
            abs(raw_start - start_ms) <= SCENE_PAIR_SPAN_TOLERANCE_MS
            and abs(raw_end - end_ms) <= SCENE_PAIR_SPAN_TOLERANCE_MS
        )
    if len(inside) != MIXED_SCENE_PAIR_FRAGMENTED_MICRO_PARTS:
        return False

    durations_ms = [
        raw_end - raw_start for raw_start, raw_end in inside
    ]
    if not (
        MIXED_SCENE_PAIR_FRAGMENT_OUTER_MIN_MS
        <= durations_ms[0]
        <= MIXED_SCENE_PAIR_FRAGMENT_OUTER_MAX_MS
        and 0
        < durations_ms[1]
        <= MIXED_SCENE_PAIR_FRAGMENT_MIDDLE_MAX_MS
        and MIXED_SCENE_PAIR_FRAGMENT_OUTER_MIN_MS
        <= durations_ms[2]
        <= MIXED_SCENE_PAIR_FRAGMENT_OUTER_MAX_MS
    ):
        return False

    cursor = start_ms
    for raw_start, raw_end in inside:
        if abs(raw_start - cursor) > SCENE_PAIR_SPAN_TOLERANCE_MS:
            return False
        duration_ms = raw_end - raw_start
        if duration_ms <= 0:
            return False
        cursor = raw_end
    return abs(cursor - end_ms) <= SCENE_PAIR_SPAN_TOLERANCE_MS


def _pair_mixed_scene_fallback_angles(
    plays: list[DetectedPlay],
    spans: list[tuple[float, float]],
    profile: FilmProfile,
    *,
    pair_ceiling_ms: int,
) -> tuple[list[DetectedPlay], int]:
    """Join repeated half-second two-angle splits in mixed-signal fallback.

    Every input must still carry the fallback's review provenance.  A
    candidate gap must be a verified raw scene span, isolated by longer cards,
    and the two components must combine to approximately one typical play.
    Weak population evidence or malformed geometry returns the exact input.
    """
    if len(plays) < MIXED_SCENE_FALLBACK_MIN_PROFILED_RUNS:
        return plays, 0
    if not profile.calibrated or profile.modal_angles != 1:
        return plays, 0
    if profile.modal_share < MIXED_SCENE_FALLBACK_MIN_MODAL_SHARE:
        return plays, 0
    if not (
        MIXED_SCENE_PAIR_MIN_MEDIAN_MS
        <= profile.median_play_ms
        <= MIXED_SCENE_PAIR_MAX_MEDIAN_MS
    ):
        return plays, 0
    if not profile.angle_count_suspect or pair_ceiling_ms <= 0:
        return plays, 0
    if any(
        not play.needs_review
        or play.review_reason != MIXED_SCENE_FALLBACK_REVIEW_REASON
        for play in plays
    ):
        return plays, 0

    raw_spans_ms = [
        (int(start * 1000), int(end * 1000))
        for start, end in spans
        if end > start
    ]
    gaps: list[tuple[int, str]] = []
    for left, right in zip(plays, plays[1:]):
        if right.start_ms <= left.start_ms or right.start_ms < left.end_ms:
            return plays, 0
        gap_ms = right.start_ms - left.end_ms
        if gap_ms == 0:
            gaps.append((gap_ms, "cut"))
            continue
        kind = _verified_scene_gap_kind(
            left.end_ms, right.start_ms, raw_spans_ms)
        if _mixed_scene_micro_gap_is_verified(
                left.end_ms, right.start_ms, raw_spans_ms):
            kind = "micro"
        elif kind == "micro":
            # Preserve the previous mixed-fallback rule for every other
            # representation, including two-part and 4+ part transitions.
            kind = "ambiguous"
        gaps.append((gap_ms, kind))

    micro_indices = [
        index for index, (_duration, kind) in enumerate(gaps)
        if kind == "micro"
    ]
    if len(micro_indices) < MIXED_SCENE_PAIR_MIN_PAIRS:
        return plays, 0

    lower_ms = round(
        profile.median_play_ms * MIXED_SCENE_PAIR_MIN_DURATION_FACTOR)
    upper_ms = min(
        pair_ceiling_ms,
        round(
            profile.median_play_ms
            * MIXED_SCENE_PAIR_MAX_DURATION_FACTOR
        ),
    )
    component_ceiling_ms = round(
        profile.median_play_ms * MIXED_SCENE_PAIR_MAX_COMPONENT_FACTOR)
    component_floor_ms = max(
        round(
            profile.median_play_ms
            * MIXED_SCENE_PAIR_MIN_COMPONENT_FACTOR
        ),
        round(DEFAULT_MIN_PLAY_S * 1000),
    )
    if (
        lower_ms <= 0
        or upper_ms < lower_ms
        or component_ceiling_ms < component_floor_ms
    ):
        return plays, 0

    eligible: list[int] = []
    for left_index in micro_indices:
        left_is_boundary = (
            left_index == 0
            or gaps[left_index - 1][1] == "cut"
            or (
                gaps[left_index - 1][1] == "card"
                and MIXED_SCENE_PAIR_OUTER_CARD_MIN_MS
                <= gaps[left_index - 1][0]
                <= MIXED_SCENE_PAIR_OUTER_CARD_MAX_MS
            )
        )
        right_is_boundary = (
            left_index == len(gaps) - 1
            or gaps[left_index + 1][1] == "cut"
            or (
                gaps[left_index + 1][1] == "card"
                and MIXED_SCENE_PAIR_OUTER_CARD_MIN_MS
                <= gaps[left_index + 1][0]
                <= MIXED_SCENE_PAIR_OUTER_CARD_MAX_MS
            )
        )
        if not left_is_boundary or not right_is_boundary:
            continue
        left = plays[left_index]
        right = plays[left_index + 1]
        if left.angle_count != 1 or right.angle_count != 1:
            continue
        if len(left.angle_starts) != 1 or len(right.angle_starts) != 1:
            continue
        if abs(left.angle_starts[0] - left.start_ms) \
                > SCENE_PAIR_SPAN_TOLERANCE_MS:
            continue
        if abs(right.angle_starts[0] - right.start_ms) \
                > SCENE_PAIR_SPAN_TOLERANCE_MS:
            continue
        if not component_floor_ms <= left.duration_ms <= component_ceiling_ms:
            continue
        if not component_floor_ms <= right.duration_ms <= component_ceiling_ms:
            continue
        combined_ms = right.end_ms - left.start_ms
        if not lower_ms <= combined_ms <= upper_ms:
            continue
        eligible.append(left_index)

    if len(eligible) < MIXED_SCENE_PAIR_MIN_PAIRS:
        return plays, 0
    if (
        len(eligible) / len(micro_indices)
        < MIXED_SCENE_PAIR_MIN_ELIGIBLE_SHARE
    ):
        return plays, 0
    input_share = len(eligible) / len(plays)
    if not (
        MIXED_SCENE_PAIR_MIN_INPUT_SHARE
        <= input_share
        <= MIXED_SCENE_PAIR_MAX_INPUT_SHARE
    ):
        return plays, 0
    if any(right == left + 1 for left, right in zip(eligible, eligible[1:])):
        return plays, 0

    pair_left = set(eligible)
    repaired: list[DetectedPlay] = []
    index = 0
    while index < len(plays):
        if index not in pair_left:
            repaired.append(plays[index])
            index += 1
            continue
        left = plays[index]
        right = plays[index + 1]
        repaired.append(DetectedPlay(
            start_ms=left.start_ms,
            end_ms=right.end_ms,
            angle_count=2,
            angle_starts=[left.start_ms, right.start_ms],
            needs_review=True,
            review_reason=MIXED_SCENE_PAIR_REVIEW_REASON,
        ))
        index += 2
    return repaired, len(eligible)


def _pair_bimodal_scene_angles(
    plays: list[DetectedPlay],
    spans: list[tuple[float, float]],
    profile: FilmProfile,
    *,
    pair_ceiling_ms: int,
) -> tuple[list[DetectedPlay], int]:
    """Pair isolated one-angle plays across a strongly bimodal micro card.

    This helper is pure. It never changes an input play, profile, span, or
    angle-start list. Any weak or malformed topology makes it return the
    original list and a zero pair count.
    """
    if len(plays) < SCENE_PAIR_MIN_PLAYS:
        return plays, 0
    if not profile.calibrated or profile.modal_angles != 1:
        return plays, 0
    if profile.modal_share < SCENE_PAIR_MIN_MODAL_SHARE:
        return plays, 0
    if not 0 < profile.median_play_ms <= SCENE_PAIR_MAX_MEDIAN_PLAY_MS:
        return plays, 0
    if pair_ceiling_ms <= 0:
        return plays, 0

    # A pairable gap must be one raw scene span. Several discarded spans or
    # an existing zero-gap split are locally ambiguous and remain untouched;
    # they do not invalidate independently proven pairs elsewhere in a long
    # film.
    raw_spans_ms = [
        (int(start * 1000), int(end * 1000))
        for start, end in spans
        if end > start
    ]
    gaps: list[tuple[int, str]] = []
    for left, right in zip(plays, plays[1:]):
        if right.start_ms <= left.start_ms or right.start_ms < left.end_ms:
            return plays, 0
        gap_ms = right.start_ms - left.end_ms
        if gap_ms == 0:
            # classify_runs may split a long source run on an exact hard cut.
            # It is safe as an outer boundary, but never as a swallowed gap.
            gaps.append((gap_ms, "cut"))
            continue
        gaps.append((
            gap_ms,
            _verified_scene_gap_kind(
                left.end_ms, right.start_ms, raw_spans_ms),
        ))

    micro_indices = [
        index for index, (_duration, kind) in enumerate(gaps)
        if kind == "micro"
    ]
    card_durations = [
        duration for duration, kind in gaps if kind == "card"
    ]
    micro_durations = [
        duration for duration, kind in gaps if kind == "micro"
    ]
    if len(micro_durations) < SCENE_PAIR_MIN_GAPS_PER_MODE \
            or len(card_durations) < SCENE_PAIR_MIN_GAPS_PER_MODE:
        return plays, 0

    median_micro = statistics.median(micro_durations)
    median_card = statistics.median(card_durations)
    if median_card < median_micro * SCENE_PAIR_MIN_GAP_RATIO:
        return plays, 0
    if median_card - median_micro < SCENE_PAIR_MIN_MODE_SEPARATION_MS:
        return plays, 0

    isolated: list[int] = []
    for gap_index in micro_indices:
        left_is_boundary = (
            gap_index == 0
            or gaps[gap_index - 1][1] in {"card", "cut"})
        right_is_boundary = (
            gap_index == len(gaps) - 1
            or gaps[gap_index + 1][1] in {"card", "cut"})
        if left_is_boundary and right_is_boundary:
            isolated.append(gap_index)
    if len(isolated) / len(micro_indices) < SCENE_PAIR_MIN_ISOLATED_SHARE:
        return plays, 0

    eligible: list[int] = []
    for left_index in isolated:
        left = plays[left_index]
        right = plays[left_index + 1]
        if left.needs_review or right.needs_review:
            continue
        if left.angle_count != 1 or right.angle_count != 1:
            continue
        if len(left.angle_starts) != 1 or len(right.angle_starts) != 1:
            continue
        if abs(left.angle_starts[0] - left.start_ms) \
                > SCENE_PAIR_SPAN_TOLERANCE_MS:
            continue
        if abs(right.angle_starts[0] - right.start_ms) \
                > SCENE_PAIR_SPAN_TOLERANCE_MS:
            continue
        if right.end_ms - left.start_ms > pair_ceiling_ms:
            continue
        eligible.append(left_index)

    if len(eligible) < SCENE_PAIR_MIN_GAPS_PER_MODE:
        return plays, 0
    # Isolated gaps cannot share a play. Keep the assertion fail-closed in
    # case this rule is changed later.
    if any(right == left + 1 for left, right in zip(eligible, eligible[1:])):
        return plays, 0

    pair_left = set(eligible)
    output_count = len(plays) - len(eligible)
    if len(eligible) / output_count < SCENE_PAIR_MIN_OUTPUT_SHARE:
        return plays, 0

    repaired: list[DetectedPlay] = []
    index = 0
    while index < len(plays):
        if index not in pair_left:
            repaired.append(plays[index])
            index += 1
            continue
        left = plays[index]
        right = plays[index + 1]
        repaired.append(DetectedPlay(
            start_ms=left.start_ms,
            end_ms=right.end_ms,
            angle_count=2,
            angle_starts=[
                left.angle_starts[0],
                right.angle_starts[0],
            ],
            needs_review=True,
            review_reason=SCENE_PAIR_REVIEW_REASON,
        ))
        index += 2
    return repaired, len(eligible)


def _scene_pairing_is_valid(
    original: list[DetectedPlay],
    repaired: list[DetectedPlay],
    pair_count: int,
    *,
    duration_ms: int,
    pair_review_reason: str = SCENE_PAIR_REVIEW_REASON,
    pair_gap_min_ms: int = SCENE_PAIR_MICRO_MIN_MS,
    pair_gap_max_ms: int = SCENE_PAIR_MICRO_MAX_MS,
) -> bool:
    """Prove that repaired output is only a one-to-one or two-to-one map."""
    if pair_count <= 0 or len(repaired) != len(original) - pair_count:
        return False
    if duration_ms <= 0:
        return False

    original_index = 0
    paired_seen = 0
    added_ms = 0
    for output_index, item in enumerate(repaired):
        if not 0 <= item.start_ms < item.end_ms <= duration_ms:
            return False
        if item.angle_count != len(item.angle_starts):
            return False
        if not item.angle_starts or item.angle_starts[0] != item.start_ms:
            return False
        if output_index and item.start_ms < repaired[output_index - 1].end_ms:
            return False
        if original_index >= len(original):
            return False

        source = original[original_index]
        if item is source:
            original_index += 1
            continue
        if original_index + 1 >= len(original):
            return False
        second = original[original_index + 1]
        if item.start_ms != source.start_ms or item.end_ms != second.end_ms:
            return False
        if item.angle_count != 2:
            return False
        if item.angle_starts != [source.start_ms, second.start_ms]:
            return False
        if not item.needs_review \
                or item.review_reason != pair_review_reason:
            return False
        gap_ms = second.start_ms - source.end_ms
        if (
            _scene_gap_kind(gap_ms) != "micro"
            or not pair_gap_min_ms <= gap_ms <= pair_gap_max_ms
        ):
            return False
        added_ms += gap_ms
        paired_seen += 1
        original_index += 2

    if original_index != len(original) or paired_seen != pair_count:
        return False
    original_coverage = sum(item.duration_ms for item in original)
    repaired_coverage = sum(item.duration_ms for item in repaired)
    return repaired_coverage == original_coverage + added_ms


def _try_scene_angle_pairing(
    plays: list[DetectedPlay],
    review: list[UnclassifiedSegment],
    spans: list[tuple[float, float]],
    profile: FilmProfile,
    *,
    signal: str,
    scene_threshold: float,
    separator_max_s: float,
    max_play_ms: int,
    duration_ms: int,
) -> tuple[list[DetectedPlay], FilmProfile, int]:
    """Apply the optional scene-angle repair without risking primary output."""
    if signal != "scene":
        return plays, profile, 0
    if scene_threshold != DEFAULT_SCENE_THRESHOLD \
            or separator_max_s != DEFAULT_SEPARATOR_MAX_S:
        return plays, profile, 0
    if review or any(play.needs_review for play in plays):
        return plays, profile, 0
    if not profile.calibrated or profile.modal_angles != 1 \
            or profile.modal_share < SCENE_PAIR_MIN_MODAL_SHARE:
        return plays, profile, 0

    original_plays = plays
    original_profile = profile
    try:
        pair_ceiling_ms = min(
            max_play_ms,
            int(2 * MAX_SECONDS_PER_ANGLE * 1000),
        )
        repaired, pair_count = _pair_bimodal_scene_angles(
            plays,
            spans,
            profile,
            pair_ceiling_ms=pair_ceiling_ms,
        )
        if pair_count == 0:
            return original_plays, original_profile, 0
        if not _scene_pairing_is_valid(
                original_plays, repaired, pair_count,
                duration_ms=duration_ms):
            raise ValueError("scene-angle pairing failed structural validation")

        repaired_profile = profile_film(repaired)
        if not repaired_profile.calibrated \
                or repaired_profile.modal_angles != 2 \
                or repaired_profile.modal_share < SCENE_PAIR_MIN_OUTPUT_SHARE:
            return original_plays, original_profile, 0
        repaired_profile.max_play_ms = min(
            repaired_profile.max_play_ms,
            max_play_ms,
        )
    except Exception:
        log.exception(
            "Unexpected scene-angle pairing failure; keeping primary result")
        return original_plays, original_profile, 0

    log.info(
        "Scene-angle pairing: joined %d two-angle play(s); "
        "%d play(s) remain",
        pair_count, len(repaired))
    return repaired, repaired_profile, pair_count


def _try_mixed_scene_angle_pairing(
    plays: list[DetectedPlay],
    spans: list[tuple[float, float]],
    profile: FilmProfile,
    *,
    signal: str,
    scene_threshold: float,
    separator_max_s: float,
    min_play_ms: int,
    max_play_ms: int,
    duration_ms: int,
) -> tuple[list[DetectedPlay], int]:
    """Apply the mixed-fallback camera-angle repair fail-closed."""
    if signal != "scene":
        return plays, 0
    if scene_threshold != DEFAULT_SCENE_THRESHOLD \
            or separator_max_s != DEFAULT_SEPARATOR_MAX_S:
        return plays, 0
    if min_play_ms != round(DEFAULT_MIN_PLAY_S * 1000):
        return plays, 0

    original_plays = plays
    try:
        pair_ceiling_ms = min(profile.max_play_ms, max_play_ms)
        repaired, pair_count = _pair_mixed_scene_fallback_angles(
            plays,
            spans,
            profile,
            pair_ceiling_ms=pair_ceiling_ms,
        )
        if pair_count == 0:
            return original_plays, 0
        if not _scene_pairing_is_valid(
            original_plays,
            repaired,
            pair_count,
            duration_ms=duration_ms,
            pair_review_reason=MIXED_SCENE_PAIR_REVIEW_REASON,
            pair_gap_min_ms=MIXED_SCENE_PAIR_MIN_GAP_MS,
            pair_gap_max_ms=MIXED_SCENE_PAIR_MAX_GAP_MS,
        ):
            raise ValueError(
                "mixed-scene angle pairing failed structural validation")
    except Exception:
        log.exception(
            "Unexpected mixed-scene angle pairing failure; "
            "keeping fallback result")
        return original_plays, 0

    log.info(
        "Mixed-scene angle pairing: joined %d two-angle play(s); "
        "%d play(s) remain",
        pair_count, len(repaired))
    return repaired, pair_count


def _recover_scene_review_segments(
    review: list[UnclassifiedSegment],
    weak_events: list[tuple[int, float]],
    *,
    floor_ms: int,
    ceiling_ms: int,
    median_play_ms: int,
) -> tuple[list[DetectedPlay], list[UnclassifiedSegment]]:
    """Peel at most one review play from each long unclassified range.

    A global 0.15 scene threshold over-splits film. The safe use of those
    events is much narrower: only footage already rejected by the primary
    pass, only a boundary near this film's learned play duration, and only
    when the scene event has enough evidence. The remainder stays
    unclassified and no existing emitted play is touched.
    """
    recovered: list[DetectedPlay] = []
    remaining: list[UnclassifiedSegment] = []
    if median_play_ms <= 0:
        return [], list(review)

    lower_ms = max(
        floor_ms,
        round(median_play_ms * WEAK_SCENE_MIN_DURATION_FACTOR),
    )
    upper_ms = min(
        ceiling_ms,
        round(median_play_ms * WEAK_SCENE_MAX_DURATION_FACTOR),
    )
    ambiguity_ms = round(
        median_play_ms * WEAK_SCENE_AMBIGUITY_FACTOR)

    for source in review:
        if source.duration_ms <= ceiling_ms:
            remaining.append(source)
            continue
        # Evidence belongs to this review range only. Clustering globally can
        # let a cut just outside the range strengthen a weak event inside it.
        # The total-width check also prevents a chain of nearby events from
        # growing into one broad transition.
        clusters: list[list[tuple[int, float]]] = []
        source_events = sorted(
            event for event in weak_events
            if source.start_ms < event[0] < source.end_ms
        )
        for event in source_events:
            if not clusters \
                    or event[0] - clusters[-1][-1][0] \
                    > WEAK_SCENE_CLUSTER_GAP_MS \
                    or event[0] - clusters[-1][0][0] \
                    > WEAK_SCENE_CLUSTER_GAP_MS:
                clusters.append([event])
            else:
                clusters[-1].append(event)
        scene_clusters = [
            (cluster[0][0], max(score for _, score in cluster), len(cluster))
            for cluster in clusters
        ]

        candidates: list[tuple[int, int]] = []
        for boundary_ms, max_score, event_count in scene_clusters:
            duration_ms = boundary_ms - source.start_ms
            if not lower_ms <= duration_ms <= upper_ms:
                continue
            if source.end_ms - boundary_ms < floor_ms:
                continue
            if max_score < WEAK_SCENE_MIN_SCORE and event_count < 2:
                continue
            candidates.append((
                abs(duration_ms - median_play_ms),
                boundary_ms,
            ))
        candidates.sort()
        if not candidates:
            remaining.append(source)
            continue
        if len(candidates) > 1 \
                and candidates[1][0] - candidates[0][0] <= ambiguity_ms:
            remaining.append(source)
            continue

        boundary_ms = candidates[0][1]
        recovered.append(DetectedPlay(
            start_ms=source.start_ms,
            end_ms=boundary_ms,
            angle_count=1,
            angle_starts=[source.start_ms],
            needs_review=True,
            review_reason=(
                "recovered from an unclassified scene-only range using "
                "weak scene cuts - check the boundaries"),
        ))
        remaining.append(UnclassifiedSegment(
            start_ms=boundary_ms,
            end_ms=source.end_ms,
            angle_count=source.angle_count,
            reason=(
                f"{(source.end_ms - boundary_ms) / 1000:.1f}s remains after "
                f"weak scene recovery - {source.reason}"),
            split_points_ms=[
                point for point in source.split_points_ms
                if boundary_ms < point < source.end_ms
            ],
        ))

    return (
        sorted(recovered, key=lambda play: (play.start_ms, play.end_ms)),
        sorted(remaining, key=lambda item: (item.start_ms, item.end_ms)),
    )


def _try_weak_scene_recovery(
    ffmpeg_path: str,
    source: Path,
    review: list[UnclassifiedSegment],
    profile: FilmProfile,
    *,
    scene_threshold: float,
    floor_ms: int,
) -> tuple[list[DetectedPlay], list[UnclassifiedSegment]]:
    """Run the guarded second pass for long scene-only review ranges.

    Failure here must never erase the successful primary result. The caller
    invokes this only for scene-only film, and the helper abstains unless the
    primary profile is calibrated and at least one review range exceeds its
    learned play ceiling.
    """
    if not review or not profile.calibrated or profile.median_play_ms <= 0:
        return [], review
    if scene_threshold <= WEAK_SCENE_THRESHOLD:
        return [], review
    if not any(item.duration_ms > profile.max_play_ms for item in review):
        return [], review
    try:
        weak_stdout, _weak_stderr = _run_ffmpeg_analysis(
            ffmpeg_path, source, WEAK_SCENE_THRESHOLD)
        events = _parse_scene_events(weak_stdout)
        if not events:
            return [], review
        recovered, remaining = _recover_scene_review_segments(
            review,
            events,
            floor_ms=floor_ms,
            ceiling_ms=profile.max_play_ms,
            median_play_ms=profile.median_play_ms,
        )
    except TapeSiftError as exc:
        log.warning(
            "Weak scene recovery failed for %s; keeping primary result: %s",
            source.name, exc)
        return [], review
    except Exception:
        # This is a nonessential beta pass. An unexpected parser or recovery
        # error must not discard the valid primary detector result.
        log.exception(
            "Unexpected weak scene recovery failure for %s; "
            "keeping primary result",
            source.name)
        return [], review
    if recovered:
        log.info(
            "Weak scene recovery: %d review play(s) recovered in %s; "
            "%d range(s) remain unclassified",
            len(recovered), source.name, len(remaining))
    return recovered, remaining


@dataclass
class MergedSuspect:
    """A detected play that looks like it actually contains several."""
    index: int                 # position in DetectionResult.plays
    start_ms: int
    end_ms: int
    angle_count: int
    split_points_ms: list[int]
    reason: str

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


def find_merged_plays(result: "DetectionResult",
                      duration_factor: float = 1.6) -> list[MergedSuspect]:
    """Spot detected clips that probably hold more than one play.

    Duration alone is a weak test - if many clips are long, the median moves
    with them. The stronger signal is structure: film shows each play from a
    consistent number of camera angles, so a clip carrying twice the usual
    number of segments is two plays whose separator was missed. Because the
    detector recorded where each angle began, the split points are known
    exactly rather than guessed.
    """
    plays = result.plays
    if len(plays) < 4:
        return []

    counts = Counter(p.angle_count for p in plays)
    modal_angles = counts.most_common(1)[0][0]
    typical = [p for p in plays if p.angle_count == modal_angles]
    median_typical = statistics.median(
        [p.duration_ms for p in typical]) if typical else 0

    suspects: list[MergedSuspect] = []
    for index, play in enumerate(plays):
        splits: list[int] = []
        reason = ""
        # Structural: extra angle groups mean extra plays.
        if modal_angles >= 1 and play.angle_count >= modal_angles * 2:
            splits = [play.angle_starts[i]
                      for i in range(modal_angles, len(play.angle_starts),
                                     modal_angles)]
            groups = play.angle_count // modal_angles
            reason = (f"{play.angle_count} camera segments - about {groups} "
                      f"plays' worth (usually {modal_angles})")
        # Fallback: unusually long with no usable internal boundary.
        elif median_typical and play.duration_ms > median_typical * duration_factor:
            mid = [s for s in play.angle_starts[1:]
                   if play.start_ms + 4000 < s < play.end_ms - 4000]
            splits = [mid[len(mid) // 2]] if mid else []
            reason = (f"{play.duration_ms / 1000:.0f}s, about "
                      f"{play.duration_ms / median_typical:.1f}x a typical play")
        if splits:
            suspects.append(MergedSuspect(
                index=index, start_ms=play.start_ms, end_ms=play.end_ms,
                angle_count=play.angle_count, split_points_ms=splits,
                reason=reason))
    return suspects


def detect_plays(
    ffmpeg_path: str,
    source: Path,
    duration_ms: int,
    separator_max_s: float = DEFAULT_SEPARATOR_MAX_S,
    min_play_s: float = DEFAULT_MIN_PLAY_S,
    max_play_s: float = DEFAULT_MAX_PLAY_S,
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD,
    process_started: Callable[[subprocess.Popen], None] | None = None,
) -> DetectionResult:
    """Analyse a film and return its plays. Safe to call off the UI thread."""
    if not source.is_file():
        raise TapeSiftError(f"Video not found: {source}",
                             "Relink the source video and try again.")
    stdout, stderr = _run_ffmpeg_analysis(
        ffmpeg_path, source, scene_threshold, process_started)
    black = [(float(a), float(b)) for a, b in _BLACK_RE.findall(stderr)]
    cuts = sorted({round(float(t), 3) for t in _PTS_RE.findall(stdout)})
    duration_s = duration_ms / 1000 if duration_ms else 0.0
    if duration_s <= 0:
        raise TapeSiftError("The video duration is unknown.",
                             "Reload the video, then try again.")

    spans, signal = spans_from_boundaries(duration_s, black, cuts)
    # Pass one forms every run without judging it, so the film can be measured
    # before anything is classified against it.
    runs = group_spans_into_plays(spans, separator_max_s,
                                  min_play_s=0.0, max_play_s=float("inf"))
    profile = profile_film(runs)
    used_mixed_scene_fallback = False
    if not profile.calibrated:
        # Too little structure to learn from; behave exactly as before.
        profile = FilmProfile(modal_angles=profile.modal_angles,
                              modal_share=0.0, median_play_ms=0,
                              max_play_ms=int(max_play_s * 1000),
                              calibrated=False)
    else:
        # The user's "Longest play" stays an absolute cap. Calibration can
        # tighten the ceiling but must never quietly raise it past what the
        # selected maximum duration.
        profile.max_play_ms = min(profile.max_play_ms, int(max_play_s * 1000))
    plays, review = classify_runs(runs, profile)
    plays, review = _try_bimodal_black_angle_fallback(
        plays,
        review,
        runs,
        spans,
        black,
        cuts,
        profile,
        signal=signal,
        scene_threshold=scene_threshold,
        separator_max_s=separator_max_s,
        min_play_s=min_play_s,
        max_play_s=max_play_s,
        duration_ms=duration_ms,
    )
    if not plays and not profile.calibrated:
        mixed_scene_fallback = _try_mixed_scene_fallback(
            duration_s,
            black,
            cuts,
            profile,
            separator_max_s=separator_max_s,
        )
        if mixed_scene_fallback is not None:
            spans, runs, profile = mixed_scene_fallback
            profile.max_play_ms = min(
                profile.max_play_ms, int(max_play_s * 1000))
            plays, review = classify_runs(runs, profile)
            for play in plays:
                play.needs_review = True
                play.review_reason = MIXED_SCENE_FALLBACK_REVIEW_REASON
            signal = "scene"
            used_mixed_scene_fallback = True
            log.info(
                "Mixed-signal scene fallback: %d black interval(s) could not "
                "calibrate the film; using %d scene-derived run(s)",
                len(black), len(runs))
    if signal == "scene" and review and not used_mixed_scene_fallback:
        recovered, review = _try_weak_scene_recovery(
            ffmpeg_path,
            source,
            review,
            profile,
            scene_threshold=scene_threshold,
            floor_ms=int(min_play_s * 1000),
        )
        if recovered:
            plays.extend(recovered)
            plays.sort(key=lambda play: (play.start_ms, play.end_ms))
    if signal == "scene" and used_mixed_scene_fallback:
        plays, _paired_count = _try_mixed_scene_angle_pairing(
            plays,
            spans,
            profile,
            signal=signal,
            scene_threshold=scene_threshold,
            separator_max_s=separator_max_s,
            min_play_ms=int(min_play_s * 1000),
            max_play_ms=int(max_play_s * 1000),
            duration_ms=duration_ms,
        )
    elif signal == "scene":
        plays, profile, _paired_count = _try_scene_angle_pairing(
            plays,
            review,
            spans,
            profile,
            signal=signal,
            scene_threshold=scene_threshold,
            separator_max_s=separator_max_s,
            max_play_ms=int(max_play_s * 1000),
            duration_ms=duration_ms,
        )

    separators = sum(1 for s, e in spans if (e - s) < separator_max_s)
    coverage_segments = build_coverage_ledger(
        duration_ms, plays, review, black)
    coverage_totals = {
        kind: sum(
            segment.duration_ms
            for segment in coverage_segments
            if segment.kind == kind
        )
        for kind in COVERAGE_KINDS
    }
    log.info("Detected %d plays in %s (signal=%s, spans=%d, review=%d, "
             "possible_missed=%.1fs, modal_angles=%d, ceiling=%.0fs, "
             "calibrated=%s)",
             len(plays), source.name, signal, len(spans), len(review),
             coverage_totals[COVERAGE_POSSIBLE_MISSED] / 1000,
             profile.modal_angles, profile.max_play_ms / 1000,
             profile.calibrated)
    if profile.angle_count_suspect:
        log.warning("%s: %.1fs per camera angle - the angle count (%d) is "
                    "probably too low; plays may be over-split",
                    source.name, profile.seconds_per_angle,
                    profile.modal_angles)
    return DetectionResult(plays=plays, signal=signal, spans_found=len(spans),
                           separators_found=separators, duration_ms=duration_ms,
                           unclassified=review, profile=profile,
                           coverage_segments=coverage_segments)
