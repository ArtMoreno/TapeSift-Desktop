"""Development scoring for completed, bounded autodetect review batches."""

from __future__ import annotations

import statistics
from typing import Any

from tapesift.services.segment_scoring import score_segments

SCORE_SCHEMA_VERSION = "1.0"
PRIMARY_IOU_THRESHOLD = 0.75
DIAGNOSTIC_IOU_THRESHOLD = 0.50
RELATIONSHIP_THRESHOLD = 0.50
RECOMMENDED_TRUTH_PER_BATCH = 20
PERSONAL_USE_TARGETS = {
    "precision": 0.80,
    "recall": 0.90,
    "median_max_boundary_error_ms": 2_000,
}


def build_development_batch_score(
    candidates: list[dict[str, Any]],
    manual_recoveries: list[dict[str, Any]],
    *,
    sample_complete: bool,
) -> dict[str, Any]:
    """Score explicitly reviewed roots without cross-root matching.

    Detector candidates are immutable evaluation roots. Keeping their matches
    isolated prevents a corrected neighboring play from accidentally matching
    the wrong prediction. Manual recoveries are zero-prediction roots and
    therefore become observable false negatives inside the confirmed range.
    """
    components = _components(candidates, manual_recoveries)
    available = bool(components)
    primary = _aggregate_components(
        components, iou_threshold=PRIMARY_IOU_THRESHOLD)
    diagnostic = _aggregate_components(
        components, iou_threshold=DIAGNOSTIC_IOU_THRESHOLD)
    boundary = _one_to_one_boundary_metrics(components)
    reviewed_truth_count = sum(
        len(component["truth"]) for component in components)
    return {
        "schema_version": SCORE_SCHEMA_VERSION,
        "score_kind": "bounded_autodetect_development_batch",
        "available": available,
        "preliminary": True,
        "film_wide": False,
        "sample_complete": bool(sample_complete),
        "scope": (
            "One explicitly confirmed contiguous source range; matches are "
            "isolated by immutable detector/recovery root."
            if sample_complete
            else
            "Provisional active or unconfirmed source range; matches are "
            "isolated by immutable detector/recovery root and are not ready "
            "for tuning."
        ),
        "reviewed_candidate_count": sum(
            component["kind"] == "candidate" for component in components),
        "reviewed_play_prediction_count": sum(
            len(component["predictions"]) for component in components),
        "reviewed_unclassified_count": sum(
            component["kind"] == "candidate"
            and not component["predictions"]
            for component in components
        ),
        "reviewed_manual_recovery_count": sum(
            component["kind"] == "manual_recovery"
            for component in components
        ),
        "reviewed_truth_count": reviewed_truth_count,
        "recommended_truth_per_batch": RECOMMENDED_TRUTH_PER_BATCH,
        "ready_for_tuning": (
            sample_complete
            and reviewed_truth_count >= RECOMMENDED_TRUTH_PER_BATCH
        ),
        "primary_metrics": primary,
        "diagnostic_metrics": diagnostic,
        "all_one_to_one_boundary_metrics": boundary,
        "personal_use_target_progress": _target_progress(primary, boundary),
        "claims": {
            "dataset_role": "development",
            "partial_film_scope": True,
            "review_blinding": "none",
            "independent_holdout": False,
            "eligible_for_generalization_claim": False,
            "eligible_for_public_accuracy_claim": False,
        },
    }


