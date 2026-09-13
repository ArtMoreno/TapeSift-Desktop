"""Iteration 7F diagnostics for detected-player snap-onset failures.

The atlas is diagnostic-only. It reads the frozen Iteration 7E report,
renders evidence around failed angle judgments, and applies a fixed failure
taxonomy. It does not select or modify an onset policy.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from tapesift.research.snap_onset_detected_players import (
    MODEL_CONFIDENCE,
    MODEL_IMAGE_SIZE,
    MOVING_DISTANCE,
    PERSON_CLASS_ID,
    PlayerDetection,
    _greedy_matches,
    configure_inference,
    filter_person_detections,
)
from tapesift.research.snap_onset_player_motion import (
    estimate_global_camera_transform,
)


ITERATION_ID = "iteration-7f-detected-player-failure-atlas-v1"
SCHEMA_VERSION = "1.0"
FAILURE_THRESHOLD_MS = 500
NEAR_BOUNDARY_LIMIT_MS = 750
SEVERE_FAILURE_MS = 1000
FRAME_STEP_MS = 250
SHEET_PANEL_WIDTH = 640


def classify_failure(
    error_ms: int,
    diagnostics: dict[str, Any],
) -> tuple[str, ...]:
    """Apply the frozen, non-exclusive Iteration 7F taxonomy."""
    absolute_error = abs(error_ms)
    tags = ["late_detection" if error_ms < 0 else "early_detection"]
    if absolute_error <= NEAR_BOUNDARY_LIMIT_MS:
        tags.append("near_boundary_quantization_candidate")
    elif absolute_error >= SEVERE_FAILURE_MS:
        tags.append("severe_timing_miss")
    else:
        tags.append("midrange_timing_miss")

    sampled_frames = max(int(diagnostics.get("sampled_frames", 0)), 1)
    low_match_share = (
        int(diagnostics.get("low_match_frames", 0)) / sampled_frames
    )
    minimum_detections = int(
        diagnostics.get("minimum_person_detections", 0)
    )
    if minimum_detections <= 2 or low_match_share >= 0.10:
        tags.append("detector_continuity_risk")
    else:
        tags.append("stable_detection_timing_ambiguity")
    return tuple(tags)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_failure_records(
    report: dict[str, Any],
    feature_paths: list[Path],
) -> list[dict[str, Any]]:
    """Join failed 7E rows to frozen source ranges and diagnostics."""
    features: dict[str, dict[str, Any]] = {}
    for path in feature_paths:
        for row in _read_jsonl(path):
            clip_id = str(row["clip_id"])
            if clip_id in features:
                raise ValueError(f"Duplicate feature clip id: {clip_id}")
            features[clip_id] = row

    diagnostics = {
        (str(row["clip_id"]), int(row["angle"])): row
        for row in report["frozen_input"]["extraction_diagnostics"]
    }
    policy = report["development"]["selected"]["policy"]
    lag_ms = round(float(policy["output_lag_seconds"]) * 1000)
    records = []
    for row in report["development"]["selected"]["rows"]:
        error_ms = int(row["error_ms"])
        if abs(error_ms) <= FAILURE_THRESHOLD_MS:
            continue
        clip_id = str(row["clip_id"])
        angle = int(row["angle"])
        feature = features.get(clip_id)
        if feature is None:
            raise ValueError(f"Missing features for failed clip {clip_id}")
        angle_feature = next(
            item
            for item in feature["temporal_diagnostics"]["angles"]
            if int(item["angle"]) == angle
        )
        range_start_ms = int(feature["start_ms"]) + round(
            float(angle_feature["start_seconds"]) * 1000
        )
        diagnostic = diagnostics[(clip_id, angle)]
        actual_ms = int(row["actual_snap_ms"])
        selected_ms = int(row["candidate_onset_ms"])
        strongest_ms = range_start_ms + round(
            float(row["strongest_candidate"]["time_seconds"]) * 1000
        ) + lag_ms
        temporal_ms = int(row["temporal_v21_onset_ms"])
        tags = list(classify_failure(error_ms, diagnostic))
        if abs(actual_ms - strongest_ms) < abs(error_ms):
            tags.append("strongest_candidate_would_improve")
        if abs(actual_ms - temporal_ms) <= FAILURE_THRESHOLD_MS:
            tags.append("temporal_v21_would_rescue")
        records.append({
            "cohort_id": str(row["cohort_id"]),
            "clip_id": clip_id,
            "clip_number": int(row["clip_number"]),
            "angle": angle,
            "source_video_path": str(feature["source_video_path"]),
            "range_start_ms": range_start_ms,
            "range_end_ms": int(feature["start_ms"]) + round(
                float(angle_feature["end_seconds"]) * 1000
            ),
            "actual_snap_ms": actual_ms,
            "candidate_onset_ms": selected_ms,
            "error_ms": error_ms,
            "absolute_error_ms": abs(error_ms),
            "strongest_candidate_onset_ms": strongest_ms,
            "strongest_candidate_error_ms": actual_ms - strongest_ms,
            "temporal_v21_onset_ms": temporal_ms,
            "temporal_v21_error_ms": actual_ms - temporal_ms,
            "diagnostics": diagnostic,
            "tags": tags,
        })
    return sorted(
        records,
        key=lambda item: (
            item["cohort_id"],
            item["clip_number"],
            item["angle"],
        ),
    )


def _read_frame(source: Path, position_ms: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open {source}")
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, float(position_ms))
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        raise RuntimeError(f"OpenCV could not read {source} at {position_ms}")
    return frame


def _resize_panel(frame: np.ndarray) -> np.ndarray:
    scale = SHEET_PANEL_WIDTH / frame.shape[1]
    return cv2.resize(
        frame,
        (SHEET_PANEL_WIDTH, round(frame.shape[0] * scale)),
        interpolation=cv2.INTER_AREA,
    )


def _draw_points(
    frame: np.ndarray,
    detections: tuple[PlayerDetection, ...],
) -> None:
    for item in detections:
        point = (round(item.foot_x), round(item.foot_y))
        cv2.circle(frame, point, 5, (0, 220, 255), 2, cv2.LINE_AA)


def _draw_residual_motion(
    previous: np.ndarray,
    current: np.ndarray,
    previous_detections: tuple[PlayerDetection, ...],
    current_detections: tuple[PlayerDetection, ...],
) -> dict[str, Any]:
    previous_gray = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
    current_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
    transform, inliers = estimate_global_camera_transform(
        previous_gray,
        current_gray,
    )
    previous_points = np.array(
        [[item.foot_x, item.foot_y] for item in previous_detections],
        dtype=np.float32,
    )
    current_points = np.array(
        [[item.foot_x, item.foot_y] for item in current_detections],
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
    diagonal = math.hypot(current.shape[1], current.shape[0])
    matches = _greedy_matches(predicted, current_points, diagonal * 0.12)
    moving = 0
    for previous_index, current_index, distance in matches:
        start = tuple(np.rint(predicted[previous_index]).astype(int))
        end = tuple(np.rint(current_points[current_index]).astype(int))
        is_moving = distance / max(diagonal, 1.0) >= MOVING_DISTANCE
        moving += is_moving
        color = (40, 80, 245) if is_moving else (80, 210, 90)
        cv2.arrowedLine(
            current,
            start,
            end,
            color,
            2,
            cv2.LINE_AA,
            tipLength=0.25,
        )
    _draw_points(current, current_detections)
    return {
        "camera_inliers": inliers,
        "matched_detections": len(matches),
        "moving_detections": moving,
        "moving_share": round(moving / max(len(matches), 1), 6),
    }


def _put_label(frame: np.ndarray, label: str) -> None:
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 42), (18, 24, 30), -1)
    cv2.putText(
        frame,
        label,
        (14, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.68,
        (245, 245, 245),
        2,
        cv2.LINE_AA,
    )


def render_failure_sheet(
    model: Any,
    record: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    """Render candidate and ground-truth motion evidence for one failure."""
    source = Path(record["source_video_path"])
    positions = (
        int(record["candidate_onset_ms"]) - FRAME_STEP_MS,
        int(record["candidate_onset_ms"]),
        int(record["actual_snap_ms"]) - FRAME_STEP_MS,
        int(record["actual_snap_ms"]),
    )
    frames = [_read_frame(source, position) for position in positions]
    results = model.predict(
        frames,
        imgsz=MODEL_IMAGE_SIZE,
        conf=MODEL_CONFIDENCE,
        classes=[PERSON_CLASS_ID],
        device="cpu",
        batch=4,
        verbose=False,
    )
    detections = []
    for frame, result in zip(frames, results, strict=True):
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            detections.append(())
        else:
            detections.append(filter_person_detections(
                boxes.xyxy.cpu().numpy(),
                boxes.conf.cpu().numpy(),
                frame.shape[1],
                frame.shape[0],
            ))

    _draw_points(frames[0], detections[0])
    candidate_motion = _draw_residual_motion(
        frames[0], frames[1], detections[0], detections[1]
    )
    _draw_points(frames[2], detections[2])
    actual_motion = _draw_residual_motion(
        frames[2], frames[3], detections[2], detections[3]
    )
    labels = (
        f"candidate -250 ms | people {len(detections[0])}",
        (
            "candidate | "
            f"matched {candidate_motion['matched_detections']} "
            f"moving {candidate_motion['moving_share']:.0%}"
        ),
        f"actual -250 ms | people {len(detections[2])}",
        (
            "actual | "
            f"matched {actual_motion['matched_detections']} "
            f"moving {actual_motion['moving_share']:.0%}"
        ),
    )
    panels = []
    for frame, label in zip(frames, labels, strict=True):
        panel = _resize_panel(frame)
        _put_label(panel, label)
        panels.append(panel)
    top = np.hstack((panels[0], panels[1]))
    bottom = np.hstack((panels[2], panels[3]))
    grid = np.vstack((top, bottom))
    banner = np.full((112, grid.shape[1], 3), (238, 241, 235), np.uint8)
    title = (
        f"clip {record['clip_number']} angle {record['angle']} | "
        f"error {record['error_ms']} ms | {record['clip_id'][:8]}"
    )
    cv2.putText(
        banner, title, (18, 36), cv2.FONT_HERSHEY_SIMPLEX,
        0.86, (24, 33, 39), 2, cv2.LINE_AA,
    )
    tag_text = " | ".join(record["tags"])
    if len(tag_text) > 145:
        tag_text = tag_text[:142] + "..."
    cv2.putText(
        banner, tag_text, (18, 74), cv2.FONT_HERSHEY_SIMPLEX,
        0.48, (50, 65, 72), 1, cv2.LINE_AA,
    )
    cv2.putText(
        banner,
        "yellow=candidate foot point, red=moving residual, green=stable residual",
        (18, 100),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.44,
        (50, 65, 72),
        1,
        cv2.LINE_AA,
    )
    sheet = np.vstack((banner, grid))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), sheet):
        raise RuntimeError(f"Could not write {output_path}")
    return {
        "candidate_motion": candidate_motion,
        "actual_motion": actual_motion,
        "rendered_detection_counts": [len(items) for items in detections],
    }


def render_overview(image_paths: list[Path], output_path: Path) -> None:
    thumbnails = []
    for path in image_paths:
        image = cv2.imread(str(path))
        if image is None:
            raise RuntimeError(f"Could not read rendered sheet {path}")
        scale = 600 / image.shape[1]
        thumbnails.append(cv2.resize(
            image,
            (600, round(image.shape[0] * scale)),
            interpolation=cv2.INTER_AREA,
        ))
    if len(thumbnails) % 2:
        thumbnails.append(np.full_like(thumbnails[0], 245))
    rows = [
        np.hstack((thumbnails[index], thumbnails[index + 1]))
        for index in range(0, len(thumbnails), 2)
    ]
    overview = np.vstack(rows)
    if not cv2.imwrite(str(output_path), overview):
        raise RuntimeError(f"Could not write {output_path}")


def render_markdown(records: list[dict[str, Any]], image_dir: str) -> str:
    counts = Counter(tag for row in records for tag in row["tags"])
    lines = [
        "# TapeSift Iteration 7F - Detected-Player Failure Atlas",
        "",
        (
            "This diagnostic-only atlas classifies every Iteration 7E angle "
            "judgment outside the frozen 500 ms gate. It does not modify or "
            "select an onset policy."
        ),
        "",
        "## Summary",
        "",
        f"- Failed angle judgments: **{len(records)}**",
        *[
            f"- `{tag}`: **{count}**"
            for tag, count in sorted(counts.items())
        ],
        "",
        "## Overview",
        "",
        f"![Iteration 7F overview]({image_dir}/overview.png)",
        "",
        "## Failure records",
        "",
        (
            "| Film | Clip | Angle | Error | 7E diagnostics | "
            "Alternative evidence |"
        ),
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in records:
        diagnostics = row["diagnostics"]
        alternative = (
            f"strongest {row['strongest_candidate_error_ms']} ms; "
            f"Temporal v2.1 {row['temporal_v21_error_ms']} ms"
        )
        lines.append(
            f"| {row['cohort_id']} | {row['clip_number']} | "
            f"{row['angle']} | {row['error_ms']} ms | "
            f"median people {diagnostics['median_person_detections']}; "
            f"median match {diagnostics['median_match_share']:.0%}; "
            f"low-match frames {diagnostics['low_match_frames']} | "
            f"{alternative} |"
        )
    lines.extend(("", "## Evidence sheets", ""))
    for row in records:
        lines.extend((
            (
                f"### Clip {row['clip_number']} angle {row['angle']} "
                f"({row['error_ms']} ms)"
            ),
            "",
            "Tags: " + ", ".join(f"`{tag}`" for tag in row["tags"]),
            "",
            f"![Failure evidence]({image_dir}/{row['image_file']})",
            "",
        ))
    return "\n".join(lines)


def build_failure_atlas(
    report_path: Path,
    feature_paths: list[Path],
    model_path: Path,
    output_dir: Path,
    output_json: Path,
    output_markdown: Path,
) -> dict[str, Any]:
    from ultralytics import YOLO

    for path in [report_path, model_path, *feature_paths]:
        if not path.is_file():
            raise FileNotFoundError(path)
    for path in (output_json, output_markdown):
        if path.exists():
            raise FileExistsError(f"Refusing to replace frozen output: {path}")
    if output_dir.exists():
        raise FileExistsError(f"Refusing to replace atlas directory: {output_dir}")

    versions = configure_inference()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    records = build_failure_records(report, feature_paths)
    model = YOLO(str(model_path), verbose=False)
    image_paths = []
    for index, record in enumerate(records, start=1):
        image_file = (
            f"clip-{record['clip_number']:02d}-angle-{record['angle']}-"
            f"{record['clip_id'][:8]}.png"
        )
        record["image_file"] = image_file
        print(f"[{index:02d}/{len(records)}] {image_file}", flush=True)
        image_path = output_dir / image_file
        record["render_diagnostics"] = render_failure_sheet(
            model, record, image_path
        )
        image_paths.append(image_path)
    render_overview(image_paths, output_dir / "overview.png")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": "diagnostic_complete_no_policy_change",
        "source_iteration": report["iteration_id"],
        "source_policy": report["development"]["selected"]["policy"],
        "failure_definition": "absolute error greater than 500 ms",
        "taxonomy_is_predeclared": True,
        "holdout_status": "third_game_unopened",
        "environment": versions,
        "records": records,
        "tag_counts": dict(sorted(Counter(
            tag for row in records for tag in row["tags"]
        ).items())),
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    output_markdown.write_text(
        render_markdown(records, output_dir.name),
        encoding="utf-8",
        newline="\n",
    )
    return payload
