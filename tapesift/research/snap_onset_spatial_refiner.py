"""Iteration 7B.2 spatial snap-onset refinement research.

This module leaves CSE Beta 4D, Temporal v2.1, and the Iteration 7B.1
candidate untouched. It measures motion in six fixed field tiles and tests
whether spatially localized or field-consensus motion better localizes the
snap on the completed two-film development calibration.
"""

from __future__ import annotations

import json
import statistics
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from tapesift.research.run_pass_features import SignalFrame, parse_signalstats
from tapesift.research.run_pass_temporal_features import TEMPORAL_SAMPLE_FPS
from tapesift.research.snap_onset_refiner import (
    EXPECTED_ANGLES,
    EXPECTED_PLAYS,
    AngleTrace,
    RefinerPolicy,
    RiseCandidate,
    _gate,
    _metric_summary,
    _read_jsonl,
    _validate_manifest_fingerprints,
    apply_policy,
    build_rise_candidates,
    policy_grid,
    sha256_file,
)
from tapesift.services import ffmpeg_service


ITERATION_ID = "iteration-7b2-spatial-field-motion-v1"
SCHEMA_VERSION = "1.0"
SPATIAL_SAMPLE_WIDTH = 160
CHANNELS = (
    "field_median",
    "field_upper_quartile",
    "localized_excess",
    "field_local_blend",
)


@dataclass(frozen=True)
class FieldRegion:
    """One normalized field crop retained by the spatial extractor."""

    region_id: str
    x: float
    y: float
    width: float
    height: float

    @property
    def crop_filter(self) -> str:
        return (
            f"crop=iw*{self.width:.6f}:ih*{self.height:.6f}:"
            f"iw*{self.x:.6f}:ih*{self.y:.6f}"
        )


FIELD_REGIONS = tuple(
    FieldRegion(
        region_id=f"field-r{row + 1}-c{column + 1}",
        x=column / 3.0,
        y=0.18 + row * 0.32,
        width=1.0 / 3.0,
        height=0.32,
    )
    for row in range(2)
    for column in range(3)
)


@dataclass(frozen=True)
class SpatialAngleTrace:
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
    candidates_by_channel: dict[str, tuple[RiseCandidate, ...]]


@dataclass(frozen=True, order=True)
class SpatialPolicy:
    channel: str
    relative_score_floor: float
    minimum_score: float
    minimum_post_pre_ratio: float
    output_lag_seconds: float

    @property
    def onset_policy(self) -> RefinerPolicy:
        return RefinerPolicy(
            relative_score_floor=self.relative_score_floor,
            minimum_score=self.minimum_score,
            minimum_post_pre_ratio=self.minimum_post_pre_ratio,
            output_lag_seconds=self.output_lag_seconds,
        )

    @property
    def policy_id(self) -> str:
        return f"{self.channel}__{self.onset_policy.policy_id}"


def build_spatial_signalstats_command(
    ffmpeg_path: str,
    source: Path,
    start_ms: int,
    end_ms: int,
    region: FieldRegion,
    *,
    sample_fps: float = TEMPORAL_SAMPLE_FPS,
    sample_width: int = SPATIAL_SAMPLE_WIDTH,
) -> list[str]:
    """Build one deterministic FFmpeg field-region measurement command."""
    if end_ms <= start_ms:
        raise ValueError("Spatial feature range must have positive duration")
    if sample_fps <= 0:
        raise ValueError("Sample FPS must be positive")
    if sample_width < 16:
        raise ValueError("Sample width must be at least 16 pixels")
    filters = (
        f"fps={sample_fps:g}",
        region.crop_filter,
        f"scale={sample_width}:-2:flags=area",
        "format=yuv420p",
        "signalstats",
        "metadata=print:file=-",
    )
    return [
        ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        ffmpeg_service.ms_to_ffmpeg_time(start_ms),
        "-t",
        ffmpeg_service.ms_to_ffmpeg_time(end_ms - start_ms),
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        ",".join(filters),
        "-f",
        "null",
        "-",
    ]


