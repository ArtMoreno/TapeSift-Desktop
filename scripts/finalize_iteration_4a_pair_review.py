"""Freeze and grade the completed Iteration 4A blind pair review.

This is a development-only review gate. It validates one narrowly guarded
fallback topology and must not be used as a general detector-accuracy claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


BASE = ROOT / "research" / "segmentation_benchmark"
VERIFICATION = BASE / "verification"
REPORTS = BASE / "reports" / "iteration_4a_development_ab_v1"
PREDICTIONS = (
    BASE / "predictions" / "iteration_4a_bimodal_pair_candidate_v2")

QUEUE_ID = "segmentation-iteration-4a-black-gap-pair-v2"
CANDIDATE_ID = "iteration-4a-bimodal-pair-v2"
CANDIDATE_KIND = "black_gap_pair"
FILM_ID = "south_carolina_o_vs_vanderbilt_d_holdout_3a_v1"
DETECTOR_SOURCE = "tapesift/services/play_detect_service.py"
DETECTOR_COMMIT = "35b5a324ebea3a90c54200229c42dd944a4d89ba"

DEVELOPMENT_WARNING = (
    "Development-only review of one film and one narrowly guarded topology. "
    "This is not a general detector-accuracy, precision, or coverage claim."
)


class PairReviewFinalizationError(ValueError):
    """Raised when the completed review cannot be proven safely."""


@dataclass(frozen=True)
class LockedArtifact:
    path: Path
    sha256: str


@dataclass(frozen=True)
class PairReviewSpec:
    root: Path
    locked_queue: LockedArtifact
    final_state: LockedArtifact
    final_truth: LockedArtifact
    selection: LockedArtifact
    candidate_lock: LockedArtifact
    prediction: LockedArtifact
    detector_source: LockedArtifact
    development_ab: LockedArtifact
    detector_commit: str
    output_json: Path
    output_markdown: Path
    expected_queue_id: str = QUEUE_ID
    expected_candidate_id: str = CANDIDATE_ID
    expected_candidate_kind: str = CANDIDATE_KIND
    expected_film_id: str = FILM_ID
    expected_review_count: int = 25


@dataclass(frozen=True)
class ReportBundle:
    report: dict[str, Any]
    json_text: str
    markdown_text: str


def default_spec() -> PairReviewSpec:
    return PairReviewSpec(
        root=ROOT,
        locked_queue=LockedArtifact(
            VERIFICATION / "iteration_4a_black_gap_pair_v2.queue.locked.json",
            "e888385f8fb2d145bd18aa62fd2c9f1fb50763cb9d4947477291544b93e63a6c",
        ),
        final_state=LockedArtifact(
            VERIFICATION / "iteration_4a_black_gap_pair_v2.final.state.json",
            "15a536b5e43a8568b89bc042752f72639050c1e76321d335b9c80cb932303c0f",
        ),
        final_truth=LockedArtifact(
            VERIFICATION
            / "iteration_4a_black_gap_pair_v2.final.verified.jsonl",
            "f995b07f2e379f0cf2da15592fc77e9c571e4db7ecf50db23bef7509416f13bb",
        ),
        selection=LockedArtifact(
            REPORTS / "black_gap_pair_selection_v2.json",
            "a63d0e58d2aec0b123a03e25032eb47600472be03c58a03674c484a9283e8b37",
        ),
        candidate_lock=LockedArtifact(
            REPORTS / "candidate_lock_v2.json",
            "c186ec314583849f02c8e63ef2d18a7fd71cc7c13e74e4d1d7b6dd906145e375",
        ),
        prediction=LockedArtifact(
            PREDICTIONS / f"{FILM_ID}.json",
            "e7d010c7c1c477ea97ca165a9cbe7372d8023c2d1fc545a42898cfbdfd5b9bb0",
        ),
        detector_source=LockedArtifact(
            ROOT / DETECTOR_SOURCE,
            "c9c7688256976e9ec937699d4141c35956d9727488ec58345910b44cbc516c75",
        ),
        development_ab=LockedArtifact(
            REPORTS / "ab_results_iteration-4a-bimodal-pair-v2_v1.json",
            "9dd3f08d1f716e82daf1df79d29e0df5c70bc9ad38c9c21af581643e6c9cc947",
        ),
        detector_commit=DETECTOR_COMMIT,
        output_json=REPORTS / "black_gap_pair_review_v2.completion.lock.json",
        output_markdown=REPORTS / "black_gap_pair_review_v2.md",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_hash(artifact: LockedArtifact, label: str) -> None:
    if not artifact.path.is_file():
        raise FileNotFoundError(f"{label} is missing: {artifact.path}")
    actual = sha256_file(artifact.path)
    if actual != artifact.sha256:
        raise PairReviewFinalizationError(
            f"{label} SHA256 changed: expected {artifact.sha256}, got {actual}")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PairReviewFinalizationError(
            f"Expected a JSON object in {path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise PairReviewFinalizationError(
                f"JSONL row {line_number} is not an object")
        rows.append(value)
    return rows


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise PairReviewFinalizationError(
            f"Artifact is outside the repository: {path}") from exc


def _git_blob_sha256(root: Path, commit: str, source_file: str) -> str:
    completed = subprocess.run(
        ["git", "cat-file", "blob", f"{commit}:{source_file}"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode(
            "utf-8", errors="replace").strip()
        raise PairReviewFinalizationError(
            f"Cannot resolve detector commit {commit}"
            + (f": {detail}" if detail else ""))
    return hashlib.sha256(completed.stdout).hexdigest()


def _artifact_row(spec: PairReviewSpec, artifact: LockedArtifact
                  ) -> dict[str, str]:
    return {
        "file": _relative(spec.root, artifact.path),
        "sha256": artifact.sha256,
    }


def _exact_success_interval(successes: int, trials: int,
                            confidence: float = 0.95
                            ) -> tuple[float, float]:
    """Return a two-sided Clopper-Pearson exact binomial interval."""

    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("Binomial successes must be between zero and trials")
    alpha = 1.0 - confidence
    if successes == trials:
        return (math.pow(alpha / 2.0, 1.0 / trials), 1.0)
    if successes == 0:
        return (0.0, 1.0 - math.pow(alpha / 2.0, 1.0 / trials))

    def cdf(k: int, probability: float) -> float:
        return sum(
            math.comb(trials, value)
            * probability ** value
            * (1.0 - probability) ** (trials - value)
            for value in range(k + 1)
        )

    target = alpha / 2.0
    low, high = 0.0, 1.0
    for _ in range(100):
        midpoint = (low + high) / 2.0
        upper_tail = 1.0 - cdf(successes - 1, midpoint)
        if upper_tail < target:
            low = midpoint
        else:
            high = midpoint
    lower = (low + high) / 2.0

    low, high = 0.0, 1.0
    for _ in range(100):
        midpoint = (low + high) / 2.0
        if cdf(successes, midpoint) > target:
            low = midpoint
        else:
            high = midpoint
    upper = (low + high) / 2.0
    return lower, upper


def _expected_truth(item: dict[str, Any], queue_id: str) -> dict[str, Any]:
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
            "candidate_stratum": item["candidate_stratum"],
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


def _truth_mirror(item: dict[str, Any], truth: dict[str, Any],
                  queue_id: str = QUEUE_ID) -> bool:
    return truth == _expected_truth(item, queue_id)


def _grade_review_roots(
    locked_items: list[dict[str, Any]],
    final_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve terminal items back to locked proposals and grade decisions."""

    root_ids = [str(item.get("item_id") or "") for item in locked_items]
    if len(set(root_ids)) != len(root_ids) or any(not value for value in root_ids):
        raise PairReviewFinalizationError(
            "Locked review item ids are missing or duplicated")
    descendants: dict[str, list[dict[str, Any]]] = {
        root_id: [] for root_id in root_ids}
    for item in final_items:
        item_id = str(item.get("item_id") or "")
        matches = [
            root_id for root_id in root_ids
            if item_id == root_id or item_id.startswith(f"{root_id}:split:")
        ]
        if len(matches) != 1:
            raise PairReviewFinalizationError(
                f"Cannot resolve final review lineage for {item_id!r}")
        descendants[matches[0]].append(item)

    stable_fields = {
        "film_id",
        "film_name",
        "source_file",
        "analysis_source",
        "frame_rate",
        "film_duration_ms",
        "original_start_ms",
        "original_end_ms",
        "candidate_kind",
        "candidate_index",
        "prediction_indices",
        "detector_needs_review",
        "detector_reason",
        "detector_signal",
        "candidate_stratum",
    }
    exact = revised = excluded = split_roots = same_snap = 0
    boundary_edits = 0
    for root in locked_items:
        root_id = root["item_id"]
        children = descendants[root_id]
        if not children:
            raise PairReviewFinalizationError(
                f"Locked proposal disappeared from final state: {root_id}")
        for child in children:
            if any(child.get(field) != root.get(field)
                   for field in stable_fields):
                raise PairReviewFinalizationError(
                    f"Review lineage changed immutable metadata for {root_id}")
            if child.get("status") not in {"verified", "excluded"}:
                raise PairReviewFinalizationError(
                    f"Review root is not complete: {root_id}")
            start_ms = int(child.get("start_ms", -1))
            end_ms = int(child.get("end_ms", -1))
            angle_starts = list(child.get("angle_starts_ms") or [])
            if not 0 <= start_ms < end_ms <= int(root["film_duration_ms"]) \
                    or not angle_starts \
                    or int(child.get("angle_count") or 0) != len(angle_starts) \
                    or any(not start_ms <= int(value) < end_ms
                           for value in angle_starts):
                raise PairReviewFinalizationError(
                    f"Final review bounds or angle metadata are invalid: "
                    f"{child.get('item_id')}")
            if any(str(entry).startswith("merged:")
                   for entry in child.get("edit_history", [])) \
                    or ":merge:" in str(child.get("item_id") or ""):
                raise PairReviewFinalizationError(
                    "Pair-review finalization does not permit merged roots")

        has_split_descendant = any(
            ":split:" in str(item.get("item_id") or "")
            for item in children
        )
        if len(children) > 1:
            split_roots += 1
            ordered = sorted(
                children, key=lambda item: (
                    int(item["start_ms"]), int(item["end_ms"])))
            if not has_split_descendant or not all(
                ":split:" in str(item.get("item_id") or "")
                and
                any(str(entry).startswith("split@")
                    for entry in item.get("edit_history", []))
                for item in ordered
            ) or any(
                left["end_ms"] > right["start_ms"]
                for left, right in zip(ordered, ordered[1:])
            ):
                raise PairReviewFinalizationError(
                    f"Split lineage is malformed for {root_id}")
            continue
        if has_split_descendant:
            raise PairReviewFinalizationError(
                f"Split lineage lost a descendant for {root_id}")

        item = children[0]
        if item["status"] == "excluded":
            if item.get("decision") != "excluded":
                raise PairReviewFinalizationError(
                    f"Excluded root has the wrong decision: {root_id}")
            excluded += 1
            continue
        if item.get("decision") not in {"accepted", "revised"}:
            raise PairReviewFinalizationError(
                f"Verified root has unsupported decision: {root_id}")
        unchanged = (
            item["start_ms"] == root["start_ms"]
            and item["end_ms"] == root["end_ms"]
            and item["angle_starts_ms"] == root["angle_starts_ms"]
            and item["angle_count"] == root["angle_count"]
            and not item.get("edit_history")
            and item["decision"] == "accepted"
        )
        if item["decision"] == "accepted":
            if not unchanged:
                raise PairReviewFinalizationError(
                    f"Accepted root contains unrecorded edits: {root_id}")
            exact += 1
        else:
            if not item.get("edit_history"):
                raise PairReviewFinalizationError(
                    f"Revised root has no edit history: {root_id}")
            original_angles = list(root.get("angle_starts_ms") or [])
            revised_angles = list(item.get("angle_starts_ms") or [])
            if len(original_angles) < 2 or len(revised_angles) < 2 \
                    or original_angles[1] not in revised_angles:
                raise PairReviewFinalizationError(
                    f"Revised root no longer proves a two-angle pair: "
                    f"{root_id}")
            revised += 1
            boundary_edits += 1
        same_snap += 1

    return {
        "same_snap_pairs": same_snap,
        "accepted_unchanged": exact,
        "accepted_with_boundary_revision": revised,
        "excluded": excluded,
        "split_roots": split_roots,
        "exact_boundary_accepts": exact,
        "boundary_edits": boundary_edits,
    }


