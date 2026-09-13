"""Iteration 7J leave-one-game-out learned temporal snap classifier."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from tapesift.research.snap_onset_center_exchange import score_rows


ITERATION_ID = "iteration-7j-learned-temporal-grid-v1"
SCHEMA_VERSION = "1.0"
SAMPLE_FPS = 8.0
WINDOW_RADIUS_MS = 1500
SAMPLE_WIDTH = 320
GRID_ROWS = 4
GRID_COLUMNS = 4
ACTIVE_DIFFERENCE_THRESHOLD = 12
TRAINING_STEPS = 600
LEARNING_RATE = 0.05
L2_PENALTY = 0.01
MINIMUM_PROBABILITY = 0.60
MINIMUM_MEDIAN_MARGIN = 0.20
EXPECTED_ANGLES = 90


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            rows.append(value)
    return rows


def temporal_context_vectors(base_vectors: Sequence[Sequence[float]]) -> list[list[float]]:
    if not base_vectors:
        return []
    width = len(base_vectors[0])
    if width == 0 or any(len(vector) != width for vector in base_vectors):
        raise ValueError("Temporal base vectors must have one non-zero width")
    result: list[list[float]] = []
    for index, current in enumerate(base_vectors):
        previous = base_vectors[max(0, index - 1)]
        following = base_vectors[min(len(base_vectors) - 1, index + 1)]
        result.append([*previous, *current, *following])
    return result


def fit_balanced_logistic(
    features: Sequence[Sequence[float]],
    labels: Sequence[int],
) -> dict[str, Any]:
    import numpy as np

    matrix = np.asarray(features, dtype=np.float64)
    target = np.asarray(labels, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] != target.shape[0]:
        raise ValueError("Classifier features and labels have incompatible shapes")
    positives = int(target.sum())
    negatives = int(target.size - positives)
    if positives == 0 or negatives == 0:
        raise ValueError("Classifier training requires positive and negative examples")
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale[scale < 1e-9] = 1.0
    standardized = (matrix - mean) / scale
    design = np.column_stack([np.ones(standardized.shape[0]), standardized])
    weights = np.zeros(design.shape[1], dtype=np.float64)
    sample_weights = np.where(target == 1.0, negatives / positives, 1.0)
    weight_total = float(sample_weights.sum())
    for _step in range(TRAINING_STEPS):
        logits = np.clip(design @ weights, -30.0, 30.0)
        probability = 1.0 / (1.0 + np.exp(-logits))
        residual = (probability - target) * sample_weights
        gradient = design.T @ residual / weight_total
        gradient[1:] += L2_PENALTY * weights[1:]
        weights -= LEARNING_RATE * gradient
    logits = np.clip(design @ weights, -30.0, 30.0)
    probability = 1.0 / (1.0 + np.exp(-logits))
    epsilon = 1e-12
    loss = -float(np.sum(sample_weights * (
        target * np.log(probability + epsilon)
        + (1.0 - target) * np.log(1.0 - probability + epsilon)
    )) / weight_total)
    loss += float(L2_PENALTY * np.sum(weights[1:] ** 2) / 2)
    return {
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "weights": weights.tolist(),
        "positive_examples": positives,
        "negative_examples": negatives,
        "training_loss": round(loss, 9),
    }


def predict_probabilities(
    model: Mapping[str, Any],
    features: Sequence[Sequence[float]],
) -> list[float]:
    import numpy as np

    matrix = np.asarray(features, dtype=np.float64)
    mean = np.asarray(model["mean"], dtype=np.float64)
    scale = np.asarray(model["scale"], dtype=np.float64)
    weights = np.asarray(model["weights"], dtype=np.float64)
    standardized = (matrix - mean) / scale
    design = np.column_stack([np.ones(standardized.shape[0]), standardized])
    logits = np.clip(design @ weights, -30.0, 30.0)
    return (1.0 / (1.0 + np.exp(-logits))).tolist()


def select_learned_candidate(
    interval_times_ms: Sequence[int],
    probabilities: Sequence[float],
) -> dict[str, Any]:
    if not interval_times_ms or len(interval_times_ms) != len(probabilities):
        raise ValueError("Candidate times and probabilities must align")
    best_index = max(
        range(len(probabilities)),
        key=lambda index: (float(probabilities[index]), -int(interval_times_ms[index])),
    )
    best_probability = float(probabilities[best_index])
    median_probability = statistics.median(float(value) for value in probabilities)
    margin = best_probability - median_probability
    confident = (
        best_probability >= MINIMUM_PROBABILITY
        and margin >= MINIMUM_MEDIAN_MARGIN
    )
    return {
        "candidate_ms": int(interval_times_ms[best_index]),
        "probability": round(best_probability, 9),
        "median_probability": round(median_probability, 9),
        "median_margin": round(margin, 9),
        "confident": confident,
    }


def _feature_metadata(feature_paths: Sequence[Path]) -> dict[tuple[str, int], dict[str, Any]]:
    metadata: dict[tuple[str, int], dict[str, Any]] = {}
    for path in feature_paths:
        for row in _read_jsonl(path):
            diagnostics = row.get("temporal_diagnostics")
            if not isinstance(diagnostics, dict):
                continue
            angles = diagnostics.get("angles")
            if not isinstance(angles, list):
                continue
            for angle in angles:
                if not isinstance(angle, dict):
                    continue
                number = int(angle["angle"])
                key = (str(row["clip_id"]), number)
                if key in metadata:
                    raise ValueError(f"Duplicate temporal angle metadata: {key}")
                metadata[key] = {
                    "source_video_path": str(row["source_video_path"]),
                    "range_start_ms": int(row["start_ms"]) + round(
                        float(angle["start_seconds"]) * 1000
                    ),
                    "range_end_ms": int(row["start_ms"]) + round(
                        float(angle["end_seconds"]) * 1000
                    ),
                }
    return metadata


def _grid_features(difference: Any) -> list[float]:
    import numpy as np

    height, width = difference.shape
    values: list[float] = []
    for row in range(GRID_ROWS):
        top = round(row * height / GRID_ROWS)
        bottom = round((row + 1) * height / GRID_ROWS)
        for column in range(GRID_COLUMNS):
            left = round(column * width / GRID_COLUMNS)
            right = round((column + 1) * width / GRID_COLUMNS)
            cell = difference[top:bottom, left:right]
            values.append(float(cell.mean()) / 255.0)
            values.append(float(np.mean(cell >= ACTIVE_DIFFERENCE_THRESHOLD)))
    return values


def extract_temporal_trace(
    metadata: Mapping[str, Any],
    coarse_candidate_ms: int,
) -> dict[str, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("OpenCV and NumPy are required for Iteration 7J") from exc

    step_ms = 1000.0 / SAMPLE_FPS
    frame_times = sorted({
        coarse_candidate_ms + round(index * step_ms)
        for index in range(-12, 13)
        if int(metadata["range_start_ms"]) <=
        coarse_candidate_ms + round(index * step_ms) <=
        int(metadata["range_end_ms"])
    })
    capture = cv2.VideoCapture(str(metadata["source_video_path"]))
    if not capture.isOpened():
        raise FileNotFoundError(str(metadata["source_video_path"]))
    frames: list[Any] = []
    try:
        for timestamp in frame_times:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError(
                    f"Could not decode {metadata['source_video_path']} at {timestamp} ms"
                )
            height, width = frame.shape[:2]
            if width != SAMPLE_WIDTH:
                scale = SAMPLE_WIDTH / width
                frame = cv2.resize(
                    frame,
                    (SAMPLE_WIDTH, round(height * scale)),
                    interpolation=cv2.INTER_AREA,
                )
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frames.append(cv2.GaussianBlur(gray, (3, 3), 0))
    finally:
        capture.release()

    interval_times: list[int] = []
    base_vectors: list[list[float]] = []
    for index, (previous, current) in enumerate(zip(frames, frames[1:])):
        shift, response = cv2.phaseCorrelate(
            previous.astype(np.float32), current.astype(np.float32)
        )
        dx, dy = shift
        aligned = cv2.warpAffine(
            current,
            np.array([[1.0, 0.0, -dx], [0.0, 1.0, -dy]], dtype=np.float32),
            (current.shape[1], current.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
        difference = cv2.absdiff(previous, aligned)
        top = round(difference.shape[0] * 0.08)
        bottom = round(difference.shape[0] * 0.92)
        field = difference[top:bottom, :]
        base_vectors.append([
            *_grid_features(field),
            float(dx) / difference.shape[1],
            float(dy) / difference.shape[0],
            float(response),
        ])
        interval_times.append(round(
            (frame_times[index] + frame_times[index + 1]) / 2
        ))
    return {
        "interval_times_ms": interval_times,
        "features": temporal_context_vectors(base_vectors),
    }


def _training_examples(
    traces: Sequence[Mapping[str, Any]],
    actual_by_key: Mapping[tuple[str, int], int],
) -> tuple[list[list[float]], list[int]]:
    features: list[list[float]] = []
    labels: list[int] = []
    for trace in traces:
        key = (str(trace["clip_id"]), int(trace["angle"]))
        times = [int(value) for value in trace["interval_times_ms"]]
        if not times:
            continue
        positive_index = min(
            range(len(times)), key=lambda index: abs(times[index] - actual_by_key[key])
        )
        features.extend([list(vector) for vector in trace["features"]])
        labels.extend(1 if index == positive_index else 0 for index in range(len(times)))
    return features, labels


def _gate(metrics: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        "median_absolute_error": metrics["median_absolute_error_ms"] <= 250,
        "within_500_ms_share": metrics["within_500_ms_share"] >= 0.90,
        "p90_absolute_error": metrics["p90_absolute_error_ms"] <= 750,
        "median_signed_bias": abs(metrics["median_signed_bias_ms"]) <= 125,
        "both_angles_within_500_ms_share": (
            metrics["both_angles_within_500_ms_share"] is not None
            and metrics["both_angles_within_500_ms_share"] >= 0.80
        ),
    }
    return {"passed": all(checks.values()), "checks": checks}


def _comparison(
    baseline_rows: Sequence[Mapping[str, Any]],
    result_rows: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    result = {
        "improved_angles": 0,
        "worsened_angles": 0,
        "rescued_failures": 0,
        "regressed_previous_passes": 0,
    }
    for baseline, candidate in zip(baseline_rows, result_rows):
        old = abs(int(baseline["error_ms"]))
        new = abs(int(candidate["error_ms"]))
        result["improved_angles"] += new < old
        result["worsened_angles"] += new > old
        result["rescued_failures"] += old > 500 and new <= 500
        result["regressed_previous_passes"] += old <= 500 and new > 500
    return result


def evaluate_learned_temporal(
    iteration_7e_report_path: Path,
    feature_paths: Sequence[Path],
) -> dict[str, Any]:
    report = json.loads(iteration_7e_report_path.read_text(encoding="utf-8"))
    if report.get("iteration_id") != "iteration-7e-detected-player-motion-v1":
        raise ValueError("Iteration 7J requires the frozen Iteration 7E report")
    declared = {
        (str(row["name"]), str(row["sha256"]))
        for row in report["frozen_input"]["source_manifest_fingerprints"]
    }
    actual_fingerprints = {(path.name, sha256_file(path)) for path in feature_paths}
    if declared != actual_fingerprints:
        raise ValueError("Iteration 7J feature manifests differ from frozen 7E")
    source_rows = report["development"]["selected"]["rows"]
    if len(source_rows) != EXPECTED_ANGLES:
        raise ValueError("Iteration 7J requires 90 frozen 7E rows")
    metadata = _feature_metadata(feature_paths)
    traces: list[dict[str, Any]] = []
    actual_by_key: dict[tuple[str, int], int] = {}
    for source in source_rows:
        key = (str(source["clip_id"]), int(source["angle"]))
        trace = extract_temporal_trace(
            metadata[key], int(source["candidate_onset_ms"])
        )
        trace.update({
            "clip_id": key[0],
            "angle": key[1],
            "cohort_id": str(source["cohort_id"]),
        })
        traces.append(trace)
        actual_by_key[key] = int(source["actual_snap_ms"])

    cohorts = sorted({str(row["cohort_id"]) for row in source_rows})
    if len(cohorts) != 2:
        raise ValueError("Iteration 7J requires exactly two development cohorts")
    folds: dict[str, Any] = {}
    predictions: dict[tuple[str, int], dict[str, Any]] = {}
    for test_cohort in cohorts:
        train_traces = [
            trace for trace in traces if trace["cohort_id"] != test_cohort
        ]
        test_traces = [
            trace for trace in traces if trace["cohort_id"] == test_cohort
        ]
        train_features, train_labels = _training_examples(
            train_traces, actual_by_key
        )
        model = fit_balanced_logistic(train_features, train_labels)
        for trace in test_traces:
            probability = predict_probabilities(model, trace["features"])
            key = (str(trace["clip_id"]), int(trace["angle"]))
            predictions[key] = select_learned_candidate(
                trace["interval_times_ms"], probability
            )
        folds[test_cohort] = {
            "trained_on": sorted({str(trace["cohort_id"]) for trace in train_traces}),
            "training_angles": len(train_traces),
            "test_angles": len(test_traces),
            "positive_examples": model["positive_examples"],
            "negative_examples": model["negative_examples"],
            "training_loss": model["training_loss"],
            "weight_sha256": hashlib.sha256(
                _canonical_bytes(model["weights"])
            ).hexdigest(),
        }

    baseline_rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    composite_rows: list[dict[str, Any]] = []
    confident_count = 0
    rows: list[dict[str, Any]] = []
    for source in source_rows:
        key = (str(source["clip_id"]), int(source["angle"]))
        prediction = predictions[key]
        actual = int(source["actual_snap_ms"])
        baseline_candidate = int(source["candidate_onset_ms"])
        learned_candidate = int(prediction["candidate_ms"])
        confident = bool(prediction["confident"])
        confident_count += confident
        composite_candidate = learned_candidate if confident else baseline_candidate
        common = {
            "item_id": str(source["item_id"]),
            "clip_id": key[0],
            "clip_number": int(source["clip_number"]),
            "cohort_id": str(source["cohort_id"]),
            "angle": key[1],
            "actual_snap_ms": actual,
        }
        baseline_rows.append({
            **common,
            "candidate_onset_ms": baseline_candidate,
            "error_ms": actual - baseline_candidate,
        })
        raw_rows.append({
            **common,
            "candidate_onset_ms": learned_candidate,
            "error_ms": actual - learned_candidate,
        })
        composite_rows.append({
            **common,
            "candidate_onset_ms": composite_candidate,
            "error_ms": actual - composite_candidate,
        })
        rows.append({
            **common,
            "iteration_7e_candidate_ms": baseline_candidate,
            "learned_candidate_ms": learned_candidate,
            "candidate_onset_ms": composite_candidate,
            "error_ms": actual - composite_candidate,
            **prediction,
            "selection_status": (
                "learned_temporal_selected" if confident else "7e_confidence_fallback"
            ),
        })

    baseline_metrics = score_rows(baseline_rows)
    raw_metrics = score_rows(raw_rows)
    composite_metrics = score_rows(composite_rows)
    comparison = _comparison(baseline_rows, composite_rows)
    comparison.update({
        "net_within_500_ms_change": (
            composite_metrics["within_500_ms"] - baseline_metrics["within_500_ms"]
        ),
        "net_paired_play_change": (
            composite_metrics["both_angles_within_500_ms"]
            - baseline_metrics["both_angles_within_500_ms"]
        ),
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": "development_complete",
        "holdout_status": "third_game_unopened",
        "frozen_inputs": {
            "iteration_7e_report_sha256": sha256_file(iteration_7e_report_path),
            "feature_manifest_fingerprints": [
                {"name": path.name, "sha256": sha256_file(path)}
                for path in feature_paths
            ],
        },
        "methodology": {
            "sample_fps": SAMPLE_FPS,
            "window_radius_ms": WINDOW_RADIUS_MS,
            "sample_width": SAMPLE_WIDTH,
            "grid": [GRID_ROWS, GRID_COLUMNS],
            "active_difference_threshold": ACTIVE_DIFFERENCE_THRESHOLD,
            "temporal_context_intervals": 3,
            "training_steps": TRAINING_STEPS,
            "learning_rate": LEARNING_RATE,
            "l2_penalty": L2_PENALTY,
            "minimum_probability": MINIMUM_PROBABILITY,
            "minimum_median_margin": MINIMUM_MEDIAN_MARGIN,
            "cross_validation": "leave_one_game_out",
            "parameter_grid": False,
            "film_specific_behavior": False,
        },
        "folds": folds,
        "development": {
            "confident_learned_candidates": confident_count,
            "iteration_7e_baseline": baseline_metrics,
            "learned_raw": raw_metrics,
            "composite": composite_metrics,
            "development_gate": _gate(composite_metrics),
            "comparison": comparison,
            "rows": rows,
        },
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    development = report["development"]
    baseline = development["iteration_7e_baseline"]
    raw = development["learned_raw"]
    composite = development["composite"]
    comparison = development["comparison"]
    gate = development["development_gate"]
    decision = (
        "PASS development gate; freeze before holdout authorization."
        if gate["passed"] else
        "HOLD; do not tune or open the third-game holdout."
    )
    lines = [
        "# TapeSift Iteration 7J - Learned Temporal Grid",
        "",
        "A deterministic balanced logistic classifier was trained with",
        "leave-one-game-out folds over camera-compensated 4x4 temporal grids.",
        "",
        f"**Decision:** {decision}",
        "",
        "## Development result",
        "",
        "| Method | Median absolute | Within 500 ms | P90 | Signed bias | Paired |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in (
        ("7E baseline", baseline),
        ("7J learned raw", raw),
        ("7J confidence fallback", composite),
    ):
        lines.append(
            f"| {name} | {metrics['median_absolute_error_ms']} ms | "
            f"{metrics['within_500_ms_share']:.1%} | "
            f"{metrics['p90_absolute_error_ms']} ms | "
            f"{metrics['median_signed_bias_ms']} ms | "
            f"{metrics['both_angles_within_500_ms_share']:.1%} |"
        )
    lines.extend([
        "",
        "## Comparison to 7E",
        "",
        f"- Confident learned candidates: {development['confident_learned_candidates']} / 90",
        f"- Rescued failures: {comparison['rescued_failures']}",
        f"- Regressed previous passes: {comparison['regressed_previous_passes']}",
        f"- Net passing-angle change: {comparison['net_within_500_ms_change']:+d}",
        f"- Net paired-play change: {comparison['net_paired_play_change']:+d}",
        "",
        "## Fold isolation",
        "",
    ])
    for cohort, fold in report["folds"].items():
        lines.append(
            f"- `{cohort}` was scored by a model trained only on "
            f"`{', '.join(fold['trained_on'])}`."
        )
    lines.extend(["", "The third-game holdout remained unopened.", ""])
    return "\n".join(lines)


def run_and_write(
    iteration_7e_report_path: Path,
    feature_paths: Sequence[Path],
    output_json_path: Path,
    output_markdown_path: Path,
    manifest_path: Path,
    *,
    reproduction_check: bool = False,
) -> dict[str, Any]:
    report = evaluate_learned_temporal(iteration_7e_report_path, feature_paths)
    report_bytes = _canonical_bytes(report)
    reproduction_identical = False
    if reproduction_check:
        second = evaluate_learned_temporal(iteration_7e_report_path, feature_paths)
        reproduction_identical = _canonical_bytes(second) == report_bytes
        if not reproduction_identical:
            raise RuntimeError("Two Iteration 7J runs were not identical")
    json_text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    markdown_text = render_markdown(report)
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json_text, encoding="utf-8")
    output_markdown_path.write_text(markdown_text, encoding="utf-8")
    development = report["development"]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": (
            "development_pass" if development["development_gate"]["passed"]
            else "development_hold_learned_temporal_not_ready"
        ),
        "decision": (
            "freeze_before_holdout_authorization"
            if development["development_gate"]["passed"]
            else "negative_result_do_not_tune_or_promote"
        ),
        "holdout_status": "third_game_unopened",
        "confident_learned_candidates": development["confident_learned_candidates"],
        "development_metrics": development["composite"],
        "development_gate": development["development_gate"],
        "comparison_to_iteration_7e": development["comparison"],
        "folds": report["folds"],
        "artifacts": {
            "json_sha256": hashlib.sha256(json_text.encode("utf-8")).hexdigest(),
            "markdown_sha256": hashlib.sha256(
                markdown_text.encode("utf-8")
            ).hexdigest(),
            "reproduction_runs_byte_identical": reproduction_identical,
        },
        "validation": {"complete_development_runs": 2 if reproduction_check else 1},
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest
