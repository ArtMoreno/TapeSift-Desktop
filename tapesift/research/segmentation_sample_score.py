"""Sample-aware scoring for stratified segmentation verification queues.

The normal benchmark scorer assumes complete, film-wide ground truth.  A
verification queue is different: it contains a frozen sample of detector
plays and detector-unclassified windows.  Comparing that sample with every
prediction in the film would turn unreviewed predictions into false
positives.

This module keeps the immutable sampled queue items as *roots*, reconstructs
human split/merge lineage, and scores only inside lineage-connected
components.  It is deliberately filesystem-agnostic; the frozen-artifact
driver is responsible for hashes, provenance, and report writes.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from tapesift.research.segmentation_benchmark import (
    duration_ms,
    intersection_ms,
    percentile,
    score_segments,
    segment_iou,
)


SAMPLE_SCORE_SCHEMA_VERSION = "2.0"
PRIMARY_IOU_THRESHOLD = 0.75
DIAGNOSTIC_IOU_THRESHOLD = 0.50
RELATIONSHIP_THRESHOLD = 0.50
TERMINAL_STATUSES = {"verified", "excluded"}
VALID_DECISIONS = {
    "verified": {"accepted", "revised"},
    "excluded": {"excluded"},
}


class SampleScoreError(ValueError):
    """Raised when a sampled benchmark cannot be scored without guessing."""


@dataclass(frozen=True)
class LockedRoot:
    root_id: str
    film_id: str
    start_ms: int
    end_ms: int
    original_start_ms: int
    original_end_ms: int
    film_duration_ms: int
    candidate_kind: str
    candidate_stratum: str
    prediction_indices: tuple[int, ...]
    payload: dict[str, Any]


@dataclass(frozen=True)
class ResolvedItem:
    item_id: str
    film_id: str
    start_ms: int
    end_ms: int
    status: str
    decision: str
    root_ids: frozenset[str]
    prediction_indices: tuple[int, ...]
    edit_history: tuple[str, ...]
    payload: dict[str, Any]


@dataclass(frozen=True)
class PredictionRef:
    film_id: str
    prediction_index: int
    root_id: str
    start_ms: int
    end_ms: int
    payload: dict[str, Any]

    @property
    def key(self) -> tuple[str, int]:
        return self.film_id, self.prediction_index


@dataclass
class EvaluationComponent:
    component_id: str
    film_id: str
    root_ids: tuple[str, ...]
    predictions: list[PredictionRef]
    items: list[ResolvedItem]

    @property
    def truth_items(self) -> list[ResolvedItem]:
        return [item for item in self.items if item.status == "verified"]

    @property
    def excluded_items(self) -> list[ResolvedItem]:
        return [item for item in self.items if item.status == "excluded"]


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _integer(payload: dict[str, Any], key: str, context: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise SampleScoreError(f"{context} has invalid {key}: {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SampleScoreError(
            f"{context} has invalid {key}: {value!r}") from exc


def _validate_interval(
    start_ms: int,
    end_ms: int,
    *,
    duration: int,
    context: str,
) -> None:
    if start_ms < 0 or end_ms <= start_ms:
        raise SampleScoreError(
            f"{context} has invalid interval {start_ms}..{end_ms}")
    if duration and end_ms > duration:
        raise SampleScoreError(
            f"{context} ends outside the film: {end_ms} > {duration}")


def load_locked_roots(
    root_payloads: Iterable[dict[str, Any]],
    *,
    expected_root_count: int | None = None,
) -> dict[str, LockedRoot]:
    """Validate and normalize the immutable sampled queue items."""

    roots: dict[str, LockedRoot] = {}
    for payload in root_payloads:
        root_id = str(payload.get("item_id") or "")
        if not root_id:
            raise SampleScoreError("A locked root is missing item_id")
        if root_id in roots:
            raise SampleScoreError(f"Duplicate locked root id: {root_id}")
        film_id = str(payload.get("film_id") or "")
        if not film_id:
            raise SampleScoreError(f"{root_id} is missing film_id")
        start_ms = _integer(payload, "start_ms", root_id)
        end_ms = _integer(payload, "end_ms", root_id)
        original_start_ms = _integer(
            payload, "original_start_ms", root_id)
        original_end_ms = _integer(payload, "original_end_ms", root_id)
        duration = _integer(payload, "film_duration_ms", root_id)
        _validate_interval(
            start_ms, end_ms, duration=duration, context=root_id)
        if (start_ms, end_ms) != (original_start_ms, original_end_ms):
            raise SampleScoreError(
                f"Locked root {root_id} is not at its original interval")
        kind = str(payload.get("candidate_kind") or "")
        if kind not in {"play", "unclassified"}:
            raise SampleScoreError(
                f"{root_id} has unsupported candidate kind {kind!r}")
        raw_indices = payload.get("prediction_indices")
        if not isinstance(raw_indices, list) or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in raw_indices
        ):
            raise SampleScoreError(
                f"{root_id} has invalid prediction_indices")
        indices = tuple(raw_indices)
        if len(set(indices)) != len(indices):
            raise SampleScoreError(
                f"{root_id} repeats a prediction index")
        if kind == "play" and len(indices) != 1:
            raise SampleScoreError(
                f"Frozen play root {root_id} must select one prediction")
        if kind == "unclassified" and indices:
            raise SampleScoreError(
                f"Unclassified root {root_id} cannot select a prediction")
        roots[root_id] = LockedRoot(
            root_id=root_id,
            film_id=film_id,
            start_ms=start_ms,
            end_ms=end_ms,
            original_start_ms=original_start_ms,
            original_end_ms=original_end_ms,
            film_duration_ms=duration,
            candidate_kind=kind,
            candidate_stratum=str(
                payload.get("candidate_stratum") or "legacy"),
            prediction_indices=indices,
            payload=payload,
        )

    if expected_root_count is not None and len(roots) != expected_root_count:
        raise SampleScoreError(
            f"Expected {expected_root_count} locked roots, found {len(roots)}")
    _require_nonoverlapping_locked_roots(roots.values())
    return roots


def _require_nonoverlapping_locked_roots(
    roots: Iterable[LockedRoot],
) -> None:
    by_film: dict[str, list[LockedRoot]] = defaultdict(list)
    for root in roots:
        by_film[root.film_id].append(root)
    for film_id, film_roots in by_film.items():
        ordered = sorted(
            film_roots, key=lambda root: (
                root.start_ms, root.end_ms, root.root_id))
        for left, right in zip(ordered, ordered[1:]):
            if left.end_ms > right.start_ms:
                raise SampleScoreError(
                    f"Locked roots overlap in {film_id}: "
                    f"{left.root_id} and {right.root_id}")


def _prediction_payload(
    predictions_by_film: dict[str, dict[str, Any]],
    film_id: str,
) -> dict[str, Any]:
    payload = predictions_by_film.get(film_id)
    if not isinstance(payload, dict):
        raise SampleScoreError(
            f"Missing frozen prediction payload for {film_id}")
    if payload.get("film_id") not in {None, film_id}:
        raise SampleScoreError(
            f"Prediction payload film mismatch for {film_id}")
    if not isinstance(payload.get("plays"), list):
        raise SampleScoreError(
            f"Prediction payload for {film_id} has no plays list")
    if not isinstance(payload.get("unclassified", []), list):
        raise SampleScoreError(
            f"Prediction payload for {film_id} has invalid unclassified data")
    return payload


def validate_frozen_predictions(
    roots: dict[str, LockedRoot],
    predictions_by_film: dict[str, dict[str, Any]],
) -> dict[tuple[str, int], PredictionRef]:
    """Tie each sampled play root to its exact frozen prediction."""

    selected: dict[tuple[str, int], PredictionRef] = {}
    for root in roots.values():
        payload = _prediction_payload(predictions_by_film, root.film_id)
        payload_duration = int(payload.get("duration_ms") or 0)
        if payload_duration and payload_duration != root.film_duration_ms:
            raise SampleScoreError(
                f"Film duration changed for {root.film_id}: "
                f"{payload_duration} != {root.film_duration_ms}")
        plays = payload["plays"]
        if root.candidate_kind == "play":
            index = root.prediction_indices[0]
            if index < 0 or index >= len(plays):
                raise SampleScoreError(
                    f"{root.root_id} selects missing prediction {index}")
            key = root.film_id, index
            if key in selected:
                raise SampleScoreError(
                    f"Prediction {root.film_id}[{index}] is sampled twice")
            prediction = plays[index]
            bounds = (
                _integer(prediction, "start_ms", f"{root.film_id}[{index}]"),
                _integer(prediction, "end_ms", f"{root.film_id}[{index}]"),
            )
            if bounds != (root.start_ms, root.end_ms):
                raise SampleScoreError(
                    f"Frozen prediction bounds changed for {root.root_id}: "
                    f"{bounds} != {(root.start_ms, root.end_ms)}")
            selected[key] = PredictionRef(
                film_id=root.film_id,
                prediction_index=index,
                root_id=root.root_id,
                start_ms=bounds[0],
                end_ms=bounds[1],
                payload=prediction,
            )
            continue

        containing = [
            segment
            for segment in payload.get("unclassified", [])
            if int(segment.get("start_ms", -1)) <= root.start_ms
            and int(segment.get("end_ms", -1)) >= root.end_ms
        ]
        if len(containing) != 1:
            raise SampleScoreError(
                f"Unclassified root {root.root_id} must be contained in "
                f"exactly one frozen unclassified range; found "
                f"{len(containing)}")
        if any(
            intersection_ms(
                {"start_ms": root.start_ms, "end_ms": root.end_ms},
                play,
            ) > 0
            for play in plays
        ):
            raise SampleScoreError(
                f"Unclassified root {root.root_id} overlaps a frozen play")
    return selected


def _root_from_token(
    token: str,
    film_roots: dict[str, LockedRoot],
    *,
    context: str,
) -> str:
    candidates = [
        root_id
        for root_id in film_roots
        if token == root_id
        or token.startswith(f"{root_id}:split:")
        or token.startswith(f"{root_id}:merge:")
    ]
    if len(candidates) != 1:
        raise SampleScoreError(
            f"{context} lineage token {token!r} resolved to "
            f"{len(candidates)} roots")
    return candidates[0]


def resolve_item_lineage(
    payload: dict[str, Any],
    roots: dict[str, LockedRoot],
) -> ResolvedItem:
    """Resolve one terminal human item back to immutable queue roots."""

    item_id = str(payload.get("item_id") or "")
    film_id = str(payload.get("film_id") or "")
    context = item_id or "<final item>"
    film_roots = {
        root_id: root
        for root_id, root in roots.items()
        if root.film_id == film_id
    }
    if not film_roots:
        raise SampleScoreError(
            f"{context} belongs to unknown film {film_id!r}")
    history_payload = payload.get("edit_history", [])
    if not isinstance(history_payload, list) or any(
        not isinstance(entry, str) for entry in history_payload
    ):
        raise SampleScoreError(f"{context} has invalid edit_history")
    history = tuple(history_payload)
    tokens = [item_id]
    merge_events = 0
    for entry in history:
        if not entry.startswith("merged:"):
            continue
        merge_events += 1
        parts = entry.removeprefix("merged:").split("+")
        if len(parts) != 2 or not all(parts):
            raise SampleScoreError(
                f"{context} has malformed merge history: {entry!r}")
        tokens.extend(parts)
    root_ids = frozenset(
        _root_from_token(token, film_roots, context=context)
        for token in tokens
    )
    if len(root_ids) > 1 and merge_events == 0:
        raise SampleScoreError(
            f"{context} spans multiple roots without merge history")
    if ":merge:" in item_id and merge_events == 0:
        raise SampleScoreError(
            f"{context} has a merge id without merge history")
    if ":split:" in item_id and not any(
        entry.startswith("split@") for entry in history
    ):
        raise SampleScoreError(
            f"{context} has a split id without split history")

    status = str(payload.get("status") or "")
    decision = str(payload.get("decision") or "")
    if status not in TERMINAL_STATUSES:
        raise SampleScoreError(
            f"{context} is not terminal: status={status!r}")
    if decision not in VALID_DECISIONS[status]:
        raise SampleScoreError(
            f"{context} has inconsistent status/decision: "
            f"{status!r}/{decision!r}")
    start_ms = _integer(payload, "start_ms", context)
    end_ms = _integer(payload, "end_ms", context)
    durations = {roots[root_id].film_duration_ms for root_id in root_ids}
    if len(durations) != 1:
        raise SampleScoreError(
            f"{context} lineage has inconsistent film durations")
    _validate_interval(
        start_ms, end_ms, duration=durations.pop(), context=context)

    expected_indices = tuple(sorted({
        index
        for root_id in root_ids
        for index in roots[root_id].prediction_indices
    }))
    raw_indices = payload.get("prediction_indices")
    if not isinstance(raw_indices, list):
        raise SampleScoreError(
            f"{context} has invalid prediction_indices")
    actual_indices = tuple(sorted(raw_indices))
    if actual_indices != expected_indices:
        raise SampleScoreError(
            f"{context} prediction lineage changed: "
            f"{actual_indices} != {expected_indices}")
    expected_original = (
        min(roots[root_id].original_start_ms for root_id in root_ids),
        max(roots[root_id].original_end_ms for root_id in root_ids),
    )
    actual_original = (
        _integer(payload, "original_start_ms", context),
        _integer(payload, "original_end_ms", context),
    )
    if actual_original != expected_original:
        raise SampleScoreError(
            f"{context} original lineage envelope changed: "
            f"{actual_original} != {expected_original}")

    return ResolvedItem(
        item_id=item_id,
        film_id=film_id,
        start_ms=start_ms,
        end_ms=end_ms,
        status=status,
        decision=decision,
        root_ids=root_ids,
        prediction_indices=actual_indices,
        edit_history=history,
        payload=payload,
    )


def resolve_final_items(
    final_payloads: Iterable[dict[str, Any]],
    roots: dict[str, LockedRoot],
) -> list[ResolvedItem]:
    """Resolve the complete final review state and prove root coverage."""

    resolved: list[ResolvedItem] = []
    item_ids: set[str] = set()
    by_root: dict[str, list[ResolvedItem]] = defaultdict(list)
    for payload in final_payloads:
        item = resolve_item_lineage(payload, roots)
        if item.item_id in item_ids:
            raise SampleScoreError(
                f"Duplicate final item id: {item.item_id}")
        item_ids.add(item.item_id)
        resolved.append(item)
        for root_id in item.root_ids:
            by_root[root_id].append(item)

    missing = sorted(set(roots) - set(by_root))
    if missing:
        raise SampleScoreError(
            f"Final review silently dropped locked roots: {missing}")
    unknown = sorted(set(by_root) - set(roots))
    if unknown:
        raise SampleScoreError(
            f"Final review introduced unknown roots: {unknown}")
    for root_id, items in by_root.items():
        if len(items) <= 1:
            continue
        if not all(
            ":split:" in item.item_id
            and any(entry.startswith("split@")
                    for entry in item.edit_history)
            for item in items
        ):
            raise SampleScoreError(
                f"Root {root_id} appears in multiple final items without "
                f"complete split lineage")
    _require_nonoverlapping_final_truth(resolved)
    return sorted(
        resolved,
        key=lambda item: (
            item.film_id, item.start_ms, item.end_ms, item.item_id),
    )


def _require_nonoverlapping_final_truth(
    items: Iterable[ResolvedItem],
) -> None:
    by_film: dict[str, list[ResolvedItem]] = defaultdict(list)
    for item in items:
        if item.status == "verified":
            by_film[item.film_id].append(item)
    for film_id, truth in by_film.items():
        ordered = sorted(
            truth,
            key=lambda item: (item.start_ms, item.end_ms, item.item_id),
        )
        for left, right in zip(ordered, ordered[1:]):
            if left.end_ms > right.start_ms:
                raise SampleScoreError(
                    f"Final truth overlaps in {film_id}: "
                    f"{left.item_id} and {right.item_id}")


def build_lineage_components(
    roots: dict[str, LockedRoot],
    items: list[ResolvedItem],
    selected_predictions: dict[tuple[str, int], PredictionRef],
) -> list[EvaluationComponent]:
    """Build independent scoring components from split/merge lineage."""

    union_find = _UnionFind(roots)
    for item in items:
        item_roots = sorted(item.root_ids)
        for root_id in item_roots[1:]:
            union_find.union(item_roots[0], root_id)

    roots_by_component: dict[str, set[str]] = defaultdict(set)
    for root_id in roots:
        roots_by_component[union_find.find(root_id)].add(root_id)
    items_by_component: dict[str, list[ResolvedItem]] = defaultdict(list)
    for item in items:
        component_key = union_find.find(next(iter(item.root_ids)))
        items_by_component[component_key].append(item)

    components: list[EvaluationComponent] = []
    seen_predictions: set[tuple[str, int]] = set()
    for component_roots in roots_by_component.values():
        ordered_roots = tuple(sorted(component_roots))
        films = {roots[root_id].film_id for root_id in ordered_roots}
        if len(films) != 1:
            raise SampleScoreError(
                f"Lineage component crosses films: {ordered_roots}")
        film_id = films.pop()
        predictions = []
        for root_id in ordered_roots:
            for index in roots[root_id].prediction_indices:
                key = film_id, index
                prediction = selected_predictions.get(key)
                if prediction is None:
                    raise SampleScoreError(
                        f"Component references unvalidated prediction {key}")
                if key in seen_predictions:
                    raise SampleScoreError(
                        f"Prediction {key} appears in multiple components")
                seen_predictions.add(key)
                predictions.append(prediction)
        predictions.sort(
            key=lambda prediction: (
                prediction.start_ms,
                prediction.end_ms,
                prediction.prediction_index,
            )
        )
        component_items = sorted(
            items_by_component[union_find.find(ordered_roots[0])],
            key=lambda item: (
                item.start_ms, item.end_ms, item.item_id),
        )
        components.append(EvaluationComponent(
            component_id=ordered_roots[0],
            film_id=film_id,
            root_ids=ordered_roots,
            predictions=predictions,
            items=component_items,
        ))

    if seen_predictions != set(selected_predictions):
        missing = sorted(set(selected_predictions) - seen_predictions)
        raise SampleScoreError(
            f"Selected predictions were not assigned to components: {missing}")
    return sorted(
        components,
        key=lambda component: (
            component.film_id,
            min(roots[root_id].start_ms for root_id in component.root_ids),
            component.component_id,
        ),
    )


def _truth_segment(item: ResolvedItem) -> dict[str, int]:
    return {"start_ms": item.start_ms, "end_ms": item.end_ms}


def _prediction_segment(prediction: PredictionRef) -> dict[str, int]:
    return {"start_ms": prediction.start_ms, "end_ms": prediction.end_ms}


def _score_component_at_threshold(
    component: EvaluationComponent,
    *,
    iou_threshold: float,
    relationship_threshold: float,
) -> dict[str, Any]:
    truth_items = component.truth_items
    predictions = component.predictions
    truth_segments = [_truth_segment(item) for item in truth_items]
    prediction_segments = [
        _prediction_segment(prediction) for prediction in predictions]
    raw = score_segments(
        truth_segments,
        prediction_segments,
        iou_threshold=iou_threshold,
        relationship_threshold=relationship_threshold,
    )
    matches = []
    for match in raw["matches"]:
        truth = truth_items[match["truth_index"]]
        prediction = predictions[match["prediction_index"]]
        start_error = abs(truth.start_ms - prediction.start_ms)
        end_error = abs(truth.end_ms - prediction.end_ms)
        matches.append({
            "truth_item_id": truth.item_id,
            "prediction": {
                "film_id": prediction.film_id,
                "prediction_index": prediction.prediction_index,
                "root_id": prediction.root_id,
            },
            "iou": match["iou"],
            "start_error_ms": start_error,
            "end_error_ms": end_error,
            "max_boundary_error_ms": max(start_error, end_error),
        })
    false_positive_refs = [
        {
            "film_id": predictions[index].film_id,
            "prediction_index": predictions[index].prediction_index,
            "root_id": predictions[index].root_id,
        }
        for index in raw["unmatched_prediction_indexes"]
    ]
    false_negative_refs = [
        {
            "film_id": truth_items[index].film_id,
            "truth_item_id": truth_items[index].item_id,
            "root_ids": sorted(truth_items[index].root_ids),
        }
        for index in raw["unmatched_truth_indexes"]
    ]
    detector_merges = [
        {
            "prediction": {
                "film_id": predictions[row["prediction_index"]].film_id,
                "prediction_index": predictions[
                    row["prediction_index"]].prediction_index,
                "root_id": predictions[row["prediction_index"]].root_id,
            },
            "truth_item_ids": [
                truth_items[index].item_id
                for index in row["truth_indexes"]
            ],
        }
        for row in raw["merge_candidates"]
    ]
    detector_splits = [
        {
            "truth_item_id": truth_items[row["truth_index"]].item_id,
            "predictions": [
                {
                    "film_id": predictions[index].film_id,
                    "prediction_index": predictions[index].prediction_index,
                    "root_id": predictions[index].root_id,
                }
                for index in row["prediction_indexes"]
            ],
        }
        for row in raw["split_candidates"]
    ]
    return {
        key: raw[key]
        for key in (
            "truth_count",
            "prediction_count",
            "count_error",
            "matched_count",
            "false_positive_count",
            "false_negative_count",
            "precision",
            "recall",
            "f1",
            "mean_iou",
            "median_iou",
            "median_start_error_ms",
            "p90_start_error_ms",
            "median_end_error_ms",
            "p90_end_error_ms",
            "merge_count",
            "split_count",
            "iou_threshold",
            "relationship_threshold",
        )
    } | {
        "matches": matches,
        "false_positive_refs": false_positive_refs,
        "false_negative_refs": false_negative_refs,
        "detector_merges": detector_merges,
        "detector_splits": detector_splits,
    }


def _aggregate_component_scores(
    component_rows: list[dict[str, Any]],
    key: str,
    *,
    iou_threshold: float,
    relationship_threshold: float,
) -> dict[str, Any]:
    scores = [row[key] for row in component_rows]
    truth_count = sum(score["truth_count"] for score in scores)
    prediction_count = sum(score["prediction_count"] for score in scores)
    matched_count = sum(score["matched_count"] for score in scores)
    precision = matched_count / prediction_count if prediction_count else 0.0
    recall = matched_count / truth_count if truth_count else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )
    matches = [
        match for score in scores for match in score["matches"]]
    ious = [match["iou"] for match in matches]
    start_errors = [match["start_error_ms"] for match in matches]
    end_errors = [match["end_error_ms"] for match in matches]
    max_errors = [match["max_boundary_error_ms"] for match in matches]
    detector_merges = [
        row for score in scores for row in score["detector_merges"]]
    detector_splits = [
        row for score in scores for row in score["detector_splits"]]
    return {
        "truth_count": truth_count,
        "prediction_count": prediction_count,
        "count_error": prediction_count - truth_count,
        "matched_count": matched_count,
        "false_positive_count": prediction_count - matched_count,
        "false_negative_count": truth_count - matched_count,
        "precision": precision,
        "recall": recall,
        "coverage": recall,
        "f1": f1,
        "mean_iou": statistics.fmean(ious) if ious else None,
        "median_iou": statistics.median(ious) if ious else None,
        "median_start_error_ms": (
            statistics.median(start_errors) if start_errors else None),
        "p90_start_error_ms": percentile(start_errors, 0.90),
        "median_end_error_ms": (
            statistics.median(end_errors) if end_errors else None),
        "p90_end_error_ms": percentile(end_errors, 0.90),
        "median_max_boundary_error_ms": (
            statistics.median(max_errors) if max_errors else None),
        "p90_max_boundary_error_ms": percentile(max_errors, 0.90),
        "boundary_error_scope": "iou_qualified_matches_only",
        "boundary_error_denominator": len(matches),
        "merge_count": len(detector_merges),
        "merge_rate": (
            len(detector_merges) / prediction_count
            if prediction_count else 0.0),
        "split_count": len(detector_splits),
        "split_rate": (
            len(detector_splits) / truth_count if truth_count else 0.0),
        "iou_threshold": iou_threshold,
        "relationship_threshold": relationship_threshold,
        "matches": matches,
        "false_positive_refs": [
            row
            for score in scores
            for row in score["false_positive_refs"]
        ],
        "false_negative_refs": [
            row
            for score in scores
            for row in score["false_negative_refs"]
        ],
        "detector_merges": detector_merges,
        "detector_splits": detector_splits,
    }


def _all_one_to_one_boundary_metrics(
    component_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Measure every comparable 1:1 component without an IoU filter."""

    comparisons: list[dict[str, Any]] = []
    skipped_shapes: Counter[str] = Counter()
    for row in component_rows:
        prediction_count = len(row["prediction_refs"])
        truth_count = len(row["truth_refs"])
        if prediction_count != 1 or truth_count != 1:
            skipped_shapes[
                f"{prediction_count}_sampled_predictions_"
                f"{truth_count}_verified_truths"
            ] += 1
            continue
        prediction = row["prediction_refs"][0]
        truth = row["truth_refs"][0]
        start_error = abs(
            int(truth["start_ms"]) - int(prediction["start_ms"]))
        end_error = abs(
            int(truth["end_ms"]) - int(prediction["end_ms"]))
        comparisons.append({
            "component_id": row["component_id"],
            "film_id": row["film_id"],
            "prediction": {
                "film_id": prediction["film_id"],
                "prediction_index": prediction["prediction_index"],
                "root_id": prediction["root_id"],
            },
            "truth_item_id": truth["truth_item_id"],
            "iou": segment_iou(prediction, truth),
            "start_error_ms": start_error,
            "end_error_ms": end_error,
            "max_boundary_error_ms": max(start_error, end_error),
        })

    start_errors = [row["start_error_ms"] for row in comparisons]
    end_errors = [row["end_error_ms"] for row in comparisons]
    max_errors = [row["max_boundary_error_ms"] for row in comparisons]
    p90_start = percentile(start_errors, 0.90)
    p90_end = percentile(end_errors, 0.90)
    p90_max = percentile(max_errors, 0.90)
    eligible_count = len(comparisons)
    skipped_count = len(component_rows) - eligible_count
    return {
        "scope": (
            "all lineage components containing exactly one sampled detector "
            "prediction and exactly one verified truth, regardless of IoU"
        ),
        "denominator": eligible_count,
        "eligible_one_to_one_component_count": eligible_count,
        "total_lineage_component_count": len(component_rows),
        "skipped_non_one_to_one_component_count": skipped_count,
        "skipped_by_component_shape": dict(sorted(skipped_shapes.items())),
        "skipped_policy": (
            "Components without exactly one sampled detector prediction and "
            "one verified truth are excluded; no artificial boundary error "
            "is assigned to missed-play, excluded, split, or merge shapes."
        ),
        "zero_max_boundary_error_count": sum(
            error == 0 for error in max_errors),
        "median_start_error_ms": (
            statistics.median(start_errors) if start_errors else None),
        "p90_start_error_ms": (
            round(p90_start, 6) if p90_start is not None else None),
        "median_end_error_ms": (
            statistics.median(end_errors) if end_errors else None),
        "p90_end_error_ms": (
            round(p90_end, 6) if p90_end is not None else None),
        "median_max_boundary_error_ms": (
            statistics.median(max_errors) if max_errors else None),
        "p90_max_boundary_error_ms": (
            round(p90_max, 6) if p90_max is not None else None),
        "comparisons": comparisons,
    }


