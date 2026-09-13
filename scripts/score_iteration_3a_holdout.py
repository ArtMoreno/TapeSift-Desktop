"""Score the frozen Iteration 3A sampled holdout.

This is the artifact-aware companion to
``tapesift.research.segmentation_sample_score``.  It validates the complete
lock chain before scoring and never overwrites a report.  The finalization
lock itself must be pinned on the command line because it is the last artifact
in the provenance chain.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tapesift.research.segmentation_benchmark import (  # noqa: E402
    intersection_ms,
)
from tapesift.research.segmentation_sample_score import (  # noqa: E402
    PRIMARY_IOU_THRESHOLD,
    RELATIONSHIP_THRESHOLD,
    score_sampled_holdout,
)


BENCHMARK_ID = "segmentation-iteration-3a-independent-holdout-v1"
BASE_DIR = ROOT / "research" / "segmentation_benchmark"
VERIFICATION_DIR = BASE_DIR / "verification"
REPORT_DIR = BASE_DIR / "reports" / "iteration_3a_holdout_v1"

SELECTION = (
    VERIFICATION_DIR / "iteration_3a_independent_holdout_v1.selection.json")
PREDICTION_LOCK = REPORT_DIR / "prediction_lock.json"
QUEUE_LOCK = REPORT_DIR / "queue_lock.json"
FINALIZATION_LOCK = REPORT_DIR / "finalization_lock_v2.json"
FINAL_STATE = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.final_v2.state.json")
FINAL_TRUTH = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.final_v2.verified.jsonl")
OUTPUT_JSON = REPORT_DIR / "sampled_results_v2.json"
OUTPUT_MARKDOWN = REPORT_DIR / "results_v2.md"

SELECTION_SHA256 = (
    "fa30f6f9868baa04c20b1cf2ee852fb2690ad48c9ecc462254341d7801e7df28")
PREDICTION_LOCK_SHA256 = (
    "439767627dee5bc9f5fbfbe202953ec9c2d15ca19b60acc39e3ed72b4dfbd7aa")
QUEUE_LOCK_SHA256 = (
    "328e03459d176855b297bfcd98c071a7cc12b2a2b40bbbc7b54b174b052d22d7")

REQUIRED_FINALIZATION_INPUTS = (
    (
        VERIFICATION_DIR
        / "iteration_3a_independent_holdout_v1.completed.raw.state.json",
        "e5ef2ed55a0eaace2d62b98b228531a34440bd296076c7d7416269ffc4dca2ef",
    ),
    (
        VERIFICATION_DIR
        / "iteration_3a_independent_holdout_v1.completed.raw.verified.jsonl",
        "d1e739787c0f2f8246e833973667f859e7b0def73ca6702b5ec36c9e609cb5fb",
    ),
    (
        VERIFICATION_DIR
        / "iteration_3a_independent_holdout_v1.queue.locked.json",
        "ce057e42b063e7d81f866ef3406f392db92f644e5febafab7ba87013e515538d",
    ),
    (
        VERIFICATION_DIR
        / "iteration_3a_independent_holdout_v1.cleaned.state.json",
        "ad3c6d5a12ec61c8a01f206ee5d5e99c95fb69cc56894570e1cc507401363795",
    ),
    (
        VERIFICATION_DIR
        / "iteration_3a_independent_holdout_v1.cleaned.verified.jsonl",
        "c27670317e8010893a140c518cb2626eaefd8815718d3f31f69636fcd2133259",
    ),
    (
        REPORT_DIR / "boundary_qa_lock.json",
        "98f0493bfdf5419236a6c231ee44bcfee67f2e0316e165f7e7895666bd6b88d6",
    ),
    (
        VERIFICATION_DIR
        / "iteration_3a_boundary_qa_v1.completed.raw.state.json",
        "ff6e6ba1fc6ec1164be8d9b860bfd42e63b585e98f7c67152e11cbaedb6a0c2c",
    ),
    (
        VERIFICATION_DIR
        / "iteration_3a_boundary_qa_v1.completed.raw.verified.jsonl",
        "bdd08e1130303f21cd9c142056148ddbea85b75bbc8288a586724ef9618f32f3",
    ),
    (
        VERIFICATION_DIR / "iteration_3a_boundary_qa_v1.queue.locked.json",
        "dbec58877235d16933a286747ab211a0026cdd8d45b1d05060d3b4e9172f33dd",
    ),
)

SEVEN_CORRECTION_IDS = (
    "south_carolina_o_vs_vanderbilt_d_holdout_3a_v1:unclassified:0131",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0023",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0032",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0035",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0042",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0062",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0065",
)
OWNER_V2_CORRECTION_IDS = SEVEN_CORRECTION_IDS[:5]
SEVEN_COMPLETION_LOCK = (
    REPORT_DIR / "final_corrections_seven_v1.completion.lock.json")
SEVEN_COMPLETION_SHA256 = (
    "b0b445a1808c1366047717295258fa27a27ec296526eb522570b2960bf187568")
OWNER_V2_COMPLETION_LOCK = (
    REPORT_DIR / "reviewer_verified_final_five_v2.completion.lock.json")
OWNER_V2_COMPLETION_SHA256 = (
    "bfe8a78b1259ad4aa11616a6d6ca3e32ee7a5d22aa606a82ad0801889af5a72a")

REQUIRED_FINALIZATION_VALIDATION_FLAGS = frozenset({
    "all_inputs_hash_locked",
    "all_items_terminal",
    "bounds_valid",
    "no_overlaps",
    "off_review_allowlist_changes_limited_to_angle_starts",
    "semantic_diff_exactly_allowlisted",
    "state_truth_mirror",
})


class FrozenScoreError(ValueError):
    """Raised when an artifact cannot be proven to belong to the holdout."""


@dataclass(frozen=True)
class LockedArtifact:
    path: Path
    sha256: str


@dataclass(frozen=True)
class ExpectedCorrectionLayer:
    layer_id: str
    queue_id: str
    item_ids: tuple[str, ...]
    completion_lock: LockedArtifact


@dataclass(frozen=True)
class FrozenScoreSpec:
    root: Path
    benchmark_id: str
    selection: LockedArtifact
    prediction_lock: LockedArtifact
    queue_lock: LockedArtifact
    finalization_lock: LockedArtifact
    output_json: Path
    output_markdown: Path
    required_finalization_inputs: tuple[LockedArtifact, ...] = ()
    required_correction_layers: tuple[ExpectedCorrectionLayer, ...] = ()
    expected_final_state: Path | None = None
    expected_final_truth: Path | None = None
    required_validation_flags: frozenset[str] = (
        REQUIRED_FINALIZATION_VALIDATION_FLAGS)
    expected_root_count: int = 125
    expected_film_count: int = 5


@dataclass(frozen=True)
class FrozenScoreBundle:
    report: dict[str, Any]
    json_text: str
    markdown_text: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_text(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FrozenScoreError(f"Cannot read JSON artifact {path}: {exc}") \
            from exc
    if not isinstance(payload, dict):
        raise FrozenScoreError(f"JSON artifact is not an object: {path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise FrozenScoreError(
                    f"{path}:{number} is not a JSON object")
            records.append(record)
    except (OSError, json.JSONDecodeError) as exc:
        raise FrozenScoreError(f"Cannot read JSONL artifact {path}: {exc}") \
            from exc
    return records


def _require_hash(artifact: LockedArtifact, label: str) -> None:
    if not artifact.path.is_file():
        raise FileNotFoundError(f"{label} is missing: {artifact.path}")
    actual = sha256_file(artifact.path)
    if actual != artifact.sha256.lower():
        raise FrozenScoreError(
            f"{label} hash mismatch for {artifact.path}: "
            f"expected {artifact.sha256.lower()}, found {actual}")


def _resolve_inside(root: Path, value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise FrozenScoreError(
            f"{label} escapes the repository root: {path}") from exc
    return path


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise FrozenScoreError(
            f"Public provenance path is outside the repository: {path}") \
            from exc


def _artifact_from_reference(
    root: Path,
    reference: dict[str, Any],
    label: str,
) -> LockedArtifact:
    if not isinstance(reference, dict):
        raise FrozenScoreError(f"{label} is not an artifact reference")
    value = reference.get("file")
    digest = str(reference.get("sha256") or "").lower()
    if not isinstance(value, str) or not value:
        raise FrozenScoreError(f"{label} has no file")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise FrozenScoreError(f"{label} has invalid SHA-256")
    return LockedArtifact(_resolve_inside(root, value, label), digest)


def _require_reference(
    root: Path,
    reference: dict[str, Any],
    label: str,
) -> LockedArtifact:
    artifact = _artifact_from_reference(root, reference, label)
    _require_hash(artifact, label)
    return artifact


def _require_expected_reference(
    root: Path,
    reference: dict[str, Any],
    expected: LockedArtifact,
    label: str,
) -> LockedArtifact:
    artifact = _require_reference(root, reference, label)
    if artifact.path != expected.path.resolve() \
            or artifact.sha256 != expected.sha256.lower():
        raise FrozenScoreError(
            f"{label} does not match the required path and hash")
    return artifact


def _verification_record(
    item: dict[str, Any],
    queue_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "queue_id": queue_id,
        "item_id": item["item_id"],
        "film_id": item["film_id"],
        "source_file": item["source_file"],
        "start_ms": item["start_ms"],
        "end_ms": item["end_ms"],
        "angle_starts_ms": item["angle_starts_ms"],
        "verification_status": item["status"],
        "decision": item["decision"],
        "edit_history": item["edit_history"],
        "verified_at": item["verified_at"],
        "detector": {
            "candidate_kind": item["candidate_kind"],
            "candidate_stratum": item.get("candidate_stratum", ""),
            "candidate_index": item["candidate_index"],
            "prediction_indices": item["prediction_indices"],
            "original_start_ms": item["original_start_ms"],
            "original_end_ms": item["original_end_ms"],
            "needs_review": item["detector_needs_review"],
            "reason": item["detector_reason"],
            "signal": item["detector_signal"],
            "angle_count": item["angle_count"],
        },
    }


def _require_state_truth_mirror(
    state: dict[str, Any],
    truth: list[dict[str, Any]],
    benchmark_id: str,
) -> list[dict[str, Any]]:
    if state.get("schema_version") != "1.0":
        raise FrozenScoreError("Final state schema is not supported")
    if state.get("queue_id") != benchmark_id:
        raise FrozenScoreError("Final state queue id does not match holdout")
    items = state.get("items")
    if not isinstance(items, list) or not all(
        isinstance(item, dict) for item in items
    ):
        raise FrozenScoreError("Final state has no valid items list")
    ids = [str(item.get("item_id") or "") for item in items]
    if not all(ids) or len(ids) != len(set(ids)):
        raise FrozenScoreError("Final state item IDs are missing or duplicated")
    if any(item.get("status") not in {"verified", "excluded"}
           for item in items):
        raise FrozenScoreError("Final state contains a pending item")
    try:
        expected = [
            _verification_record(item, benchmark_id) for item in items]
    except KeyError as exc:
        raise FrozenScoreError(
            f"Final state item is missing required field {exc}") from exc
    if truth != expected:
        raise FrozenScoreError(
            "Final state and final truth are not exact mirrors")
    return items


def _validate_selection(
    spec: FrozenScoreSpec,
    selection: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if selection.get("schema_version") != "1.0" \
            or selection.get("status") != "locked_before_detection":
        raise FrozenScoreError("Selection is not locked before detection")
    if selection.get("queue_id") != spec.benchmark_id:
        raise FrozenScoreError("Selection queue id does not match")
    policy = selection.get("selection_policy")
    if not isinstance(policy, dict):
        raise FrozenScoreError("Selection has no policy")
    if int(policy.get("total_review_samples") or 0) != \
            spec.expected_root_count:
        raise FrozenScoreError("Selection root count changed")
    films = selection.get("films")
    if not isinstance(films, list) or len(films) != spec.expected_film_count:
        raise FrozenScoreError("Selection film count changed")
    film_ids = [str(film.get("film_id") or "") for film in films]
    if not all(film_ids) or len(film_ids) != len(set(film_ids)):
        raise FrozenScoreError("Selection film ids are missing or duplicated")

    # Source hashes are part of the pre-detection lock. Validate them without
    # ever copying machine-specific paths into the public report.
    for film in films:
        source = Path(str(film.get("source_file") or "")).resolve()
        digest = str(film.get("source_sha256") or "").lower()
        if not source.is_file():
            raise FileNotFoundError(
                f"Locked source is missing for {film['film_id']}: {source}")
        if int(film.get("source_bytes") or -1) != source.stat().st_size:
            raise FrozenScoreError(
                f"Locked source size changed for {film['film_id']}")
        if sha256_file(source) != digest:
            raise FrozenScoreError(
                f"Locked source hash changed for {film['film_id']}")
    review = selection.get("predeclared_review")
    if not isinstance(review, dict) or not isinstance(
        review.get("gates"), dict
    ):
        raise FrozenScoreError("Selection has no predeclared gates")
    return films, review["gates"]


def _validate_prediction_lock(
    spec: FrozenScoreSpec,
    lock: dict[str, Any],
    selection: dict[str, Any],
    film_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if lock.get("schema_version") != "1.0" \
            or lock.get("status") != \
            "predictions_frozen_before_human_review":
        raise FrozenScoreError("Prediction lock is not frozen")
    if lock.get("queue_id") != spec.benchmark_id:
        raise FrozenScoreError("Prediction lock queue id does not match")
    selection_ref = lock.get("selection")
    if not isinstance(selection_ref, dict):
        raise FrozenScoreError("Prediction lock has no selection reference")
    if str(selection_ref.get("sha256") or "").lower() != \
            spec.selection.sha256.lower():
        raise FrozenScoreError("Prediction lock selection hash changed")
    selection_path = _resolve_inside(
        spec.root, str(selection_ref.get("file") or ""),
        "prediction-lock selection")
    if selection_path != spec.selection.path.resolve():
        raise FrozenScoreError("Prediction lock points to another selection")

    detector = lock.get("detector")
    selected_detector = selection.get("detector_lock")
    if not isinstance(detector, dict) or not isinstance(
        selected_detector, dict
    ):
        raise FrozenScoreError("Detector provenance is incomplete")
    for key in ("source_sha256", "source_commit", "settings"):
        if detector.get(key) != selected_detector.get(key):
            raise FrozenScoreError(
                f"Detector lock disagrees with selection on {key}")
    detector_source = _resolve_inside(
        spec.root, str(detector.get("source_file") or ""),
        "detector source")
    expected_detector_hash = str(
        detector.get("source_sha256") or ""
    ).lower()
    if sha256_file(detector_source) != expected_detector_hash:
        raise FrozenScoreError("Frozen detector source hash changed")

    rows = lock.get("predictions")
    if not isinstance(rows, list) or len(rows) != len(film_ids):
        raise FrozenScoreError("Prediction lock film count changed")
    predictions: dict[str, dict[str, Any]] = {}
    public_rows = []
    for row in rows:
        film_id = str(row.get("film_id") or "")
        if film_id not in film_ids or film_id in predictions:
            raise FrozenScoreError(
                f"Unexpected or duplicate prediction film {film_id!r}")
        artifact = _require_reference(
            spec.root, row, f"prediction {film_id}")
        payload = _read_json(artifact.path)
        if payload.get("film_id") != film_id:
            raise FrozenScoreError(
                f"Prediction payload film mismatch for {film_id}")
        if int(payload.get("duration_ms") or 0) != int(
            row.get("duration_ms") or -1
        ):
            raise FrozenScoreError(
                f"Prediction duration mismatch for {film_id}")
        if len(payload.get("plays", [])) != int(
            row.get("play_count") or 0
        ) or len(payload.get("unclassified", [])) != int(
            row.get("unclassified_count") or 0
        ):
            raise FrozenScoreError(
                f"Prediction counts changed for {film_id}")
        predictions[film_id] = payload
        public_rows.append({
            "film_id": film_id,
            "file": _relative(spec.root, artifact.path),
            "sha256": artifact.sha256,
            "duration_ms": int(row["duration_ms"]),
            "play_count": int(row["play_count"]),
            "unclassified_count": int(row["unclassified_count"]),
        })
    if set(predictions) != film_ids:
        raise FrozenScoreError("Prediction lock does not cover every film")
    if not bool(lock.get("structural_checks", {}).get("passed")):
        raise FrozenScoreError("Frozen prediction structural checks did not pass")
    return predictions, sorted(public_rows, key=lambda row: row["film_id"])


def _validate_prediction_structure(
    predictions: dict[str, dict[str, Any]],
) -> dict[str, int]:
    overlaps = 0
    out_of_bounds = 0
    for payload in predictions.values():
        duration = int(payload.get("duration_ms") or 0)
        plays = payload.get("plays", [])
        unclassified = payload.get("unclassified", [])
        for collection in (plays, unclassified):
            ordered = sorted(
                collection,
                key=lambda segment: (
                    int(segment.get("start_ms", -1)),
                    int(segment.get("end_ms", -1)),
                ),
            )
            for segment in ordered:
                start = int(segment.get("start_ms", -1))
                end = int(segment.get("end_ms", -1))
                if start < 0 or end <= start or end > duration:
                    out_of_bounds += 1
            for left, right in zip(ordered, ordered[1:]):
                if int(left["end_ms"]) > int(right["start_ms"]):
                    overlaps += 1
        overlaps += sum(
            intersection_ms(play, gap) > 0
            for play in plays
            for gap in unclassified
        )
    return {
        "overlap_count": overlaps,
        "out_of_bounds_count": out_of_bounds,
    }


def _validate_queue_lock(
    spec: FrozenScoreSpec,
    lock: dict[str, Any],
    prediction_lock: LockedArtifact,
    film_ids: set[str],
) -> tuple[list[dict[str, Any]], LockedArtifact]:
    if lock.get("schema_version") != "1.0" \
            or lock.get("status") != "queue_frozen_before_human_review":
        raise FrozenScoreError("Queue lock is not frozen before review")
    if lock.get("queue_id") != spec.benchmark_id:
        raise FrozenScoreError("Queue lock id does not match")
    prediction_ref = lock.get("prediction_lock")
    if not isinstance(prediction_ref, dict):
        raise FrozenScoreError("Queue lock has no prediction lock reference")
    pinned_prediction = _require_reference(
        spec.root, prediction_ref, "queue prediction lock")
    if pinned_prediction.path != prediction_lock.path.resolve() \
            or pinned_prediction.sha256 != prediction_lock.sha256.lower():
        raise FrozenScoreError("Queue lock points to another prediction lock")
    queue = lock.get("queue")
    if not isinstance(queue, dict):
        raise FrozenScoreError("Queue lock has no queue artifact")
    queue_artifact = LockedArtifact(
        _resolve_inside(
            spec.root, str(queue.get("locked_selection_file") or ""),
            "locked queue"),
        str(queue.get("locked_selection_sha256") or "").lower(),
    )
    _require_hash(queue_artifact, "locked queue")
    payload = _read_json(queue_artifact.path)
    if payload.get("queue_id") != spec.benchmark_id \
            or payload.get("schema_version") != "1.0":
        raise FrozenScoreError("Locked queue identity changed")
    roots = payload.get("items")
    if not isinstance(roots, list) or len(roots) != spec.expected_root_count:
        raise FrozenScoreError("Locked queue root count changed")
    if int(queue.get("initial_review_units") or 0) != \
            spec.expected_root_count:
        raise FrozenScoreError("Queue lock review-unit count changed")
    counts_by_film = Counter(str(root.get("film_id") or "") for root in roots)
    if set(counts_by_film) != film_ids or any(
        count != int(queue.get("items_per_film") or -1)
        for count in counts_by_film.values()
    ):
        raise FrozenScoreError("Locked queue film distribution changed")
    if any(
        root.get("status") != "pending"
        or root.get("decision") not in {"", None}
        or root.get("edit_history") not in ([], None)
        for root in roots
    ):
        raise FrozenScoreError("Locked queue already contains human decisions")

    actual = lock.get("actual_counts", {}).get("all_films")
    expected = Counter(
        str(root.get("candidate_stratum") or "legacy") for root in roots)
    if not isinstance(actual, dict):
        raise FrozenScoreError("Queue stratum counts changed")
    actual_counts = {
        str(key): int(value) for key, value in actual.items()
    }
    all_strata = set(actual_counts) | set(expected)
    if any(
        actual_counts.get(stratum, 0) != expected.get(stratum, 0)
        for stratum in all_strata
    ):
        raise FrozenScoreError("Queue stratum counts changed")
    return roots, queue_artifact


def _walk_artifact_references(
    value: Any,
    trail: str = "finalization lock",
) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if "file" in value and "sha256" in value:
            yield trail, value
            return
        for key, child in value.items():
            yield from _walk_artifact_references(
                child, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_artifact_references(
                child, f"{trail}[{index}]")


def _validate_finalization_inputs(
    spec: FrozenScoreSpec,
    lock: dict[str, Any],
    original_queue: LockedArtifact,
) -> None:
    expected = spec.required_finalization_inputs
    rows = lock.get("inputs")
    if not expected or not isinstance(rows, list) \
            or len(rows) != len(expected):
        raise FrozenScoreError(
            "Finalization base inputs do not match the required contract")
    resolved = [
        _require_expected_reference(
            spec.root,
            row,
            expected_artifact,
            f"finalization input {index}",
        )
        for index, (row, expected_artifact) in enumerate(
            zip(rows, expected, strict=True))
    ]
    queue_matches = [
        artifact for artifact in resolved
        if artifact.path == original_queue.path.resolve()
    ]
    if len(queue_matches) != 1 \
            or queue_matches[0].sha256 != original_queue.sha256.lower():
        raise FrozenScoreError(
            "Finalization original queue is not the queue_lock artifact")


def _completion_artifact(
    root: Path,
    payload: dict[str, Any],
    prefix: str,
    label: str,
) -> LockedArtifact:
    return _require_reference(
        root,
        {
            "file": payload.get(f"{prefix}_file"),
            "sha256": payload.get(f"{prefix}_sha256"),
        },
        label,
    )


def _validate_correction_layers(
    spec: FrozenScoreSpec,
    lock: dict[str, Any],
) -> None:
    expected_layers = spec.required_correction_layers
    rows = lock.get("correction_layers")
    if not expected_layers or not isinstance(rows, list) \
            or len(rows) != len(expected_layers):
        raise FrozenScoreError(
            "Finalization correction layers do not match the required "
            "ordered contract")
    for index, (row, expected) in enumerate(
        zip(rows, expected_layers, strict=True)
    ):
        label = f"correction layer {index} ({expected.layer_id})"
        if not isinstance(row, dict):
            raise FrozenScoreError(f"{label} is not an object")
        item_ids = row.get("item_ids")
        if row.get("layer_id") != expected.layer_id \
                or row.get("queue_id") != expected.queue_id \
                or not isinstance(item_ids, list) \
                or tuple(item_ids) != expected.item_ids:
            raise FrozenScoreError(
                f"{label} identity or item IDs changed")
        completion_artifact = _require_expected_reference(
            spec.root,
            row.get("completion_lock"),
            expected.completion_lock,
            f"{label} completion lock",
        )
        completion = _read_json(completion_artifact.path)
        completion_ids = completion.get("item_ids")
        if completion.get("schema_version") != "1.0" \
                or completion.get("status") != "complete" \
                or completion.get("layer_id") != expected.layer_id \
                or completion.get("queue_id") != expected.queue_id \
                or not isinstance(completion_ids, list) \
                or tuple(completion_ids) != expected.item_ids:
            raise FrozenScoreError(
                f"{label} completion lock identity changed")

        completion_outputs = {
            "state": _completion_artifact(
                spec.root, completion, "state",
                f"{label} completed state"),
            "truth": _completion_artifact(
                spec.root, completion, "truth",
                f"{label} completed truth"),
            "locked_queue": _completion_artifact(
                spec.root, completion, "locked_queue",
                f"{label} locked queue"),
        }
        for key, expected_artifact in completion_outputs.items():
            reference = row.get(key)
            if not isinstance(reference, dict):
                raise FrozenScoreError(f"{label} has no {key} reference")
            _require_expected_reference(
                spec.root,
                reference,
                expected_artifact,
                f"{label} {key}",
            )

        # The pinned completion-lock hash binds this metadata. Validate every
        # upstream artifact it names as well.
        for trail, reference in _walk_artifact_references(
            completion.get("input_locks", {}),
            f"{label} completion inputs",
        ):
            _require_reference(spec.root, reference, trail)

    expected_required_ids = {
        item_id
        for layer in expected_layers
        for item_id in layer.item_ids
    }
    approved = lock.get("approved_changes")
    required_ids = (
        approved.get("required_correction_ids")
        if isinstance(approved, dict) else None
    )
    if not isinstance(required_ids, list) \
            or len(required_ids) != len(set(required_ids)) \
            or set(required_ids) != expected_required_ids:
        raise FrozenScoreError(
            "Finalization required correction IDs changed")


def _validate_finalization(
    spec: FrozenScoreSpec,
    lock: dict[str, Any],
    original_queue: LockedArtifact,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if lock.get("schema_version") != "1.0" \
            or lock.get("status") != "final":
        raise FrozenScoreError("Finalization lock is not final")
    references = list(_walk_artifact_references(lock))
    if not references:
        raise FrozenScoreError("Finalization lock has no artifact references")
    resolved = {
        trail: _require_reference(spec.root, reference, trail)
        for trail, reference in references
    }
    _validate_finalization_inputs(spec, lock, original_queue)
    _validate_correction_layers(spec, lock)
    state_artifact = resolved.get("finalization lock.outputs.state")
    truth_artifact = resolved.get("finalization lock.outputs.truth")
    if state_artifact is None or truth_artifact is None:
        raise FrozenScoreError(
            "Finalization lock has no final state/truth outputs")
    if spec.expected_final_state is None or spec.expected_final_truth is None:
        raise FrozenScoreError(
            "Expected finalization output paths are not configured")
    if state_artifact.path != spec.expected_final_state.resolve() \
            or truth_artifact.path != spec.expected_final_truth.resolve():
        raise FrozenScoreError(
            "Finalization outputs do not use the required v2 paths")
    state = _read_json(state_artifact.path)
    truth = _read_jsonl(truth_artifact.path)
    items = _require_state_truth_mirror(
        state, truth, spec.benchmark_id)

    validation = lock.get("validation")
    required_validation = spec.required_validation_flags
    if not required_validation:
        raise FrozenScoreError(
            "Required finalization validation flags are not configured")
    if not isinstance(validation, dict) \
            or set(validation) != set(required_validation) \
            or any(
        validation.get(key) is not True for key in required_validation
    ):
        raise FrozenScoreError(
            "Finalization lock validation is incomplete")
    counts = lock.get("counts")
    if not isinstance(counts, dict) \
            or int(counts.get("items") or -1) != len(items):
        raise FrozenScoreError("Finalization item count changed")
    if {
        key: int(value)
        for key, value in counts.get("statuses", {}).items()
    } != dict(Counter(str(item["status"]) for item in items)):
        raise FrozenScoreError("Finalization status counts changed")
    return items, {
        "state": {
            "file": _relative(spec.root, state_artifact.path),
            "sha256": state_artifact.sha256,
        },
        "truth": {
            "file": _relative(spec.root, truth_artifact.path),
            "sha256": truth_artifact.sha256,
        },
        "base_input_count": len(spec.required_finalization_inputs),
        "correction_layers": [
            {
                "layer_id": layer.layer_id,
                "queue_id": layer.queue_id,
                "item_ids": list(layer.item_ids),
                "completion_lock": {
                    "file": _relative(
                        spec.root, layer.completion_lock.path),
                    "sha256": layer.completion_lock.sha256,
                },
            }
            for layer in spec.required_correction_layers
        ],
        "required_validation_flags": sorted(
            spec.required_validation_flags),
        "finalized_at": str(lock.get("finalized_at") or ""),
        "referenced_artifact_count": len(references),
    }


def _gate(
    value: float | int | bool | None,
    threshold: float | int | bool,
    comparator: str,
    *,
    measurable: bool = True,
    note: str = "",
) -> dict[str, Any]:
    if not measurable:
        return {
            "value": None,
            "threshold": threshold,
            "comparator": comparator,
            "passed": None,
            "status": "not_measurable",
            "note": note,
        }
    if comparator == ">=":
        passed = float(value) >= float(threshold)  # type: ignore[arg-type]
    elif comparator == "<=":
        passed = float(value) <= float(threshold)  # type: ignore[arg-type]
    elif comparator == "==":
        passed = value == threshold
    else:
        raise FrozenScoreError(f"Unsupported gate comparator {comparator!r}")
    return {
        "value": value,
        "threshold": threshold,
        "comparator": comparator,
        "passed": bool(passed),
        "status": "pass" if passed else "fail",
        "note": note,
    }


def evaluate_predeclared_gates(
    score: dict[str, Any],
    gate_spec: dict[str, Any],
    structural: dict[str, int],
) -> dict[str, Any]:
    primary = score["aggregate"]["primary"]
    boundary = score["aggregate"]["all_one_to_one_boundary"]
    by_stratum = score["by_stratum"]
    confident = by_stratum.get("confident", {})
    weak = by_stratum.get("weak_recovered", {})
    pairs = by_stratum.get("scene_angle_pair", {})
    pair_count = int(pairs.get("selected_prediction_count") or 0)
    film_values = {
        film_id: row["primary"]["coverage"]
        for film_id, row in score["by_film"].items()
    }
    film_threshold = float(
        gate_spec["per_film_sampled_coverage_iou_0_75_min"])
    failed_films = sorted(
        film_id for film_id, value in film_values.items()
        if value < film_threshold
    )
    per_film_gate = {
        "value": film_values,
        "threshold": film_threshold,
        "comparator": ">=",
        "passed": not failed_films,
        "status": "pass" if not failed_films else "fail",
        "failed_films": failed_films,
    }
    gates = {
        "sampled_coverage_iou_0_75": _gate(
            primary["coverage"],
            float(gate_spec["sampled_coverage_iou_0_75_min"]),
            ">=",
        ),
        "per_film_sampled_coverage_iou_0_75": per_film_gate,
        "confident_prediction_match": _gate(
            confident.get("candidate_match_rate"),
            float(gate_spec["confident_prediction_match_min"]),
            ">=",
            measurable=bool(confident.get("selected_prediction_count")),
            note="No confident predictions were sampled."
            if not confident.get("selected_prediction_count") else "",
        ),
        "weak_recovery_match": _gate(
            weak.get("candidate_match_rate"),
            float(gate_spec["weak_recovery_match_min"]),
            ">=",
            measurable=bool(weak.get("selected_prediction_count")),
            note="No weak-recovery predictions were sampled."
            if not weak.get("selected_prediction_count") else "",
        ),
        "scene_angle_pair_precision": _gate(
            pairs.get("candidate_match_rate"),
            float(gate_spec["scene_angle_pair_precision_min"]),
            ">=",
            measurable=pair_count > 0,
            note=(
                "No scene-angle-pair proposals occurred in this holdout; "
                "pair precision is not measurable."
            ) if pair_count == 0 else "",
        ),
        "merge_rate": _gate(
            primary["merge_rate"],
            float(gate_spec["merge_rate_max"]),
            "<=",
        ),
        "split_rate": _gate(
            primary["split_rate"],
            float(gate_spec["split_rate_max"]),
            "<=",
        ),
        "median_boundary_error_ms": _gate(
            boundary["median_max_boundary_error_ms"],
            int(gate_spec["median_boundary_error_ms_max"]),
            "<=",
            measurable=boundary["denominator"] > 0,
            note=(
                f"All {boundary['denominator']} eligible 1:1 lineage "
                "components are included regardless of IoU; "
                f"{boundary['skipped_non_one_to_one_component_count']} "
                "non-1:1 components are outside this boundary metric."
            ),
        ),
        "p90_boundary_error_ms": _gate(
            boundary["p90_max_boundary_error_ms"],
            int(gate_spec["p90_boundary_error_ms_max"]),
            "<=",
            measurable=boundary["denominator"] > 0,
            note=(
                f"All {boundary['denominator']} eligible 1:1 lineage "
                "components are included regardless of IoU; "
                f"{boundary['skipped_non_one_to_one_component_count']} "
                "non-1:1 components are outside this boundary metric."
            ),
        ),
        "overlap_count": _gate(
            structural["overlap_count"],
            int(gate_spec["overlap_count_max"]),
            "<=",
        ),
        "out_of_bounds_count": _gate(
            structural["out_of_bounds_count"],
            int(gate_spec["out_of_bounds_count_max"]),
            "<=",
        ),
        "artifact_accounting_complete": _gate(
            not bool(gate_spec["data_loss_allowed"]),
            True,
            "==",
            note=(
                "All frozen payloads, sampled roots, and final lineage "
                "records were hash-validated. This checks artifact "
                "accounting, not detector recall."
            ),
        ),
    }
    for name in ("median_boundary_error_ms", "p90_boundary_error_ms"):
        gates[name]["metric_scope"] = boundary["scope"]
        gates[name]["denominator"] = boundary["denominator"]
        gates[name]["skipped_non_one_to_one_component_count"] = (
            boundary["skipped_non_one_to_one_component_count"])
    measurable = [
        row["passed"] for row in gates.values()
        if row.get("passed") is not None
    ]
    if not all(measurable):
        overall = "fail"
    elif any(row.get("passed") is None for row in gates.values()):
        overall = "incomplete"
    else:
        overall = "pass"
    return {
        "overall_status": overall,
        "results": gates,
    }


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{100 * value:.1f}%"


def _number(value: float | int | None) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, int):
        return str(value)
    return f"{value:.1f}"


def render_markdown(report: dict[str, Any]) -> str:
    primary = report["results"]["aggregate"]["primary"]
    diagnostic = report["results"]["aggregate"]["diagnostic"]
    boundary = report["results"]["aggregate"]["all_one_to_one_boundary"]
    population = report["results"]["population"]
    integrity_status = (
        "COMPLETE" if report["integrity"]["passed"] else "FAILED")
    gate_status = report["gates"]["overall_status"].upper()
    lines = [
        "# TapeSift Iteration 3A Independent Holdout",
        "",
        (
            "> **Internal engineering diagnostic. This report is not "
            "eligible for a public performance or accuracy claim.**"
        ),
        "",
        "## Status",
        "",
        f"- Report generation: **{report['report_status'].upper()}**",
        f"- Artifact integrity and accounting: **{integrity_status}**",
        f"- Predeclared performance gates: **{gate_status}**",
        (
            "- Scope: temporally distributed, stratified sampled detector "
            "candidates and detector-unclassified windows"
        ),
        "",
        "## Sampled results",
        "",
        "| Measure | Result |",
        "| --- | ---: |",
        (
            f"| Locked roots | "
            f"{population['root_count']} |"
        ),
        (
            f"| Verified plays within sampled roots | "
            f"{primary['truth_count']} |"
        ),
        f"| Sampled detector candidates | {primary['prediction_count']} |",
        (
            f"| IoU 0.75 matches | {primary['matched_count']} of "
            f"{primary['truth_count']} verified sampled plays |"
        ),
        (
            f"| Coverage within sampled roots at IoU 0.75 | "
            f"{_percent(primary['coverage'])} |"
        ),
        (
            f"| IoU pass rate among sampled detector candidates | "
            f"{_percent(primary['precision'])} |"
        ),
        f"| Sample-scoped F1 at IoU 0.75 | {_percent(primary['f1'])} |",
        (
            f"| Coverage within sampled roots at diagnostic IoU 0.50 | "
            f"{_percent(diagnostic['coverage'])} |"
        ),
        (
            f"| Sampled one-candidate-to-many-truth relationship rate "
            f"(containment 0.50) | {_percent(primary['merge_rate'])} |"
        ),
        (
            f"| Sampled many-candidate-to-one-truth relationship rate "
            f"(containment 0.50) | {_percent(primary['split_rate'])} |"
        ),
        (
            f"| All-1:1 boundary comparisons | {boundary['denominator']} "
            f"eligible; {boundary['skipped_non_one_to_one_component_count']} "
            "non-1:1 skipped |"
        ),
        (
            f"| Median worst-edge boundary error across all eligible 1:1 "
            f"components | "
            f"{_number(boundary['median_max_boundary_error_ms'])} ms |"
        ),
        (
            f"| P90 worst-edge boundary error across all eligible 1:1 "
            f"components | "
            f"{_number(boundary['p90_max_boundary_error_ms'])} ms |"
        ),
        (
            f"| Survivor-only boundary comparisons at IoU 0.75 | "
            f"{primary['boundary_error_denominator']} |"
        ),
        (
            f"| Survivor-only median worst-edge boundary error | "
            f"{_number(primary['median_max_boundary_error_ms'])} ms |"
        ),
        (
            f"| Survivor-only P90 worst-edge boundary error | "
            f"{_number(primary['p90_max_boundary_error_ms'])} ms |"
        ),
        "",
        "## By film",
        "",
        (
            "| Film | Verified sampled plays | Sampled detector candidates | "
            "IoU matches | Sampled-root coverage | Candidate IoU pass rate |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for film_id, row in report["results"]["by_film"].items():
        metrics = row["primary"]
        candidate_rate = (
            _percent(metrics["precision"])
            if metrics["prediction_count"] else "N/A"
        )
        lines.append(
            f"| {film_id} | {metrics['truth_count']} | "
            f"{metrics['prediction_count']} | {metrics['matched_count']} | "
            f"{_percent(metrics['coverage'])} | "
            f"{candidate_rate} |"
        )
    prediction_rows = {
        row["film_id"]: row
        for row in report["provenance"]["predictions"]
    }
    for film_id, row in report["results"]["by_film"].items():
        frozen = prediction_rows[film_id]
        if int(frozen["play_count"]) == 0:
            excluded = row["root_dispositions"].get("excluded", 0)
            lines.extend([
                "",
                (
                    f"- **{film_id}:** the frozen detector produced zero "
                    "full-film play candidates. All "
                    f"{row['root_count']} sampled roots came from "
                    "unclassified space; review verified "
                    f"{row['primary']['truth_count']} plays and excluded "
                    f"{excluded}. Candidate pass rate is N/A."
                ),
            ])
    lines.extend([
        "",
        "## Detector-unclassified sample outcomes",
        "",
        "| Review outcome | Sampled roots |",
        "| --- | ---: |",
        (
            f"| Standalone missed play | "
            f"{population['unclassified_review'].get('standalone_missed_play', 0)} |"
        ),
        (
            f"| Boundary continuation | "
            f"{population['unclassified_review'].get('boundary_continuation', 0)} |"
        ),
        (
            f"| Reviewed no-play | "
            f"{population['unclassified_review'].get('reviewed_no_play', 0)} |"
        ),
        "",
        (
            "These outcomes describe the selected unclassified windows only; "
            "they are not an unbiased full-film prevalence estimate."
        ),
        "",
        "## By sampling stratum",
        "",
        (
            "| Stratum | Sampled roots | Sampled detector candidates | "
            "IoU matches | Candidate IoU pass rate |"
        ),
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for stratum, row in report["results"]["by_stratum"].items():
        lines.append(
            f"| {stratum} | {row['root_count']} | "
            f"{row['selected_prediction_count']} | "
            f"{row['matched_prediction_count']} | "
            f"{_percent(row['candidate_match_rate'])} |"
        )
    lines.extend([
        "",
        "## Predeclared gates",
        "",
        "| Gate | Value | Threshold | Status |",
        "| --- | --- | --- | --- |",
    ])
    gate_labels = {
        "sampled_coverage_iou_0_75": (
            "coverage within sampled roots at IoU 0.75"),
        "per_film_sampled_coverage_iou_0_75": (
            "per-film coverage within sampled roots at IoU 0.75"),
        "confident_prediction_match": (
            "confident sampled-candidate IoU pass rate"),
        "weak_recovery_match": (
            "weak-recovery sampled-candidate IoU pass rate"),
        "scene_angle_pair_precision": (
            "scene-angle-pair sampled-candidate IoU pass rate"),
        "merge_rate": (
            "sampled one-candidate-to-many-truth relationship rate "
            "(containment 0.50)"
        ),
        "split_rate": (
            "sampled many-candidate-to-one-truth relationship rate "
            "(containment 0.50)"
        ),
        "median_boundary_error_ms": (
            "all-1:1 median worst-edge boundary error ms"),
        "p90_boundary_error_ms": (
            "all-1:1 p90 worst-edge boundary error ms"),
        "artifact_accounting_complete": "artifact accounting complete",
    }
    for name, row in report["gates"]["results"].items():
        value = row.get("value")
        if isinstance(value, dict):
            value_text = ", ".join(
                f"{key}: {_percent(float(entry))}"
                for key, entry in value.items()
            )
        elif isinstance(value, float) and "error_ms" not in name \
                and "count" not in name:
            value_text = _percent(value)
        else:
            value_text = _number(value)
        threshold = row.get("threshold")
        if isinstance(threshold, float) and "error_ms" not in name \
                and "count" not in name:
            threshold_text = _percent(threshold)
        else:
            threshold_text = str(threshold)
        if name == "artifact_accounting_complete":
            value_text = "Yes" if value else "No"
            threshold_cell = "must be Yes"
        else:
            threshold_cell = f"{row['comparator']} {threshold_text}"
        lines.append(
            f"| {gate_labels.get(name, name.replace('_', ' '))} | "
            f"{value_text} | {threshold_cell} | {row['status']} |"
        )
    lines.extend([
        "",
        "## Methodology notes",
        "",
        (
            "- Primary matching uses greedy one-to-one segment IoU at 0.75 "
            "inside human-lineage components only."
        ),
        (
            "- Unreviewed full-film predictions are outside the scoring "
            "universe and are never counted as false positives."
        ),
        (
            f"- The boundary gates use all {boundary['denominator']} eligible "
            "1:1 lineage components regardless of IoU. "
            f"{boundary['skipped_non_one_to_one_component_count']} non-1:1 "
            "components are skipped under the stated policy."
        ),
        (
            "- Primary and diagnostic match-boundary statistics remain "
            "survivor-only and are labeled with their match denominator."
        ),
        (
            "- Scene-angle-pair precision is not measurable because this "
            "holdout produced zero pair proposals."
        ),
        (
            "- Artifact accounting means the frozen files and sampled "
            "lineage were hash-validated. It does not mean the detector had "
            "zero missed plays."
        ),
        "",
    ])
    return "\n".join(lines)


def build_frozen_report(spec: FrozenScoreSpec) -> FrozenScoreBundle:
    """Validate every lock and build report bytes without writing."""

    for artifact, label in (
        (spec.selection, "selection lock"),
        (spec.prediction_lock, "prediction lock"),
        (spec.queue_lock, "queue lock"),
        (spec.finalization_lock, "finalization lock"),
    ):
        _require_hash(artifact, label)
    selection = _read_json(spec.selection.path)
    films, gate_spec = _validate_selection(spec, selection)
    film_ids = {str(film["film_id"]) for film in films}
    prediction_lock = _read_json(spec.prediction_lock.path)
    predictions, public_prediction_rows = _validate_prediction_lock(
        spec, prediction_lock, selection, film_ids)
    structural = _validate_prediction_structure(predictions)
    queue_lock = _read_json(spec.queue_lock.path)
    roots, original_queue = _validate_queue_lock(
        spec, queue_lock, spec.prediction_lock, film_ids)
    finalization = _read_json(spec.finalization_lock.path)
    final_items, public_finalization = _validate_finalization(
        spec, finalization, original_queue)

    score = score_sampled_holdout(
        roots,
        final_items,
        predictions,
        expected_root_count=spec.expected_root_count,
        primary_iou_threshold=PRIMARY_IOU_THRESHOLD,
        diagnostic_iou_threshold=0.50,
        relationship_threshold=RELATIONSHIP_THRESHOLD,
    )
    if score["population"]["film_count"] != spec.expected_film_count:
        raise FrozenScoreError("Scorer film count changed")
    gates = evaluate_predeclared_gates(score, gate_spec, structural)
    report = {
        "schema_version": "2.0",
        "benchmark_id": spec.benchmark_id,
        "report_status": "complete",
        "performance_gate_status": gates["overall_status"],
        # A frozen completion timestamp keeps repeated check-only builds
        # deterministic and avoids claiming that wall-clock time is evidence.
        "generated_at": public_finalization["finalized_at"],
        "scope": {
            "design": (
                "temporally distributed stratified sample of detector plays "
                "and detector-unclassified windows"
            ),
            "public_claim_eligible": False,
            "note": (
                "Internal engineering diagnostic only. This report is not "
                "eligible for a public performance or accuracy claim and "
                "does not estimate every prediction in the five films."
            ),
        },
        "provenance": {
            "selection": {
                "file": _relative(spec.root, spec.selection.path),
                "sha256": spec.selection.sha256,
            },
            "prediction_lock": {
                "file": _relative(spec.root, spec.prediction_lock.path),
                "sha256": spec.prediction_lock.sha256,
            },
            "queue_lock": {
                "file": _relative(spec.root, spec.queue_lock.path),
                "sha256": spec.queue_lock.sha256,
            },
            "finalization_lock": {
                "file": _relative(spec.root, spec.finalization_lock.path),
                "sha256": spec.finalization_lock.sha256,
            },
            "finalization": public_finalization,
            "detector": {
                "source_commit": prediction_lock["detector"]["source_commit"],
                "source_sha256": prediction_lock["detector"]["source_sha256"],
                "settings": prediction_lock["detector"]["settings"],
            },
            "films": [
                {
                    "film_id": film["film_id"],
                    "duration_ms": film["duration_ms"],
                    "resolution": film["resolution"],
                    "frame_rate": film["frame_rate"],
                    "source_sha256": film["source_sha256"],
                }
                for film in sorted(films, key=lambda row: row["film_id"])
            ],
            "predictions": public_prediction_rows,
        },
        "integrity": {
            "passed": True,
            "status": "complete",
            "scope": "artifact_integrity_and_sample_lineage_accounting",
            "selection_hash_valid": True,
            "prediction_hashes_valid": True,
            "queue_hash_valid": True,
            "finalization_hashes_valid": True,
            "state_truth_mirror": True,
            "source_hashes_valid": True,
            "prediction_structure": structural,
            "scorer": score["integrity"],
        },
        "results": score,
        "gates": gates,
    }
    json_text = _json_text(report)
    markdown_text = render_markdown(report)
    # Machine-specific paths are consumed for validation but never published.
    root_text = str(spec.root.resolve())
    if root_text.casefold() in json_text.casefold() \
            or root_text.casefold() in markdown_text.casefold():
        raise FrozenScoreError(
            "Public report contains an absolute repository path")
    return FrozenScoreBundle(report, json_text, markdown_text)


def write_reports(
    spec: FrozenScoreSpec,
    bundle: FrozenScoreBundle,
) -> None:
    """Publish JSON and Markdown together, refusing every overwrite."""

    outputs = (
        (spec.output_json, bundle.json_text),
        (spec.output_markdown, bundle.markdown_text),
    )
    existing = [str(path) for path, _text in outputs if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to replace sampled holdout reports: "
            + ", ".join(existing))
    staged: list[tuple[Path, Path]] = []
    created: list[Path] = []
    try:
        for target, text in outputs:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(text.encode("utf-8"))
            staged.append((temporary, target))
        for temporary, target in staged:
            os.link(temporary, target)
            created.append(target)
    except Exception:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise
    finally:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)


def _sha256_argument(value: str) -> str:
    digest = value.strip().lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise argparse.ArgumentTypeError(
            "Expected a 64-character SHA-256 digest")
    return digest


def default_spec(finalization_sha256: str) -> FrozenScoreSpec:
    return FrozenScoreSpec(
        root=ROOT,
        benchmark_id=BENCHMARK_ID,
        selection=LockedArtifact(SELECTION, SELECTION_SHA256),
        prediction_lock=LockedArtifact(
            PREDICTION_LOCK, PREDICTION_LOCK_SHA256),
        queue_lock=LockedArtifact(QUEUE_LOCK, QUEUE_LOCK_SHA256),
        finalization_lock=LockedArtifact(
            FINALIZATION_LOCK, finalization_sha256),
        output_json=OUTPUT_JSON,
        output_markdown=OUTPUT_MARKDOWN,
        required_finalization_inputs=tuple(
            LockedArtifact(path, digest)
            for path, digest in REQUIRED_FINALIZATION_INPUTS
        ),
        required_correction_layers=(
            ExpectedCorrectionLayer(
                layer_id="final-corrections-seven-v1",
                queue_id=(
                    "segmentation-iteration-3a-holdout-v1-"
                    "final-corrections"
                ),
                item_ids=SEVEN_CORRECTION_IDS,
                completion_lock=LockedArtifact(
                    SEVEN_COMPLETION_LOCK, SEVEN_COMPLETION_SHA256),
            ),
            ExpectedCorrectionLayer(
                layer_id="reviewer-verified-final-five-v2",
                queue_id=(
                    "segmentation-iteration-3a-holdout-v1-"
                    "reviewer-verified-final-five-v2"
                ),
                item_ids=OWNER_V2_CORRECTION_IDS,
                completion_lock=LockedArtifact(
                    OWNER_V2_COMPLETION_LOCK,
                    OWNER_V2_COMPLETION_SHA256,
                ),
            ),
        ),
        expected_final_state=FINAL_STATE,
        expected_final_truth=FINAL_TRUTH,
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Score the hash-locked Iteration 3A sampled holdout")
    value.add_argument(
        "--finalization-lock-sha256",
        required=True,
        type=_sha256_argument,
        help="SHA-256 printed or independently recorded after finalization.",
    )
    value.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and build both reports in memory without writing.",
    )
    return value


def main() -> int:
    args = parser().parse_args()
    spec = default_spec(args.finalization_lock_sha256)
    bundle = build_frozen_report(spec)
    if not args.check_only:
        write_reports(spec, bundle)
    print(_json_text({
        "status": "validated" if args.check_only else "scored",
        "gate_status": bundle.report["gates"]["overall_status"],
        "json_report": _relative(spec.root, spec.output_json),
        "markdown_report": _relative(spec.root, spec.output_markdown),
        "root_count": bundle.report["results"]["population"]["root_count"],
        "truth_count": bundle.report["results"]["aggregate"][
            "primary"]["truth_count"],
        "matches_iou_0_75": bundle.report["results"]["aggregate"][
            "primary"]["matched_count"],
    }).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