def build_report(spec: PairReviewSpec) -> ReportBundle:
    """Validate the frozen review and build deterministic completion reports."""

    artifacts = (
        (spec.locked_queue, "locked review queue"),
        (spec.final_state, "final review state"),
        (spec.final_truth, "final review truth"),
        (spec.selection, "pair selection sidecar"),
        (spec.candidate_lock, "candidate lock"),
        (spec.prediction, "South candidate prediction"),
        (spec.detector_source, "detector source"),
        (spec.development_ab, "development A/B report"),
    )
    for artifact, label in artifacts:
        _require_hash(artifact, label)

    if _git_blob_sha256(
            spec.root, spec.detector_commit, DETECTOR_SOURCE
    ) != spec.detector_source.sha256:
        raise PairReviewFinalizationError(
            "Recorded detector commit does not contain the reviewed source")

    locked = _read_json(spec.locked_queue.path)
    final = _read_json(spec.final_state.path)
    truth_rows = _read_jsonl(spec.final_truth.path)
    selection = _read_json(spec.selection.path)
    candidate_lock = _read_json(spec.candidate_lock.path)
    prediction = _read_json(spec.prediction.path)
    development_ab = _read_json(spec.development_ab.path)

    if locked.get("queue_id") != spec.expected_queue_id \
            or final.get("queue_id") != spec.expected_queue_id:
        raise PairReviewFinalizationError("Review queue identity changed")
    locked_items = locked.get("items")
    final_items = final.get("items")
    if not isinstance(locked_items, list) or not isinstance(final_items, list):
        raise PairReviewFinalizationError("Review state has no item list")
    if len(locked_items) != spec.expected_review_count:
        raise PairReviewFinalizationError(
            "Locked review count differs from the 25-item sample")
    review_grade = _grade_review_roots(locked_items, final_items)

    final_ids = [str(item.get("item_id") or "") for item in final_items]
    truth_by_id = {
        str(row.get("item_id") or ""): row for row in truth_rows}
    if len(truth_by_id) != len(truth_rows) \
            or set(truth_by_id) != set(final_ids):
        raise PairReviewFinalizationError(
            "Final truth does not exactly cover the final state")
    if any(not _truth_mirror(
            item, truth_by_id[item["item_id"]], spec.expected_queue_id)
           for item in final_items):
        raise PairReviewFinalizationError(
            "Final truth is not an exact mirror of final state")

    if any(item.get("status") not in {"verified", "excluded"}
           for item in final_items):
        raise PairReviewFinalizationError(
            "The pair review still has pending items")
    if any(item.get("candidate_kind") != spec.expected_candidate_kind
           or item.get("film_id") != spec.expected_film_id
           for item in final_items):
        raise PairReviewFinalizationError(
            "Final review contains the wrong film or candidate kind")

    selected = selection.get("selected")
    if selection.get("candidate_kind") != spec.expected_candidate_kind \
            or selection.get("film_id") != spec.expected_film_id \
            or selection.get("selected_count") != spec.expected_review_count \
            or not isinstance(selected, list) \
            or len(selected) != spec.expected_review_count:
        raise PairReviewFinalizationError(
            "Selection sidecar identity or item count changed")
    if selection.get("prediction_sha256") != spec.prediction.sha256 \
            or selection.get("detector_source_sha256") != \
            spec.detector_source.sha256:
        raise PairReviewFinalizationError(
            "Selection sidecar no longer points to reviewed detector output")

    plays = prediction.get("plays")
    if prediction.get("film_id") != spec.expected_film_id \
            or not isinstance(plays, list):
        raise PairReviewFinalizationError(
            "Candidate prediction identity changed")
    for chosen, item in zip(selected, locked_items):
        output_index = int(chosen.get("output_index", -1))
        if not 0 <= output_index < len(plays):
            raise PairReviewFinalizationError(
                "Selection points outside candidate predictions")
        play = plays[output_index]
        expected = (
            int(chosen["start_ms"]),
            int(chosen["end_ms"]),
            list(chosen["angle_starts_ms"]),
        )
        actual = (
            int(play["start_ms"]),
            int(play["end_ms"]),
            list(play["angle_starts"]),
        )
        item_values = (
            int(item["start_ms"]),
            int(item["end_ms"]),
            list(item["angle_starts_ms"]),
        )
        if expected != actual or expected != item_values \
                or int(item["candidate_index"]) != output_index:
            raise PairReviewFinalizationError(
                "Selected proposal, prediction, and locked queue diverged")

    lock_rows = candidate_lock.get("predictions")
    if candidate_lock.get("schema_version") != "1.0" \
            or candidate_lock.get("status") != \
            "candidate_predictions_frozen_before_scoring" \
            or candidate_lock.get("candidate_id") != \
            spec.expected_candidate_id \
            or not isinstance(lock_rows, list) \
            or len(lock_rows) != 5:
        raise PairReviewFinalizationError("Candidate lock identity changed")
    if [row.get("film_id") for row in lock_rows] != sorted(
            str(row.get("film_id") or "") for row in lock_rows):
        raise PairReviewFinalizationError(
            "Candidate lock prediction rows are not deterministic")
    for row in lock_rows:
        file_name = str(row.get("file") or "")
        prediction_path = spec.prediction.path.parent / file_name
        if not file_name.endswith(".json") or not prediction_path.is_file() \
                or sha256_file(prediction_path) != row.get("sha256"):
            raise PairReviewFinalizationError(
                "Candidate lock prediction hashes no longer validate")
        payload = _read_json(prediction_path)
        if payload.get("film_id") != row.get("film_id") \
                or int(payload.get("duration_ms") or -1) != \
                int(row.get("duration_ms") or -2) \
                or len(payload.get("plays", [])) != row.get("play_count") \
                or len(payload.get("unclassified", [])) != \
                row.get("unclassified_count"):
            raise PairReviewFinalizationError(
                "Candidate lock prediction metadata no longer validates")
    lock_row = next(
        (row for row in lock_rows
         if row.get("film_id") == spec.expected_film_id),
        None,
    )
    if not isinstance(lock_row, dict) \
            or lock_row.get("sha256") != spec.prediction.sha256:
        raise PairReviewFinalizationError(
            "Candidate lock does not pin the reviewed South prediction")
    detector = candidate_lock.get("detector", {})
    configuration = detector.get("configuration", {})
    configuration_body = {
        "id": configuration.get("id"),
        "parameters": configuration.get("parameters"),
    }
    if detector.get("source_file") != DETECTOR_SOURCE \
            or detector.get("source_sha256") != \
            spec.detector_source.sha256 \
            or not isinstance(configuration.get("parameters"), dict) \
            or configuration.get("sha256") != \
            _canonical_sha256(configuration_body):
        raise PairReviewFinalizationError(
            "Candidate lock detector or configuration no longer validates")

    integrity = development_ab.get("integrity", {})
    provenance = development_ab.get("provenance", {})
    ab_prediction = next(
        (
            row for row in provenance.get("candidate_predictions", [])
            if row.get("film_id") == spec.expected_film_id
        ),
        None,
    )
    structure = development_ab.get("results", {}).get(
        "full_output_structure", {}).get("candidate", {})
    if development_ab.get("candidate_id") != spec.expected_candidate_id \
            or development_ab.get("report_status") != "complete" \
            or integrity.get("passed") is not True \
            or integrity.get("candidate_full_output_structure_valid") \
            is not True \
            or provenance.get("candidate_lock", {}).get("sha256") != \
            spec.candidate_lock.sha256 \
            or provenance.get("candidate_detector", {}).get(
                "source_sha256") != spec.detector_source.sha256 \
            or not isinstance(ab_prediction, dict) \
            or ab_prediction.get("sha256") != spec.prediction.sha256 \
            or structure.get("out_of_bounds_count") != 0 \
            or structure.get("overlap_pair_count") != 0:
        raise PairReviewFinalizationError(
            "Development A/B report is incomplete or has different lineage")

    reviewed = len(locked_items)
    same_snap = review_grade["same_snap_pairs"]
    edited = review_grade["boundary_edits"]
    pair_lower, pair_upper = _exact_success_interval(same_snap, reviewed)
    edit_lower, edit_upper = _exact_success_interval(edited, reviewed)
    pool_count = int(selection.get("proposal_pool_count") or 0)
    output_indices = [int(row["output_index"]) for row in selected]
    max_unsampled_between = max(
        (right - left - 1
         for left, right in zip(output_indices, output_indices[1:])),
        default=0,
    )
    gate_passed = (
        reviewed == spec.expected_review_count
        and same_snap == reviewed
        and review_grade["excluded"] == 0
        and review_grade["split_roots"] == 0
        and output_indices[0] == 0
        and output_indices[-1] == pool_count - 1
    )
    if not gate_passed:
        raise PairReviewFinalizationError(
            "Iteration 4A narrow ship gate did not pass")

    report = {
        "schema_version": "1.0",
        "review_id": "iteration-4a-black-gap-pair-review-v2",
        "status": "final",
        "disposition": "ship_narrow_review_required",
        "finalized_at": final.get("updated_at"),
        "scope": {
            "role": "development_pairing_review",
            "public_claim_eligible": False,
            "warning": DEVELOPMENT_WARNING,
            "validated_behavior": (
                "Same-snap pairing and exact outer boundaries for a "
                "systematic sample of the strict alternating black-gap "
                "fallback on one South Carolina film."
            ),
            "not_validated": [
                "general All-22 detector accuracy",
                "film-wide precision or recall",
                "pairing generalization to other films or topologies",
                "the 40 unsampled proposals individually",
            ],
        },
        "provenance": {
            "detector_commit": spec.detector_commit,
            "artifacts": {
                "locked_queue": _artifact_row(spec, spec.locked_queue),
                "final_state": _artifact_row(spec, spec.final_state),
                "final_truth": _artifact_row(spec, spec.final_truth),
                "selection": _artifact_row(spec, spec.selection),
                "candidate_lock": _artifact_row(spec, spec.candidate_lock),
                "prediction": _artifact_row(spec, spec.prediction),
                "detector_source": _artifact_row(
                    spec, spec.detector_source),
                "development_ab": _artifact_row(
                    spec, spec.development_ab),
            },
        },
        "integrity": {
            "passed": True,
            "hashes_valid": True,
            "detector_commit_contains_reviewed_source": True,
            "state_truth_exact_mirror": True,
            "locked_item_identity_preserved": True,
            "immutable_fields_preserved": True,
            "selection_matches_predictions": True,
            "candidate_lock_valid": True,
            "development_ab_valid": True,
        },
        "sample": {
            "proposal_pool_count": pool_count,
            "reviewed_count": reviewed,
            "reviewed_fraction": reviewed / pool_count,
            "selection_strategy": selection.get("selection_strategy"),
            "output_indices": output_indices,
            "includes_first_and_last": (
                output_indices[0] == 0
                and output_indices[-1] == pool_count - 1
            ),
            "max_unsampled_between_reviewed": max_unsampled_between,
        },
        "results": {
            **review_grade,
            "observed_same_snap_pair_rate": same_snap / reviewed,
            "observed_exact_boundary_rate": (
                review_grade["exact_boundary_accepts"] / reviewed),
            "same_snap_pair_rate_exact_95_interval": {
                "lower": pair_lower,
                "upper": pair_upper,
                "note": (
                    "Descriptive because the review used a systematic, "
                    "not random, sample."
                ),
            },
            "boundary_edit_rate_exact_95_interval": {
                "lower": edit_lower,
                "upper": edit_upper,
            },
        },
        "release_constraints": [
            "Keep the fallback activation gate unchanged.",
            "Keep every recovered play marked needs_review.",
            "Keep the terminal frozen block unclassified.",
            "Do not use this review as a general performance claim.",
        ],
    }
    json_text = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown_text = render_markdown(report)
    for absolute in (str(spec.root.resolve()),):
        if absolute.casefold() in json_text.casefold() \
                or absolute.casefold() in markdown_text.casefold():
            raise PairReviewFinalizationError(
                "Completion report contains an absolute machine path")
    return ReportBundle(report, json_text, markdown_text)


