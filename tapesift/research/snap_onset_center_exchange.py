"""Iteration 7I local center/ball exchange evidence experiment.

One fixed image-change rule is evaluated around human spatial anchors. Human
snap times are used for scoring and for the already-frozen anchor reference
frame, never for temporal candidate selection. Occluded anchors and weak local
signals retain the frozen Iteration 7E candidate.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


ITERATION_ID = "iteration-7i-center-exchange-evidence-v1"
SCHEMA_VERSION = "1.0"
SAMPLE_FPS = 16.0
WINDOW_RADIUS_MS = 750
SEARCH_START_MS = -500
ROI_RADIUS_X = 0.045
ROI_RADIUS_Y = 0.065
TOP_PIXEL_SHARE = 0.10
MINIMUM_ABSOLUTE_RISE = 8.0
MINIMUM_MAD_MULTIPLIER = 3.0
MINIMUM_RISE_RATIO = 1.8
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


def select_local_candidate(
    sample_times_ms: Sequence[int],
    local_change_scores: Sequence[float],
    coarse_candidate_ms: int,
) -> dict[str, Any]:
    if len(sample_times_ms) != len(local_change_scores):
        raise ValueError("Local sample times and scores must have equal length")
    if len(sample_times_ms) < 6:
        return {"status": "insufficient_samples", "candidate_ms": None}

    baseline = [
        float(score)
        for timestamp, score in zip(sample_times_ms, local_change_scores)
        if timestamp <= coarse_candidate_ms - 250
    ]
    if len(baseline) < 3:
        return {"status": "insufficient_baseline", "candidate_ms": None}
    baseline_median = statistics.median(baseline)
    baseline_mad = statistics.median(
        abs(value - baseline_median) for value in baseline
    )
    threshold = baseline_median + max(
        MINIMUM_ABSOLUTE_RISE,
        MINIMUM_MAD_MULTIPLIER * baseline_mad,
    )
    qualified: list[dict[str, Any]] = []
    for index in range(1, len(sample_times_ms)):
        timestamp = int(sample_times_ms[index])
        score = float(local_change_scores[index])
        if not coarse_candidate_ms + SEARCH_START_MS <= timestamp <= \
                coarse_candidate_ms + WINDOW_RADIUS_MS:
            continue
        ratio = score / max(baseline_median, 1.0)
        if score < threshold or ratio < MINIMUM_RISE_RATIO:
            continue
        qualified.append({
            "candidate_ms": round(
                (int(sample_times_ms[index - 1]) + timestamp) / 2
            ),
            "sample_ms": timestamp,
            "score": round(score, 6),
            "rise_ratio": round(ratio, 6),
        })
    if not qualified:
        return {
            "status": "no_qualified_local_rise",
            "candidate_ms": None,
            "baseline_median": round(baseline_median, 6),
            "baseline_mad": round(baseline_mad, 6),
            "threshold": round(threshold, 6),
        }
    selected = sorted(
        qualified,
        key=lambda row: (
            -float(row["score"]),
            abs(int(row["candidate_ms"]) - coarse_candidate_ms),
            int(row["candidate_ms"]),
        ),
    )[0]
    return {
        "status": "selected_local_rise",
        **selected,
        "baseline_median": round(baseline_median, 6),
        "baseline_mad": round(baseline_mad, 6),
        "threshold": round(threshold, 6),
        "qualified_count": len(qualified),
    }


def _top_change_score(values: Any) -> float:
    import numpy as np

    flattened = values.reshape(-1)
    if flattened.size == 0:
        return 0.0
    count = max(1, math.ceil(flattened.size * TOP_PIXEL_SHARE))
    start = flattened.size - count
    strongest = np.partition(flattened, start)[start:]
    return float(strongest.mean())


def extract_local_trace(
    record: Mapping[str, Any],
    label: Mapping[str, Any],
    coarse_candidate_ms: int,
) -> dict[str, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("OpenCV and NumPy are required for Iteration 7I") from exc

    source_path = str(record["source_video_path"])
    capture = cv2.VideoCapture(source_path)
    if not capture.isOpened():
        raise FileNotFoundError(source_path)
    step_ms = 1000.0 / SAMPLE_FPS
    times = sorted({
        coarse_candidate_ms + round(index * step_ms)
        for index in range(-12, 13)
        if int(record["range_start_ms"]) <=
        coarse_candidate_ms + round(index * step_ms) <=
        int(record["range_end_ms"])
    })
    frames: list[Any] = []
    try:
        for timestamp in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp)
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError(f"Could not decode {source_path} at {timestamp} ms")
            height, width = frame.shape[:2]
            if width > 640:
                scale = 640 / width
                frame = cv2.resize(
                    frame,
                    (640, round(height * scale)),
                    interpolation=cv2.INTER_AREA,
                )
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frames.append(cv2.GaussianBlur(gray, (3, 3), 0))
    finally:
        capture.release()

    scores = [0.0]
    shifts: list[list[float]] = [[0.0, 0.0]]
    for previous, current in zip(frames, frames[1:]):
        shift, _response = cv2.phaseCorrelate(
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
        height, width = previous.shape
        center_x = round(float(label["anchor_x"]) * (width - 1))
        center_y = round(float(label["anchor_y"]) * (height - 1))
        radius_x = max(8, round(width * ROI_RADIUS_X))
        radius_y = max(8, round(height * ROI_RADIUS_Y))
        left = max(0, center_x - radius_x)
        right = min(width, center_x + radius_x + 1)
        top = max(0, center_y - radius_y)
        bottom = min(height, center_y + radius_y + 1)
        difference = cv2.absdiff(
            previous[top:bottom, left:right],
            aligned[top:bottom, left:right],
        )
        scores.append(_top_change_score(difference))
        shifts.append([round(float(dx), 6), round(float(dy), 6)])
    selection = select_local_candidate(times, scores, coarse_candidate_ms)
    return {
        "sample_times_ms": times,
        "local_change_scores": [round(score, 6) for score in scores],
        "global_shifts": shifts,
        "selection": selection,
    }


def score_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("Cannot score an empty row set")
    errors = [int(row["error_ms"]) for row in rows]
    absolute = sorted(abs(error) for error in errors)
    within = sum(value <= 500 for value in absolute)
    paired: dict[str, list[bool]] = {}
    for row in rows:
        paired.setdefault(str(row["item_id"]), []).append(
            abs(int(row["error_ms"])) <= 500
        )
    complete_pairs = [values for values in paired.values() if len(values) == 2]
    paired_within = sum(all(values) for values in complete_pairs)

    def percentile(values: Sequence[int], share: float) -> float:
        position = (len(values) - 1) * share
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return float(values[lower])
        weight = position - lower
        return values[lower] * (1 - weight) + values[upper] * weight

    return {
        "angle_judgments": len(rows),
        "within_500_ms": within,
        "within_500_ms_share": round(within / len(rows), 6),
        "median_absolute_error_ms": statistics.median(absolute),
        "median_signed_bias_ms": statistics.median(errors),
        "p90_absolute_error_ms": round(percentile(absolute, 0.90), 3),
        "plays_with_both_angles": len(complete_pairs),
        "both_angles_within_500_ms": paired_within,
        "both_angles_within_500_ms_share": (
            round(paired_within / len(complete_pairs), 6)
            if complete_pairs else None
        ),
    }


def _development_gate(metrics: Mapping[str, Any]) -> dict[str, Any]:
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


def evaluate_center_exchange(
    iteration_7e_report_path: Path,
    package_path: Path,
    labels_path: Path,
) -> dict[str, Any]:
    baseline_report = json.loads(
        iteration_7e_report_path.read_text(encoding="utf-8")
    )
    if baseline_report.get("iteration_id") != \
            "iteration-7e-detected-player-motion-v1":
        raise ValueError("Iteration 7I requires the frozen Iteration 7E report")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package_rows = {
        str(row["review_item_id"]): row for row in package["items"]
    }
    labels = {
        str(row["review_item_id"]): row for row in _read_jsonl(labels_path)
    }
    source_rows = baseline_report["development"]["selected"]["rows"]
    if len(source_rows) != EXPECTED_ANGLES or len(labels) != EXPECTED_ANGLES:
        raise ValueError("Iteration 7I requires 90 baseline rows and 90 labels")

    result_rows: list[dict[str, Any]] = []
    visible_baseline_rows: list[dict[str, Any]] = []
    visible_local_rows: list[dict[str, Any]] = []
    selected_count = 0
    for source in source_rows:
        review_item_id = f"{source['item_id']}:angle-{source['angle']}"
        record = package_rows[review_item_id]
        label = labels[review_item_id]
        baseline_candidate = int(source["candidate_onset_ms"])
        actual = int(source["actual_snap_ms"])
        candidate = baseline_candidate
        trace = None
        selection_status = f"anchor_{label['anchor_status']}_7e_fallback"
        if label["anchor_status"] == "visible":
            trace = extract_local_trace(record, label, baseline_candidate)
            selection = trace["selection"]
            if selection["candidate_ms"] is not None:
                candidate = int(selection["candidate_ms"])
                selected_count += 1
                selection_status = "local_center_exchange_selected"
            else:
                selection_status = "weak_local_signal_7e_fallback"
            visible_baseline_rows.append({
                **source,
                "error_ms": actual - baseline_candidate,
            })
            visible_local_rows.append({
                **source,
                "candidate_onset_ms": candidate,
                "error_ms": actual - candidate,
            })
        result_rows.append({
            "item_id": str(source["item_id"]),
            "clip_id": str(source["clip_id"]),
            "clip_number": int(source["clip_number"]),
            "cohort_id": str(source["cohort_id"]),
            "angle": int(source["angle"]),
            "anchor_status": str(label["anchor_status"]),
            "priority_failure": bool(record.get("priority_failure")),
            "actual_snap_ms": actual,
            "iteration_7e_candidate_ms": baseline_candidate,
            "candidate_onset_ms": candidate,
            "error_ms": actual - candidate,
            "selection_status": selection_status,
            "local_trace": trace,
        })

    baseline_rows = [{**row, "error_ms": int(row["error_ms"])} for row in source_rows]
    baseline_metrics = score_rows(baseline_rows)
    composite_metrics = score_rows(result_rows)
    visible_baseline = score_rows(visible_baseline_rows)
    visible_local = score_rows(visible_local_rows)
    comparison = {
        "improved_angles": 0,
        "worsened_angles": 0,
        "rescued_failures": 0,
        "regressed_previous_passes": 0,
        "net_within_500_ms_change": (
            composite_metrics["within_500_ms"] - baseline_metrics["within_500_ms"]
        ),
        "net_paired_play_change": (
            composite_metrics["both_angles_within_500_ms"]
            - baseline_metrics["both_angles_within_500_ms"]
        ),
    }
    for baseline, result in zip(baseline_rows, result_rows):
        old = abs(int(baseline["error_ms"]))
        new = abs(int(result["error_ms"]))
        comparison["improved_angles"] += new < old
        comparison["worsened_angles"] += new > old
        comparison["rescued_failures"] += old > 500 and new <= 500
        comparison["regressed_previous_passes"] += old <= 500 and new > 500

    return {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": "development_complete",
        "holdout_status": "third_game_unopened",
        "frozen_inputs": {
            "iteration_7e_report_sha256": sha256_file(iteration_7e_report_path),
            "anchor_package_sha256": sha256_file(package_path),
            "anchor_labels_sha256": sha256_file(labels_path),
        },
        "methodology": {
            "sample_fps": SAMPLE_FPS,
            "window_radius_ms": WINDOW_RADIUS_MS,
            "search_start_ms": SEARCH_START_MS,
            "roi_radius_x": ROI_RADIUS_X,
            "roi_radius_y": ROI_RADIUS_Y,
            "top_pixel_share": TOP_PIXEL_SHARE,
            "minimum_absolute_rise": MINIMUM_ABSOLUTE_RISE,
            "minimum_mad_multiplier": MINIMUM_MAD_MULTIPLIER,
            "minimum_rise_ratio": MINIMUM_RISE_RATIO,
            "global_shift_compensation": "phase_correlation",
            "parameter_grid": False,
            "film_specific_behavior": False,
            "human_anchor_role": "spatial_oracle_viability_only",
        },
        "coverage": {
            "visible_anchors": len(visible_local_rows),
            "occluded_anchors": EXPECTED_ANGLES - len(visible_local_rows),
            "local_candidates_selected": selected_count,
        },
        "development": {
            "iteration_7e_baseline": baseline_metrics,
            "visible_subset_7e": visible_baseline,
            "visible_subset_local": visible_local,
            "composite": composite_metrics,
            "development_gate": _development_gate(composite_metrics),
            "comparison": comparison,
            "rows": result_rows,
        },
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    development = report["development"]
    baseline = development["iteration_7e_baseline"]
    composite = development["composite"]
    visible_old = development["visible_subset_7e"]
    visible_new = development["visible_subset_local"]
    comparison = development["comparison"]
    coverage = report["coverage"]
    gate = development["development_gate"]
    decision = (
        "PASS development gate; freeze before holdout authorization."
        if gate["passed"] else
        "HOLD; do not tune or open the third-game holdout."
    )
    return "\n".join([
        "# TapeSift Iteration 7I - Center Exchange Evidence",
        "",
        "One fixed local image-change rule was applied around human spatial",
        "anchors. Occluded and weak-signal angles retain frozen 7E timing.",
        "",
        f"**Decision:** {decision}",
        "",
        "## Coverage",
        "",
        f"- Visible anchors: {coverage['visible_anchors']} / 90",
        f"- Occluded anchors: {coverage['occluded_anchors']} / 90",
        f"- Local candidates selected: {coverage['local_candidates_selected']}",
        "",
        "## Development result",
        "",
        "| Scope | Median absolute | Within 500 ms | P90 | Signed bias | Paired |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| 7E all | {baseline['median_absolute_error_ms']} ms | "
        f"{baseline['within_500_ms_share']:.1%} | {baseline['p90_absolute_error_ms']} ms | "
        f"{baseline['median_signed_bias_ms']} ms | {baseline['both_angles_within_500_ms_share']:.1%} |",
        f"| 7I composite | {composite['median_absolute_error_ms']} ms | "
        f"{composite['within_500_ms_share']:.1%} | {composite['p90_absolute_error_ms']} ms | "
        f"{composite['median_signed_bias_ms']} ms | {composite['both_angles_within_500_ms_share']:.1%} |",
        f"| Visible 7E | {visible_old['median_absolute_error_ms']} ms | "
        f"{visible_old['within_500_ms_share']:.1%} | {visible_old['p90_absolute_error_ms']} ms | "
        f"{visible_old['median_signed_bias_ms']} ms | n/a |",
        f"| Visible local | {visible_new['median_absolute_error_ms']} ms | "
        f"{visible_new['within_500_ms_share']:.1%} | {visible_new['p90_absolute_error_ms']} ms | "
        f"{visible_new['median_signed_bias_ms']} ms | n/a |",
        "",
        "## Comparison to 7E",
        "",
        f"- Rescued failures: {comparison['rescued_failures']}",
        f"- Regressed previous passes: {comparison['regressed_previous_passes']}",
        f"- Net passing-angle change: {comparison['net_within_500_ms_change']:+d}",
        f"- Net paired-play change: {comparison['net_paired_play_change']:+d}",
        "",
        "The third-game holdout remained unopened.",
        "",
    ])


def run_and_write(
    iteration_7e_report_path: Path,
    package_path: Path,
    labels_path: Path,
    output_json_path: Path,
    output_markdown_path: Path,
    manifest_path: Path,
    *,
    reproduction_check: bool = False,
) -> dict[str, Any]:
    report = evaluate_center_exchange(
        iteration_7e_report_path, package_path, labels_path
    )
    report_bytes = _canonical_bytes(report)
    reproduction_identical = False
    if reproduction_check:
        second = evaluate_center_exchange(
            iteration_7e_report_path, package_path, labels_path
        )
        reproduction_identical = _canonical_bytes(second) == report_bytes
        if not reproduction_identical:
            raise RuntimeError("Two Iteration 7I runs were not identical")
    json_text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    markdown_text = render_markdown(report)
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json_text, encoding="utf-8")
    output_markdown_path.write_text(markdown_text, encoding="utf-8")
    metrics = report["development"]["composite"]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": (
            "development_pass" if report["development"]["development_gate"]["passed"]
            else "development_hold_center_exchange_not_ready"
        ),
        "decision": (
            "freeze_before_holdout_authorization"
            if report["development"]["development_gate"]["passed"]
            else "negative_result_do_not_tune_or_promote"
        ),
        "holdout_status": "third_game_unopened",
        "coverage": report["coverage"],
        "development_metrics": metrics,
        "development_gate": report["development"]["development_gate"],
        "comparison_to_iteration_7e": report["development"]["comparison"],
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