def read_spatial_region_frames(
    ffmpeg_path: str,
    source: Path,
    start_ms: int,
    end_ms: int,
    region: FieldRegion,
) -> list[SignalFrame]:
    command = build_spatial_signalstats_command(
        ffmpeg_path,
        source,
        start_ms,
        end_ms,
        region,
    )
    timeout_s = max(60.0, ((end_ms - start_ms) / 1000.0) * 5.0)
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=ffmpeg_service.CREATE_NO_WINDOW
            | ffmpeg_service.IDLE_PRIORITY_CLASS,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"FFmpeg timed out measuring {source.name} {region.region_id}"
        ) from exc
    if process.returncode:
        detail = (process.stderr or "").strip() or "unknown FFmpeg error"
        raise RuntimeError(
            f"FFmpeg could not measure {source.name} "
            f"{region.region_id}: {detail}"
        )
    frames = parse_signalstats(process.stdout or "")
    if not frames:
        raise ValueError(
            f"FFmpeg returned no frames for {source.name} {region.region_id}"
        )
    return frames


def _percentile(values: Iterable[float], fraction: float) -> float:
    items = sorted(values)
    if not items:
        return 0.0
    position = (len(items) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(items) - 1)
    weight = position - low
    return items[low] + (items[high] - items[low]) * weight


def build_spatial_channels(
    region_frames: dict[str, list[SignalFrame]],
) -> dict[str, list[SignalFrame]]:
    """Align six field tiles and construct four transparent motion channels."""
    expected = {region.region_id for region in FIELD_REGIONS}
    if set(region_frames) != expected:
        raise ValueError("Spatial frames do not match the frozen field regions")
    frame_count = min(len(frames) for frames in region_frames.values())
    if frame_count < 1:
        raise ValueError("Spatial extraction returned an empty region")
    ordered = [region_frames[region.region_id] for region in FIELD_REGIONS]
    channels = {channel: [] for channel in CHANNELS}
    for index in range(frame_count):
        tile_frames = [frames[index] for frames in ordered]
        times = [frame.time_s for frame in tile_frames]
        if max(times) - min(times) > 0.02:
            raise ValueError("Spatial region frame times are not aligned")
        motions = [
            frame.ydif + frame.udif + frame.vdif
            for frame in tile_frames
        ]
        median = statistics.median(motions)
        upper = _percentile(motions, 0.75)
        localized = max(motions) - median
        values = {
            "field_median": median,
            "field_upper_quartile": upper,
            "localized_excess": localized,
            "field_local_blend": median + (localized * 0.5),
        }
        for channel, value in values.items():
            channels[channel].append(SignalFrame(
                time_s=statistics.median(times),
                yavg=0.0,
                satavg=0.0,
                ydif=round(value, 6),
                udif=0.0,
                vdif=0.0,
            ))
    return channels


def spatial_policy_grid() -> tuple[SpatialPolicy, ...]:
    """Cross the frozen channels with the bounded Iteration 7B.1 grid."""
    return tuple(
        SpatialPolicy(
            channel=channel,
            relative_score_floor=policy.relative_score_floor,
            minimum_score=policy.minimum_score,
            minimum_post_pre_ratio=policy.minimum_post_pre_ratio,
            output_lag_seconds=policy.output_lag_seconds,
        )
        for channel in CHANNELS
        for policy in policy_grid()
    )


def _as_angle_trace(
    trace: SpatialAngleTrace,
    channel: str,
) -> AngleTrace:
    return AngleTrace(
        item_id=trace.item_id,
        cohort_id=trace.cohort_id,
        clip_id=trace.clip_id,
        clip_number=trace.clip_number,
        angle=trace.angle,
        source_video_path=trace.source_video_path,
        range_start_ms=trace.range_start_ms,
        range_end_ms=trace.range_end_ms,
        actual_snap_ms=trace.actual_snap_ms,
        temporal_v21_onset_ms=trace.temporal_v21_onset_ms,
        candidates=trace.candidates_by_channel[channel],
    )


