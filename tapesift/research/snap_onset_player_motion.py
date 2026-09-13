"""Iteration 7C camera-compensated player-motion onset research.

The extractor estimates global camera motion between sampled frames, aligns
the previous frame to the current frame, and measures only the remaining
field-region motion. This is a research-only candidate and does not alter
CSE Beta 4D, Temporal v2.1, or either Iteration 7B refiner.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from tapesift.research.run_pass_features import SignalFrame
from tapesift.research.run_pass_temporal_features import TEMPORAL_SAMPLE_FPS
from tapesift.research.snap_onset_refiner import (
    EXPECTED_ANGLES,
    EXPECTED_PLAYS,
    RiseCandidate,
    _gate,
    _read_jsonl,
    _validate_manifest_fingerprints,
    build_rise_candidates,
    sha256_file,
)
from tapesift.research.snap_onset_spatial_refiner import (
    SpatialAngleTrace,
    SpatialPolicy,
    _cohort_rank,
    _load_frozen_7b1_report,
    _robust_rank,
    score_spatial_policy,
)


ITERATION_ID = "iteration-7c-camera-compensated-player-motion-v1"
SCHEMA_VERSION = "1.0"
SAMPLE_WIDTH = 480
FIELD_TOP_FRACTION = 0.15
FIELD_BOTTOM_FRACTION = 0.85
GRID_ROWS = 4
GRID_COLUMNS = 8
RESIDUAL_THRESHOLD = 12
CHANNELS = (
    "compensated_active_share",
    "compensated_cell_p75",
    "coordinated_cell_share",
    "localized_cell_excess",
)


def _configure_opencv() -> None:
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)


def _to_gray(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    scaled_height = max(2, round(height * SAMPLE_WIDTH / width))
    resized = cv2.resize(
        frame,
        (SAMPLE_WIDTH, scaled_height),
        interpolation=cv2.INTER_AREA,
    )
    return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)


def _field_bounds(height: int) -> tuple[int, int]:
    top = round(height * FIELD_TOP_FRACTION)
    bottom = round(height * FIELD_BOTTOM_FRACTION)
    return max(0, top), min(height, max(top + 1, bottom))


def estimate_global_camera_transform(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
) -> tuple[np.ndarray, int]:
    """Estimate a deterministic partial affine transform on field features."""
    if previous_gray.shape != current_gray.shape:
        raise ValueError("Camera-compensation frames must have equal shapes")
    top, bottom = _field_bounds(previous_gray.shape[0])
    feature_mask = np.zeros_like(previous_gray)
    feature_mask[top:bottom, :] = 255
    points = cv2.goodFeaturesToTrack(
        previous_gray,
        maxCorners=300,
        qualityLevel=0.01,
        minDistance=8,
        mask=feature_mask,
        blockSize=7,
    )
    identity = np.array(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        dtype=np.float32,
    )
    if points is None or len(points) < 8:
        return identity, 0
    tracked, status, _errors = cv2.calcOpticalFlowPyrLK(
        previous_gray,
        current_gray,
        points,
        None,
        winSize=(21, 21),
        maxLevel=3,
        criteria=(
            cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
            30,
            0.01,
        ),
    )
    if tracked is None or status is None:
        return identity, 0
    valid = status.reshape(-1).astype(bool)
    previous_points = points.reshape(-1, 2)[valid]
    current_points = tracked.reshape(-1, 2)[valid]
    if len(previous_points) < 8:
        return identity, len(previous_points)
    transform, inliers = cv2.estimateAffinePartial2D(
        previous_points,
        current_points,
        method=cv2.RANSAC,
        ransacReprojThreshold=2.0,
        maxIters=500,
        confidence=0.99,
        refineIters=10,
    )
    if transform is None:
        return identity, 0
    inlier_count = (
        int(np.count_nonzero(inliers))
        if inliers is not None
        else len(previous_points)
    )
    if inlier_count < 6:
        return identity, inlier_count
    return transform.astype(np.float32), inlier_count


def compensated_motion_channels(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
) -> tuple[dict[str, float], dict[str, float | int]]:
    """Measure residual field motion after removing global camera motion."""
    transform, inliers = estimate_global_camera_transform(
        previous_gray,
        current_gray,
    )
    aligned_previous = cv2.warpAffine(
        previous_gray,
        transform,
        (current_gray.shape[1], current_gray.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    previous_blur = cv2.GaussianBlur(aligned_previous, (5, 5), 0)
    current_blur = cv2.GaussianBlur(current_gray, (5, 5), 0)
    difference = cv2.absdiff(previous_blur, current_blur)
    top, bottom = _field_bounds(difference.shape[0])
    field_difference = difference[top:bottom, :]
    active = (field_difference >= RESIDUAL_THRESHOLD).astype(np.uint8)
    active = cv2.morphologyEx(
        active,
        cv2.MORPH_OPEN,
        np.ones((3, 3), dtype=np.uint8),
    )
    cell_shares: list[float] = []
    for row in range(GRID_ROWS):
        y0 = round(active.shape[0] * row / GRID_ROWS)
        y1 = round(active.shape[0] * (row + 1) / GRID_ROWS)
        for column in range(GRID_COLUMNS):
            x0 = round(active.shape[1] * column / GRID_COLUMNS)
            x1 = round(active.shape[1] * (column + 1) / GRID_COLUMNS)
            cell = active[y0:y1, x0:x1]
            cell_shares.append(float(np.mean(cell)) if cell.size else 0.0)
    median = statistics.median(cell_shares)
    p75 = float(np.percentile(cell_shares, 75))
    p90 = float(np.percentile(cell_shares, 90))
    channels = {
        "compensated_active_share": round(float(np.mean(active)) * 100, 6),
        "compensated_cell_p75": round(p75 * 100, 6),
        "coordinated_cell_share": round(
            sum(share >= 0.015 for share in cell_shares)
            / len(cell_shares)
            * 100,
            6,
        ),
        "localized_cell_excess": round(max(0.0, p90 - median) * 100, 6),
    }
    diagnostics: dict[str, float | int] = {
        "tracked_inliers": inliers,
        "translation_x": round(float(transform[0, 2]), 6),
        "translation_y": round(float(transform[1, 2]), 6),
        "scale_rotation_a": round(float(transform[0, 0]), 6),
        "scale_rotation_b": round(float(transform[0, 1]), 6),
    }
    return channels, diagnostics


def read_player_motion_channels(
    source: Path,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[str, list[SignalFrame]], dict[str, Any]]:
    """Decode one clip sequentially and retain sampled compensated signals."""
    if end_ms <= start_ms:
        raise ValueError("Player-motion range must have positive duration")
    _configure_opencv()
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open {source}")
    capture.set(cv2.CAP_PROP_POS_MSEC, float(start_ms))
    interval_ms = 1000.0 / TEMPORAL_SAMPLE_FPS
    target_ms = float(start_ms)
    channels = {channel: [] for channel in CHANNELS}
    previous_gray: np.ndarray | None = None
    sampled = 0
    compensation_failures = 0
    inlier_counts: list[int] = []
    try:
        while target_ms < end_ms:
            ok, frame = capture.read()
            if not ok:
                break
            position_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC))
            if position_ms + 0.5 < target_ms:
                continue
            gray = _to_gray(frame)
            time_s = round((target_ms - start_ms) / 1000.0, 6)
            if previous_gray is None:
                values = {channel: 0.0 for channel in CHANNELS}
            else:
                values, diagnostics = compensated_motion_channels(
                    previous_gray,
                    gray,
                )
                inliers = int(diagnostics["tracked_inliers"])
                inlier_counts.append(inliers)
                compensation_failures += inliers < 6
            for channel, value in values.items():
                channels[channel].append(SignalFrame(
                    time_s=time_s,
                    yavg=0.0,
                    satavg=0.0,
                    ydif=value,
                    udif=0.0,
                    vdif=0.0,
                ))
            sampled += 1
            previous_gray = gray
            target_ms += interval_ms
    finally:
        capture.release()
    if sampled < 1:
        raise ValueError(f"OpenCV returned no frames for {source.name}")
    return channels, {
        "sampled_frames": sampled,
        "compensation_failures": compensation_failures,
        "median_tracked_inliers": (
            statistics.median(inlier_counts) if inlier_counts else 0
        ),
    }


def player_motion_policy_grid() -> tuple[SpatialPolicy, ...]:
    from tapesift.research.snap_onset_refiner import policy_grid

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


def select_player_motion_policy(
    traces: list[SpatialAngleTrace],
) -> dict[str, Any]:
    results = [
        score_spatial_policy(traces, policy)
        for policy in player_motion_policy_grid()
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


def _load_frozen_7b2_report(
    path: Path,
    judgments_sha256: str,
    manifest_fingerprints: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, str]]:
    path = path.resolve()
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("iteration_id") != "iteration-7b2-spatial-field-motion-v1":
        raise ValueError("Input is not the frozen Iteration 7B.2 report")
    frozen = report.get("frozen_input", {})
    if frozen.get("judgments_sha256") != judgments_sha256:
        raise ValueError("Iteration 7B.2 judgment fingerprint changed")
    if frozen.get("source_manifest_fingerprints") != manifest_fingerprints:
        raise ValueError("Iteration 7B.2 manifest fingerprints changed")
    return report, {"path": str(path), "sha256": sha256_file(path)}


def build_player_motion_traces(
    judgments_path: Path,
    feature_paths: list[Path],
) -> tuple[list[SpatialAngleTrace], dict[str, Any]]:
    judgments_path = judgments_path.resolve()
    feature_paths = [path.resolve() for path in feature_paths]
    for path in [judgments_path, *feature_paths]:
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
    extraction_diagnostics = []
    for index, judgment in enumerate(judgments, start=1):
        clip_id = str(judgment.get("clip_id") or "")
        feature = features.get(clip_id)
        if feature is None:
            raise ValueError(f"Missing temporal feature record: {clip_id}")
        source = Path(str(feature["source_video_path"])).resolve()
        clip_start = int(feature["start_ms"])
        clip_end = int(feature["end_ms"])
        channel_frames, clip_diagnostics = read_player_motion_channels(
            source,
            clip_start,
            clip_end,
        )
        extraction_diagnostics.append({
            "clip_id": clip_id,
            **clip_diagnostics,
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
            candidates: dict[str, tuple[RiseCandidate, ...]] = {}
            for channel, frames in channel_frames.items():
                angle_frames = [
                    SignalFrame(
                        time_s=frame.time_s - angle_start_s,
                        yavg=0.0,
                        satavg=0.0,
                        ydif=frame.ydif,
                        udif=0.0,
                        vdif=0.0,
                    )
                    for frame in frames
                    if angle_start_s <= frame.time_s < angle_end_s
                ]
                candidates[channel] = build_rise_candidates(angle_frames)
            traces.append(SpatialAngleTrace(
                item_id=str(judgment["item_id"]),
                cohort_id=str(judgment["research_cohort_id"]),
                clip_id=clip_id,
                clip_number=int(judgment["clip_number"]),
                angle=angle,
                source_video_path=str(source),
                range_start_ms=clip_start + round(angle_start_s * 1000),
                range_end_ms=clip_start + round(angle_end_s * 1000),
                actual_snap_ms=int(measured["actual_snap_ms"]),
                temporal_v21_onset_ms=int(measured["proposed_onset_ms"]),
                candidates_by_channel=candidates,
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
        "sample_fps": TEMPORAL_SAMPLE_FPS,
        "sample_width": SAMPLE_WIDTH,
        "field_top_fraction": FIELD_TOP_FRACTION,
        "field_bottom_fraction": FIELD_BOTTOM_FRACTION,
        "grid_rows": GRID_ROWS,
        "grid_columns": GRID_COLUMNS,
        "residual_threshold": RESIDUAL_THRESHOLD,
        "opencv_version": cv2.__version__,
        "numpy_version": np.__version__,
        "extraction_diagnostics": extraction_diagnostics,
    }


def evaluate_iteration_7c(
    judgments_path: Path,
    feature_paths: list[Path],
    iteration_7b1_report_path: Path,
    iteration_7b2_report_path: Path,
) -> dict[str, Any]:
    traces, frozen_input = build_player_motion_traces(
        judgments_path,
        feature_paths,
    )
    prior_7b1, fingerprint_7b1 = _load_frozen_7b1_report(
        iteration_7b1_report_path,
        frozen_input["judgments_sha256"],
        frozen_input["source_manifest_fingerprints"],
    )
    prior_7b2, fingerprint_7b2 = _load_frozen_7b2_report(
        iteration_7b2_report_path,
        frozen_input["judgments_sha256"],
        frozen_input["source_manifest_fingerprints"],
    )
    development = select_player_motion_policy(traces)
    development["temporal_v21_baseline"] = prior_7b1[
        "development"
    ]["temporal_v21_baseline"]
    development["iteration_7b1_baseline"] = {
        "policy": prior_7b1["development"]["selected"]["policy"],
        "overall": prior_7b1["development"]["selected"]["overall"],
        "by_cohort": prior_7b1["development"]["selected"]["by_cohort"],
    }
    development["iteration_7b2_baseline"] = {
        "policy": prior_7b2["development"]["selected"]["policy"],
        "overall": prior_7b2["development"]["selected"]["overall"],
        "by_cohort": prior_7b2["development"]["selected"]["by_cohort"],
    }
    gate_passed = development["selected"]["development_gate"]["passed"]
    frozen_input["iteration_7b1_report"] = fingerprint_7b1
    frozen_input["iteration_7b2_report"] = fingerprint_7b2
    return {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": (
            "development_candidate_ready_for_third_game"
            if gate_passed
            else "development_hold_player_motion_not_ready"
        ),
        "frozen_input": frozen_input,
        "methodology": {
            "angle_split": "reuse frozen Temporal v2.1 angle ranges",
            "camera_compensation": (
                "RANSAC partial-affine alignment from tracked field features"
            ),
            "player_motion": (
                "thresholded residual motion in a fixed 4x8 field grid"
            ),
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
    prior_7b1 = development["iteration_7b1_baseline"]["overall"]
    prior_7b2 = development["iteration_7b2_baseline"]["overall"]
    gate = selected["development_gate"]
    rows = (
        ("Temporal v2.1", temporal),
        ("7B.1 global refiner", prior_7b1),
        ("7B.2 fixed spatial", prior_7b2),
        ("7C player motion", metrics),
    )
    lines = [
        "# TapeSift Iteration 7C - Player-Motion Snap Onset",
        "",
        (
            "This two-film development experiment subtracts estimated global "
            "camera motion before measuring field movement. It does not "
            "modify any production detector."
        ),
        "",
        "## Selected deterministic policy",
        "",
        f"- Policy: `{selected['policy']['policy_id']}`",
        f"- Motion channel: `{selected['policy']['channel']}`",
        f"- Policies evaluated: **{development['policy_count']}**",
        "",
        "## Development comparison",
        "",
        (
            "| Candidate | Median absolute error | Within 500 ms | "
            "P90 error | Signed bias | Both angles |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        *[
            (
                f"| {name} | {values['median_absolute_error_ms']} ms | "
                f"{values['within_500_ms_share']:.1%} | "
                f"{values['p90_absolute_error_ms']} ms | "
                f"{values['median_signed_bias_ms']} ms | "
                f"{values['both_angles_within_500_ms_share']:.1%} |"
            )
            for name, values in rows
        ],
        "",
        f"- Development gate passed: **{gate['passed']}**",
        "",
        "## Decision",
        "",
        *(
            [
                (
                    "**THIRD GAME REQUIRED.** Freeze this player-motion "
                    "candidate before evaluating one untouched game."
                )
            ]
            if gate["passed"]
            else [
                (
                    "**HOLD.** Camera-compensated player motion still misses "
                    "the localization gate. Do not consume a third game or "
                    "promote this candidate."
                )
            ]
        ),
        "",
    ]
    return "\n".join(lines)


def write_iteration_7c_report(
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