def _root_dispositions(
    roots: dict[str, LockedRoot],
    components: list[EvaluationComponent],
) -> tuple[dict[str, str], dict[str, str]]:
    dispositions: dict[str, str] = {}
    unclassified_outcomes: dict[str, str] = {}
    for component in components:
        truth_count = len(component.truth_items)
        prediction_count = len(component.predictions)
        for root_id in component.root_ids:
            root = roots[root_id]
            root_items = [
                item for item in component.items if root_id in item.root_ids]
            split = len(root_items) > 1
            merged = len(component.root_ids) > 1
            if split and merged:
                disposition = "complex"
            elif split:
                disposition = "structural_split"
            elif merged:
                disposition = "structural_merge"
            elif not any(item.status == "verified" for item in root_items):
                disposition = "excluded"
            else:
                item = next(
                    item for item in root_items if item.status == "verified")
                unchanged = (
                    item.item_id == root_id
                    and item.start_ms == root.start_ms
                    and item.end_ms == root.end_ms
                    and not item.edit_history
                    and item.decision == "accepted"
                )
                disposition = (
                    "unchanged" if unchanged else "boundary_revised")
            dispositions[root_id] = disposition

            if root.candidate_kind != "unclassified":
                continue
            if truth_count == 0 and prediction_count == 0:
                outcome = "reviewed_no_play"
            elif truth_count > 0 and prediction_count == 0:
                outcome = "standalone_missed_play"
            elif truth_count > 0 and prediction_count > 0 and merged:
                outcome = "boundary_continuation"
            else:
                outcome = "complex"
            unclassified_outcomes[root_id] = outcome
    return dispositions, unclassified_outcomes