def render_markdown(report: dict[str, Any]) -> str:
    result = report["results"]
    sample = report["sample"]
    interval = result["same_snap_pair_rate_exact_95_interval"]
    return "\n".join([
        "# TapeSift Iteration 4A Pair Review",
        "",
        f"> **{report['scope']['warning']}**",
        "",
        "## Decision",
        "",
        "- **PASS** for the narrow, fail-closed, review-required fallback.",
        "- Keep the activation gate and `needs_review` behavior unchanged.",
        "",
        "## Blind review result",
        "",
        f"- Reviewed: **{sample['reviewed_count']} of "
        f"{sample['proposal_pool_count']} proposals** "
        f"({sample['reviewed_fraction'] * 100:.1f}%)",
        f"- Accepted unchanged: **{result['accepted_unchanged']}**",
        f"- Boundary revisions: **{result['accepted_with_boundary_revision']}**",
        f"- Splits: **{result['split_roots']}**",
        f"- Exclusions: **{result['excluded']}**",
        f"- Observed same-snap pair rate: "
        f"**{result['observed_same_snap_pair_rate'] * 100:.1f}%**",
        f"- Descriptive exact 95% interval: "
        f"**{interval['lower'] * 100:.2f}% to "
        f"{interval['upper'] * 100:.2f}%**",
        "",
        "The systematic sample included the first and last proposal and left "
        f"at most {sample['max_unsampled_between_reviewed']} unchecked "
        "proposals between reviewed items.",
        "",
        "## Limits",
        "",
        "- One film and one strict topology were reviewed.",
        "- This is not film-wide precision, recall, coverage, or accuracy.",
        "- Forty proposals were not individually reviewed.",
        "- The next step is a frozen untouched-corpus generalization pass.",
        "",
    ])


def write_reports(spec: PairReviewSpec, bundle: ReportBundle) -> None:
    outputs = (
        (spec.output_json, bundle.json_text),
        (spec.output_markdown, bundle.markdown_text),
    )
    existing = [str(path) for path, _ in outputs if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to replace Iteration 4A completion report: "
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
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Finalize the frozen Iteration 4A blind pair review")
    value.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and grade in memory without creating reports.",
    )
    return value


def main() -> int:
    args = parser().parse_args()
    spec = default_spec()
    bundle = build_report(spec)
    if not args.check_only:
        write_reports(spec, bundle)
    result = bundle.report["results"]
    print(json.dumps({
        "status": "validated" if args.check_only else "finalized",
        "disposition": bundle.report["disposition"],
        "reviewed": bundle.report["sample"]["reviewed_count"],
        "accepted_unchanged": result["accepted_unchanged"],
        "boundary_revisions": result["accepted_with_boundary_revision"],
        "splits": result["split_roots"],
        "exclusions": result["excluded"],
        "observed_same_snap_pair_rate": (
            result["observed_same_snap_pair_rate"]),
        "public_claim_eligible": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
