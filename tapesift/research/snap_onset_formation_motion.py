"""Iteration 7D formation-anchored player-motion onset research.

The extractor estimates a formation band from the opening appearance of each
frozen camera angle. Camera-compensated residual motion is then measured only
inside that band. This research path does not modify any production detector
or prior onset candidate.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from tapesift.research.run_pass_features import SignalFrame
from tapesift.research.run_pass_temporal_features import TEMPORAL_SAMPLE_FPS
from tapesift.research.snap_onset_player_motion import (
    FIELD_BOTTOM_FRACTION,
    FIELD_TOP_FRACTION,
    GRID_COLUMNS,
    GRID_ROWS,
    RESIDUAL_THRESHOLD,
    SAMPLE_WIDTH,
    _configure_opencv,
    _field_bounds,
    estimate_global_camera_transform,
)
from tapesift.research.snap_onset_refiner import (
    EXPECTED_ANGLES,
    EXPECTED_PLAYS,
    RiseCandidate,
    _gate,
    _read_jsonl,
    _validate_manifest_fingerprints,
    build_rise_candidates,
    policy_grid,
    sha256_file,
)
from tapesift.research.snap_onset_spatial_refiner import (
    SpatialAngleTrace,
    SpatialPolicy,
    _cohort_rank,
    _robust_rank,
    score_spatial_policy,
)


ITERATION_ID = "iteration-7d-formation-anchored-player-motion-v1"
SCHEMA_VERSION = "1.0"
FORMATION_FRAMES = 12
FORMATION_GRID_ROWS = 6
FORMATION_GRID_COLUMNS = 12
CHANNELS = (
    "formation_active_share",
    "formation_cell_p75",
    "formation_coordinated_share",
    "formation_localized_excess",
)


@dataclass(frozen=True)
class FormationAnchor:
    axis: str
    center_fraction: float
    band_fraction: float
    confidence: float
    dominant_field_hue: int
    foreground_share: float


def _resize_bgr(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    scaled_height = max(2, round(height * SAMPLE_WIDTH / width))
    return cv2.resize(
        frame,
        (SAMPLE_WIDTH, scaled_height),
        interpolation=cv2.INTER_AREA,
    )


def _circular_hue_distance(hue: np.ndarray, target: int) -> np.ndarray:
    direct = np.abs(hue.astype(np.int16) - int(target))
    return np.minimum(direct, 180 - direct)


def _player_foreground_mask(frame: np.ndarray) -> tuple[np.ndarray, int]:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    top, bottom = _field_bounds(frame.shape[0])
    field_hsv = hsv[top:bottom, :]
    eligible = (
        (field_hsv[:, :, 1] >= 35)
        & (field_hsv[:, :, 2] >= 20)
    )
    hues = field_hsv[:, :, 0][eligible]
    dominant_hue = (
        int(np.bincount(hues, minlength=180).argmax())
        if hues.size
        else 60
    )
    hue_distance = _circular_hue_distance(hsv[:, :, 0], dominant_hue)
    field_like = (
        (hue_distance <= 14)
        & (hsv[:, :, 1] >= 30)
        & (hsv[:, :, 2] >= 18)
    )
    foreground = (~field_like).astype(np.uint8)
    field_gate = np.zeros_like(foreground)
    field_gate[top:bottom, :] = 1
    foreground *= field_gate
    foreground = cv2.morphologyEx(
        foreground,
        cv2.MORPH_OPEN,
        np.ones((3, 3), dtype=np.uint8),
    )
    component_count, labels, stats, _centroids = (
        cv2.connectedComponentsWithStats(foreground, connectivity=8)
    )
    cleaned = np.zeros_like(foreground)
    maximum_area = round(frame.shape[0] * frame.shape[1] * 0.015)
    for label in range(1, component_count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        if area < 8 or area > maximum_area:
            continue
        if width > frame.shape[1] * 0.35:
            continue
        if height > frame.shape[0] * 0.35:
            continue
        cleaned[labels == label] = 1
    cleaned = cv2.morphologyEx(
        cleaned,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
    )
    return cleaned, dominant_hue


def _smooth_axis(values: list[float]) -> list[float]:
    return [
        statistics.fmean(values[max(0, index - 1):index + 2])
        for index in range(len(values))
    ]


def localize_formation_band(
    opening_frames: list[np.ndarray],
) -> FormationAnchor:
    """Estimate whether the formation is concentrated along x or y."""
    if not opening_frames:
        raise ValueError("Formation localization requires opening frames")
    shape = opening_frames[0].shape
    if any(frame.shape != shape for frame in opening_frames):
        raise ValueError("Formation frames must have equal shapes")
    median_frame = np.median(
        np.stack(opening_frames, axis=0),
        axis=0,
    ).astype(np.uint8)
    foreground, dominant_hue = _player_foreground_mask(median_frame)
    top, bottom = _field_bounds(foreground.shape[0])
    field = foreground[top:bottom, :]
    densities = np.zeros(
        (FORMATION_GRID_ROWS, FORMATION_GRID_COLUMNS),
        dtype=np.float64,
    )
    for row in range(FORMATION_GRID_ROWS):
        y0 = round(field.shape[0] * row / FORMATION_GRID_ROWS)
        y1 = round(field.shape[0] * (row + 1) / FORMATION_GRID_ROWS)
        for column in range(FORMATION_GRID_COLUMNS):
            x0 = round(
                field.shape[1] * column / FORMATION_GRID_COLUMNS
            )
            x1 = round(
                field.shape[1] * (column + 1) / FORMATION_GRID_COLUMNS
            )
            cell = field[y0:y1, x0:x1]
            densities[row, column] = (
                float(np.mean(cell)) if cell.size else 0.0
            )
    column_values = _smooth_axis(densities.mean(axis=0).tolist())
    row_values = _smooth_axis(densities.mean(axis=1).tolist())
    column_concentration = max(column_values) / max(
        statistics.fmean(column_values),
        0.000001,
    )
    row_concentration = max(row_values) / max(
        statistics.fmean(row_values),
        0.000001,
    )
    if column_concentration >= row_concentration:
        axis = "x"
        values = column_values
        band_fraction = 5 / FORMATION_GRID_COLUMNS
        concentration = column_concentration
    else:
        axis = "y"
        values = row_values
        band_fraction = 3 / FORMATION_GRID_ROWS
        concentration = row_concentration
    peak = int(np.argmax(values))
    total = sum(values[max(0, peak - 1):peak + 2])
    if total > 0:
        weighted = sum(
            index * values[index]
            for index in range(max(0, peak - 1), min(len(values), peak + 2))
        ) / total
    else:
        weighted = (len(values) - 1) / 2
    center = (weighted + 0.5) / len(values)
    foreground_share = float(np.mean(field)) if field.size else 0.0
    confidence = min(1.0, max(0.0, (concentration - 1.0) / 2.0))
    if foreground_share < 0.001:
        center = 0.5
        confidence = 0.0
    return FormationAnchor(
        axis=axis,
        center_fraction=round(float(center), 6),
        band_fraction=round(float(band_fraction), 6),
        confidence=round(float(confidence), 6),
        dominant_field_hue=dominant_hue,
        foreground_share=round(foreground_share, 6),
    )


def formation_roi_mask(
    shape: tuple[int, int],
    anchor: FormationAnchor,
) -> np.ndarray:
    height, width = shape
    top, bottom = _field_bounds(height)
    mask = np.zeros((height, width), dtype=np.uint8)
    if anchor.axis == "x":
        center = round(anchor.center_fraction * width)
        half = round(anchor.band_fraction * width / 2)
        left = max(0, center - half)
        right = min(width, center + half)
        mask[top:bottom, left:right] = 1
    elif anchor.axis == "y":
        field_height = bottom - top
        center = top + round(anchor.center_fraction * field_height)
        half = round(anchor.band_fraction * field_height / 2)
        y0 = max(top, center - half)
        y1 = min(bottom, center + half)
        mask[y0:y1, :] = 1
    else:
        raise ValueError(f"Unknown formation axis: {anchor.axis}")
    return mask


def formation_motion_channels(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    anchor: FormationAnchor,
) -> tuple[dict[str, float], dict[str, float | int]]:
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
    difference = cv2.absdiff(
        cv2.GaussianBlur(aligned_previous, (5, 5), 0),
        cv2.GaussianBlur(current_gray, (5, 5), 0),
    )
    active = (difference >= RESIDUAL_THRESHOLD).astype(np.uint8)
    active = cv2.morphologyEx(
        active,
        cv2.MORPH_OPEN,
        np.ones((3, 3), dtype=np.uint8),
    )
    roi = formation_roi_mask(active.shape, anchor)
    roi_size = int(np.count_nonzero(roi))
    active *= roi
    cell_shares: list[float] = []
    top, bottom = _field_bounds(active.shape[0])
    for row in range(GRID_ROWS):
        y0 = top + round((bottom - top) * row / GRID_ROWS)
        y1 = top + round((bottom - top) * (row + 1) / GRID_ROWS)
        for column in range(GRID_COLUMNS):
            x0 = round(active.shape[1] * column / GRID_COLUMNS)
            x1 = round(
                active.shape[1] * (column + 1) / GRID_COLUMNS
            )
            cell_roi = roi[y0:y1, x0:x1]
            if np.mean(cell_roi) < 0.5:
                continue
            cell = active[y0:y1, x0:x1]
            cell_shares.append(float(np.mean(cell)) if cell.size else 0.0)
    if not cell_shares:
        cell_shares = [0.0]
    median = statistics.median(cell_shares)
    p75 = float(np.percentile(cell_shares, 75))
    p90 = float(np.percentile(cell_shares, 90))
    values = {
        "formation_active_share": round(
            float(np.count_nonzero(active)) / max(roi_size, 1) * 100,
            6,
        ),
        "formation_cell_p75": round(p75 * 100, 6),
        "formation_coordinated_share": round(
            sum(share >= 0.015 for share in cell_shares)
            / len(cell_shares)
            * 100,
            6,
        ),
        "formation_localized_excess": round(
            max(0.0, p90 - median) * 100,
            6,
        ),
    }
    return values, {
        "tracked_inliers": inliers,
        "roi_cells": len(cell_shares),
    }


def read_formation_motion_channels(
    source: Path,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[str, list[SignalFrame]], dict[str, Any]]:
    if end_ms <= start_ms:
        raise ValueError("Formation-motion range must have positive duration")
    _configure_opencv()
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open {source}")
    capture.set(cv2.CAP_PROP_POS_MSEC, float(start_ms))
    interval_ms = 1000.0 / TEMPORAL_SAMPLE_FPS
    target_ms = float(start_ms)
    sampled_frames: list[tuple[float, np.ndarray]] = []
    opening_frames: list[np.ndarray] = []
    try:
        while target_ms < end_ms:
            ok, frame = capture.read()
            if not ok:
                break
            position_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC))
            if position_ms + 0.5 < target_ms:
                continue
            resized = _resize_bgr(frame)
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            time_s = round((target_ms - start_ms) / 1000.0, 6)
            sampled_frames.append((time_s, gray))
            if len(opening_frames) < FORMATION_FRAMES:
                opening_frames.append(resized)
            target_ms += interval_ms
    finally:
        capture.release()
    if not sampled_frames:
        raise ValueError(f"OpenCV returned no frames for {source.name}")
    anchor = localize_formation_band(opening_frames)
    channels = {channel: [] for channel in CHANNELS}
    failures = 0
    roi_cells: list[int] = []
    previous_gray: np.ndarray | None = None
    for time_s, gray in sampled_frames:
        if previous_gray is None:
            values = {channel: 0.0 for channel in CHANNELS}
        else:
            values, diagnostics = formation_motion_channels(
                previous_gray,
                gray,
                anchor,
            )
            failures += int(diagnostics["tracked_inliers"]) < 6
            roi_cells.append(int(diagnostics["roi_cells"]))
        for channel, value in values.items():
            channels[channel].append(SignalFrame(
                time_s=time_s,
                yavg=0.0,
                satavg=0.0,
                ydif=value,
                udif=0.0,
                vdif=0.0,
            ))
        previous_gray = gray
    return channels, {
        "sampled_frames": len(sampled_frames),
        "compensation_failures": failures,
        "formation_anchor": asdict(anchor),
        "median_roi_cells": statistics.median(roi_cells) if roi_cells else 0,
    }


def formation_policy_grid() -> tuple[SpatialPolicy, ...]:
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


def select_formation_policy(
    traces: list[SpatialAngleTrace],
) -> dict[str, Any]:
    results = [
        score_spatial_policy(traces, policy)
        for policy in formation_policy_grid()
    ]
    selected = min(results, key=_robust_rank)
    cohorts = sorted(selected["by_cohort"])
    transfer = []
    for training in cohorts:
        winner = min(
            results,
            key=lambda result: _cohort_rank(result, training),
        )
        validation = next(cohort for cohort in cohorts if cohort != training)
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


def _load_prior_report(
    path: Path,
    iteration_id: str,
    judgments_sha256: str,
    manifest_fingerprints: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, str]]:
    path = path.resolve()
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("iteration_id") != iteration_id:
        raise ValueError(f"Unexpected prior iteration report: {path}")
    frozen = report.get("frozen_input", {})
    if frozen.get("judgments_sha256") != judgments_sha256:
        raise ValueError("Prior report judgment fingerprint changed")
    if frozen.get("source_manifest_fingerprints") != manifest_fingerprints:
        raise ValueError("Prior report manifest fingerprints changed")
    return report, {"path": str(path), "sha256": sha256_file(path)}


def build_formation_motion_traces(
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
    progress = 0
    for judgment in judgments:
        clip_id = str(judgment.get("clip_id") or "")
        feature = features.get(clip_id)
        if feature is None:
            raise ValueError(f"Missing temporal feature record: {clip_id}")
        source = Path(str(feature["source_video_path"])).resolve()
        clip_start = int(feature["start_ms"])
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
            frames, angle_diagnostics = read_formation_motion_channels(
                source,
                range_start_ms,
                range_end_ms,
            )
            candidates: dict[str, tuple[RiseCandidate, ...]] = {
                channel: build_rise_candidates(channel_frames)
                for channel, channel_frames in frames.items()
            }
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
                candidates_by_channel=candidates,
            ))
            extraction_diagnostics.append({
                "clip_id": clip_id,
                "angle": angle,
                **angle_diagnostics,
            })
            progress += 1
            print(
                f"[{progress:02d}/{EXPECTED_ANGLES}] "
                f"{judgment['research_cohort_id']} clip "
                f"{judgment['clip_number']} angle {angle}",
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
        "formation_frames": FORMATION_FRAMES,
        "formation_grid_rows": FORMATION_GRID_ROWS,
        "formation_grid_columns": FORMATION_GRID_COLUMNS,
        "motion_grid_rows": GRID_ROWS,
        "motion_grid_columns": GRID_COLUMNS,
        "residual_threshold": RESIDUAL_THRESHOLD,
        "opencv_version": cv2.__version__,
        "numpy_version": np.__version__,
        "extraction_diagnostics": extraction_diagnostics,
    }


def evaluate_iteration_7d(
    judgments_path: Path,
    feature_paths: list[Path],
    iteration_7b1_report_path: Path,
    iteration_7c_report_path: Path,
    iteration_7c1_report_path: Path,
) -> dict[str, Any]:
    traces, frozen_input = build_formation_motion_traces(
        judgments_path,
        feature_paths,
    )
    prior_7b1, fingerprint_7b1 = _load_prior_report(
        iteration_7b1_report_path,
        "iteration-7b-earliest-qualified-rise-v1",
        frozen_input["judgments_sha256"],
        frozen_input["source_manifest_fingerprints"],
    )
    prior_7c, fingerprint_7c = _load_prior_report(
        iteration_7c_report_path,
        "iteration-7c-camera-compensated-player-motion-v1",
        frozen_input["judgments_sha256"],
        frozen_input["source_manifest_fingerprints"],
    )
    prior_7c1, fingerprint_7c1 = _load_prior_report(
        iteration_7c1_report_path,
        "iteration-7c1-global-player-disagreement-fusion-v1",
        frozen_input["judgments_sha256"],
        frozen_input["source_manifest_fingerprints"],
    )
    development = select_formation_policy(traces)
    development["temporal_v21_baseline"] = prior_7b1[
        "development"
    ]["temporal_v21_baseline"]
    for key, report in (
        ("iteration_7b1_baseline", prior_7b1),
        ("iteration_7c_baseline", prior_7c),
        ("iteration_7c1_baseline", prior_7c1),
    ):
        selected = report["development"]["selected"]
        development[key] = {
            "policy": selected["policy"],
            "overall": selected["overall"],
            "by_cohort": selected["by_cohort"],
        }
    selected = development["selected"]
    gate_passed = selected["development_gate"]["passed"]
    frozen_input["iteration_7b1_report"] = fingerprint_7b1
    frozen_input["iteration_7c_report"] = fingerprint_7c
    frozen_input["iteration_7c1_report"] = fingerprint_7c1
    return {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": (
            "development_candidate_ready_for_third_game"
            if gate_passed
            else "development_hold_formation_motion_not_ready"
        ),
        "frozen_input": frozen_input,
        "methodology": {
            "angle_split": "reuse frozen Temporal v2.1 angle ranges",
            "formation_anchor": (
                "dominant-field-color foreground concentration in the "
                "opening 1.5 seconds of each angle"
            ),
            "camera_compensation": (
                "RANSAC partial-affine alignment from tracked field features"
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
    rows = (
        ("7B.1 global", development["iteration_7b1_baseline"]["overall"]),
        ("7C player", development["iteration_7c_baseline"]["overall"]),
        ("7C.1 fusion", development["iteration_7c1_baseline"]["overall"]),
        ("7D formation", metrics),
    )
    gate = selected["development_gate"]
    lines = [
        "# TapeSift Iteration 7D - Formation-Anchored Player Motion",
        "",
        (
            "This development experiment localizes a formation band before "
            "measuring camera-compensated player motion. It does not modify "
            "a production detector."
        ),
        "",
        "## Selected deterministic policy",
        "",
        f"- Policy: `{selected['policy']['policy_id']}`",
        f"- Policies evaluated: **{development['policy_count']}**",
        "",
        "## Development comparison",
        "",
        (
            "| Candidate | Median error | Within 500 ms | P90 error | "
            "Signed bias | Both angles |"
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
                    "**THIRD GAME REQUIRED.** Freeze this formation policy "
                    "before evaluating one untouched game."
                )
            ]
            if gate["passed"]
            else [
                (
                    "**HOLD.** Formation anchoring still misses the "
                    "localization gate. Do not consume a third game or "
                    "promote this candidate."
                )
            ]
        ),
        "",
    ]
    return "\n".join(lines)


def write_iteration_7d_report(
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