def _components(
    candidates: list[dict[str, Any]],
    manual_recoveries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("review_status") != "reviewed":
            continue
        predictions = []
        prediction_refs = []
        if candidate.get("candidate_kind") == "play":
            # Applied bounds are the shipping product prediction. Raw detector
            # bounds may be longer when "first camera angle only" is enabled.
            predictions.append({
                "start_ms": int(candidate["created_start_ms"]),
                "end_ms": int(candidate["created_end_ms"]),
            })
            prediction_refs.append({
                "kind": "detector_candidate",
                "candidate_id": str(candidate["candidate_id"]),
            })
        truth, truth_refs = _truth_ranges(
            candidate.get("final_ranges", []),
            kind="candidate_correction",
            root_id=str(candidate["candidate_id"]),
        )
        components.append({
            "component_id": f"candidate:{candidate['candidate_id']}",
            "kind": "candidate",
            "predictions": predictions,
            "prediction_refs": prediction_refs,
            "truth": truth,
            "truth_refs": truth_refs,
        })

    for recovery in manual_recoveries:
        if recovery.get("review_status") != "reviewed":
            continue
        truth, truth_refs = _truth_ranges(
            recovery.get("final_ranges", []),
            kind="manual_recovered_miss",
            root_id=str(recovery["recovery_id"]),
        )
        components.append({
            "component_id": f"recovery:{recovery['recovery_id']}",
            "kind": "manual_recovery",
            "predictions": [],
            "prediction_refs": [],
            "truth": truth,
            "truth_refs": truth_refs,
        })
    return components


def _truth_ranges(
    ranges: list[dict[str, Any]],
    *,
    kind: str,
    root_id: str,
) -> tuple[list[dict[str, int]], list[dict[str, Any]]]:
    truth = [
        {
            "start_ms": int(item["start_ms"]),
            "end_ms": int(item["end_ms"]),
        }
        for item in ranges
    ]
    refs = [
        {"kind": kind, "root_id": root_id, "range_index": index}
        for index in range(len(truth))
    ]
    return truth, refs


def _aggregate_components(
    components: list[dict[str, Any]],
    *,
    iou_threshold: float,
) -> dict[str, Any] | None:
    if not components:
        return None
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for component in components:
        score = score_segments(
            component["truth"],
            component["predictions"],
            iou_threshold=iou_threshold,
            relationship_threshold=RELATIONSHIP_THRESHOLD,
        )
        rows.append((component, score))

    truth_count = sum(row["truth_count"] for _, row in rows)
    prediction_count = sum(row["prediction_count"] for _, row in rows)
    matched_count = sum(row["matched_count"] for _, row in rows)
    precision = (
        matched_count / prediction_count if prediction_count else None)
    recall = matched_count / truth_count if truth_count else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None
        and recall is not None
        and precision + recall
        else 0.0
        if precision is not None and recall is not None
        else None
    )
    matches: list[dict[str, Any]] = []
    false_positive_refs: list[dict[str, Any]] = []
    false_negative_refs: list[dict[str, Any]] = []
    ious: list[float] = []
    start_errors: list[int] = []
    end_errors: list[int] = []
    merge_count = 0
    split_count = 0
    for component, row in rows:
        for match in row["matches"]:
            prediction_index = match["prediction_index"]
            truth_index = match["truth_index"]
            start_error = abs(
                component["truth"][truth_index]["start_ms"]
                - component["predictions"][prediction_index]["start_ms"])
            end_error = abs(
                component["truth"][truth_index]["end_ms"]
                - component["predictions"][prediction_index]["end_ms"])
            matches.append({
                "component_id": component["component_id"],
                "prediction_ref": component[
                    "prediction_refs"][prediction_index],
                "truth_ref": component["truth_refs"][truth_index],
                "iou": match["iou"],
                "start_error_ms": start_error,
                "end_error_ms": end_error,
                "max_boundary_error_ms": max(start_error, end_error),
            })
            ious.append(float(match["iou"]))
            start_errors.append(start_error)
            end_errors.append(end_error)
        false_positive_refs.extend(
            component["prediction_refs"][index]
            for index in row["unmatched_prediction_indexes"]
        )
        false_negative_refs.extend(
            component["truth_refs"][index]
            for index in row["unmatched_truth_indexes"]
        )
        merge_count += row["merge_count"]
        split_count += row["split_count"]

    return {
        "truth_count": truth_count,
        "prediction_count": prediction_count,
        "count_error": prediction_count - truth_count,
        "matched_count": matched_count,
        "false_positive_count": prediction_count - matched_count,
        "false_negative_count": truth_count - matched_count,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_iou": statistics.fmean(ious) if ious else None,
        "median_iou": statistics.median(ious) if ious else None,
        "median_start_error_ms": (
            statistics.median(start_errors) if start_errors else None),
        "p90_start_error_ms": _percentile(start_errors, 0.90),
        "median_end_error_ms": (
            statistics.median(end_errors) if end_errors else None),
        "p90_end_error_ms": _percentile(end_errors, 0.90),
        "merge_count": merge_count,
        "split_count": split_count,
        "matches": matches,
        "false_positive_refs": false_positive_refs,
        "false_negative_refs": false_negative_refs,
        "iou_threshold": iou_threshold,
        "relationship_threshold": RELATIONSHIP_THRESHOLD,
    }


def _one_to_one_boundary_metrics(
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    skipped = 0
    for component in components:
        if (
            len(component["predictions"]) != 1
            or len(component["truth"]) != 1
        ):
            skipped += 1
            continue
        prediction = component["predictions"][0]
        truth = component["truth"][0]
        start_error = abs(truth["start_ms"] - prediction["start_ms"])
        end_error = abs(truth["end_ms"] - prediction["end_ms"])
        comparisons.append({
            "component_id": component["component_id"],
            "prediction_ref": component["prediction_refs"][0],
            "truth_ref": component["truth_refs"][0],
            "start_error_ms": start_error,
            "end_error_ms": end_error,
            "max_boundary_error_ms": max(start_error, end_error),
        })
    max_errors = [row["max_boundary_error_ms"] for row in comparisons]
    start_errors = [row["start_error_ms"] for row in comparisons]
    end_errors = [row["end_error_ms"] for row in comparisons]
    return {
        "scope": (
            "Every root with exactly one applied prediction and one confirmed "
            "truth range, regardless of IoU."
        ),
        "denominator": len(comparisons),
        "skipped_non_one_to_one_component_count": skipped,
        "median_start_error_ms": (
            statistics.median(start_errors) if start_errors else None),
        "p90_start_error_ms": _percentile(start_errors, 0.90),
        "median_end_error_ms": (
            statistics.median(end_errors) if end_errors else None),
        "p90_end_error_ms": _percentile(end_errors, 0.90),
        "median_max_boundary_error_ms": (
            statistics.median(max_errors) if max_errors else None),
        "p90_max_boundary_error_ms": _percentile(max_errors, 0.90),
        "comparisons": comparisons,
    }


def _target_progress(
    primary: dict[str, Any] | None,
    boundary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "precision": _target(
            primary.get("precision") if primary else None,
            PERSONAL_USE_TARGETS["precision"],
            "min",
        ),
        "recall": _target(
            primary.get("recall") if primary else None,
            PERSONAL_USE_TARGETS["recall"],
            "min",
        ),
        "median_max_boundary_error_ms": _target(
            boundary.get("median_max_boundary_error_ms"),
            PERSONAL_USE_TARGETS["median_max_boundary_error_ms"],
            "max",
        ),
    }


def _target(
    observed: float | int | None,
    target: float | int,
    direction: str,
) -> dict[str, Any]:
    if observed is None:
        passed = None
    elif direction == "min":
        passed = float(observed) >= float(target)
    else:
        passed = float(observed) <= float(target)
    return {
        "observed": observed,
        "target": target,
        "direction": direction,
        "passed": passed,
    }


def _percentile(values: list[int], percentage: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentage
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction
