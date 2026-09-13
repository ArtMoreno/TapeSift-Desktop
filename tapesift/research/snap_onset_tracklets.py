"""Iteration 7H deterministic short-tracklet snap-onset research.

Short player identities suppress frame-to-frame detector flicker before the
frozen Iteration 7E onset policy is applied. There is one predeclared
configuration and no parameter search.
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
from tapesift.research.snap_onset_detected_players import (
    MODEL_CONFIDENCE,
    MODEL_IMAGE_SIZE,
    PERSON_CLASS_ID,
    PlayerDetection,
    _greedy_matches,
    configure_inference,
    detections_from_results,
    read_sampled_frames,
)
from tapesift.research.snap_onset_formation_motion import _load_prior_report
from tapesift.research.snap_onset_player_motion import (
    estimate_global_camera_transform,
)
from tapesift.research.snap_onset_refiner import (
    EXPECTED_ANGLES,
    EXPECTED_PLAYS,
    _gate,
    _read_jsonl,
    _validate_manifest_fingerprints,
    build_rise_candidates,
    sha256_file,
)
from tapesift.research.snap_onset_spatial_refiner import (
    SpatialAngleTrace,
    SpatialPolicy,
    score_spatial_policy,
)


ITERATION_ID = "iteration-7h-deterministic-short-tracklets-v1"
SCHEMA_VERSION = "1.0"
SAMPLE_FPS = 4.0
CHANNEL = "tracklet_moving_share"
MAXIMUM_ASSIGNMENT_DISTANCE = 0.08
MOVING_DISTANCE = 0.008
MAXIMUM_MISSED_FRAMES = 1
MINIMUM_STABLE_AGE = 3
MINIMUM_STABLE_MATCHES = 8
MINIMUM_STABLE_MATCH_SHARE = 0.40
VELOCITY_DAMPING = 0.50
TRACKLET_POLICY = SpatialPolicy(
    channel=CHANNEL,
    relative_score_floor=0.80,
    minimum_score=0.12,
    minimum_post_pre_ratio=2.0,
    output_lag_seconds=0.25,
)


@dataclass(frozen=True)
class PlayerTrack:
    track_id: int
    foot_x: float
    foot_y: float
    velocity_x: float
    velocity_y: float
    age: int
    matched_streak: int
    missed_frames: int


def initialize_tracklets(
    detections: tuple[PlayerDetection, ...],
    next_track_id: int = 0,
) -> tuple[tuple[PlayerTrack, ...], int]:
    tracks = tuple(
        PlayerTrack(
            track_id=next_track_id + index,
            foot_x=item.foot_x,
            foot_y=item.foot_y,
            velocity_x=0.0,
            velocity_y=0.0,
            age=1,
            matched_streak=1,
            missed_frames=0,
        )
        for index, item in enumerate(detections)
    )
    return tracks, next_track_id + len(tracks)


def advance_tracklets(
    tracks: tuple[PlayerTrack, ...],
    detections: tuple[PlayerDetection, ...],
    camera_transform: np.ndarray,
    frame_shape: tuple[int, int],
    next_track_id: int,
) -> tuple[tuple[PlayerTrack, ...], float, dict[str, Any], int]:
    """Advance stable identities and return a quality-gated motion share."""
    height, width = frame_shape
    diagonal = math.hypot(width, height)
    track_points = np.array(
        [[track.foot_x, track.foot_y] for track in tracks],
        dtype=np.float32,
    )
    if len(track_points):
        homogeneous = np.column_stack((
            track_points,
            np.ones(len(track_points), dtype=np.float32),
        ))
        transformed = homogeneous @ camera_transform.T
        predicted = transformed + np.array([
            [
                track.velocity_x * VELOCITY_DAMPING,
                track.velocity_y * VELOCITY_DAMPING,
            ]
            for track in tracks
        ], dtype=np.float32)
    else:
        transformed = predicted = track_points
    current_points = np.array(
        [[item.foot_x, item.foot_y] for item in detections],
        dtype=np.float32,
    )
    matches = _greedy_matches(
        predicted,
        current_points,
        diagonal * MAXIMUM_ASSIGNMENT_DISTANCE,
    )
    matched_tracks = {track_index for track_index, _det, _dist in matches}
    matched_detections = {det_index for _track, det_index, _dist in matches}
    updated = []
    stable_matches = 0
    moving_stable_matches = 0
    residual_distances = []
    for track_index, detection_index, _distance in matches:
        track = tracks[track_index]
        detection = detections[detection_index]
        residual_x = float(detection.foot_x - transformed[track_index][0])
        residual_y = float(detection.foot_y - transformed[track_index][1])
        residual_distance = math.hypot(residual_x, residual_y) / max(
            diagonal, 1.0
        )
        residual_distances.append(residual_distance)
        streak = track.matched_streak + 1 if track.missed_frames == 0 else 1
        age = track.age + 1
        is_stable = age >= MINIMUM_STABLE_AGE and streak >= MINIMUM_STABLE_AGE
        if is_stable:
            stable_matches += 1
            moving_stable_matches += residual_distance >= MOVING_DISTANCE
        updated.append(PlayerTrack(
            track_id=track.track_id,
            foot_x=round(detection.foot_x, 6),
            foot_y=round(detection.foot_y, 6),
            velocity_x=round(
                track.velocity_x * VELOCITY_DAMPING
                + residual_x * (1.0 - VELOCITY_DAMPING),
                6,
            ),
            velocity_y=round(
                track.velocity_y * VELOCITY_DAMPING
                + residual_y * (1.0 - VELOCITY_DAMPING),
                6,
            ),
            age=age,
            matched_streak=streak,
            missed_frames=0,
        ))

    retained_missed = 0
    for track_index, track in enumerate(tracks):
        if track_index in matched_tracks:
            continue
        missed = track.missed_frames + 1
        if missed > MAXIMUM_MISSED_FRAMES:
            continue
        retained_missed += 1
        updated.append(PlayerTrack(
            track_id=track.track_id,
            foot_x=round(float(predicted[track_index][0]), 6),
            foot_y=round(float(predicted[track_index][1]), 6),
            velocity_x=track.velocity_x,
            velocity_y=track.velocity_y,
            age=track.age + 1,
            matched_streak=0,
            missed_frames=missed,
        ))

    for detection_index, detection in enumerate(detections):
        if detection_index in matched_detections:
            continue
        updated.append(PlayerTrack(
            track_id=next_track_id,
            foot_x=detection.foot_x,
            foot_y=detection.foot_y,
            velocity_x=0.0,
            velocity_y=0.0,
            age=1,
            matched_streak=1,
            missed_frames=0,
        ))
        next_track_id += 1

    stable_match_share = stable_matches / max(len(detections), 1)
    quality_passed = (
        stable_matches >= MINIMUM_STABLE_MATCHES
        and stable_match_share >= MINIMUM_STABLE_MATCH_SHARE
    )
    moving_share = (
        moving_stable_matches / stable_matches * 100
        if quality_passed and stable_matches
        else 0.0
    )
    return (
        tuple(sorted(updated, key=lambda item: item.track_id)),
        round(moving_share, 6),
        {
            "active_tracks": len(updated),
            "detections": len(detections),
            "frame_matches": len(matches),
            "stable_matches": stable_matches,
            "stable_match_share": round(stable_match_share, 6),
            "moving_stable_matches": moving_stable_matches,
            "median_residual_distance": round(
                statistics.median(residual_distances), 6
            ) if residual_distances else 0.0,
            "retained_missed_tracks": retained_missed,
            "quality_passed": quality_passed,
        },
        next_track_id,
    )


def measure_tracklet_angle(
    model: Any,
    source: Path,
    start_ms: int,
    end_ms: int,
) -> tuple[list[SignalFrame], dict[str, Any]]:
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
    frames = []
    track_counts = []
    stable_counts = []
    quality_frames = 0
    retained_missed = 0
    tracks: tuple[PlayerTrack, ...] = ()
    next_track_id = 0
    previous_gray: np.ndarray | None = None
    for (time_s, frame), current_detections in zip(
        sampled, detections, strict=True
    ):
        current_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if previous_gray is None:
            tracks, next_track_id = initialize_tracklets(
                current_detections, next_track_id
            )
            value = 0.0
            diagnostics = {
                "stable_matches": 0,
                "quality_passed": False,
                "retained_missed_tracks": 0,
            }
        else:
            transform, _inliers = estimate_global_camera_transform(
                previous_gray, current_gray
            )
            tracks, value, diagnostics, next_track_id = advance_tracklets(
                tracks,
                current_detections,
                transform,
                current_gray.shape,
                next_track_id,
            )
        frames.append(SignalFrame(
            time_s=time_s,
            yavg=0.0,
            satavg=0.0,
            ydif=value,
            udif=0.0,
            vdif=0.0,
        ))
        track_counts.append(len(tracks))
        stable_counts.append(int(diagnostics["stable_matches"]))
        quality_frames += bool(diagnostics["quality_passed"])
        retained_missed += int(diagnostics["retained_missed_tracks"])
        previous_gray = current_gray
    return frames, {
        "sampled_frames": len(sampled),
        "median_active_tracks": (
            statistics.median(track_counts) if track_counts else 0
        ),
        "median_stable_matches": (
            statistics.median(stable_counts) if stable_counts else 0
        ),
        "quality_frames": quality_frames,
        "quality_frame_share": round(
            quality_frames / max(len(sampled), 1), 6
        ),
        "retained_missed_track_events": retained_missed,
    }


def build_tracklet_traces(
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
    judgments = _read_jsonl(judgments_path)
    if len(judgments) != EXPECTED_PLAYS:
        raise ValueError(f"Expected {EXPECTED_PLAYS} judgments")
    fingerprints = _validate_manifest_fingerprints(judgments, feature_paths)
    features = {
        str(row["clip_id"]): row
        for path in feature_paths
        for row in _read_jsonl(path)
    }
    model = YOLO(str(model_path), verbose=False)
    traces = []
    extraction_diagnostics = []
    progress = 0
    for judgment in judgments:
        clip_id = str(judgment["clip_id"])
        feature = features[clip_id]
        clip_start = int(feature["start_ms"])
        for measured, diagnostic in zip(
            judgment["angles"],
            feature["temporal_diagnostics"]["angles"],
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
            frames, angle_diagnostics = measure_tracklet_angle(
                model,
                Path(str(feature["source_video_path"])).resolve(),
                range_start_ms,
                range_end_ms,
            )
            traces.append(SpatialAngleTrace(
                item_id=str(judgment["item_id"]),
                cohort_id=str(judgment["research_cohort_id"]),
                clip_id=clip_id,
                clip_number=int(judgment["clip_number"]),
                angle=angle,
                source_video_path=str(feature["source_video_path"]),
                range_start_ms=range_start_ms,
                range_end_ms=range_end_ms,
                actual_snap_ms=int(measured["actual_snap_ms"]),
                temporal_v21_onset_ms=int(measured["proposed_onset_ms"]),
                candidates_by_channel={
                    CHANNEL: build_rise_candidates(frames)
                },
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
        raise ValueError(f"Expected {EXPECTED_ANGLES} tracklet traces")
    return traces, {
        "judgments_path": str(judgments_path),
        "judgments_sha256": sha256_file(judgments_path),
        "source_manifest_fingerprints": fingerprints,
        "model_path": str(model_path),
        "model_sha256": sha256_file(model_path),
        "environment": versions,
        "extraction_diagnostics": extraction_diagnostics,
    }


def evaluate_iteration_7h(
    judgments_path: Path,
    feature_paths: list[Path],
    model_path: Path,
    iteration_7e_report_path: Path,
) -> dict[str, Any]:
    traces, frozen_input = build_tracklet_traces(
        judgments_path, feature_paths, model_path
    )
    iteration_7e, fingerprint = _load_prior_report(
        iteration_7e_report_path,
        "iteration-7e-detected-player-motion-v1",
        frozen_input["judgments_sha256"],
        frozen_input["source_manifest_fingerprints"],
    )
    if iteration_7e["frozen_input"]["model_sha256"] != frozen_input["model_sha256"]:
        raise ValueError("Iteration 7H model differs from frozen 7E")
    candidate = score_spatial_policy(traces, TRACKLET_POLICY)
    candidate["development_gate"] = _gate(candidate["overall"])
    baseline = iteration_7e["development"]["selected"]
    gate_passed = candidate["development_gate"]["passed"]
    frozen_input["iteration_7e_report"] = fingerprint
    return {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": (
            "development_candidate_ready_for_third_game"
            if gate_passed
            else "development_hold_short_tracklets_not_ready"
        ),
        "frozen_input": frozen_input,
        "methodology": {
            "sample_fps": SAMPLE_FPS,
            "channel": CHANNEL,
            "maximum_assignment_distance": MAXIMUM_ASSIGNMENT_DISTANCE,
            "moving_distance": MOVING_DISTANCE,
            "maximum_missed_frames": MAXIMUM_MISSED_FRAMES,
            "minimum_stable_age": MINIMUM_STABLE_AGE,
            "minimum_stable_matches": MINIMUM_STABLE_MATCHES,
            "minimum_stable_match_share": MINIMUM_STABLE_MATCH_SHARE,
            "velocity_damping": VELOCITY_DAMPING,
            "onset_policy": asdict(TRACKLET_POLICY) | {
                "policy_id": TRACKLET_POLICY.policy_id
            },
            "parameter_grid": False,
            "film_specific_behavior": False,
            "holdout_status": "third game unopened",
        },
        "development": {
            "iteration_7e_baseline": {
                "policy": baseline["policy"],
                "overall": baseline["overall"],
                "by_cohort": baseline["by_cohort"],
            },
            "candidate": candidate,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    baseline = report["development"]["iteration_7e_baseline"]["overall"]
    candidate = report["development"]["candidate"]["overall"]
    gate = report["development"]["candidate"]["development_gate"]
    rows = (("7E detected players", baseline), ("7H short tracklets", candidate))
    lines = [
        "# TapeSift Iteration 7H - Deterministic Short Tracklets",
        "",
        (
            "This development experiment quality-gates coordinated motion "
            "using persistent player identities before applying the frozen "
            "Iteration 7E onset policy."
        ),
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
        (
            "**FREEZE REQUIRED.** Reproduce this candidate before opening "
            "the third game."
            if gate["passed"]
            else
            "**HOLD.** Short tracklets do not clear the frozen development "
            "gate. Do not open the third game."
        ),
        "",
    ]
    return "\n".join(lines)


def write_iteration_7h_report(
    report: dict[str, Any],
    output_json: Path,
    output_markdown: Path,
) -> None:
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