def score_spatial_policy(
    traces: list[SpatialAngleTrace],
    policy: SpatialPolicy,
) -> dict[str, Any]:
    rows = []
    for trace in traces:
        selection = apply_policy(
            _as_angle_trace(trace, policy.channel),
            policy.onset_policy,
        )
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
    overall = _metric_summary(rows)
    overall["fallback_count"] = sum(
        row["selection_status"].startswith("temporal_v21_fallback")
        for row in rows
    )
    return {
        "policy": asdict(policy) | {"policy_id": policy.policy_id},
        "overall": overall,
        "by_cohort": cohorts,
        "rows": rows,
    }


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


def select_spatial_policy(
    traces: list[SpatialAngleTrace],
) -> dict[str, Any]:
    results = [
        score_spatial_policy(traces, policy)
        for policy in spatial_policy_grid()
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
        "selected": selected,
        "film_separated_transfer": transfer,
        "policy_count": len(results),
        "selection_rule": (
            "minimize worst-cohort paired and angle failures, then boundary "
            "error, overall tail error, bias, fallbacks, and policy id"
        ),
    }


def _load_frozen_7b1_report(
    path: Path,
    judgments_sha256: str,
    manifest_fingerprints: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, str]]:
    path = path.resolve()
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("iteration_id") != "iteration-7b-earliest-qualified-rise-v1":
        raise ValueError("Input is not the frozen Iteration 7B.1 report")
    frozen = report.get("frozen_input", {})
    if frozen.get("judgments_sha256") != judgments_sha256:
        raise ValueError("Iteration 7B.1 judgment fingerprint changed")
    if frozen.get("source_manifest_fingerprints") != manifest_fingerprints:
        raise ValueError("Iteration 7B.1 manifest fingerprints changed")
    return report, {
        "path": str(path),
        "sha256": sha256_file(path),
    }


def build_spatial_angle_traces(
    judgments_path: Path,
    feature_paths: list[Path],
    ffmpeg_path: Path,
) -> tuple[list[SpatialAngleTrace], dict[str, Any]]:
    judgments_path = judgments_path.resolve()
    feature_paths = [path.resolve() for path in feature_paths]
    ffmpeg_path = ffmpeg_path.resolve()
    for path in [judgments_path, ffmpeg_path, *feature_paths]:
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

    traces: list[SpatialAngleTrace] = []
    for index, judgment in enumerate(judgments, start=1):
        clip_id = str(judgment.get("clip_id") or "")
        feature = features.get(clip_id)
        if feature is None:
            raise ValueError(f"Missing temporal feature record: {clip_id}")
        source = Path(str(feature["source_video_path"])).resolve()
        clip_start = int(feature["start_ms"])
        clip_end = int(feature["end_ms"])
        spatial_frames = build_spatial_channels({
            region.region_id: read_spatial_region_frames(
                str(ffmpeg_path),
                source,
                clip_start,
                clip_end,
                region,
            )
            for region in FIELD_REGIONS
        })
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
            angle_start_s = float(diagnostic["start_seconds"])
            angle_end_s = float(diagnostic["end_seconds"])
            range_start_ms = clip_start + round(angle_start_s * 1000)
            range_end_ms = clip_start + round(angle_end_s * 1000)
            candidates_by_channel = {}
            for channel, frames in spatial_frames.items():
                angle_frames = [
                    SignalFrame(
                        time_s=frame.time_s - angle_start_s,
                        yavg=frame.yavg,
                        satavg=frame.satavg,
                        ydif=frame.ydif,
                        udif=frame.udif,
                        vdif=frame.vdif,
                        scene_score=frame.scene_score,
                    )
                    for frame in frames
                    if angle_start_s <= frame.time_s < angle_end_s
                ]
                candidates_by_channel[channel] = build_rise_candidates(
                    angle_frames
                )
            traces.append(SpatialAngleTrace(
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
                candidates_by_channel=candidates_by_channel,
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
        "region_sample_width": SPATIAL_SAMPLE_WIDTH,
        "field_regions": [asdict(region) for region in FIELD_REGIONS],
    }


def evaluate_iteration_7b2(
    judgments_path: Path,
    feature_paths: list[Path],
    ffmpeg_path: Path,
    iteration_7b1_report_path: Path,
) -> dict[str, Any]:
    traces, frozen_input = build_spatial_angle_traces(
        judgments_path,
        feature_paths,
        ffmpeg_path,
    )
    previous, previous_fingerprint = _load_frozen_7b1_report(
        iteration_7b1_report_path,
        frozen_input["judgments_sha256"],
        frozen_input["source_manifest_fingerprints"],
    )
    development = select_spatial_policy(traces)
    development["temporal_v21_baseline"] = previous[
        "development"
    ]["temporal_v21_baseline"]
    development["iteration_7b1_baseline"] = {
        "policy": previous["development"]["selected"]["policy"],
        "overall": previous["development"]["selected"]["overall"],
        "by_cohort": previous["development"]["selected"]["by_cohort"],
    }
    gate_passed = development["selected"]["development_gate"]["passed"]
    frozen_input["iteration_7b1_report"] = previous_fingerprint
    return {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": (
            "development_candidate_ready_for_third_game"
            if gate_passed
            else "development_hold_spatial_refiner_not_ready"
        ),
        "frozen_input": frozen_input,
        "methodology": {
            "angle_split": "reuse frozen Temporal v2.1 angle ranges",
            "field_layout": "six fixed tiles across y=18%-82% of frame",
            "channels": list(CHANNELS),
            "policy_grid_is_predeclared": True,
            "run_pass_labels_used": False,
            "snap_labels_used_for_development_selection": True,
            "promotion_holdout": "third untouched game required",
        },
        "development": development,
    }


