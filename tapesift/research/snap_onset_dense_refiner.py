"""Iteration 7G dense transition bracketing for snap onset.

The frozen Iteration 7E candidate supplies a bounded search center. A single
predeclared 8 fps transition rule refines that center without a parameter
grid, film-specific behavior, or access to the third-game holdout.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

import cv2

from tapesift.research.snap_onset_detected_players import (
    MODEL_CONFIDENCE,
    MODEL_IMAGE_SIZE,
    PERSON_CLASS_ID,
    configure_inference,
    detected_player_motion_channels,
    detections_from_results,
)
from tapesift.research.snap_onset_refiner import (
    EXPECTED_ANGLES,
    _gate,
    _metric_summary,
    sha256_file,
)


ITERATION_ID = "iteration-7g-dense-transition-bracketing-v1"
SCHEMA_VERSION = "1.0"
SAMPLE_FPS = 8.0
SAMPLE_INTERVAL_MS = round(1000 / SAMPLE_FPS)
WINDOW_RADIUS_MS = 750
MINIMUM_POST_MOTION = 25.0
MINIMUM_GAIN = 15.0
MINIMUM_RISE_RATIO = 1.5
PRE_SAMPLES = 2
POST_SAMPLES = 3


def _median_smooth(values: list[float]) -> list[float]:
    return [
        round(statistics.median(
            values[max(0, index - 1):min(len(values), index + 2)]
        ), 6)
        for index in range(len(values))
    ]


def select_dense_transition(
    sample_times_ms: list[int],
    moving_shares: list[float],
    coarse_candidate_ms: int,
) -> dict[str, Any]:
    """Select one strongest sustained local transition or retain 7E."""
    if len(sample_times_ms) != len(moving_shares):
        raise ValueError("Dense times and motion values must have equal length")
    if len(sample_times_ms) < PRE_SAMPLES + POST_SAMPLES:
        return {
            "candidate_onset_ms": coarse_candidate_ms,
            "selection_status": "iteration_7e_fallback_short_window",
            "qualified_transitions": 0,
        }
    if sample_times_ms != sorted(sample_times_ms):
        raise ValueError("Dense sample times must be ordered")

    smoothed = _median_smooth(moving_shares)
    candidates = []
    for index in range(PRE_SAMPLES, len(smoothed) - POST_SAMPLES + 1):
        pre = statistics.median(smoothed[index - PRE_SAMPLES:index])
        post = statistics.median(smoothed[index:index + POST_SAMPLES])
        gain = post - pre
        ratio = post / max(pre, 1.0)
        if (
            post < MINIMUM_POST_MOTION
            or gain < MINIMUM_GAIN
            or ratio < MINIMUM_RISE_RATIO
        ):
            continue
        onset_ms = round(
            (sample_times_ms[index - 1] + sample_times_ms[index]) / 2
        )
        candidates.append({
            "candidate_onset_ms": onset_ms,
            "transition_index": index,
            "pre_motion": round(pre, 6),
            "post_motion": round(post, 6),
            "motion_gain": round(gain, 6),
            "rise_ratio": round(ratio, 6),
            "distance_from_coarse_ms": abs(onset_ms - coarse_candidate_ms),
        })
    if not candidates:
        return {
            "candidate_onset_ms": coarse_candidate_ms,
            "selection_status": "iteration_7e_fallback_no_dense_transition",
            "qualified_transitions": 0,
            "smoothed_moving_shares": smoothed,
        }
    selected = min(candidates, key=lambda item: (
        -item["motion_gain"],
        -item["post_motion"],
        item["distance_from_coarse_ms"],
        item["candidate_onset_ms"],
    ))
    return {
        **selected,
        "selection_status": "dense_transition_interval_midpoint",
        "qualified_transitions": len(candidates),
        "smoothed_moving_shares": smoothed,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_features(feature_paths: list[Path]) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {}
    for path in feature_paths:
        for row in _read_jsonl(path):
            clip_id = str(row["clip_id"])
            if clip_id in features:
                raise ValueError(f"Duplicate feature clip id: {clip_id}")
            features[clip_id] = row
    return features


def read_dense_frames(
    source: Path,
    start_ms: int,
    end_ms: int,
) -> list[tuple[int, Any]]:
    if end_ms <= start_ms:
        raise ValueError("Dense range must have positive duration")
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open {source}")
    capture.set(cv2.CAP_PROP_POS_MSEC, float(start_ms))
    target_ms = start_ms
    frames = []
    try:
        while target_ms <= end_ms:
            ok, frame = capture.read()
            if not ok:
                break
            position_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC))
            if position_ms + 0.5 < target_ms:
                continue
            frames.append((target_ms, frame))
            target_ms += SAMPLE_INTERVAL_MS
    finally:
        capture.release()
    if not frames:
        raise ValueError(f"OpenCV returned no dense frames for {source.name}")
    return frames


def measure_dense_motion(
    model: Any,
    source: Path,
    start_ms: int,
    end_ms: int,
) -> tuple[list[int], list[float], dict[str, Any]]:
    frames = read_dense_frames(source, start_ms, end_ms)
    results = model.predict(
        [frame for _time_ms, frame in frames],
        imgsz=MODEL_IMAGE_SIZE,
        conf=MODEL_CONFIDENCE,
        classes=[PERSON_CLASS_ID],
        device="cpu",
        batch=16,
        verbose=False,
    )
    detected = detections_from_results(
        [(time_ms / 1000.0, frame) for time_ms, frame in frames],
        results,
    )
    moving_shares = [0.0]
    match_shares = []
    detection_counts = [len(items) for items in detected]
    for index in range(1, len(frames)):
        previous_gray = cv2.cvtColor(frames[index - 1][1], cv2.COLOR_BGR2GRAY)
        current_gray = cv2.cvtColor(frames[index][1], cv2.COLOR_BGR2GRAY)
        values, diagnostics = detected_player_motion_channels(
            previous_gray,
            current_gray,
            detected[index - 1],
            detected[index],
        )
        moving_shares.append(float(values["detected_moving_share"]))
        match_shares.append(float(diagnostics["match_share"]))
    return (
        [time_ms for time_ms, _frame in frames],
        moving_shares,
        {
            "sampled_frames": len(frames),
            "minimum_person_detections": min(detection_counts, default=0),
            "median_person_detections": (
                statistics.median(detection_counts) if detection_counts else 0
            ),
            "median_match_share": (
                statistics.median(match_shares) if match_shares else 0
            ),
        },
    )


def _feature_angle_range(
    feature: dict[str, Any],
    angle: int,
) -> tuple[int, int]:
    angle_feature = next(
        row
        for row in feature["temporal_diagnostics"]["angles"]
        if int(row["angle"]) == angle
    )
    clip_start_ms = int(feature["start_ms"])
    return (
        clip_start_ms + round(float(angle_feature["start_seconds"]) * 1000),
        clip_start_ms + round(float(angle_feature["end_seconds"]) * 1000),
    )


def _score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    overall = _metric_summary(rows)
    overall["fallback_count"] = sum(
        row["selection_status"].startswith("iteration_7e_fallback")
        for row in rows
    )
    return {
        "overall": overall,
        "by_cohort": {
            cohort: _metric_summary([
                row for row in rows if row["cohort_id"] == cohort
            ])
            for cohort in sorted({row["cohort_id"] for row in rows})
        },
        "rows": rows,
    }


def evaluate_iteration_7g(
    iteration_7e_report_path: Path,
    feature_paths: list[Path],
    model_path: Path,
) -> dict[str, Any]:
    from ultralytics import YOLO

    paths = [iteration_7e_report_path, model_path, *feature_paths]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    report = json.loads(iteration_7e_report_path.read_text(encoding="utf-8"))
    if report.get("iteration_id") != "iteration-7e-detected-player-motion-v1":
        raise ValueError("Iteration 7G requires the frozen Iteration 7E report")
    declared_features = {
        (str(row["name"]), str(row["sha256"]))
        for row in report["frozen_input"]["source_manifest_fingerprints"]
    }
    actual_features = {
        (path.name, sha256_file(path)) for path in feature_paths
    }
    if actual_features != declared_features:
        raise ValueError("Iteration 7G feature manifests differ from 7E")
    if sha256_file(model_path) != report["frozen_input"]["model_sha256"]:
        raise ValueError("Iteration 7G model differs from 7E")

    versions = configure_inference()
    features = _load_features(feature_paths)
    model = YOLO(str(model_path.resolve()), verbose=False)
    rows = []
    extraction_diagnostics = []
    source_rows = report["development"]["selected"]["rows"]
    if len(source_rows) != EXPECTED_ANGLES:
        raise ValueError(f"Expected {EXPECTED_ANGLES} 7E rows")
    for index, source_row in enumerate(source_rows, start=1):
        clip_id = str(source_row["clip_id"])
        angle = int(source_row["angle"])
        feature = features[clip_id]
        angle_start_ms, angle_end_ms = _feature_angle_range(feature, angle)
        coarse_ms = int(source_row["candidate_onset_ms"])
        dense_start_ms = max(angle_start_ms, coarse_ms - WINDOW_RADIUS_MS)
        dense_end_ms = min(angle_end_ms, coarse_ms + WINDOW_RADIUS_MS)
        print(
            f"[{index:02d}/{EXPECTED_ANGLES}] "
            f"{source_row['cohort_id']} clip {source_row['clip_number']} "
            f"angle {angle}",
            flush=True,
        )
        times_ms, moving_shares, diagnostics = measure_dense_motion(
            model,
            Path(str(feature["source_video_path"])),
            dense_start_ms,
            dense_end_ms,
        )
        selection = select_dense_transition(
            times_ms,
            moving_shares,
            coarse_ms,
        )
        candidate_ms = int(selection["candidate_onset_ms"])
        rows.append({
            "item_id": str(source_row["item_id"]),
            "cohort_id": str(source_row["cohort_id"]),
            "clip_id": clip_id,
            "clip_number": int(source_row["clip_number"]),
            "angle": angle,
            "actual_snap_ms": int(source_row["actual_snap_ms"]),
            "temporal_v21_onset_ms": int(
                source_row["temporal_v21_onset_ms"]
            ),
            "iteration_7e_onset_ms": coarse_ms,
            "candidate_onset_ms": candidate_ms,
            "error_ms": int(source_row["actual_snap_ms"]) - candidate_ms,
            **selection,
        })
        extraction_diagnostics.append({
            "clip_id": clip_id,
            "angle": angle,
            "dense_start_ms": dense_start_ms,
            "dense_end_ms": dense_end_ms,
            "sample_times_ms": times_ms,
            "moving_shares": moving_shares,
            **diagnostics,
        })

    candidate = _score_rows(rows)
    candidate["development_gate"] = _gate(candidate["overall"])
    baseline_rows = [
        {
            "item_id": str(row["item_id"]),
            "cohort_id": str(row["cohort_id"]),
            "error_ms": int(row["error_ms"]),
        }
        for row in source_rows
    ]
    baseline = _score_rows([
        {
            **row,
            "selection_status": "iteration_7e_baseline",
        }
        for row in baseline_rows
    ])
    changed = [
        row for row in rows
        if row["candidate_onset_ms"] != row["iteration_7e_onset_ms"]
    ]
    gate_passed = candidate["development_gate"]["passed"]
    return {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": (
            "development_candidate_ready_for_third_game"
            if gate_passed
            else "development_hold_dense_transition_not_ready"
        ),
        "frozen_input": {
            "iteration_7e_report": {
                "name": iteration_7e_report_path.name,
                "sha256": sha256_file(iteration_7e_report_path),
            },
            "source_manifest_fingerprints": [
                {"name": name, "sha256": sha}
                for name, sha in sorted(actual_features)
            ],
            "model": {
                "name": model_path.name,
                "sha256": sha256_file(model_path),
            },
            "environment": versions,
            "extraction_diagnostics": extraction_diagnostics,
        },
        "methodology": {
            "source_candidate": "frozen Iteration 7E selected row",
            "sample_fps": SAMPLE_FPS,
            "window_radius_ms": WINDOW_RADIUS_MS,
            "motion_channel": "detected_moving_share",
            "smoothing": "centered three-sample median",
            "pre_samples": PRE_SAMPLES,
            "post_samples": POST_SAMPLES,
            "minimum_post_motion": MINIMUM_POST_MOTION,
            "minimum_gain": MINIMUM_GAIN,
            "minimum_rise_ratio": MINIMUM_RISE_RATIO,
            "selection": (
                "largest qualifying post-minus-pre transition, then larger "
                "post motion, proximity to 7E, and earlier onset"
            ),
            "onset": "midpoint of the selected 125 ms transition interval",
            "parameter_grid": False,
            "global_offset": False,
            "holdout_status": "third game unopened",
        },
        "development": {
            "iteration_7e_baseline": {
                "overall": baseline["overall"],
                "by_cohort": baseline["by_cohort"],
            },
            "candidate": candidate,
            "changed_angles": len(changed),
            "unchanged_angles": EXPECTED_ANGLES - len(changed),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    baseline = report["development"]["iteration_7e_baseline"]["overall"]
    candidate = report["development"]["candidate"]["overall"]
    gate = report["development"]["candidate"]["development_gate"]
    lines = [
        "# TapeSift Iteration 7G - Dense Transition Bracketing",
        "",
        (
            "This development experiment applies one predeclared 8 fps "
            "transition rule around each frozen Iteration 7E candidate."
        ),
        "",
        "## Development comparison",
        "",
        (
            "| Candidate | Median error | Within 500 ms | P90 error | "
            "Signed bias | Both angles |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| 7E detected players | "
            f"{baseline['median_absolute_error_ms']} ms | "
            f"{baseline['within_500_ms_share']:.1%} | "
            f"{baseline['p90_absolute_error_ms']} ms | "
            f"{baseline['median_signed_bias_ms']} ms | "
            f"{baseline['both_angles_within_500_ms_share']:.1%} |"
        ),
        (
            f"| 7G dense transition | "
            f"{candidate['median_absolute_error_ms']} ms | "
            f"{candidate['within_500_ms_share']:.1%} | "
            f"{candidate['p90_absolute_error_ms']} ms | "
            f"{candidate['median_signed_bias_ms']} ms | "
            f"{candidate['both_angles_within_500_ms_share']:.1%} |"
        ),
        "",
        (
            f"- Changed angles: "
            f"**{report['development']['changed_angles']} / {EXPECTED_ANGLES}**"
        ),
        f"- Development gate passed: **{gate['passed']}**",
        "",
        "## Decision",
        "",
    ]
    if gate["passed"]:
        lines.append(
            "**FREEZE REQUIRED.** Reproduce this candidate before any "
            "third-game evaluation."
        )
    else:
        lines.append(
            "**HOLD.** Dense transition bracketing does not clear the frozen "
            "development gate. Do not open the third game."
        )
    lines.append("")
    return "\n".join(lines)


def write_iteration_7g_report(
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