def _counter_dict(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def score_sampled_holdout(
    locked_root_payloads: Iterable[dict[str, Any]],
    final_item_payloads: Iterable[dict[str, Any]],
    predictions_by_film: dict[str, dict[str, Any]],
    *,
    expected_root_count: int | None = None,
    primary_iou_threshold: float = PRIMARY_IOU_THRESHOLD,
    diagnostic_iou_threshold: float = DIAGNOSTIC_IOU_THRESHOLD,
    relationship_threshold: float = RELATIONSHIP_THRESHOLD,
) -> dict[str, Any]:
    """Score a complete sampled holdout without using unsampled predictions."""

    roots = load_locked_roots(
        locked_root_payloads, expected_root_count=expected_root_count)
    selected_predictions = validate_frozen_predictions(
        roots, predictions_by_film)
    items = resolve_final_items(final_item_payloads, roots)
    components = build_lineage_components(
        roots, items, selected_predictions)

    component_rows = []
    for component in components:
        primary = _score_component_at_threshold(
            component,
            iou_threshold=primary_iou_threshold,
            relationship_threshold=relationship_threshold,
        )
        diagnostic = _score_component_at_threshold(
            component,
            iou_threshold=diagnostic_iou_threshold,
            relationship_threshold=relationship_threshold,
        )
        component_rows.append({
            "component_id": component.component_id,
            "film_id": component.film_id,
            "root_ids": list(component.root_ids),
            "prediction_refs": [
                {
                    "film_id": prediction.film_id,
                    "prediction_index": prediction.prediction_index,
                    "root_id": prediction.root_id,
                    "start_ms": prediction.start_ms,
                    "end_ms": prediction.end_ms,
                }
                for prediction in component.predictions
            ],
            "truth_refs": [
                {
                    "truth_item_id": item.item_id,
                    "root_ids": sorted(item.root_ids),
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                }
                for item in component.truth_items
            ],
            "excluded_refs": [
                {
                    "item_id": item.item_id,
                    "root_ids": sorted(item.root_ids),
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                }
                for item in component.excluded_items
            ],
            "primary": primary,
            "diagnostic": diagnostic,
        })

    primary = _aggregate_component_scores(
        component_rows,
        "primary",
        iou_threshold=primary_iou_threshold,
        relationship_threshold=relationship_threshold,
    )
    diagnostic = _aggregate_component_scores(
        component_rows,
        "diagnostic",
        iou_threshold=diagnostic_iou_threshold,
        relationship_threshold=relationship_threshold,
    )
    all_one_to_one_boundary = _all_one_to_one_boundary_metrics(
        component_rows)
    dispositions, unclassified_outcomes = _root_dispositions(
        roots, components)
    matched_prediction_keys = {
        (
            match["prediction"]["film_id"],
            match["prediction"]["prediction_index"],
        )
        for match in primary["matches"]
    }

    by_film: dict[str, dict[str, Any]] = {}
    for film_id in sorted({root.film_id for root in roots.values()}):
        rows = [
            row for row in component_rows if row["film_id"] == film_id]
        film_root_ids = [
            root_id for root_id, root in roots.items()
            if root.film_id == film_id
        ]
        by_film[film_id] = {
            "root_count": len(film_root_ids),
            "root_dispositions": _counter_dict(
                dispositions[root_id] for root_id in film_root_ids),
            "primary": _aggregate_component_scores(
                rows,
                "primary",
                iou_threshold=primary_iou_threshold,
                relationship_threshold=relationship_threshold,
            ),
            "diagnostic": _aggregate_component_scores(
                rows,
                "diagnostic",
                iou_threshold=diagnostic_iou_threshold,
                relationship_threshold=relationship_threshold,
            ),
        }

    by_stratum: dict[str, dict[str, Any]] = {}
    for stratum in sorted({
        root.candidate_stratum for root in roots.values()
    }):
        stratum_roots = [
            root for root in roots.values()
            if root.candidate_stratum == stratum
        ]
        predictions = [
            (root.film_id, index)
            for root in stratum_roots
            for index in root.prediction_indices
        ]
        matched = sum(
            key in matched_prediction_keys for key in predictions)
        stratum_rows = []
        mixed_component_count = 0
        for row in component_rows:
            component_strata = {
                roots[root_id].candidate_stratum
                for root_id in row["root_ids"]
            }
            if component_strata == {stratum}:
                stratum_rows.append(row)
            elif stratum in component_strata:
                mixed_component_count += 1
        by_stratum[stratum] = {
            "root_count": len(stratum_roots),
            "selected_prediction_count": len(predictions),
            "matched_prediction_count": matched,
            "candidate_match_rate": (
                matched / len(predictions) if predictions else None),
            "root_dispositions": _counter_dict(
                dispositions[root.root_id] for root in stratum_roots),
            "mixed_component_count": mixed_component_count,
            "primary": _aggregate_component_scores(
                stratum_rows,
                "primary",
                iou_threshold=primary_iou_threshold,
                relationship_threshold=relationship_threshold,
            ),
            "diagnostic": _aggregate_component_scores(
                stratum_rows,
                "diagnostic",
                iou_threshold=diagnostic_iou_threshold,
                relationship_threshold=relationship_threshold,
            ),
        }

    return {
        "schema_version": SAMPLE_SCORE_SCHEMA_VERSION,
        "scoring_status": "complete",
        "methodology": {
            "evaluation_unit": "immutable sampled queue root",
            "matching_scope": "lineage-connected components only",
            "primary_iou_threshold": primary_iou_threshold,
            "diagnostic_iou_threshold": diagnostic_iou_threshold,
            "relationship_threshold": relationship_threshold,
            "limitations": [
                (
                    "Results describe the frozen stratified sample, not "
                    "unsampled full-film predictions."
                ),
                (
                    "Boundary errors inside primary and diagnostic match "
                    "metrics include only IoU-qualified matches and are "
                    "therefore survivor-biased."
                ),
                (
                    "The all-one-to-one boundary metric includes every "
                    "lineage component with exactly one sampled detector "
                    "prediction and one verified truth, regardless of IoU; "
                    "non-1:1 shapes are reported as skipped."
                ),
            ],
        },
        "integrity": {
            "passed": True,
            "locked_root_count": len(roots),
            "resolved_root_count": len({
                root_id
                for item in items
                for root_id in item.root_ids
            }),
            "selected_prediction_count": len(selected_predictions),
            "final_item_count": len(items),
            "truth_item_count": sum(
                item.status == "verified" for item in items),
            "excluded_item_count": sum(
                item.status == "excluded" for item in items),
            "component_count": len(components),
        },
        "population": {
            "film_count": len({root.film_id for root in roots.values()}),
            "root_count": len(roots),
            "root_kinds": _counter_dict(
                root.candidate_kind for root in roots.values()),
            "root_strata": _counter_dict(
                root.candidate_stratum for root in roots.values()),
            "selected_prediction_count": len(selected_predictions),
            "truth_item_count": primary["truth_count"],
            "root_dispositions": _counter_dict(dispositions.values()),
            "unclassified_review": _counter_dict(
                unclassified_outcomes.values()),
        },
        "aggregate": {
            "primary": primary,
            "diagnostic": diagnostic,
            "all_one_to_one_boundary": all_one_to_one_boundary,
            "root_dispositions": _counter_dict(dispositions.values()),
            "unclassified_review": _counter_dict(
                unclassified_outcomes.values()),
        },
        "by_film": by_film,
        "by_stratum": by_stratum,
        "components": component_rows,
    }