def render_markdown(report: dict[str, Any]) -> str:
    development = report["development"]
    selected = development["selected"]
    metrics = selected["overall"]
    temporal = development["temporal_v21_baseline"]["overall"]
    prior = development["iteration_7b1_baseline"]["overall"]
    gate = selected["development_gate"]
    lines = [
        "# TapeSift Iteration 7B.2 - Spatial Snap-Onset Refiner",
        "",
        (
            "This is development evidence from the two already-reviewed "
            "calibration games. It does not modify CSE Beta 4D, Temporal "
            "v2.1, or Iteration 7B.1."
        ),
        "",
        "## Selected deterministic policy",
        "",
        f"- Policy: `{selected['policy']['policy_id']}`",
        f"- Spatial channel: `{selected['policy']['channel']}`",
        f"- Policies evaluated: **{development['policy_count']}**",
        "",
        "## Development comparison",
        "",
        "| Measure | Temporal v2.1 | 7B.1 | 7B.2 spatial |",
        "| --- | ---: | ---: | ---: |",
        (
            f"| Median absolute error | "
            f"{temporal['median_absolute_error_ms']} ms | "
            f"{prior['median_absolute_error_ms']} ms | "
            f"{metrics['median_absolute_error_ms']} ms |"
        ),
        (
            f"| Within 500 ms | "
            f"{temporal['within_500_ms_share']:.1%} | "
            f"{prior['within_500_ms_share']:.1%} | "
            f"{metrics['within_500_ms_share']:.1%} |"
        ),
        (
            f"| P90 absolute error | "
            f"{temporal['p90_absolute_error_ms']} ms | "
            f"{prior['p90_absolute_error_ms']} ms | "
            f"{metrics['p90_absolute_error_ms']} ms |"
        ),
        (
            f"| Median signed bias | "
            f"{temporal['median_signed_bias_ms']} ms | "
            f"{prior['median_signed_bias_ms']} ms | "
            f"{metrics['median_signed_bias_ms']} ms |"
        ),
        (
            f"| Both angles within 500 ms | "
            f"{temporal['both_angles_within_500_ms_share']:.1%} | "
            f"{prior['both_angles_within_500_ms_share']:.1%} | "
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
                    "**THIRD GAME REQUIRED.** Freeze this spatial candidate "
                    "before evaluating one untouched game; do not promote "
                    "from this development report."
                )
            ]
            if gate["passed"]
            else [
                (
                    "**HOLD.** The spatial candidate still misses the "
                    "predeclared localization gate. Do not consume a third "
                    "untouched game and do not promote it."
                )
            ]
        ),
        "",
    ]
    return "\n".join(lines)


def write_iteration_7b2_report(
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
