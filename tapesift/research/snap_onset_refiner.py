"""Iteration 7B deterministic snap-onset refiner research.

Temporal v2.1 selects the strongest sustained motion rise in each camera
angle.  Iteration 7B leaves the frozen angle split untouched and evaluates a
separate candidate rule: select the earliest sufficiently strong sustained
rise relative to the strongest candidate in that angle.

The completed two-film calibration is development evidence, not a promotion
holdout.  Any selected policy must be frozen before it is evaluated on a
third untouched game.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from tapesift.research.run_pass_features import (
    DEFAULT_SAMPLE_WIDTH,
    SignalFrame,
    read_signalstats_frames,
)
from tapesift.research.run_pass_temporal_features import TEMPORAL_SAMPLE_FPS


ITERATION_ID = "iteration-7b-earliest-qualified-rise-v1"
SCHEMA_VERSION = "1.0"
EXPECTED_PLAYS = 45
EXPECTED_ANGLES = 90

PRE_START_SECONDS = 1.75
PRE_END_SECONDS = 0.25
EARLY_START_SECONDS = 0.25
EARLY_END_SECONDS = 1.75
LATE_START_SECONDS = 1.75
LATE_END_SECONDS = 3.75
STRONG_SCORE = 0.45

MAX_MEDIAN_ABSOLUTE_ERROR_MS = 250
MIN_WITHIN_500_MS_SHARE = 0.90
MAX_P90_ABSOLUTE_ERROR_MS = 750
MAX_MEDIAN_SIGNED_BIAS_ABS_MS = 125
MIN_BOTH_ANGLES_WITHIN_500_MS_SHARE = 0.80


@dataclass(frozen=True, order=True)
class RefinerPolicy:
    """One member of the small predeclared 7B development grid."""

    relative_score_floor: float
    minimum_score: float
    minimum_post_pre_ratio: float
    output_lag_seconds: float

    @property
    def policy_id(self) -> str:
        return (
            f"rel-{self.relative_score_floor:.3f}"
            f"_score-{self.minimum_score:.3f}"
            f"_ratio-{self.minimum_post_pre_ratio:.3f}"
            f"_lag-{self.output_lag_seconds:.3f}"
        )


@dataclass(frozen=True)
class RiseCandidate:
    time_seconds: float
    score: float
    post_pre_ratio: float


@dataclass(frozen=True)
class AngleTrace:
    item_id: str
    cohort_id: str
    clip_id: str
    clip_number: int
    angle: int
    source_video_path: str
    range_start_ms: int
    range_end_ms: int
    actual_snap_ms: int
    temporal_v21_onset_ms: int
    candidates: tuple[RiseCandidate, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON at {path}:{line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(f"Expected object at {path}:{line_number}")
        rows.append(row)
    return rows


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


def _nearest_rank(values: Iterable[int], fraction: float) -> int:
    items = sorted(values)
    if not items:
        raise ValueError("Nearest-rank percentile requires values")
    index = max(0, math.ceil(len(items) * fraction) - 1)
    return items[index]


def _sample_interval(frames: list[SignalFrame]) -> float:
    deltas = [
        current.time_s - previous.time_s
        for previous, current in zip(frames, frames[1:])
        if current.time_s > previous.time_s
    ]
    return statistics.median(deltas) if deltas else 1.0 / TEMPORAL_SAMPLE_FPS


def _smooth(values: list[float], radius: int) -> list[float]:
    return [
        _mean(values[max(0, index - radius):index + radius + 1])
        for index in range(len(values))
    ]


def _window_values(
    frames: list[SignalFrame],
    values: list[float],
    start_seconds: float,
    end_seconds: float,
) -> list[float]:
    return [
        value
        for frame, value in zip(frames, values)
        if start_seconds <= frame.time_s < end_seconds
    ]


def build_rise_candidates(
    frames: list[SignalFrame],
) -> tuple[RiseCandidate, ...]:
    """Reproduce v2.1 candidate measurements without selecting its maximum."""

    if len(frames) < 32:
        return ()
    interval = _sample_interval(frames)
    radius = max(1, round(0.5 / max(interval, 0.001)))
    smoothed = _smooth(
        [frame.ydif + frame.udif + frame.vdif for frame in frames],
        radius,
    )
    scale = _percentile(smoothed, 0.90) - _percentile(smoothed, 0.10)
    if scale <= 0.000001:
        return ()

    candidates = []
    for frame in frames:
        time_seconds = frame.time_s
        if time_seconds < PRE_START_SECONDS:
            continue
        if time_seconds + LATE_END_SECONDS > frames[-1].time_s + interval:
            continue
        pre = _window_values(
            frames,
            smoothed,
            time_seconds - PRE_START_SECONDS,
            time_seconds - PRE_END_SECONDS,
        )
        early = _window_values(
            frames,
            smoothed,
            time_seconds + EARLY_START_SECONDS,
            time_seconds + EARLY_END_SECONDS,
        )
        late = _window_values(
            frames,
            smoothed,
            time_seconds + LATE_START_SECONDS,
            time_seconds + LATE_END_SECONDS,
        )
        if min(len(pre), len(early), len(late)) < 4:
            continue
        pre_median = statistics.median(pre)
        sustained_post = min(
            statistics.median(early),
            statistics.median(late),
        )
        candidates.append(RiseCandidate(
            time_seconds=round(time_seconds, 6),
            score=round((sustained_post - pre_median) / scale, 6),
            post_pre_ratio=round(
                sustained_post / max(pre_median, 0.000001),
                6,
            ),
        ))
    return tuple(candidates)


def apply_policy(
    trace: AngleTrace,
    policy: RefinerPolicy,
) -> dict[str, Any]:
    if not trace.candidates:
        selected_ms = trace.temporal_v21_onset_ms
        return {
            "candidate_onset_ms": selected_ms,
            "selection_status": "temporal_v21_fallback_no_candidates",
            "selected_candidate": None,
        }

    strongest = max(
        trace.candidates,
        key=lambda candidate: (
            candidate.score,
            -candidate.time_seconds,
        ),
    )
    score_floor = max(
        policy.minimum_score,
        strongest.score * policy.relative_score_floor,
    )
    qualified = [
        candidate
        for candidate in trace.candidates
        if candidate.score >= score_floor
        and (
            candidate.post_pre_ratio >= policy.minimum_post_pre_ratio
            or candidate.score >= STRONG_SCORE
        )
    ]
    if qualified:
        selected = min(
            qualified,
            key=lambda candidate: (
                candidate.time_seconds,
                -candidate.score,
            ),
        )
        selected_ms = (
            trace.range_start_ms
            + round(
                (
                    selected.time_seconds
                    + policy.output_lag_seconds
                ) * 1000
            )
        )
        selected_ms = min(
            max(selected_ms, trace.range_start_ms),
            trace.range_end_ms - 1,
        )
        return {
            "candidate_onset_ms": selected_ms,
            "selection_status": "earliest_qualified_sustained_rise",
            "selected_candidate": asdict(selected),
            "strongest_candidate": asdict(strongest),
            "score_floor": round(score_floor, 6),
        }

    return {
        "candidate_onset_ms": trace.temporal_v21_onset_ms,
        "selection_status": "temporal_v21_fallback_no_qualified_rise",
        "selected_candidate": None,
        "strongest_candidate": asdict(strongest),
        "score_floor": round(score_floor, 6),
    }


def _metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [int(row["error_ms"]) for row in rows]
    absolute = [abs(error) for error in errors]
    within = sum(value <= 500 for value in absolute)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["item_id"])].append(row)
    paired = sum(
        len(group) == 2
        and all(abs(int(row["error_ms"])) <= 500 for row in group)
        for group in grouped.values()
    )
    return {
        "angle_judgments": len(rows),
        "plays": len(grouped),
        "median_absolute_error_ms": statistics.median(absolute),
        "within_500_ms": within,
        "within_500_ms_share": round(within / len(rows), 6),
        "p90_absolute_error_ms": _nearest_rank(absolute, 0.90),
        "median_signed_bias_ms": statistics.median(errors),
        "both_angles_within_500_ms": paired,
        "both_angles_within_500_ms_share": round(
            paired / len(grouped),
            6,
        ),
    }


def score_policy(
    traces: list[AngleTrace],
    policy: RefinerPolicy,
) -> dict[str, Any]:
    rows = []
    for trace in traces:
        selection = apply_policy(trace, policy)
        onset = int(selection["candidate_onset_ms"])
        rows.append({
            "item_id": trace.item_id,
            "cohort_id": trace.cohort_id,
            "clip_id": trace.clip_id,
            "clip_number": trace.clip_number,
            "angle": trace.angle,
            "actual_snap_ms": trace.actual_snap_ms,
            "temporal_v21_onset_ms": trace.temporal_v21_onset_ms,
            "candidate_onset_ms": onset,
            "error_ms": trace.actual_snap_ms - onset,
            **{
                key: value
                for key, value in selection.items()
                if key != "candidate_onset_ms"
            },
        })
    cohorts = {
        cohort: _metric_summary([
            row for row in rows if row["cohort_id"] == cohort
        ])
        for cohort in sorted({row["cohort_id"] for row in rows})
    }
    summary = _metric_summary(rows)
    summary["fallback_count"] = sum(
        row["selection_status"].startswith("temporal_v21_fallback")
        for row in rows
    )
    return {
        "policy": asdict(policy) | {"policy_id": policy.policy_id},
        "overall": summary,
        "by_cohort": cohorts,
        "rows": rows,
    }


def score_temporal_v21(
    traces: list[AngleTrace],
) -> dict[str, Any]:
    rows = [{
        "item_id": trace.item_id,
        "cohort_id": trace.cohort_id,
        "error_ms": (
            trace.actual_snap_ms - trace.temporal_v21_onset_ms
        ),
    } for trace in traces]
    return {
        "overall": _metric_summary(rows),
        "by_cohort": {
            cohort: _metric_summary([
                row for row in rows if row["cohort_id"] == cohort
            ])
            for cohort in sorted({row["cohort_id"] for row in rows})
        },
    }


def policy_grid() -> tuple[RefinerPolicy, ...]:
    """Return the deliberately small, deterministic development grid."""

    return tuple(
        RefinerPolicy(relative, score, ratio, lag)
        for relative in (0.50, 0.65, 0.80)
        for score in (0.12, 0.18, 0.24)
        for ratio in (1.5, 1.75, 2.0)
        for lag in (0.0, 0.125, 0.25, 0.375)
    )


def _cohort_rank(result: dict[str, Any], cohort_id: str) -> tuple[Any, ...]:
    metrics = result["by_cohort"][cohort_id]
    return (
        -metrics["both_angles_within_500_ms_share"],
        -metrics["within_500_ms_share"],
        metrics["median_absolute_error_ms"],
        metrics["p90_absolute_error_ms"],
        abs(metrics["median_signed_bias_ms"]),
        result["overall"]["fallback_count"],
        result["policy"]["policy_id"],
    )


def _robust_rank(result: dict[str, Any]) -> tuple[Any, ...]:
    cohorts = list(result["by_cohort"].values())
    overall = result["overall"]
    return (
        -min(row["both_angles_within_500_ms_share"] for row in cohorts),
        -min(row["within_500_ms_share"] for row in cohorts),
        max(row["median_absolute_error_ms"] for row in cohorts),
        overall["median_absolute_error_ms"],
        overall["p90_absolute_error_ms"],
        abs(overall["median_signed_bias_ms"]),
        overall["fallback_count"],
        result["policy"]["policy_id"],
    )


def _gate(metrics: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "median_absolute_error": (
            metrics["median_absolute_error_ms"]
            <= MAX_MEDIAN_ABSOLUTE_ERROR_MS
        ),
        "within_500_ms_share": (
            metrics["within_500_ms_share"]
            >= MIN_WITHIN_500_MS_SHARE
        ),
        "p90_absolute_error": (
            metrics["p90_absolute_error_ms"]
            <= MAX_P90_ABSOLUTE_ERROR_MS
        ),
        "median_signed_bias": (
            abs(metrics["median_signed_bias_ms"])
            <= MAX_MEDIAN_SIGNED_BIAS_ABS_MS
        ),
        "both_angles_within_500_ms_share": (
            metrics["both_angles_within_500_ms_share"]
            >= MIN_BOTH_ANGLES_WITHIN_500_MS_SHARE
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
    }


def select_development_policy(
    traces: list[AngleTrace],
) -> dict[str, Any]:
    results = [
        score_policy(traces, policy)
        for policy in policy_grid()
    ]
    selected = min(results, key=_robust_rank)
    cohort_ids = sorted(selected["by_cohort"])
    transfer = []
    for training in cohort_ids:
        winner = min(
            results,
            key=lambda result: _cohort_rank(result, training),
        )
        validation = next(
            cohort for cohort in cohort_ids if cohort != training
        )
        transfer.append({
            "training_cohort": training,
            "validation_cohort": validation,
            "selected_policy": winner["policy"],
            "training_metrics": winner["by_cohort"][training],
            "validation_metrics": winner["by_cohort"][validation],
        })
    selected["development_gate"] = _gate(selected["overall"])
    return {
        "temporal_v21_baseline": score_temporal_v21(traces),
        "selected": selected,
        "film_separated_transfer": transfer,
        "policy_count": len(results),
        "selection_rule": (
            "minimize worst-cohort paired and angle failures, then boundary "
            "error, overall tail error, bias, fallbacks, and policy id"
        ),
    }


def _validate_manifest_fingerprints(
    judgment_rows: list[dict[str, Any]],
    feature_paths: list[Path],
) -> list[dict[str, str]]:
    declared = {
        (str(row["name"]), str(row["sha256"]))
        for judgment in judgment_rows
        for row in judgment.get("source_manifests", [])
    }
    actual = {
        (path.name, sha256_file(path))
        for path in feature_paths
    }
    if actual != declared:
        raise ValueError(
            "Temporal feature manifests do not match frozen judgments"
        )
    return [
        {"name": name, "sha256": sha}
        for name, sha in sorted(actual)
    ]


def build_angle_traces(
    judgments_path: Path,
    feature_paths: list[Path],
    ffmpeg_path: Path,
) -> tuple[list[AngleTrace], dict[str, Any]]:
    judgments_path = judgments_path.resolve()
    feature_paths = [path.resolve() for path in feature_paths]
    ffmpeg_path = ffmpeg_path.resolve()
    if not judgments_path.is_file():
        raise FileNotFoundError(judgments_path)
    if not ffmpeg_path.is_file():
        raise FileNotFoundError(ffmpeg_path)
    for path in feature_paths:
        if not path.is_file():
            raise FileNotFoundError(path)

    judgments = _read_jsonl(judgments_path)
    if len(judgments) != EXPECTED_PLAYS:
        raise ValueError(
            f"Expected {EXPECTED_PLAYS} judgments, found {len(judgments)}"
        )
    fingerprints = _validate_manifest_fingerprints(
        judgments,
        feature_paths,
    )
    features: dict[str, dict[str, Any]] = {}
    for path in feature_paths:
        for row in _read_jsonl(path):
            clip_id = str(row.get("clip_id") or "")
            if not clip_id or clip_id in features:
                raise ValueError(
                    f"Missing or duplicate feature clip id: {clip_id!r}"
                )
            features[clip_id] = row

    traces: list[AngleTrace] = []
    for index, judgment in enumerate(judgments, start=1):
        clip_id = str(judgment.get("clip_id") or "")
        feature = features.get(clip_id)
        if feature is None:
            raise ValueError(f"Missing temporal feature record: {clip_id}")
        source = Path(str(feature["source_video_path"])).resolve()
        clip_start = int(feature["start_ms"])
        clip_end = int(feature["end_ms"])
        frames = read_signalstats_frames(
            str(ffmpeg_path),
            source,
            clip_start,
            clip_end,
            sample_fps=TEMPORAL_SAMPLE_FPS,
            sample_width=DEFAULT_SAMPLE_WIDTH,
            include_scene_score=False,
        )
        diagnostics = feature["temporal_diagnostics"]["angles"]
        angle_judgments = judgment.get("angles")
        if not isinstance(angle_judgments, list) or len(angle_judgments) != 2:
            raise ValueError(f"{clip_id} does not have two snap judgments")
        if not isinstance(diagnostics, list) or len(diagnostics) != 2:
            raise ValueError(f"{clip_id} does not have two angle diagnostics")
        for measured, diagnostic in zip(
            angle_judgments,
            diagnostics,
            strict=True,
        ):
            angle = int(measured["angle"])
            if angle != int(diagnostic["angle"]):
                raise ValueError(f"Angle order changed for {clip_id}")
            range_start_ms = clip_start + round(
                float(diagnostic["start_seconds"]) * 1000
            )
            range_end_ms = clip_start + round(
                float(diagnostic["end_seconds"]) * 1000
            )
            angle_frames = [
                SignalFrame(
                    time_s=frame.time_s
                    - float(diagnostic["start_seconds"]),
                    yavg=frame.yavg,
                    satavg=frame.satavg,
                    ydif=frame.ydif,
                    udif=frame.udif,
                    vdif=frame.vdif,
                    scene_score=frame.scene_score,
                )
                for frame in frames
                if (
                    float(diagnostic["start_seconds"])
                    <= frame.time_s
                    < float(diagnostic["end_seconds"])
                )
            ]
            traces.append(AngleTrace(
                item_id=str(judgment["item_id"]),
                cohort_id=str(judgment["research_cohort_id"]),
                clip_id=clip_id,
                clip_number=int(judgment["clip_number"]),
                angle=angle,
                source_video_path=str(source),
                range_start_ms=range_start_ms,
                range_end_ms=range_end_ms,
                actual_snap_ms=int(measured["actual_snap_ms"]),
                temporal_v21_onset_ms=int(measured["proposed_onset_ms"]),
                candidates=build_rise_candidates(angle_frames),
            ))
        print(
            f"[{index:02d}/{len(judgments)}] "
            f"{judgment['research_cohort_id']} clip "
            f"{judgment['clip_number']}",
            flush=True,
        )
    if len(traces) != EXPECTED_ANGLES:
        raise ValueError(
            f"Expected {EXPECTED_ANGLES} traces, found {len(traces)}"
        )
    return traces, {
        "judgments_path": str(judgments_path),
        "judgments_sha256": sha256_file(judgments_path),
        "source_manifest_fingerprints": fingerprints,
        "ffmpeg_path": str(ffmpeg_path),
        "sample_fps": TEMPORAL_SAMPLE_FPS,
        "sample_width": DEFAULT_SAMPLE_WIDTH,
    }


def evaluate_iteration_7b(
    judgments_path: Path,
    feature_paths: list[Path],
    ffmpeg_path: Path,
) -> dict[str, Any]:
    traces, frozen_input = build_angle_traces(
        judgments_path,
        feature_paths,
        ffmpeg_path,
    )
    development = select_development_policy(traces)
    gate_passed = development["selected"]["development_gate"]["passed"]
    return {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": (
            "development_candidate_ready_for_third_game"
            if gate_passed
            else "development_hold_refiner_not_ready"
        ),
        "frozen_input": frozen_input,
        "methodology": {
            "angle_split": "reuse frozen Temporal v2.1 angle ranges",
            "candidate": "earliest qualified sustained motion rise",
            "policy_grid_is_predeclared": True,
            "run_pass_labels_used": False,
            "snap_labels_used_for_development_selection": True,
            "promotion_holdout": "third untouched game required",
        },
        "development": development,
    }


def render_markdown(report: dict[str, Any]) -> str:
    selected = report["development"]["selected"]
    metrics = selected["overall"]
    baseline = report["development"]["temporal_v21_baseline"]["overall"]
    gate = selected["development_gate"]
    lines = [
        "# TapeSift Iteration 7B — Candidate Snap-Onset Refiner",
        "",
        (
            "This is development evidence from the two already-reviewed "
            "calibration games. It does not modify or replace Temporal v2.1."
        ),
        "",
        "## Selected deterministic policy",
        "",
        f"- Policy: `{selected['policy']['policy_id']}`",
        (
            "- Strategy: earliest sustained rise above both an absolute and "
            "strongest-candidate-relative floor"
        ),
        f"- Policies evaluated: **{report['development']['policy_count']}**",
        "",
        "## Development comparison",
        "",
        "| Measure | Temporal v2.1 | 7B candidate |",
        "| --- | ---: | ---: |",
        (
            f"| Median absolute error | "
            f"{baseline['median_absolute_error_ms']} ms | "
            f"{metrics['median_absolute_error_ms']} ms |"
        ),
        (
            f"| Within 500 ms | "
            f"{baseline['within_500_ms_share']:.1%} | "
            f"{metrics['within_500_ms_share']:.1%} |"
        ),
        (
            f"| P90 absolute error | "
            f"{baseline['p90_absolute_error_ms']} ms | "
            f"{metrics['p90_absolute_error_ms']} ms |"
        ),
        (
            f"| Median signed bias | "
            f"{baseline['median_signed_bias_ms']} ms | "
            f"{metrics['median_signed_bias_ms']} ms |"
        ),
        (
            f"| Both angles within 500 ms | "
            f"{baseline['both_angles_within_500_ms_share']:.1%} | "
            f"{metrics['both_angles_within_500_ms_share']:.1%} |"
        ),
        "",
        f"- Development gate passed: **{gate['passed']}**",
        "",
        "## Decision",
        "",
        *(
            [
                (
                    "**THIRD GAME REQUIRED.** These two games were used to "
                    "select the policy. Freeze the candidate before evaluating "
                    "one untouched game; do not promote from this report."
                )
            ]
            if gate["passed"]
            else [
                (
                    "**HOLD.** The candidate materially improves Temporal "
                    "v2.1 but still misses the predeclared localization gate. "
                    "Do not consume a third untouched game yet."
                )
            ]
        ),
        "",
    ]
    return "\n".join(lines)


def write_iteration_7b_report(
    report: dict[str, Any],
    output_json: Path,
    output_markdown: Path,
) -> None:
    output_json = output_json.resolve()
    output_markdown = output_markdown.resolve()
    for path in (output_json, output_markdown):
        if path.exists():
            raise FileExistsError(f"Refusing to replace frozen output: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    output_markdown.write_text(
        render_markdown(report),
        encoding="utf-8",
        newline="\n",
    )
