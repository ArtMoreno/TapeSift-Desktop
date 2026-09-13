"""Scale-resistant temporal features for explainable run/pass research.

Temporal v2 treats a confirmed clip as two camera views of the same snap.
FFmpeg motion traces locate the quiet replay transition between those views,
then each view receives an independent sustained-motion onset. Classifier
features describe only the shape of four seconds after those onsets; raw
motion scale, timing confidence, and clip geometry stay in diagnostics.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from tapesift.research.run_pass_features import (
    DEFAULT_SAMPLE_WIDTH,
    SignalFrame,
    read_signalstats_frames,
    reject_quarantined_input,
)
from tapesift.services import ffmpeg_service


TEMPORAL_SCHEMA_VERSION = "2.1"
TEMPORAL_EXTRACTOR_NAME = "ffmpeg-temporal-cadence"
TEMPORAL_EXTRACTOR_VERSION = "3"
TEMPORAL_SAMPLE_FPS = 8.0
MIN_ANGLE_SECONDS = 6.0
CUT_MIDPOINT_MIN_FRACTION = 0.30
CUT_MIDPOINT_MAX_FRACTION = 0.65
CUT_PULSE_MIN_SEPARATION_SECONDS = 0.375
CUT_PULSE_MAX_SEPARATION_SECONDS = 0.75
CUT_CLUSTER_SECONDS = 1.25
CUT_MIN_PULSE_PERCENTILE = 0.90
CUT_MAX_VALLEY_PERCENTILE = 0.75
CUT_MIN_RAW_CONTRAST = 1.8
ONSET_PRE_START_SECONDS = 1.75
ONSET_PRE_END_SECONDS = 0.25
ONSET_EARLY_START_SECONDS = 0.25
ONSET_EARLY_END_SECONDS = 1.75
ONSET_LATE_START_SECONDS = 1.75
ONSET_LATE_END_SECONDS = 3.75
POST_WINDOW_START_SECONDS = 0.25
POST_WINDOW_END_SECONDS = 4.0
MIN_ONSET_SCORE = 0.18
MIN_ONSET_PROMINENCE = 0.025
MIN_ONSET_POST_PRE_RATIO = 2.0
MIN_ONSET_AFFINE_STRONG_SCORE = 0.45
MIN_ONSET_CONFIDENCE = 0.25

_ANGLE_FEATURES = (
    "early_excess_share",
    "middle_excess_share",
    "late_excess_share",
    "motion_centroid",
    "post_above_pre_p90_share",
)
TEMPORAL_CLASSIFIER_FEATURES = tuple(
    f"temporal_{name}" for name in _ANGLE_FEATURES
)


@dataclass(frozen=True)
class AngleRange:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class MotionOnset:
    time_s: float
    confidence: float
    method: str
    sustained_rise_score: float = 0.0
    prominence: float = 0.0
    post_pre_ratio: float = 0.0
    eligible: bool = False
    reason: str = ""


@dataclass(frozen=True)
class CutTransition:
    left_pulse_s: float
    right_pulse_s: float
    midpoint_s: float
    raw_contrast: float
    normalized_drop: float
    min_pulse_percentile: float
    candidate_count: int
    cluster_count: int


@dataclass(frozen=True)
class AngleResolution:
    """Angle split plus the reasons it may not enter a classifier."""

    ranges: tuple[AngleRange, ...]
    source: str
    confidence: float
    transition: CutTransition | None
    cse_internal_starts_s: tuple[float, ...]
    classifier_eligible: bool
    abstain_reasons: tuple[str, ...]

    def __iter__(self):
        """Keep the former three-value unpacking API for small callers."""
        yield list(self.ranges)
        yield self.source
        yield self.confidence


def _rounded(value: float) -> float:
    return round(float(value), 6)


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return statistics.fmean(items) if items else 0.0


def _percentile(values: Iterable[float], fraction: float) -> float:
    items = sorted(values)
    if not items:
        return 0.0
    position = (len(items) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return items[low]
    weight = position - low
    return items[low] + (items[high] - items[low]) * weight


def _smooth(values: list[float], radius: int = 1) -> list[float]:
    return [
        _mean(values[max(0, index - radius):index + radius + 1])
        for index in range(len(values))
    ]


def _sample_interval(frames: list[SignalFrame]) -> float:
    deltas = [
        current.time_s - previous.time_s
        for previous, current in zip(frames, frames[1:])
        if current.time_s > previous.time_s
    ]
    return statistics.median(deltas) if deltas else 1.0 / TEMPORAL_SAMPLE_FPS


def _combined_motion(frame: SignalFrame) -> float:
    return frame.ydif + frame.udif + frame.vdif


def _window_values(
        frames: list[SignalFrame],
        values: list[float],
        start_s: float,
        end_s: float,
) -> list[float]:
    return [
        value for frame, value in zip(frames, values)
        if start_s <= frame.time_s < end_s
    ]


def _value_percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    return sum(item <= value for item in values) / len(values)


def _paired_transition(
        frames: list[SignalFrame],
        duration_s: float,
) -> tuple[CutTransition | None, str]:
    """Find one central pulse-quiet-pulse replay transition."""
    if len(frames) < 16:
        return None, "insufficient_transition_samples"
    motion = [_combined_motion(frame) for frame in frames]
    p10 = _percentile(motion, 0.10)
    p20 = _percentile(motion, 0.20)
    p90 = _percentile(motion, 0.90)
    scale = p90 - p10
    if scale <= 0.000001:
        return None, "flat_transition_trace"

    candidates: list[dict[str, float]] = []
    for left_index, left_frame in enumerate(frames):
        if left_frame.time_s < MIN_ANGLE_SECONDS:
            continue
        for right_index in range(left_index + 1, len(frames)):
            right_frame = frames[right_index]
            separation = right_frame.time_s - left_frame.time_s
            if separation < CUT_PULSE_MIN_SEPARATION_SECONDS:
                continue
            if separation > CUT_PULSE_MAX_SEPARATION_SECONDS:
                break
            midpoint = (left_frame.time_s + right_frame.time_s) / 2.0
            fraction = midpoint / duration_s
            if not (
                    CUT_MIDPOINT_MIN_FRACTION
                    <= fraction
                    <= CUT_MIDPOINT_MAX_FRACTION):
                continue
            if duration_s - right_frame.time_s < MIN_ANGLE_SECONDS:
                continue
            interior = motion[left_index + 1:right_index]
            if not interior:
                continue
            left_motion = motion[left_index]
            right_motion = motion[right_index]
            min_pulse = min(left_motion, right_motion)
            min_percentile = min(
                _value_percentile(motion, left_motion),
                _value_percentile(motion, right_motion),
            )
            valley = max(interior)
            valley_percentile = _value_percentile(motion, valley)
            raw_contrast = min_pulse / max(valley, 0.000001)
            normalized_drop = (min_pulse - valley) / max(
                p90 - p20, 0.000001)
            if min_percentile < CUT_MIN_PULSE_PERCENTILE:
                continue
            if valley_percentile > CUT_MAX_VALLEY_PERCENTILE:
                continue
            if raw_contrast < CUT_MIN_RAW_CONTRAST:
                continue
            candidates.append({
                "left_s": left_frame.time_s,
                "right_s": right_frame.time_s,
                "midpoint_s": midpoint,
                "raw_contrast": raw_contrast,
                "normalized_drop": normalized_drop,
                "min_percentile": min_percentile,
            })
    if not candidates:
        return None, "no_paired_transition"

    clusters: list[list[dict[str, float]]] = []
    for candidate in sorted(
            candidates, key=lambda item: item["midpoint_s"]):
        if (
                not clusters
                or candidate["midpoint_s"]
                - clusters[-1][-1]["midpoint_s"]
                > CUT_CLUSTER_SECONDS):
            clusters.append([candidate])
        else:
            clusters[-1].append(candidate)
    if len(clusters) != 1:
        return None, "ambiguous_paired_transitions"

    representative = max(
        clusters[0],
        key=lambda item: (
            item["normalized_drop"],
            item["raw_contrast"],
            item["min_percentile"],
        ),
    )
    return CutTransition(
        left_pulse_s=_rounded(representative["left_s"]),
        right_pulse_s=_rounded(representative["right_s"]),
        midpoint_s=_rounded(representative["midpoint_s"]),
        raw_contrast=_rounded(representative["raw_contrast"]),
        normalized_drop=_rounded(representative["normalized_drop"]),
        min_pulse_percentile=_rounded(
            representative["min_percentile"]),
        candidate_count=len(candidates),
        cluster_count=len(clusters),
    ), "paired_transition"


def resolve_angle_ranges(
        frames: list[SignalFrame],
        *,
        clip_start_ms: int,
        clip_end_ms: int,
    angle_starts_ms: Iterable[int] = (),
) -> AngleResolution:
    """Resolve two views without inventing a midpoint when evidence is weak."""
    duration_s = (clip_end_ms - clip_start_ms) / 1000
    if duration_s <= 0:
        raise ValueError("Temporal clip range must have positive duration")
    relative_starts = tuple(sorted({
        (int(value) - clip_start_ms) / 1000
        for value in angle_starts_ms
        if clip_start_ms < int(value) < clip_end_ms
    }))
    transition, transition_status = _paired_transition(
        frames, duration_s)
    if transition is None:
        return AngleResolution(
            ranges=(AngleRange(0.0, duration_s),),
            source=transition_status,
            confidence=0.0,
            transition=None,
            cse_internal_starts_s=relative_starts,
            classifier_eligible=False,
            abstain_reasons=(transition_status,),
        )

    sample_interval = _sample_interval(frames)
    ranges = (
        AngleRange(0.0, transition.left_pulse_s),
        AngleRange(
            transition.right_pulse_s + sample_interval,
            duration_s,
        ),
    )
    reasons: list[str] = []
    source = "paired_transition"
    if len(relative_starts) > 1:
        source = "paired_transition_cse_topology_conflict"
        reasons.append("cse_reports_more_than_two_angles")
    elif len(relative_starts) == 1:
        if abs(relative_starts[0] - transition.midpoint_s) \
                <= CUT_CLUSTER_SECONDS:
            source = "paired_transition_cse_agree"
        else:
            source = "paired_transition_cse_conflict"
            reasons.append("cse_transition_disagreement")

    confidence = min(
        1.0,
        0.55
        + min(0.25, max(0.0, transition.raw_contrast - 1.8) / 8.0)
        + min(
            0.20,
            max(
                0.0,
                transition.min_pulse_percentile
                - CUT_MIN_PULSE_PERCENTILE,
            ) * 2.0,
        ),
    )
    if reasons:
        confidence = min(confidence, 0.40)
    return AngleResolution(
        ranges=ranges,
        source=source,
        confidence=_rounded(confidence),
        transition=transition,
        cse_internal_starts_s=relative_starts,
        classifier_eligible=not reasons,
        abstain_reasons=tuple(reasons),
    )


def _slice_angle(
        frames: list[SignalFrame], angle: AngleRange) -> list[SignalFrame]:
    return [
        SignalFrame(
            time_s=frame.time_s - angle.start_s,
            yavg=frame.yavg,
            satavg=frame.satavg,
            ydif=frame.ydif,
            udif=frame.udif,
            vdif=frame.vdif,
            scene_score=frame.scene_score,
        )
        for frame in frames
        if angle.start_s <= frame.time_s < angle.end_s
    ]


def detect_motion_onset(frames: list[SignalFrame]) -> MotionOnset:
    """Locate a calm-to-sustained-motion transition in one camera view."""
    if len(frames) < 32:
        return MotionOnset(
            0.0, 0.0, "insufficient_frames",
            reason="insufficient_frames")
    interval = _sample_interval(frames)
    radius = max(1, round(0.5 / max(interval, 0.001)))
    smoothed = _smooth(
        [_combined_motion(frame) for frame in frames],
        radius=radius,
    )
    scale = _percentile(smoothed, 0.90) - _percentile(smoothed, 0.10)
    if scale <= 0.000001:
        return MotionOnset(
            0.0, 0.0, "flat_motion_trace",
            reason="flat_motion_trace")

    scored: list[tuple[float, float, float]] = []
    for frame in frames:
        time_s = frame.time_s
        if time_s < ONSET_PRE_START_SECONDS:
            continue
        if time_s + max(
                ONSET_LATE_END_SECONDS,
                POST_WINDOW_END_SECONDS,
        ) \
                > frames[-1].time_s + interval:
            continue
        pre = _window_values(
            frames,
            smoothed,
            time_s - ONSET_PRE_START_SECONDS,
            time_s - ONSET_PRE_END_SECONDS,
        )
        early = _window_values(
            frames,
            smoothed,
            time_s + ONSET_EARLY_START_SECONDS,
            time_s + ONSET_EARLY_END_SECONDS,
        )
        late = _window_values(
            frames,
            smoothed,
            time_s + ONSET_LATE_START_SECONDS,
            time_s + ONSET_LATE_END_SECONDS,
        )
        if min(len(pre), len(early), len(late)) < 4:
            continue
        pre_median = statistics.median(pre)
        sustained_post = min(
            statistics.median(early),
            statistics.median(late),
        )
        score = (sustained_post - pre_median) / scale
        ratio = sustained_post / max(pre_median, 0.000001)
        scored.append((score, time_s, ratio))
    if not scored:
        return MotionOnset(
            0.0, 0.0, "no_onset_candidate",
            reason="no_onset_candidate")

    best_score, best_time, best_ratio = max(
        scored, key=lambda item: (item[0], -item[1]))
    competitors = [
        score for score, time_s, _ratio in scored
        if abs(time_s - best_time) >= POST_WINDOW_END_SECONDS
    ]
    second_score = max(competitors) if competitors else 0.0
    prominence = best_score - second_score
    strength_confidence = min(
        1.0, max(0.0, (best_score - 0.10) / 0.55))
    prominence_confidence = min(
        1.0, max(0.0, prominence / 0.20))
    confidence = (
        strength_confidence * 0.75
        + prominence_confidence * 0.25
    )
    reasons: list[str] = []
    if best_score < MIN_ONSET_SCORE:
        reasons.append("weak_sustained_motion_rise")
    if prominence < MIN_ONSET_PROMINENCE:
        reasons.append("ambiguous_motion_onset")
    if (
            best_ratio < MIN_ONSET_POST_PRE_RATIO
            and best_score < MIN_ONSET_AFFINE_STRONG_SCORE):
        reasons.append("weak_post_pre_motion_ratio")
    if confidence < MIN_ONSET_CONFIDENCE:
        reasons.append("low_onset_confidence")
    eligible = not reasons
    return MotionOnset(
        time_s=_rounded(best_time),
        confidence=_rounded(confidence),
        method=(
            "sustained_motion_rise"
            if eligible else "low_confidence_motion_rise"
        ),
        sustained_rise_score=_rounded(best_score),
        prominence=_rounded(prominence),
        post_pre_ratio=_rounded(best_ratio),
        eligible=eligible,
        reason=";".join(reasons),
    )


def summarize_temporal_angle(
        frames: list[SignalFrame],
) -> tuple[dict[str, float], dict[str, Any]]:
    if len(frames) < 32:
        raise ValueError("Temporal angle has too few sampled frames")
    onset = detect_motion_onset(frames)
    motion = [_combined_motion(frame) for frame in frames]
    interval = _sample_interval(frames)
    pre = _window_values(
        frames,
        motion,
        onset.time_s - ONSET_PRE_START_SECONDS,
        onset.time_s - ONSET_PRE_END_SECONDS,
    )
    baseline = statistics.median(pre) if pre else 0.0
    pre_p90 = _percentile(pre, 0.90)
    post_pairs = [
        (frame.time_s - onset.time_s, value)
        for frame, value in zip(frames, motion)
        if (
            POST_WINDOW_START_SECONDS
            <= frame.time_s - onset.time_s
            < POST_WINDOW_END_SECONDS
        )
    ]
    weights = [max(0.0, value - baseline) for _time, value in post_pairs]
    total_excess = sum(weights)
    bins = ((0.25, 1.5), (1.5, 2.75), (2.75, 4.0))
    bin_excess = [
        sum(
            weight
            for (relative_s, _value), weight in zip(post_pairs, weights)
            if start_s <= relative_s < end_s
        )
        for start_s, end_s in bins
    ]
    centroid = (
        sum(
            min(
                max(
                    (relative_s - POST_WINDOW_START_SECONDS)
                    / (
                        POST_WINDOW_END_SECONDS
                        - POST_WINDOW_START_SECONDS
                    ),
                    0.0,
                ),
                1.0,
            ) * weight
            for (relative_s, _value), weight
            in zip(post_pairs, weights)
        ) / total_excess
        if total_excess > 0.000001 else 0.5
    )
    features = {
        "early_excess_share": _rounded(
            bin_excess[0] / total_excess
            if total_excess > 0.000001 else 0.0),
        "middle_excess_share": _rounded(
            bin_excess[1] / total_excess
            if total_excess > 0.000001 else 0.0),
        "late_excess_share": _rounded(
            bin_excess[2] / total_excess
            if total_excess > 0.000001 else 0.0),
        "motion_centroid": _rounded(centroid),
        "post_above_pre_p90_share": _rounded(
            sum(value > pre_p90 for _time, value in post_pairs)
            / len(post_pairs)
            if post_pairs else 0.0),
    }
    available_seconds = max(
        0.0,
        frames[-1].time_s + interval - onset.time_s,
    )
    eligibility_reasons: list[str] = []
    if not onset.eligible:
        eligibility_reasons.append(
            onset.reason or "ineligible_motion_onset")
    if len(pre) < 4:
        eligibility_reasons.append("insufficient_pre_onset_samples")
    if available_seconds < POST_WINDOW_END_SECONDS:
        eligibility_reasons.append("insufficient_post_onset_window")
    if not post_pairs:
        eligibility_reasons.append("missing_post_onset_samples")
    if total_excess <= 0.000001:
        eligibility_reasons.append("no_post_onset_excess_motion")
    diagnostics = {
        "duration_seconds": _rounded(frames[-1].time_s + interval),
        "sampled_frames": len(frames),
        "onset_seconds": onset.time_s,
        "onset_confidence": onset.confidence,
        "onset_method": onset.method,
        "onset_sustained_rise_score": onset.sustained_rise_score,
        "onset_prominence": onset.prominence,
        "onset_pre_post_ratio": onset.post_pre_ratio,
        "post_window_seconds_available": _rounded(available_seconds),
        "pre_motion_median": _rounded(baseline),
        "pre_motion_p90": _rounded(pre_p90),
        "post_excess_motion_total": _rounded(total_excess),
        "classifier_usable": not eligibility_reasons,
        "eligibility_reasons": eligibility_reasons,
        "features": features,
    }
    return features, diagnostics


def analyze_temporal_frames(
        frames: list[SignalFrame],
        *,
        clip_start_ms: int,
        clip_end_ms: int,
        angle_starts_ms: Iterable[int] = (),
) -> dict[str, Any]:
    """Create aggregated scale-resistant features plus visible diagnostics."""
    if not frames:
        raise ValueError("Temporal analysis requires sampled frames")
    resolution = resolve_angle_ranges(
        frames,
        clip_start_ms=clip_start_ms,
        clip_end_ms=clip_end_ms,
        angle_starts_ms=angle_starts_ms,
    )
    angle_features: list[dict[str, float]] = []
    angle_diagnostics: list[dict[str, Any]] = []
    for index, angle in enumerate(resolution.ranges, start=1):
        angle_frames = _slice_angle(frames, angle)
        if len(angle_frames) < 32:
            angle_diagnostics.append({
                "angle": index,
                "start_seconds": _rounded(angle.start_s),
                "end_seconds": _rounded(angle.end_s),
                "usable": False,
                "classifier_usable": False,
                "reason": "too_few_frames",
            })
            continue
        features, diagnostics = summarize_temporal_angle(angle_frames)
        if diagnostics["classifier_usable"]:
            angle_features.append(features)
        angle_diagnostics.append({
            "angle": index,
            "start_seconds": _rounded(angle.start_s),
            "end_seconds": _rounded(angle.end_s),
            "usable": True,
            **diagnostics,
        })
    reasons = list(resolution.abstain_reasons)
    if len(resolution.ranges) != 2:
        reasons.append("two_angle_transition_not_resolved")
    if len(angle_features) != 2:
        reasons.append(
            f"expected_two_usable_angles_found_{len(angle_features)}")
    classifier_eligible = (
        resolution.classifier_eligible
        and len(resolution.ranges) == 2
        and len(angle_features) == 2
    )
    combined: dict[str, float] = {}
    differences: dict[str, float] = {}
    if classifier_eligible:
        for name in _ANGLE_FEATURES:
            values = [
                features[name] for features in angle_features]
            combined[f"temporal_{name}"] = _rounded(
                statistics.median(values))
            differences[name] = _rounded(abs(values[0] - values[1]))
    if classifier_eligible:
        mode = "two_angle"
    elif len(angle_features) == 1:
        mode = "one_angle_fallback"
    else:
        mode = "abstain"
    transition_diagnostics = (
        {
            "left_pulse_seconds": resolution.transition.left_pulse_s,
            "right_pulse_seconds": resolution.transition.right_pulse_s,
            "midpoint_seconds": resolution.transition.midpoint_s,
            "raw_contrast": resolution.transition.raw_contrast,
            "normalized_drop": resolution.transition.normalized_drop,
            "min_pulse_percentile": (
                resolution.transition.min_pulse_percentile
            ),
            "candidate_count": resolution.transition.candidate_count,
            "cluster_count": resolution.transition.cluster_count,
        }
        if resolution.transition is not None else None
    )
    return {
        "features": combined,
        "diagnostics": {
            "classifier_eligible": classifier_eligible,
            "analysis_mode": mode,
            "abstain_reasons": sorted(set(reasons)),
            "classifier_feature_names": list(
                TEMPORAL_CLASSIFIER_FEATURES),
            "clip_duration_seconds": _rounded(
                (clip_end_ms - clip_start_ms) / 1000),
            "angle_split_source": resolution.source,
            "angle_split_confidence": resolution.confidence,
            "angles_detected": len(resolution.ranges),
            "cse_internal_starts_seconds": [
                _rounded(value)
                for value in resolution.cse_internal_starts_s
            ],
            "transition": transition_diagnostics,
            "angles": angle_diagnostics,
            "angle_feature_abs_differences": differences,
        },
    }


def measure_temporal_clip(
        ffmpeg_path: str,
        source: Path,
        start_ms: int,
        end_ms: int,
        *,
        angle_starts_ms: Iterable[int] = (),
        sample_fps: float = TEMPORAL_SAMPLE_FPS,
        sample_width: int = DEFAULT_SAMPLE_WIDTH,
) -> dict[str, Any]:
    frames = read_signalstats_frames(
        ffmpeg_path,
        source,
        start_ms,
        end_ms,
        sample_fps=sample_fps,
        sample_width=sample_width,
        include_scene_score=False,
    )
    return analyze_temporal_frames(
        frames,
        clip_start_ms=start_ms,
        clip_end_ms=end_ms,
        angle_starts_ms=angle_starts_ms,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on {path.name} line {line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(
                f"Expected an object on {path.name} line {line_number}")
        records.append(value)
    return records


def extract_run_pass_temporal_features(
        labels_path: Path,
        output_path: Path,
        ffmpeg_path: Path,
        *,
        sample_fps: float = TEMPORAL_SAMPLE_FPS,
        sample_width: int = DEFAULT_SAMPLE_WIDTH,
        overwrite: bool = False,
        on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
        measure: Callable[..., dict[str, Any]] = measure_temporal_clip,
) -> dict[str, Any]:
    """Extract Temporal v2 features from one standard label cohort."""
    labels_path = reject_quarantined_input(labels_path)
    output_path = output_path.resolve()
    ffmpeg_path = ffmpeg_path.resolve()
    if not labels_path.is_file():
        raise FileNotFoundError(labels_path)
    if not ffmpeg_path.is_file():
        raise FileNotFoundError(ffmpeg_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Refusing to replace temporal features: {output_path}")

    trainable = [
        record for record in _read_jsonl(labels_path)
        if record.get("trainable")
    ]
    if not trainable:
        raise ValueError("Label manifest has no trainable records")
    cohort_ids = {
        str(record.get("research_cohort_id", "")).strip()
        for record in trainable
    }
    if "" in cohort_ids or len(cohort_ids) != 1:
        raise ValueError(
            "Trainable labels must belong to one approved research cohort")
    cohort_id = next(iter(cohort_ids))

    results: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    split_sources: Counter[str] = Counter()
    analysis_modes: Counter[str] = Counter()
    eligible_records = 0
    binary_records = 0
    binary_eligible_records = 0
    for index, record in enumerate(trainable, start=1):
        if on_progress is not None:
            on_progress(index, len(trainable), record)
        source = Path(str(record.get("source_video_path", "")))
        if not source.is_file():
            raise FileNotFoundError(source)
        measured = measure(
            str(ffmpeg_path),
            source,
            int(record["start_ms"]),
            int(record["end_ms"]),
            angle_starts_ms=record.get("angle_starts_ms", []),
            sample_fps=sample_fps,
            sample_width=sample_width,
        )
        label = str(record.get("label", ""))
        counts[label] += 1
        split_sources[
            measured["diagnostics"]["angle_split_source"]
        ] += 1
        analysis_modes[
            measured["diagnostics"]["analysis_mode"]
        ] += 1
        classifier_eligible = bool(
            measured["diagnostics"]["classifier_eligible"])
        eligible_records += int(classifier_eligible)
        if label in {"run", "pass"}:
            binary_records += 1
            binary_eligible_records += int(classifier_eligible)
        results.append({
            "schema_version": TEMPORAL_SCHEMA_VERSION,
            "dataset_kind": "tapesift_run_pass_temporal_features",
            "research_cohort_id": cohort_id,
            "feature_extractor": {
                "name": TEMPORAL_EXTRACTOR_NAME,
                "version": TEMPORAL_EXTRACTOR_VERSION,
                "ffmpeg_version": ffmpeg_service.get_version(
                    str(ffmpeg_path)),
                "sample_fps": sample_fps,
                "sample_width": sample_width,
            },
            "clip_id": str(record.get("clip_id", "")),
            "clip_number": int(record.get("clip_number", 0)),
            "project_name": str(record.get("project_name", "")),
            "source_project": str(record.get("source_project", "")),
            "source_video_path": str(source),
            "start_ms": int(record["start_ms"]),
            "end_ms": int(record["end_ms"]),
            "label": label,
            "label_display": str(record.get("label_display", "")),
            "play_type": str(record.get("play_type", "")),
            "play_action": str(record.get("play_action", "")),
            "classifier_eligible": classifier_eligible,
            "abstain_reasons": measured[
                "diagnostics"]["abstain_reasons"],
            "features": measured["features"],
            "temporal_diagnostics": measured["diagnostics"],
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in results
        ),
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return {
        "labels": str(labels_path),
        "output": str(output_path),
        "records": len(results),
        "label_counts": dict(sorted(counts.items())),
        "research_cohort_id": cohort_id,
        "extractor": (
            f"{TEMPORAL_EXTRACTOR_NAME}-v{TEMPORAL_EXTRACTOR_VERSION}"
        ),
        "sample_fps": sample_fps,
        "sample_width": sample_width,
        "classifier_eligible_records": eligible_records,
        "classifier_coverage": _rounded(
            eligible_records / len(results)),
        "binary_run_pass_records": binary_records,
        "binary_run_pass_eligible_records": binary_eligible_records,
        "binary_run_pass_coverage": _rounded(
            binary_eligible_records / binary_records
            if binary_records else 0.0),
        "analysis_modes": dict(sorted(analysis_modes.items())),
        "angle_split_sources": dict(sorted(split_sources.items())),
    }
