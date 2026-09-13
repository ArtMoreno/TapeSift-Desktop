"""Segment matching and scoring: how close two sets of ranges are.

Pure arithmetic over {"start_ms", "end_ms"} dicts - no I/O, no FFmpeg, no
project. It lives here because both sides need it and the dependency has to
point this way: `services.autodetect_score_service` used to import it from
`research.segmentation_benchmark`, which imports
`services.play_detect_service` straight back. A service reaching up into the
research sandbox for product logic made `research/` load-bearing for the
shipped app, which is the opposite of what that directory is for.

`research.segmentation_benchmark` re-exports these names, so the research
modules and scripts that already import them from there keep working.
"""

from __future__ import annotations

import statistics
from typing import Any

DEFAULT_IOU_THRESHOLD = 0.50
DEFAULT_RELATIONSHIP_THRESHOLD = 0.30
def intersection_ms(left: dict[str, Any], right: dict[str, Any]) -> int:
    return max(
        0,
        min(int(left["end_ms"]), int(right["end_ms"]))
        - max(int(left["start_ms"]), int(right["start_ms"])),
    )


def duration_ms(segment: dict[str, Any]) -> int:
    return max(0, int(segment["end_ms"]) - int(segment["start_ms"]))


def segment_iou(left: dict[str, Any], right: dict[str, Any]) -> float:
    intersection = intersection_ms(left, right)
    union = duration_ms(left) + duration_ms(right) - intersection
    return intersection / union if union else 0.0


def match_segments(
    truth: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> list[tuple[int, int, float]]:
    candidates = sorted(
        (
            (segment_iou(truth_item, prediction), truth_index, pred_index)
            for truth_index, truth_item in enumerate(truth)
            for pred_index, prediction in enumerate(predictions)
        ),
        reverse=True,
    )
    used_truth: set[int] = set()
    used_predictions: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for iou, truth_index, pred_index in candidates:
        if iou < iou_threshold:
            break
        if truth_index in used_truth or pred_index in used_predictions:
            continue
        used_truth.add(truth_index)
        used_predictions.add(pred_index)
        matches.append((truth_index, pred_index, iou))
    return sorted(matches)


def percentile(values: list[float], percentage: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentage
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def relationship_failures(
    truth: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    threshold: float = DEFAULT_RELATIONSHIP_THRESHOLD,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merges: list[dict[str, Any]] = []
    for pred_index, prediction in enumerate(predictions):
        truth_indexes = [
            truth_index
            for truth_index, truth_item in enumerate(truth)
            if duration_ms(truth_item)
            and intersection_ms(truth_item, prediction)
            / duration_ms(truth_item) >= threshold
        ]
        if len(truth_indexes) >= 2:
            merges.append({
                "prediction_index": pred_index,
                "truth_indexes": truth_indexes,
            })

    splits: list[dict[str, Any]] = []
    for truth_index, truth_item in enumerate(truth):
        prediction_indexes = [
            pred_index
            for pred_index, prediction in enumerate(predictions)
            if duration_ms(prediction)
            and intersection_ms(truth_item, prediction)
            / duration_ms(prediction) >= threshold
        ]
        if len(prediction_indexes) >= 2:
            splits.append({
                "truth_index": truth_index,
                "prediction_indexes": prediction_indexes,
            })
    return merges, splits


def score_segments(
    truth: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    relationship_threshold: float = DEFAULT_RELATIONSHIP_THRESHOLD,
) -> dict[str, Any]:
    matches = match_segments(truth, predictions, iou_threshold)
    matched_truth = {match[0] for match in matches}
    matched_predictions = {match[1] for match in matches}
    true_positives = len(matches)
    false_positives = len(predictions) - true_positives
    false_negatives = len(truth) - true_positives
    precision = (
        true_positives / len(predictions) if predictions else 0.0)
    recall = true_positives / len(truth) if truth else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )
    start_errors = [
        abs(int(truth[truth_index]["start_ms"])
            - int(predictions[pred_index]["start_ms"]))
        for truth_index, pred_index, _ in matches
    ]
    end_errors = [
        abs(int(truth[truth_index]["end_ms"])
            - int(predictions[pred_index]["end_ms"]))
        for truth_index, pred_index, _ in matches
    ]
    ious = [iou for _, _, iou in matches]
    merges, splits = relationship_failures(
        truth, predictions, relationship_threshold)
    return {
        "truth_count": len(truth),
        "prediction_count": len(predictions),
        "count_error": len(predictions) - len(truth),
        "matched_count": true_positives,
        "false_positive_count": false_positives,
        "false_negative_count": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_iou": statistics.fmean(ious) if ious else None,
        "median_iou": statistics.median(ious) if ious else None,
        "median_start_error_ms": (
            statistics.median(start_errors) if start_errors else None),
        "p90_start_error_ms": percentile(start_errors, 0.90),
        "median_end_error_ms": (
            statistics.median(end_errors) if end_errors else None),
        "p90_end_error_ms": percentile(end_errors, 0.90),
        "merge_count": len(merges),
        "split_count": len(splits),
        "merge_candidates": merges,
        "split_candidates": splits,
        "unmatched_truth_indexes": sorted(
            set(range(len(truth))) - matched_truth),
        "unmatched_prediction_indexes": sorted(
            set(range(len(predictions))) - matched_predictions),
        "matches": [
            {
                "truth_index": truth_index,
                "prediction_index": pred_index,
                "iou": iou,
            }
            for truth_index, pred_index, iou in matches
        ],
        "iou_threshold": iou_threshold,
        "relationship_threshold": relationship_threshold,
    }
