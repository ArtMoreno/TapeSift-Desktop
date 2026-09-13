"""Iteration 7E detected-player snap-onset research.

An official pretrained person detector supplies player foot-points. Global
camera motion is estimated from image features, and only residual matched
person movement is retained. The implementation is research-only and does
not modify prior candidates or any production detector.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from tapesift.research.run_pass_features import SignalFrame
from tapesift.research.snap_onset_formation_motion import _load_prior_report
from tapesift.research.snap_onset_player_motion import (
    _configure_opencv,
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


ITERATION_ID = "iteration-7e-detected-player-motion-v1"
SCHEMA_VERSION = "1.0"
SAMPLE_FPS = 4.0
MODEL_IMAGE_SIZE = 640
MODEL_CONFIDENCE = 0.15
PERSON_CLASS_ID = 0
MAX_MATCH_DISTANCE = 0.12
MOVING_DISTANCE = 0.008
CHANNELS = (
    "detected_moving_share",
    "detected_speed_median",
    "detected_speed_p75",
    "detected_motion_mass",
)


@dataclass(frozen=True)
class PlayerDetection:
    foot_x: float
    foot_y: float
    confidence: float


def configure_inference() -> dict[str, str]:
    import torch
    import ultralytics

    _configure_opencv()
    torch.manual_seed(0)
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    return {
        "torch_version": torch.__version__,
        "ultralytics_version": ultralytics.__version__,
        "opencv_version": cv2.__version__,
        "numpy_version": np.__version__,
    }


def filter_person_detections(
    boxes_xyxy: np.ndarray,
    confidences: np.ndarray,
    frame_width: int,
    frame_height: int,
) -> tuple[PlayerDetection, ...]:
    """Retain plausible on-field person boxes and return their foot-points."""
    retained = []
    for box, confidence in zip(boxes_xyxy, confidences, strict=True):
        x1, y1, x2, y2 = (float(value) for value in box)
        width = x2 - x1
        height = y2 - y1
        foot_x = (x1 + x2) / 2
        foot_y = y2
        if float(confidence) < MODEL_CONFIDENCE:
            continue
        if width < 2 or height < frame_height * 0.015:
            continue
        if width > frame_width * 0.20 or height > frame_height * 0.35:
            continue
        if not (0 <= foot_x < frame_width):
            continue
        if not (frame_height * 0.08 <= foot_y <= frame_height * 0.92):
            continue
        retained.append(PlayerDetection(
            foot_x=round(foot_x, 6),
            foot_y=round(foot_y, 6),
            confidence=round(float(confidence), 6),
        ))
    return tuple(sorted(
        retained,
        key=lambda item: (item.foot_x, item.foot_y, -item.confidence),
    ))


def _greedy_matches(
    predicted: np.ndarray,
    current: np.ndarray,
    maximum_distance_px: float,
) -> list[tuple[int, int, float]]:
    if not len(predicted) or not len(current):
        return []
    distances = np.linalg.norm(
        predicted[:, None, :] - current[None, :, :],
        axis=2,
    )
    candidates = [
        (float(distances[previous, present]), previous, present)
        for previous in range(len(predicted))
        for present in range(len(current))
        if distances[previous, present] <= maximum_distance_px
    ]
    matches = []
    used_previous: set[int] = set()
    used_current: set[int] = set()
    for distance, previous, present in sorted(candidates):
        if previous in used_previous or present in used_current:
            continue
        used_previous.add(previous)
        used_current.add(present)
        matches.append((previous, present, distance))
    return matches


def detected_player_motion_channels(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    previous: tuple[PlayerDetection, ...],
    current: tuple[PlayerDetection, ...],
) -> tuple[dict[str, float], dict[str, int | float]]:
    """Match detected foot-points after applying global camera motion."""
    transform, inliers = estimate_global_camera_transform(
        previous_gray,
        current_gray,
    )
    previous_points = np.array(
        [[item.foot_x, item.foot_y] for item in previous],
        dtype=np.float32,
    )
    current_points = np.array(
        [[item.foot_x, item.foot_y] for item in current],
        dtype=np.float32,
    )
    if len(previous_points):
        homogeneous = np.column_stack((
            previous_points,
            np.ones(len(previous_points), dtype=np.float32),
        ))
        predicted = homogeneous @ transform.T
    else:
        predicted = previous_points
    diagonal = math.hypot(current_gray.shape[1], current_gray.shape[0])
    matches = _greedy_matches(
        predicted,
        current_points,
        diagonal * MAX_MATCH_DISTANCE,
    )
    normalized = [
        distance / max(diagonal, 1.0)
        for _previous, _current, distance in matches
    ]
    if normalized:
        moving_share = sum(
            distance >= MOVING_DISTANCE for distance in normalized
        ) / len(normalized)
        median = statistics.median(normalized)
        p75 = float(np.percentile(normalized, 75))
        motion_mass = sum(normalized) / max(
            len(previous),
            len(current),
            1,
        )
    else:
        moving_share = median = p75 = motion_mass = 0.0
    values = {
        "detected_moving_share": round(moving_share * 100, 6),
        "detected_speed_median": round(median * 1000, 6),
        "detected_speed_p75": round(p75 * 1000, 6),
        "detected_motion_mass": round(motion_mass * 1000, 6),
    }
    return values, {
        "camera_inliers": inliers,
        "previous_detections": len(previous),
        "current_detections": len(current),
        "matched_detections": len(matches),
        "match_share": round(
            len(matches) / max(len(previous), len(current), 1),
            6,
        ),
    }


def read_sampled_frames(
    source: Path,
    start_ms: int,
    end_ms: int,
) -> list[tuple[float, np.ndarray]]:
    if end_ms <= start_ms:
        raise ValueError("Detected-player range must have positive duration")
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open {source}")
    capture.set(cv2.CAP_PROP_POS_MSEC, float(start_ms))
    interval_ms = 1000.0 / SAMPLE_FPS
    target_ms = float(start_ms)
    frames = []
    try:
        while target_ms < end_ms:
            ok, frame = capture.read()
            if not ok:
                break
            position_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC))
            if position_ms + 0.5 < target_ms:
                continue
            frames.append((
                round((target_ms - start_ms) / 1000.0, 6),
                frame,
            ))
            target_ms += interval_ms
    finally:
        capture.release()
    if not frames:
        raise ValueError(f"OpenCV returned no frames for {source.name}")
    return frames


def detections_from_results(
    frames: list[tuple[float, np.ndarray]],
    results: list[Any],
) -> list[tuple[PlayerDetection, ...]]:
    if len(frames) != len(results):
        raise ValueError("Detector result count does not match sampled frames")
    output = []
    for (_time_s, frame), result in zip(frames, results, strict=True):
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            output.append(())
            continue
        output.append(filter_person_detections(
            boxes.xyxy.cpu().numpy(),
            boxes.conf.cpu().numpy(),
            frame.shape[1],
            frame.shape[0],
        ))
    return output


def measure_detected_player_angle(
    model: Any,
    source: Path,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[str, list[SignalFrame]], dict[str, Any]]:
    sampled = read_sampled_frames(source, start_ms, end_ms)
    results = model.predict(
        [frame for _time_s, frame in sampled],
        imgsz=MODEL_IMAGE_SIZE,
        conf=MODEL_CONFIDENCE,
        classes=[PERSON_CLASS_ID],
        device="cpu",
        batch=16,
        verbose=False,
    )
    detections = detections_from_results(sampled, results)
    channels = {channel: [] for channel in CHANNELS}
    detection_counts = []
    match_shares = []
    low_match_frames = 0
    previous_gray: np.ndarray | None = None
    previous_detections: tuple[PlayerDetection, ...] = ()
    for (time_s, frame), current_detections in zip(
        sampled,
        detections,
        strict=True,
    ):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detection_counts.append(len(current_detections))
        if previous_gray is None:
            values = {channel: 0.0 for channel in CHANNELS}
        else:
            values, diagnostics = detected_player_motion_channels(
                previous_gray,
                gray,
                previous_detections,
                current_detections,
            )
            match_share = float(diagnostics["match_share"])
            match_shares.append(match_share)
            low_match_frames += match_share < 0.40
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
        previous_detections = current_detections
    return channels, {
        "sampled_frames": len(sampled),
        "median_person_detections": (
            statistics.median(detection_counts) if detection_counts else 0
        ),
        "minimum_person_detections": min(detection_counts, default=0),
        "median_match_share": (
            statistics.median(match_shares) if match_shares else 0
        ),
        "low_match_frames": low_match_frames,
    }


def detected_player_policy_grid() -> tuple[SpatialPolicy, ...]:
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


def select_detected_player_policy(
    traces: list[SpatialAngleTrace],
) -> dict[str, Any]:
    results = [
        score_spatial_policy(traces, policy)
        for policy in detected_player_policy_grid()
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


def build_detected_player_traces(
    judgments_path: Path,
    feature_paths: list[Path],
    model_path: Path,
) -> tuple[list[SpatialAngleTrace], dict[str, Any]]:
    from ultralytics import YOLO

    judgments_path = judgments_path.resolve()
    feature_paths = [path.resolve() for path in feature_paths]
    model_path = model_path.resolve()
    for path in [judgments_path, model_path, *feature_paths]:
        if not path.is_file():
            raise FileNotFoundError(path)
    versions = configure_inference()
    model = YOLO(str(model_path), verbose=False)
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
        for measured, diagnostic in zip(
            angle_judgments,
            diagnostics,
            strict=True,
        ):
            angle = int(measured["angle"])
            if angle != int(diagnostic["angle"]):
                raise ValueError(f"Angle order changed for {clip_id}")
            start_s = float(diagnostic["start_seconds"])
            end_s = float(diagnostic["end_seconds"])
            range_start_ms = clip_start + round(start_s * 1000)
            range_end_ms = clip_start + round(end_s * 1000)
            frames, angle_diagnostics = measure_detected_player_angle(
                model,
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
        "model_path": str(model_path),
        "model_sha256": sha256_file(model_path),
        "sample_fps": SAMPLE_FPS,
        "model_image_size": MODEL_IMAGE_SIZE,
        "model_confidence": MODEL_CONFIDENCE,
        "person_class_id": PERSON_CLASS_ID,
        "maximum_match_distance": MAX_MATCH_DISTANCE,
        "moving_distance": MOVING_DISTANCE,
        **versions,
        "extraction_diagnostics": extraction_diagnostics,
    }


def evaluate_iteration_7e(
    judgments_path: Path,
    feature_paths: list[Path],
    model_path: Path,
    iteration_7b1_report_path: Path,
    iteration_7c1_report_path: Path,
    iteration_7d_report_path: Path,
) -> dict[str, Any]:
    traces, frozen_input = build_detected_player_traces(
        judgments_path,
        feature_paths,
        model_path,
    )
    prior_reports = {}
    fingerprints = {}
    for key, path, iteration_id in (
        (
            "iteration_7b1",
            iteration_7b1_report_path,
            "iteration-7b-earliest-qualified-rise-v1",
        ),
        (
            "iteration_7c1",
            iteration_7c1_report_path,
            "iteration-7c1-global-player-disagreement-fusion-v1",
        ),
        (
            "iteration_7d",
            iteration_7d_report_path,
            "iteration-7d-formation-anchored-player-motion-v1",
        ),
    ):
        report, fingerprint = _load_prior_report(
            path,
            iteration_id,
            frozen_input["judgments_sha256"],
            frozen_input["source_manifest_fingerprints"],
        )
        prior_reports[key] = report
        fingerprints[key] = fingerprint
    development = select_detected_player_policy(traces)
    development["temporal_v21_baseline"] = prior_reports[
        "iteration_7b1"
    ]["development"]["temporal_v21_baseline"]
    for key, report in prior_reports.items():
        selected = report["development"]["selected"]
        development[f"{key}_baseline"] = {
            "policy": selected["policy"],
            "overall": selected["overall"],
            "by_cohort": selected["by_cohort"],
        }
        frozen_input[f"{key}_report"] = fingerprints[key]
    selected = development["selected"]
    gate_passed = selected["development_gate"]["passed"]
    return {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": (
            "development_candidate_ready_for_third_game"
            if gate_passed
            else "development_hold_detected_players_not_ready"
        ),
        "frozen_input": frozen_input,
        "methodology": {
            "angle_split": "reuse frozen Temporal v2.1 angle ranges",
            "detector": "official COCO-pretrained YOLO26n person class",
            "player_point": "bottom-center of filtered person box",
            "camera_compensation": (
                "RANSAC partial-affine alignment from tracked field features"
            ),
            "matching": (
                "deterministic greedy nearest point after camera transform"
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
        ("7C.1 fusion", development["iteration_7c1_baseline"]["overall"]),
        ("7D formation", development["iteration_7d_baseline"]["overall"]),
        ("7E detected players", metrics),
    )
    gate = selected["development_gate"]
    lines = [
        "# TapeSift Iteration 7E - Detected-Player Motion",
        "",
        (
            "This development experiment measures camera-compensated motion "
            "of pretrained person detections. It does not modify a "
            "production detector."
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
                    "**THIRD GAME REQUIRED.** Freeze this detected-player "
                    "policy before evaluating one untouched game."
                )
            ]
            if gate["passed"]
            else [
                (
                    "**HOLD.** Detected-player motion still misses the "
                    "localization gate. Do not consume a third game or "
                    "promote this candidate."
                )
            ]
        ),
        "",
    ]
    return "\n".join(lines)


def write_iteration_7e_report(
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
