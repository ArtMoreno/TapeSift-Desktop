"""Score an Iteration 4A detector candidate on frozen development truth.

The five films in this comparison are no longer an independent holdout.  The
reviewed truth and the Iteration 3A v2 result have already been inspected, so
this driver deliberately labels every result as a development-only regression
diagnostic.

The primary comparison is truth-centric.  A detector prediction may recover a
frozen verified truth item, but predictions that do not match sampled truth
are not called false positives: most of each film has no human truth.  A
separate reviewed-support diagnostic owns a prediction only when its temporal
midpoint falls inside one of the 125 original reviewed queue roots.  All other
predictions are explicitly ignored by that diagnostic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tapesift.research.segmentation_benchmark import (  # noqa: E402
    intersection_ms,
    match_segments,
    score_segments,
)
from tapesift.research.segmentation_sample_score import (  # noqa: E402
    LockedRoot,
    ResolvedItem,
    load_locked_roots,
    resolve_final_items,
)


EVALUATION_ID = "segmentation-iteration-4a-development-ab-v1"
BASELINE_BENCHMARK_ID = "segmentation-iteration-3a-independent-holdout-v1"
PRIMARY_IOU_THRESHOLD = 0.75
DIAGNOSTIC_IOU_THRESHOLD = 0.50
RELATIONSHIP_THRESHOLD = 0.50

BASE_DIR = ROOT / "research" / "segmentation_benchmark"
VERIFICATION_DIR = BASE_DIR / "verification"
BASELINE_REPORT_DIR = BASE_DIR / "reports" / "iteration_3a_holdout_v1"
DEFAULT_OUTPUT_DIR = (
    BASE_DIR / "reports" / "iteration_4a_development_ab_v1")

SELECTION = (
    VERIFICATION_DIR / "iteration_3a_independent_holdout_v1.selection.json")
BASELINE_PREDICTION_LOCK = BASELINE_REPORT_DIR / "prediction_lock.json"
QUEUE_LOCK = BASELINE_REPORT_DIR / "queue_lock.json"
LOCKED_QUEUE = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.queue.locked.json")
FINALIZATION_LOCK = BASELINE_REPORT_DIR / "finalization_lock_v2.json"
FINAL_STATE = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.final_v2.state.json")
FINAL_TRUTH = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.final_v2.verified.jsonl")
BASELINE_REPORT = BASELINE_REPORT_DIR / "sampled_results_v2.json"

SELECTION_SHA256 = (
    "fa30f6f9868baa04c20b1cf2ee852fb2690ad48c9ecc462254341d7801e7df28")
BASELINE_PREDICTION_LOCK_SHA256 = (
    "439767627dee5bc9f5fbfbe202953ec9c2d15ca19b60acc39e3ed72b4dfbd7aa")
QUEUE_LOCK_SHA256 = (
    "328e03459d176855b297bfcd98c071a7cc12b2a2b40bbbc7b54b174b052d22d7")
LOCKED_QUEUE_SHA256 = (
    "ce057e42b063e7d81f866ef3406f392db92f644e5febafab7ba87013e515538d")
FINALIZATION_LOCK_SHA256 = (
    "b9df6ca1f28d14ee8ed4fd1facddd22d273bee98d12a11d91ed4a9b5715875d9")
FINAL_STATE_SHA256 = (
    "c2300b2dbc7c57e66e1e8922a30b3a13accbd31f373e201d4b9b9ab2bf31ad46")
FINAL_TRUTH_SHA256 = (
    "ed6a1ceaa184e23cf0af96eb0583578e5e8a54f70ed2f653459f085148f5fc5e")
BASELINE_REPORT_SHA256 = (
    "77e96a9a01710f26a5cd6093f024c6107f9cbdee1b87a02ed4c0bdcda59b72e6")

DEVELOPMENT_WARNING = (
    "Development-only regression diagnostic. These five films and their "
    "reviewed truth have already been inspected. This result is not an "
    "independent holdout and is not eligible for a public performance or "
    "accuracy claim."
)
TIE_ABS_TOLERANCE = 1e-12
VALID_CANDIDATE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
VALID_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
VALID_GIT_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
CANDIDATE_LOCK_SCHEMA_VERSION = "1.0"
CANDIDATE_LOCK_STATUS = "candidate_predictions_frozen_before_scoring"
CANDIDATE_DETECTOR_SOURCE = "tapesift/services/play_detect_service.py"


class DevelopmentABError(ValueError):
    """Raised when an A/B result cannot be proven or scored safely."""


@dataclass(frozen=True)
class LockedArtifact:
    path: Path
    sha256: str


@dataclass(frozen=True)
class DevelopmentABSpec:
    root: Path
    selection: LockedArtifact
    baseline_prediction_lock: LockedArtifact
    queue_lock: LockedArtifact
    locked_queue: LockedArtifact
    finalization_lock: LockedArtifact
    final_state: LockedArtifact
    final_truth: LockedArtifact
    baseline_report: LockedArtifact
    expected_film_count: int = 5
    expected_root_count: int = 125
    expected_truth_count: int = 118
    expected_excluded_count: int = 6


@dataclass(frozen=True)
class FrozenDevelopmentData:
    film_specs: dict[str, dict[str, Any]]
    roots: dict[str, LockedRoot]
    final_items: tuple[ResolvedItem, ...]
    baseline_predictions: dict[str, dict[str, Any]]
    baseline_prediction_rows: tuple[dict[str, Any], ...]
    baseline_report: dict[str, Any]
    provenance: dict[str, Any]


@dataclass(frozen=True)
class CandidateLockData:
    path: Path
    sha256: str
    candidate_id: str
    detector: dict[str, Any]
    parameters: dict[str, Any]
    predictions: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ReportBundle:
    report: dict[str, Any]
    json_text: str
    markdown_text: str


def default_spec() -> DevelopmentABSpec:
    return DevelopmentABSpec(
        root=ROOT,
        selection=LockedArtifact(SELECTION, SELECTION_SHA256),
        baseline_prediction_lock=LockedArtifact(
            BASELINE_PREDICTION_LOCK, BASELINE_PREDICTION_LOCK_SHA256),
        queue_lock=LockedArtifact(QUEUE_LOCK, QUEUE_LOCK_SHA256),
        locked_queue=LockedArtifact(LOCKED_QUEUE, LOCKED_QUEUE_SHA256),
        finalization_lock=LockedArtifact(
            FINALIZATION_LOCK, FINALIZATION_LOCK_SHA256),
        final_state=LockedArtifact(FINAL_STATE, FINAL_STATE_SHA256),
        final_truth=LockedArtifact(FINAL_TRUTH, FINAL_TRUTH_SHA256),
        baseline_report=LockedArtifact(
            BASELINE_REPORT, BASELINE_REPORT_SHA256),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_text(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _canonical_sha256(value: Any, label: str) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DevelopmentABError(
            f"{label} is not canonical JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _require_sha256(value: Any, label: str) -> str:
    digest = str(value or "")
    if not VALID_SHA256.fullmatch(digest):
        raise DevelopmentABError(
            f"{label} must be a lowercase 64-character SHA256")
    return digest


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevelopmentABError(
            f"Cannot read JSON artifact {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DevelopmentABError(f"JSON artifact is not an object: {path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise DevelopmentABError(
                    f"{path}:{line_number} is not a JSON object")
            records.append(record)
    except (OSError, json.JSONDecodeError) as exc:
        raise DevelopmentABError(
            f"Cannot read JSONL artifact {path}: {exc}") from exc
    return records


def _require_hash(artifact: LockedArtifact, label: str) -> None:
    if not artifact.path.is_file():
        raise FileNotFoundError(f"{label} is missing: {artifact.path}")
    actual = sha256_file(artifact.path)
    if actual != artifact.sha256.lower():
        raise DevelopmentABError(
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
        raise DevelopmentABError(
            f"{label} escapes the repository root: {path}") from exc
    return path


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise DevelopmentABError(
            f"Frozen artifact is outside the repository: {path}") from exc


def _require_expected_reference(
    root: Path,
    reference: Any,
    expected: LockedArtifact,
    label: str,
) -> None:
    if not isinstance(reference, dict):
        raise DevelopmentABError(f"{label} is not an artifact reference")
    value = reference.get("file")
    digest = str(reference.get("sha256") or "").lower()
    if not isinstance(value, str) or not value:
        raise DevelopmentABError(f"{label} has no file")
    path = _resolve_inside(root, value, label)
    if path != expected.path.resolve() or digest != expected.sha256.lower():
        raise DevelopmentABError(
            f"{label} does not match the required frozen path and hash")


def _state_truth_record(
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


def _normalize_segment(
    payload: Any,
    *,
    film_id: str,
    kind: str,
    index: int,
) -> dict[str, Any]:
    context = f"{film_id} {kind}[{index}]"
    if not isinstance(payload, dict):
        raise DevelopmentABError(f"{context} is not an object")
    normalized = dict(payload)
    for key in ("start_ms", "end_ms"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise DevelopmentABError(
                f"{context} has non-integral {key}: {value!r}")
        normalized[key] = value
    normalized["_prediction_index"] = index
    return normalized


def _normalize_prediction_payload(
    payload: dict[str, Any],
    *,
    film_id: str,
    duration_ms: int,
) -> dict[str, Any]:
    if payload.get("film_id") != film_id:
        raise DevelopmentABError(
            f"Prediction payload film mismatch for {film_id}")
    value = payload.get("duration_ms")
    if isinstance(value, bool) or not isinstance(value, int):
        raise DevelopmentABError(
            f"Prediction payload for {film_id} has invalid duration_ms")
    if value != duration_ms:
        raise DevelopmentABError(
            f"Prediction duration mismatch for {film_id}: "
            f"{value} != {duration_ms}")
    plays = payload.get("plays")
    unclassified = payload.get("unclassified", [])
    if not isinstance(plays, list):
        raise DevelopmentABError(
            f"Prediction payload for {film_id} has no plays list")
    if not isinstance(unclassified, list):
        raise DevelopmentABError(
            f"Prediction payload for {film_id} has invalid unclassified list")
    return {
        **payload,
        "plays": [
            _normalize_segment(
                row, film_id=film_id, kind="play", index=index)
            for index, row in enumerate(plays)
        ],
        "unclassified": [
            _normalize_segment(
                row, film_id=film_id, kind="unclassified", index=index)
            for index, row in enumerate(unclassified)
        ],
    }


def _load_baseline_predictions(
    spec: DevelopmentABSpec,
    lock: dict[str, Any],
    film_specs: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], tuple[dict[str, Any], ...]]:
    if lock.get("schema_version") != "1.0" \
            or lock.get("queue_id") != BASELINE_BENCHMARK_ID \
            or lock.get("status") != "predictions_frozen_before_human_review":
        raise DevelopmentABError("Baseline prediction lock identity changed")
    _require_expected_reference(
        spec.root,
        lock.get("selection"),
        spec.selection,
        "baseline prediction-lock selection",
    )
    rows = lock.get("predictions")
    if not isinstance(rows, list) or len(rows) != len(film_specs):
        raise DevelopmentABError(
            "Baseline prediction lock film count changed")
    payloads: dict[str, dict[str, Any]] = {}
    public_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise DevelopmentABError("Baseline prediction row is invalid")
        film_id = str(row.get("film_id") or "")
        if film_id not in film_specs or film_id in payloads:
            raise DevelopmentABError(
                f"Unexpected or duplicate baseline film {film_id!r}")
        value = row.get("file")
        digest = str(row.get("sha256") or "").lower()
        if not isinstance(value, str) or not value:
            raise DevelopmentABError(
                f"Baseline prediction {film_id} has no file")
        path = _resolve_inside(
            spec.root, value, f"baseline prediction {film_id}")
        artifact = LockedArtifact(path, digest)
        _require_hash(artifact, f"baseline prediction {film_id}")
        payload = _normalize_prediction_payload(
            _read_json(path),
            film_id=film_id,
            duration_ms=int(film_specs[film_id]["duration_ms"]),
        )
        if len(payload["plays"]) != int(row.get("play_count") or 0) \
                or len(payload["unclassified"]) != int(
                    row.get("unclassified_count") or 0
                ):
            raise DevelopmentABError(
                f"Baseline prediction counts changed for {film_id}")
        payloads[film_id] = payload
        public_rows.append({
            "film_id": film_id,
            "file": _relative(spec.root, path),
            "sha256": digest,
            "duration_ms": int(row["duration_ms"]),
            "play_count": len(payload["plays"]),
            "unclassified_count": len(payload["unclassified"]),
        })
    if set(payloads) != set(film_specs):
        raise DevelopmentABError(
            "Baseline prediction lock does not cover every frozen film")
    return payloads, tuple(sorted(
        public_rows, key=lambda row: row["film_id"]))


def load_frozen_development_data(
    spec: DevelopmentABSpec,
) -> FrozenDevelopmentData:
    """Validate the pinned Iteration 3A v2 chain and load its population."""

    frozen_artifacts = (
        (spec.selection, "selection"),
        (spec.baseline_prediction_lock, "baseline prediction lock"),
        (spec.queue_lock, "queue lock"),
        (spec.locked_queue, "locked queue"),
        (spec.finalization_lock, "finalization lock v2"),
        (spec.final_state, "final state v2"),
        (spec.final_truth, "final truth v2"),
        (spec.baseline_report, "sampled results v2"),
    )
    for artifact, label in frozen_artifacts:
        _require_hash(artifact, label)

    selection = _read_json(spec.selection.path)
    if selection.get("schema_version") != "1.0" \
            or selection.get("queue_id") != BASELINE_BENCHMARK_ID \
            or selection.get("status") != "locked_before_detection":
        raise DevelopmentABError("Frozen selection identity changed")
    films = selection.get("films")
    if not isinstance(films, list) \
            or len(films) != spec.expected_film_count:
        raise DevelopmentABError("Frozen film count changed")
    film_specs: dict[str, dict[str, Any]] = {}
    for film in films:
        if not isinstance(film, dict):
            raise DevelopmentABError("Frozen film row is invalid")
        film_id = str(film.get("film_id") or "")
        if not film_id or film_id in film_specs:
            raise DevelopmentABError(
                f"Frozen film id is missing or duplicated: {film_id!r}")
        duration = film.get("duration_ms")
        if isinstance(duration, bool) or not isinstance(duration, int) \
                or duration <= 0:
            raise DevelopmentABError(
                f"Frozen film duration is invalid for {film_id}")
        film_specs[film_id] = film

    prediction_lock = _read_json(spec.baseline_prediction_lock.path)
    baseline_predictions, baseline_rows = _load_baseline_predictions(
        spec, prediction_lock, film_specs)

    queue_lock = _read_json(spec.queue_lock.path)
    if queue_lock.get("schema_version") != "1.0" \
            or queue_lock.get("queue_id") != BASELINE_BENCHMARK_ID \
            or queue_lock.get("status") != \
            "queue_frozen_before_human_review":
        raise DevelopmentABError("Frozen queue lock identity changed")
    _require_expected_reference(
        spec.root,
        queue_lock.get("prediction_lock"),
        spec.baseline_prediction_lock,
        "queue-lock prediction lock",
    )
    queue_section = queue_lock.get("queue")
    if not isinstance(queue_section, dict):
        raise DevelopmentABError("Frozen queue lock has no queue section")
    _require_expected_reference(
        spec.root,
        {
            "file": queue_section.get("locked_selection_file"),
            "sha256": queue_section.get("locked_selection_sha256"),
        },
        spec.locked_queue,
        "queue-lock locked queue",
    )

    locked_queue = _read_json(spec.locked_queue.path)
    if locked_queue.get("schema_version") != "1.0" \
            or locked_queue.get("queue_id") != BASELINE_BENCHMARK_ID:
        raise DevelopmentABError("Locked queue identity changed")
    root_payloads = locked_queue.get("items")
    if not isinstance(root_payloads, list):
        raise DevelopmentABError("Locked queue has no items list")
    roots = load_locked_roots(
        root_payloads, expected_root_count=spec.expected_root_count)
    if {root.film_id for root in roots.values()} != set(film_specs):
        raise DevelopmentABError("Locked roots do not cover the frozen films")

    finalization = _read_json(spec.finalization_lock.path)
    if finalization.get("schema_version") != "1.0" \
            or finalization.get("status") != "final":
        raise DevelopmentABError("Finalization lock is not final")
    outputs = finalization.get("outputs")
    if not isinstance(outputs, dict):
        raise DevelopmentABError("Finalization lock has no outputs")
    _require_expected_reference(
        spec.root, outputs.get("state"), spec.final_state,
        "finalization state output")
    _require_expected_reference(
        spec.root, outputs.get("truth"), spec.final_truth,
        "finalization truth output")

    state = _read_json(spec.final_state.path)
    if state.get("schema_version") != "1.0" \
            or state.get("queue_id") != BASELINE_BENCHMARK_ID:
        raise DevelopmentABError("Final state identity changed")
    state_items = state.get("items")
    if not isinstance(state_items, list) or not all(
        isinstance(item, dict) for item in state_items
    ):
        raise DevelopmentABError("Final state has no valid items list")
    truth_records = _read_jsonl(spec.final_truth.path)
    try:
        expected_truth_records = [
            _state_truth_record(item, BASELINE_BENCHMARK_ID)
            for item in state_items
        ]
    except KeyError as exc:
        raise DevelopmentABError(
            f"Final state item is missing required field {exc}") from exc
    if truth_records != expected_truth_records:
        raise DevelopmentABError(
            "Final state and final truth are not exact mirrors")

    final_items = tuple(resolve_final_items(state_items, roots))
    verified_count = sum(
        item.status == "verified" for item in final_items)
    excluded_count = sum(
        item.status == "excluded" for item in final_items)
    if verified_count != spec.expected_truth_count \
            or excluded_count != spec.expected_excluded_count:
        raise DevelopmentABError(
            "Frozen terminal truth counts changed: "
            f"{verified_count} verified, {excluded_count} excluded")

    baseline_report = _read_json(spec.baseline_report.path)
    if baseline_report.get("schema_version") != "2.0" \
            or baseline_report.get("benchmark_id") != BASELINE_BENCHMARK_ID \
            or baseline_report.get("report_status") != "complete":
        raise DevelopmentABError("Frozen v2 report identity changed")
    provenance = baseline_report.get("provenance")
    if not isinstance(provenance, dict):
        raise DevelopmentABError("Frozen v2 report has no provenance")
    for key, expected in (
        ("selection", spec.selection),
        ("prediction_lock", spec.baseline_prediction_lock),
        ("queue_lock", spec.queue_lock),
        ("finalization_lock", spec.finalization_lock),
    ):
        _require_expected_reference(
            spec.root, provenance.get(key), expected,
            f"v2 report {key}")

    return FrozenDevelopmentData(
        film_specs=film_specs,
        roots=roots,
        final_items=final_items,
        baseline_predictions=baseline_predictions,
        baseline_prediction_rows=baseline_rows,
        baseline_report=baseline_report,
        provenance={
            label: {
                "file": _relative(spec.root, artifact.path),
                "sha256": artifact.sha256,
            }
            for artifact, label in frozen_artifacts
        },
    )


def _verify_candidate_source_commit(
    root: Path,
    source_file: str,
    source_sha256: str,
    source_commit: Any,
    source_commit_reason: Any,
) -> tuple[str | None, str]:
    if source_commit is None:
        reason = str(source_commit_reason or "").strip()
        if not reason:
            raise DevelopmentABError(
                "Candidate detector source_commit is null without a "
                "source_commit_reason")
        return None, reason

    commit = str(source_commit)
    if not VALID_GIT_COMMIT.fullmatch(commit):
        raise DevelopmentABError(
            "Candidate detector source_commit must be a full lowercase "
            "40-character Git commit or null")
    try:
        completed = subprocess.run(
            ["git", "show", f"{commit}:{source_file}"],
            cwd=root,
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise DevelopmentABError(
            f"Cannot validate candidate detector source_commit: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode(
            "utf-8", errors="replace").strip()
        raise DevelopmentABError(
            "Candidate detector source_commit cannot be resolved"
            + (f": {detail}" if detail else ""))
    commit_digest = hashlib.sha256(completed.stdout).hexdigest()
    if commit_digest != source_sha256:
        raise DevelopmentABError(
            "Candidate detector source_commit does not contain the locked "
            "detector source SHA256")
    return commit, ""


def _load_candidate_lock(
    spec: DevelopmentABSpec,
    candidate_lock_path: Path,
    candidate_id: str,
    film_specs: dict[str, dict[str, Any]],
) -> CandidateLockData:
    candidate_lock_path = candidate_lock_path.resolve()
    if not candidate_lock_path.is_file():
        raise FileNotFoundError(
            f"Candidate lock is missing: {candidate_lock_path}")
    lock = _read_json(candidate_lock_path)
    if lock.get("schema_version") != CANDIDATE_LOCK_SCHEMA_VERSION \
            or lock.get("status") != CANDIDATE_LOCK_STATUS:
        raise DevelopmentABError(
            "Candidate lock schema_version/status is invalid")
    if lock.get("candidate_id") != candidate_id:
        raise DevelopmentABError(
            "Candidate lock candidate_id does not match --candidate-id")

    detector = lock.get("detector")
    if not isinstance(detector, dict):
        raise DevelopmentABError(
            "Candidate lock has no detector object")
    source_file = detector.get("source_file")
    if source_file != CANDIDATE_DETECTOR_SOURCE:
        raise DevelopmentABError(
            "Candidate lock detector source_file is not the shipping "
            f"detector {CANDIDATE_DETECTOR_SOURCE}")
    source_path = _resolve_inside(
        spec.root, source_file, "candidate detector source")
    source_sha256 = _require_sha256(
        detector.get("source_sha256"),
        "Candidate detector source_sha256",
    )
    _require_hash(
        LockedArtifact(source_path, source_sha256),
        "candidate detector source",
    )
    if "source_commit" not in detector:
        raise DevelopmentABError(
            "Candidate detector source_commit is required; use null plus "
            "source_commit_reason when the source is uncommitted")
    source_commit, source_commit_reason = _verify_candidate_source_commit(
        spec.root,
        source_file,
        source_sha256,
        detector.get("source_commit"),
        detector.get("source_commit_reason"),
    )

    configuration = detector.get("configuration")
    if not isinstance(configuration, dict):
        raise DevelopmentABError(
            "Candidate detector has no configuration identity")
    configuration_id = configuration.get("id")
    if not isinstance(configuration_id, str) \
            or not VALID_CANDIDATE_ID.fullmatch(configuration_id):
        raise DevelopmentABError(
            "Candidate detector configuration id must use the same stable "
            "lowercase format as candidate-id")
    parameters = configuration.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise DevelopmentABError(
            "Candidate detector configuration parameters must be a "
            "non-empty object")
    configuration_sha256 = _require_sha256(
        configuration.get("sha256"),
        "Candidate detector configuration sha256",
    )
    expected_configuration_sha256 = _canonical_sha256(
        {"id": configuration_id, "parameters": parameters},
        "Candidate detector configuration",
    )
    if configuration_sha256 != expected_configuration_sha256:
        raise DevelopmentABError(
            "Candidate detector configuration sha256 does not match its "
            "id and parameters")

    rows = lock.get("predictions")
    if not isinstance(rows, list) or len(rows) != len(film_specs):
        raise DevelopmentABError(
            "Candidate lock prediction count does not match the frozen "
            "film count")
    row_film_ids = [
        str(row.get("film_id") or "") if isinstance(row, dict) else ""
        for row in rows
    ]
    if row_film_ids != sorted(film_specs):
        raise DevelopmentABError(
            "Candidate lock predictions must be in deterministic film_id "
            "order")
    predictions: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise DevelopmentABError(
                "Candidate lock prediction row is not an object")
        film_id = str(row.get("film_id") or "")
        if film_id not in film_specs or film_id in predictions:
            raise DevelopmentABError(
                f"Unexpected or duplicate candidate lock film {film_id!r}")
        expected_file = f"{film_id}.json"
        if row.get("file") != expected_file:
            raise DevelopmentABError(
                f"Candidate lock prediction {film_id} file must be "
                f"{expected_file!r}")
        duration_ms = row.get("duration_ms")
        play_count = row.get("play_count")
        unclassified_count = row.get("unclassified_count")
        for value, field in (
            (duration_ms, "duration_ms"),
            (play_count, "play_count"),
            (unclassified_count, "unclassified_count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) \
                    or value < 0:
                raise DevelopmentABError(
                    f"Candidate lock prediction {film_id} has invalid "
                    f"{field}")
        if duration_ms != int(film_specs[film_id]["duration_ms"]):
            raise DevelopmentABError(
                f"Candidate lock prediction duration changed for {film_id}")
        predictions[film_id] = {
            "film_id": film_id,
            "file": expected_file,
            "sha256": _require_sha256(
                row.get("sha256"),
                f"Candidate lock prediction {film_id} sha256",
            ),
            "duration_ms": duration_ms,
            "play_count": play_count,
            "unclassified_count": unclassified_count,
        }
    if set(predictions) != set(film_specs):
        raise DevelopmentABError(
            "Candidate lock does not cover every frozen film")

    return CandidateLockData(
        path=candidate_lock_path,
        sha256=sha256_file(candidate_lock_path),
        candidate_id=candidate_id,
        detector={
            "source_file": source_file,
            "source_sha256": source_sha256,
            "source_commit": source_commit,
            "source_commit_reason": source_commit_reason,
            "configuration": {
                "id": configuration_id,
                "parameters": parameters,
                "sha256": configuration_sha256,
            },
        },
        parameters=dict(parameters),
        predictions=predictions,
    )


def _load_candidate_predictions(
    candidate_dir: Path,
    film_specs: dict[str, dict[str, Any]],
    candidate_lock: CandidateLockData,
) -> tuple[
    dict[str, dict[str, Any]],
    tuple[dict[str, Any], ...],
]:
    candidate_dir = candidate_dir.resolve()
    if not candidate_dir.is_dir():
        raise FileNotFoundError(
            f"Candidate prediction directory is missing: {candidate_dir}")
    expected_names = {f"{film_id}.json" for film_id in film_specs}
    actual_names = {path.name for path in candidate_dir.glob("*.json")}
    missing = sorted(expected_names - actual_names)
    extra = sorted(actual_names - expected_names)
    if missing or extra:
        raise DevelopmentABError(
            "Candidate prediction file set changed; "
            f"missing={missing}, extra={extra}")

    payloads: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for film_id in sorted(film_specs):
        locked = candidate_lock.predictions[film_id]
        path = candidate_dir / locked["file"]
        _require_hash(
            LockedArtifact(path, locked["sha256"]),
            f"candidate prediction {film_id}",
        )
        payload = _normalize_prediction_payload(
            _read_json(path),
            film_id=film_id,
            duration_ms=int(film_specs[film_id]["duration_ms"]),
        )
        payload_parameters = payload.get("parameters")
        if not isinstance(payload_parameters, dict) \
                or _canonical_sha256(
                    payload_parameters,
                    f"Candidate prediction {film_id} parameters",
                ) != _canonical_sha256(
                    candidate_lock.parameters,
                    "Candidate detector locked parameters",
                ):
            raise DevelopmentABError(
                f"Candidate prediction parameters do not match the locked "
                f"configuration for {film_id}")
        if len(payload["plays"]) != locked["play_count"] \
                or len(payload["unclassified"]) != \
                locked["unclassified_count"]:
            raise DevelopmentABError(
                f"Candidate prediction counts do not match the lock for "
                f"{film_id}")
        payloads[film_id] = payload
        rows.append({
            "film_id": film_id,
            "file": locked["file"],
            "sha256": locked["sha256"],
            "duration_ms": int(payload["duration_ms"]),
            "play_count": len(payload["plays"]),
            "unclassified_count": len(payload["unclassified"]),
        })
    return payloads, tuple(rows)


def _valid_plays(
    payloads: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    valid: dict[str, list[dict[str, Any]]] = {}
    for film_id, payload in payloads.items():
        duration = int(payload["duration_ms"])
        valid[film_id] = [
            segment
            for segment in payload["plays"]
            if 0 <= int(segment["start_ms"])
            < int(segment["end_ms"]) <= duration
        ]
    return valid


def _overlap_pair_count(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    same_collection: bool,
) -> int:
    count = 0
    for left_index, left_segment in enumerate(left):
        right_start = left_index + 1 if same_collection else 0
        for right_segment in right[right_start:]:
            if intersection_ms(left_segment, right_segment) > 0:
                count += 1
    return count


def _structural_diagnostics(
    payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    by_film: dict[str, dict[str, Any]] = {}
    for film_id in sorted(payloads):
        payload = payloads[film_id]
        duration = int(payload["duration_ms"])
        plays = payload["plays"]
        unclassified = payload["unclassified"]
        invalid_plays = [
            row for row in plays
            if not (
                0 <= int(row["start_ms"])
                < int(row["end_ms"]) <= duration
            )
        ]
        invalid_unclassified = [
            row for row in unclassified
            if not (
                0 <= int(row["start_ms"])
                < int(row["end_ms"]) <= duration
            )
        ]
        positive_plays = [
            row for row in plays
            if int(row["end_ms"]) > int(row["start_ms"])]
        positive_unclassified = [
            row for row in unclassified
            if int(row["end_ms"]) > int(row["start_ms"])]
        play_play = _overlap_pair_count(
            positive_plays, positive_plays, same_collection=True)
        unclassified_unclassified = _overlap_pair_count(
            positive_unclassified,
            positive_unclassified,
            same_collection=True,
        )
        cross = _overlap_pair_count(
            positive_plays,
            positive_unclassified,
            same_collection=False,
        )
        by_film[film_id] = {
            "duration_ms": duration,
            "play_count": len(plays),
            "unclassified_count": len(unclassified),
            "invalid_play_count": len(invalid_plays),
            "invalid_unclassified_count": len(invalid_unclassified),
            "out_of_bounds_count": (
                len(invalid_plays) + len(invalid_unclassified)),
            "play_play_overlap_pair_count": play_play,
            "unclassified_unclassified_overlap_pair_count": (
                unclassified_unclassified),
            "play_unclassified_overlap_pair_count": cross,
            "overlap_pair_count": (
                play_play + unclassified_unclassified + cross),
        }
    return {
        "scope": (
            "all full-film candidate outputs, including predictions outside "
            "the reviewed support"
        ),
        "overlap_counting": (
            "Every positive-duration overlapping pair is counted once; "
            "invalid intervals are counted once as out-of-bounds."
        ),
        "play_count": sum(row["play_count"] for row in by_film.values()),
        "unclassified_count": sum(
            row["unclassified_count"] for row in by_film.values()),
        "invalid_play_count": sum(
            row["invalid_play_count"] for row in by_film.values()),
        "invalid_unclassified_count": sum(
            row["invalid_unclassified_count"] for row in by_film.values()),
        "out_of_bounds_count": sum(
            row["out_of_bounds_count"] for row in by_film.values()),
        "play_play_overlap_pair_count": sum(
            row["play_play_overlap_pair_count"]
            for row in by_film.values()),
        "unclassified_unclassified_overlap_pair_count": sum(
            row["unclassified_unclassified_overlap_pair_count"]
            for row in by_film.values()),
        "play_unclassified_overlap_pair_count": sum(
            row["play_unclassified_overlap_pair_count"]
            for row in by_film.values()),
        "overlap_pair_count": sum(
            row["overlap_pair_count"] for row in by_film.values()),
        "by_film": by_film,
    }


def _require_structural_integrity(
    diagnostics: dict[str, Any],
    label: str,
) -> None:
    out_of_bounds = int(diagnostics["out_of_bounds_count"])
    overlaps = int(diagnostics["overlap_pair_count"])
    if out_of_bounds or overlaps:
        affected = [
            film_id
            for film_id, row in diagnostics["by_film"].items()
            if int(row["out_of_bounds_count"])
            or int(row["overlap_pair_count"])
        ]
        raise DevelopmentABError(
            f"{label} structural integrity failed before scoring: "
            f"out_of_bounds_count={out_of_bounds}, "
            f"overlap_pair_count={overlaps}, "
            f"affected_films={affected}"
        )


def _truth_by_film(
    items: Iterable[ResolvedItem],
) -> dict[str, list[ResolvedItem]]:
    grouped: dict[str, list[ResolvedItem]] = defaultdict(list)
    for item in items:
        if item.status == "verified":
            grouped[item.film_id].append(item)
    for film_items in grouped.values():
        film_items.sort(
            key=lambda item: (item.start_ms, item.end_ms, item.item_id))
    return dict(grouped)


def _truth_segment(item: ResolvedItem) -> dict[str, int]:
    return {"start_ms": item.start_ms, "end_ms": item.end_ms}


def _truth_recovery_at_threshold(
    truth_by_film: dict[str, list[ResolvedItem]],
    plays_by_film: dict[str, list[dict[str, Any]]],
    threshold: float,
) -> dict[str, Any]:
    by_film: dict[str, dict[str, Any]] = {}
    all_matches: list[dict[str, Any]] = []
    for film_id in sorted(truth_by_film):
        truth_items = truth_by_film[film_id]
        predictions = plays_by_film.get(film_id, [])
        raw = score_segments(
            [_truth_segment(item) for item in truth_items],
            predictions,
            iou_threshold=threshold,
            relationship_threshold=RELATIONSHIP_THRESHOLD,
        )
        matches = [
            {
                "film_id": film_id,
                "truth_item_id": truth_items[
                    row["truth_index"]].item_id,
                "prediction_index": int(
                    predictions[row["prediction_index"]][
                        "_prediction_index"]),
                "iou": row["iou"],
            }
            for row in raw["matches"]
        ]
        all_matches.extend(matches)
        truth_count = len(truth_items)
        matched_count = len(matches)
        by_film[film_id] = {
            "truth_count": truth_count,
            "full_film_valid_prediction_count": len(predictions),
            "matched_truth_count": matched_count,
            "unrecovered_truth_count": truth_count - matched_count,
            "truth_recovery_rate": (
                matched_count / truth_count if truth_count else None),
            "matches": matches,
        }
    truth_count = sum(row["truth_count"] for row in by_film.values())
    matched_count = sum(
        row["matched_truth_count"] for row in by_film.values())
    return {
        "iou_threshold": threshold,
        "matching": "greedy_descending_iou_one_to_one_per_film",
        "truth_count": truth_count,
        "matched_truth_count": matched_count,
        "unrecovered_truth_count": truth_count - matched_count,
        "truth_recovery_rate": (
            matched_count / truth_count if truth_count else None),
        "unmatched_prediction_policy": (
            "ignored; unsampled full-film predictions are not false "
            "positives"
        ),
        "matches": all_matches,
        "by_film": by_film,
    }


def _threshold_comparison(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    by_film: dict[str, Any] = {}
    for film_id in sorted(baseline["by_film"]):
        left = baseline["by_film"][film_id]
        right = candidate["by_film"][film_id]
        by_film[film_id] = {
            "truth_count": left["truth_count"],
            "baseline_matched_truth_count": left["matched_truth_count"],
            "candidate_matched_truth_count": right["matched_truth_count"],
            "matched_truth_count_delta": (
                right["matched_truth_count"] - left["matched_truth_count"]),
            "baseline_truth_recovery_rate": left["truth_recovery_rate"],
            "candidate_truth_recovery_rate": right["truth_recovery_rate"],
            "truth_recovery_rate_delta": (
                right["truth_recovery_rate"] - left["truth_recovery_rate"]),
        }
    return {
        "iou_threshold": baseline["iou_threshold"],
        "baseline": baseline,
        "candidate": candidate,
        "delta": {
            "matched_truth_count": (
                candidate["matched_truth_count"]
                - baseline["matched_truth_count"]),
            "unrecovered_truth_count": (
                candidate["unrecovered_truth_count"]
                - baseline["unrecovered_truth_count"]),
            "truth_recovery_rate": (
                candidate["truth_recovery_rate"]
                - baseline["truth_recovery_rate"]),
        },
        "by_film_delta": by_film,
    }


def _assigned_iou_by_truth(
    truth_by_film: dict[str, list[ResolvedItem]],
    plays_by_film: dict[str, list[dict[str, Any]]],
) -> dict[str, tuple[float, int | None]]:
    assigned: dict[str, tuple[float, int | None]] = {}
    for film_id in sorted(truth_by_film):
        truth_items = truth_by_film[film_id]
        predictions = plays_by_film.get(film_id, [])
        rows = match_segments(
            [_truth_segment(item) for item in truth_items],
            predictions,
            iou_threshold=0.0,
        )
        matched = {
            truth_index: (
                iou,
                int(predictions[prediction_index][
                    "_prediction_index"]),
            )
            for truth_index, prediction_index, iou in rows
        }
        for truth_index, item in enumerate(truth_items):
            assigned[item.item_id] = matched.get(
                truth_index, (0.0, None))
    return assigned


def _assigned_iou_comparison(
    truth_by_film: dict[str, list[ResolvedItem]],
    baseline_plays: dict[str, list[dict[str, Any]]],
    candidate_plays: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    baseline = _assigned_iou_by_truth(truth_by_film, baseline_plays)
    candidate = _assigned_iou_by_truth(truth_by_film, candidate_plays)
    rows: list[dict[str, Any]] = []
    by_film_counts: dict[str, dict[str, int]] = {}
    for film_id in sorted(truth_by_film):
        counts = {"win": 0, "tie": 0, "loss": 0}
        for item in truth_by_film[film_id]:
            baseline_iou, baseline_index = baseline[item.item_id]
            candidate_iou, candidate_index = candidate[item.item_id]
            delta = candidate_iou - baseline_iou
            if math.isclose(
                candidate_iou,
                baseline_iou,
                rel_tol=0.0,
                abs_tol=TIE_ABS_TOLERANCE,
            ):
                outcome = "tie"
            elif delta > 0:
                outcome = "win"
            else:
                outcome = "loss"
            counts[outcome] += 1
            rows.append({
                "film_id": film_id,
                "truth_item_id": item.item_id,
                "baseline_prediction_index": baseline_index,
                "candidate_prediction_index": candidate_index,
                "baseline_assigned_iou": baseline_iou,
                "candidate_assigned_iou": candidate_iou,
                "assigned_iou_delta": delta,
                "outcome": outcome,
            })
        by_film_counts[film_id] = counts
    baseline_values = [row["baseline_assigned_iou"] for row in rows]
    candidate_values = [row["candidate_assigned_iou"] for row in rows]
    counts = {
        outcome: sum(
            row[outcome] for row in by_film_counts.values())
        for outcome in ("win", "tie", "loss")
    }
    return {
        "assignment": (
            "greedy descending-IoU one-to-one per film at threshold 0; "
            "unassigned truths receive IoU 0"
        ),
        "tie_absolute_tolerance": TIE_ABS_TOLERANCE,
        "truth_count": len(rows),
        "wins": counts["win"],
        "ties": counts["tie"],
        "losses": counts["loss"],
        "baseline_mean_assigned_iou": (
            statistics.fmean(baseline_values) if baseline_values else None),
        "candidate_mean_assigned_iou": (
            statistics.fmean(candidate_values) if candidate_values else None),
        "mean_assigned_iou_delta": (
            statistics.fmean(candidate_values)
            - statistics.fmean(baseline_values)
            if baseline_values else None
        ),
        "baseline_median_assigned_iou": (
            statistics.median(baseline_values)
            if baseline_values else None),
        "candidate_median_assigned_iou": (
            statistics.median(candidate_values)
            if candidate_values else None),
        "by_film": by_film_counts,
        "per_truth": rows,
    }


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


def _lineage_components(
    roots: dict[str, LockedRoot],
    final_items: Iterable[ResolvedItem],
) -> tuple[
    dict[str, str],
    dict[str, list[ResolvedItem]],
]:
    items = list(final_items)
    union_find = _UnionFind(roots)
    for item in items:
        item_roots = sorted(item.root_ids)
        for root_id in item_roots[1:]:
            union_find.union(item_roots[0], root_id)
    members: dict[str, list[str]] = defaultdict(list)
    for root_id in roots:
        members[union_find.find(root_id)].append(root_id)
    canonical = {
        root_id: min(members[union_find.find(root_id)])
        for root_id in roots
    }
    truth_by_component: dict[str, list[ResolvedItem]] = defaultdict(list)
    for item in items:
        if item.status != "verified":
            continue
        component = canonical[next(iter(item.root_ids))]
        truth_by_component[component].append(item)
    for component_items in truth_by_component.values():
        component_items.sort(
            key=lambda item: (item.start_ms, item.end_ms, item.item_id))
    return canonical, dict(truth_by_component)


def _midpoint_owned_root(
    prediction: dict[str, Any],
    film_roots: list[LockedRoot],
) -> LockedRoot | None:
    """Return the half-open root containing the exact temporal midpoint."""

    midpoint_twice = (
        int(prediction["start_ms"]) + int(prediction["end_ms"]))
    for root in film_roots:
        if 2 * root.start_ms <= midpoint_twice < 2 * root.end_ms:
            return root
    return None


def _reviewed_support_at_threshold(
    roots: dict[str, LockedRoot],
    final_items: tuple[ResolvedItem, ...],
    payloads: dict[str, dict[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    root_component, truth_by_component = _lineage_components(
        roots, final_items)
    roots_by_film: dict[str, list[LockedRoot]] = defaultdict(list)
    for root in roots.values():
        roots_by_film[root.film_id].append(root)
    for film_roots in roots_by_film.values():
        film_roots.sort(
            key=lambda root: (root.start_ms, root.end_ms, root.root_id))

    owned_by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)
    owned_by_film: dict[str, int] = defaultdict(int)
    outside_by_film: dict[str, int] = defaultdict(int)
    invalid_by_film: dict[str, int] = defaultdict(int)
    for film_id in sorted(payloads):
        duration = int(payloads[film_id]["duration_ms"])
        for prediction in payloads[film_id]["plays"]:
            if not (
                0 <= int(prediction["start_ms"])
                < int(prediction["end_ms"]) <= duration
            ):
                invalid_by_film[film_id] += 1
                continue
            root = _midpoint_owned_root(
                prediction, roots_by_film.get(film_id, []))
            if root is None:
                outside_by_film[film_id] += 1
                continue
            component = root_component[root.root_id]
            owned_by_component[component].append(prediction)
            owned_by_film[film_id] += 1

    component_ids = sorted(set(root_component.values()))
    by_film_counts: dict[str, dict[str, int]] = {
        film_id: {
            "truth_count": 0,
            "owned_prediction_count": owned_by_film[film_id],
            "matched_count": 0,
            "ignored_outside_support_count": outside_by_film[film_id],
            "ignored_invalid_prediction_count": invalid_by_film[film_id],
        }
        for film_id in sorted(payloads)
    }
    matches: list[dict[str, Any]] = []
    for component in component_ids:
        component_root = roots[component]
        truth_items = truth_by_component.get(component, [])
        predictions = sorted(
            owned_by_component.get(component, []),
            key=lambda row: (
                int(row["start_ms"]),
                int(row["end_ms"]),
                int(row["_prediction_index"]),
            ),
        )
        raw = score_segments(
            [_truth_segment(item) for item in truth_items],
            predictions,
            iou_threshold=threshold,
            relationship_threshold=RELATIONSHIP_THRESHOLD,
        )
        film_row = by_film_counts[component_root.film_id]
        film_row["truth_count"] += len(truth_items)
        film_row["matched_count"] += int(raw["matched_count"])
        for match in raw["matches"]:
            matches.append({
                "film_id": component_root.film_id,
                "component_id": component,
                "truth_item_id": truth_items[
                    match["truth_index"]].item_id,
                "prediction_index": int(
                    predictions[match["prediction_index"]][
                        "_prediction_index"]),
                "iou": match["iou"],
            })

    for row in by_film_counts.values():
        row["unmatched_owned_prediction_count"] = (
            row["owned_prediction_count"] - row["matched_count"])
        row["unrecovered_truth_count"] = (
            row["truth_count"] - row["matched_count"])
        row["candidate_iou_pass_rate"] = (
            row["matched_count"] / row["owned_prediction_count"]
            if row["owned_prediction_count"] else None
        )
        row["truth_recovery_rate"] = (
            row["matched_count"] / row["truth_count"]
            if row["truth_count"] else None
        )

    truth_count = sum(
        row["truth_count"] for row in by_film_counts.values())
    owned_count = sum(
        row["owned_prediction_count"] for row in by_film_counts.values())
    matched_count = sum(
        row["matched_count"] for row in by_film_counts.values())
    return {
        "iou_threshold": threshold,
        "support_definition": (
            "A valid prediction is owned when its exact temporal midpoint "
            "falls in an original locked root [start_ms, end_ms). It is then "
            "scored only in that root's frozen human-lineage component."
        ),
        "support_root_count": len(roots),
        "support_duration_ms": sum(
            root.end_ms - root.start_ms for root in roots.values()),
        "truth_count": truth_count,
        "owned_prediction_count": owned_count,
        "matched_count": matched_count,
        "unmatched_owned_prediction_count": owned_count - matched_count,
        "unrecovered_truth_count": truth_count - matched_count,
        "candidate_iou_pass_rate": (
            matched_count / owned_count if owned_count else None),
        "truth_recovery_rate": (
            matched_count / truth_count if truth_count else None),
        "ignored_outside_support_count": sum(
            outside_by_film.values()),
        "ignored_invalid_prediction_count": sum(
            invalid_by_film.values()),
        "outside_prediction_policy": (
            "ignored, never counted as a false positive"),
        "interpretation": (
            "Local baseline-conditioned reviewed-support diagnostic; not "
            "film-wide precision, recall, F1, or accuracy."
        ),
        "matches": sorted(
            matches,
            key=lambda row: (
                row["film_id"],
                row["truth_item_id"],
                row["prediction_index"],
            ),
        ),
        "by_film": by_film_counts,
    }


def _reviewed_support_comparison(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "iou_threshold": baseline["iou_threshold"],
        "baseline": baseline,
        "candidate": candidate,
        "delta": {
            "owned_prediction_count": (
                candidate["owned_prediction_count"]
                - baseline["owned_prediction_count"]),
            "matched_count": (
                candidate["matched_count"] - baseline["matched_count"]),
            "unmatched_owned_prediction_count": (
                candidate["unmatched_owned_prediction_count"]
                - baseline["unmatched_owned_prediction_count"]),
            "candidate_iou_pass_rate": (
                candidate["candidate_iou_pass_rate"]
                - baseline["candidate_iou_pass_rate"]
                if candidate["candidate_iou_pass_rate"] is not None
                and baseline["candidate_iou_pass_rate"] is not None
                else None
            ),
        },
    }


def _match_key(row: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(row["film_id"]),
        int(row["prediction_index"]),
        str(row["truth_item_id"]),
    )


def _validate_baseline_reproduction(
    frozen: FrozenDevelopmentData,
    baseline_thresholds: dict[str, dict[str, Any]],
    baseline_support: dict[str, dict[str, Any]],
) -> None:
    results = frozen.baseline_report.get("results")
    if not isinstance(results, dict):
        raise DevelopmentABError("Frozen v2 report has no results")
    aggregate = results.get("aggregate")
    by_film = results.get("by_film")
    if not isinstance(aggregate, dict) or not isinstance(by_film, dict):
        raise DevelopmentABError(
            "Frozen v2 report aggregate/by-film results are invalid")
    for key, report_key in (
        ("iou_0_75", "primary"),
        ("iou_0_50", "diagnostic"),
    ):
        recomputed = baseline_thresholds[key]
        historical = aggregate.get(report_key)
        if not isinstance(historical, dict):
            raise DevelopmentABError(
                f"Frozen v2 report has no {report_key} result")
        if int(historical.get("truth_count", -1)) != \
                recomputed["truth_count"] \
                or int(historical.get("matched_count", -1)) != \
                recomputed["matched_truth_count"]:
            raise DevelopmentABError(
                f"Baseline truth recovery no longer reproduces v2 {key}")
        historical_matches = {
            (
                str(row["prediction"]["film_id"]),
                int(row["prediction"]["prediction_index"]),
                str(row["truth_item_id"]),
            )
            for row in historical.get("matches", [])
        }
        recomputed_matches = {
            _match_key(row) for row in recomputed["matches"]}
        if historical_matches != recomputed_matches:
            raise DevelopmentABError(
                f"Baseline match identities no longer reproduce v2 {key}")
        support = baseline_support[key]
        if support["owned_prediction_count"] != int(
            historical.get("prediction_count", -1)
        ) or support["matched_count"] != int(
            historical.get("matched_count", -1)
        ):
            raise DevelopmentABError(
                f"Midpoint support no longer reproduces v2 {key}")
        for film_id, row in recomputed["by_film"].items():
            historical_film = by_film.get(film_id, {}).get(report_key, {})
            if row["matched_truth_count"] != int(
                historical_film.get("matched_count", -1)
            ) or row["truth_count"] != int(
                historical_film.get("truth_count", -1)
            ):
                raise DevelopmentABError(
                    f"Baseline film result no longer reproduces v2: "
                    f"{film_id} {key}")


def _artifact_rows(
    spec: DevelopmentABSpec,
) -> dict[str, dict[str, str]]:
    return {
        "selection": {
            "file": _relative(spec.root, spec.selection.path),
            "sha256": spec.selection.sha256,
        },
        "baseline_prediction_lock": {
            "file": _relative(
                spec.root, spec.baseline_prediction_lock.path),
            "sha256": spec.baseline_prediction_lock.sha256,
        },
        "queue_lock": {
            "file": _relative(spec.root, spec.queue_lock.path),
            "sha256": spec.queue_lock.sha256,
        },
        "locked_queue": {
            "file": _relative(spec.root, spec.locked_queue.path),
            "sha256": spec.locked_queue.sha256,
        },
        "finalization_lock_v2": {
            "file": _relative(spec.root, spec.finalization_lock.path),
            "sha256": spec.finalization_lock.sha256,
        },
        "final_state_v2": {
            "file": _relative(spec.root, spec.final_state.path),
            "sha256": spec.final_state.sha256,
        },
        "final_truth_v2": {
            "file": _relative(spec.root, spec.final_truth.path),
            "sha256": spec.final_truth.sha256,
        },
        "sampled_results_v2": {
            "file": _relative(spec.root, spec.baseline_report.path),
            "sha256": spec.baseline_report.sha256,
        },
    }


def _candidate_lock_reference(
    spec: DevelopmentABSpec,
    candidate_lock: CandidateLockData,
) -> dict[str, str]:
    try:
        file_value = _relative(spec.root, candidate_lock.path)
        path_scope = "repository"
    except DevelopmentABError:
        file_value = candidate_lock.path.name
        path_scope = "external_input_filename"
    return {
        "file": file_value,
        "sha256": candidate_lock.sha256,
        "path_scope": path_scope,
    }


def build_report(
    spec: DevelopmentABSpec,
    candidate_dir: Path,
    candidate_lock_path: Path,
    candidate_id: str,
) -> ReportBundle:
    """Build a deterministic A/B report in memory without writing files."""

    if not VALID_CANDIDATE_ID.fullmatch(candidate_id):
        raise DevelopmentABError(
            "candidate-id must be 1-64 lowercase letters, digits, dots, "
            "underscores, or hyphens and must start with a letter or digit")

    frozen = load_frozen_development_data(spec)
    candidate_lock = _load_candidate_lock(
        spec,
        candidate_lock_path,
        candidate_id,
        frozen.film_specs,
    )
    candidate_predictions, candidate_rows = _load_candidate_predictions(
        candidate_dir, frozen.film_specs, candidate_lock)
    baseline_structural = _structural_diagnostics(
        frozen.baseline_predictions)
    candidate_structural = _structural_diagnostics(candidate_predictions)
    _require_structural_integrity(
        baseline_structural, "Pinned baseline")
    _require_structural_integrity(
        candidate_structural, "Candidate")

    baseline_plays = _valid_plays(frozen.baseline_predictions)
    candidate_plays = _valid_plays(candidate_predictions)
    truth_by_film = _truth_by_film(frozen.final_items)

    baseline_thresholds = {
        "iou_0_75": _truth_recovery_at_threshold(
            truth_by_film, baseline_plays, PRIMARY_IOU_THRESHOLD),
        "iou_0_50": _truth_recovery_at_threshold(
            truth_by_film, baseline_plays, DIAGNOSTIC_IOU_THRESHOLD),
    }
    candidate_thresholds = {
        "iou_0_75": _truth_recovery_at_threshold(
            truth_by_film, candidate_plays, PRIMARY_IOU_THRESHOLD),
        "iou_0_50": _truth_recovery_at_threshold(
            truth_by_film, candidate_plays, DIAGNOSTIC_IOU_THRESHOLD),
    }
    baseline_support = {
        "iou_0_75": _reviewed_support_at_threshold(
            frozen.roots,
            frozen.final_items,
            frozen.baseline_predictions,
            PRIMARY_IOU_THRESHOLD,
        ),
        "iou_0_50": _reviewed_support_at_threshold(
            frozen.roots,
            frozen.final_items,
            frozen.baseline_predictions,
            DIAGNOSTIC_IOU_THRESHOLD,
        ),
    }
    candidate_support = {
        "iou_0_75": _reviewed_support_at_threshold(
            frozen.roots,
            frozen.final_items,
            candidate_predictions,
            PRIMARY_IOU_THRESHOLD,
        ),
        "iou_0_50": _reviewed_support_at_threshold(
            frozen.roots,
            frozen.final_items,
            candidate_predictions,
            DIAGNOSTIC_IOU_THRESHOLD,
        ),
    }
    _validate_baseline_reproduction(
        frozen, baseline_thresholds, baseline_support)

    comparisons = {
        key: _threshold_comparison(
            baseline_thresholds[key], candidate_thresholds[key])
        for key in ("iou_0_75", "iou_0_50")
    }
    reviewed_support = {
        key: _reviewed_support_comparison(
            baseline_support[key], candidate_support[key])
        for key in ("iou_0_75", "iou_0_50")
    }
    assigned_iou = _assigned_iou_comparison(
        truth_by_film, baseline_plays, candidate_plays)

    report = {
        "schema_version": "1.0",
        "evaluation_id": EVALUATION_ID,
        "candidate_id": candidate_id,
        "report_status": "complete",
        "scope": {
            "role": "development_regression",
            "public_claim_eligible": False,
            "independent_holdout": False,
            "warning": DEVELOPMENT_WARNING,
        },
        "methodology": {
            "primary_unit": "frozen verified truth item",
            "truth_population": (
                "118 verified final-v2 items descended from the 125 locked "
                "review roots; terminal exclusions are not positive truth"
            ),
            "primary_matching": (
                "Greedy descending-IoU one-to-one matching per film at "
                "IoU 0.75; IoU 0.50 is diagnostic."
            ),
            "unmatched_prediction_policy": (
                "Ignored for truth-centric recovery because most full-film "
                "predictions are unsampled and unreviewed."
            ),
            "not_reported": [
                "film-wide false positives",
                "film-wide precision",
                "film-wide F1",
                "film-wide accuracy",
            ],
            "reviewed_support": (
                "Secondary midpoint-owned local diagnostic on original "
                "locked roots. Predictions outside support are ignored."
            ),
            "limitations": [
                (
                    "The locked roots and final truth describe sampled play "
                    "boundaries. They do not contain exhaustive labels for "
                    "whether two camera angles belong to the same snap."
                ),
                (
                    "A paired candidate can improve sampled boundary IoU "
                    "without proving that its two angles were paired "
                    "correctly."
                ),
                (
                    "Same-snap pairing precision, pair false positives, and "
                    "the correctness of proposals outside reviewed roots "
                    "require a new blind human review of candidate pair "
                    "proposals."
                ),
            ],
        },
        "provenance": {
            "frozen_v2_artifacts": _artifact_rows(spec),
            "baseline_detector": frozen.baseline_report[
                "provenance"]["detector"],
            "baseline_predictions": list(
                frozen.baseline_prediction_rows),
            "candidate_lock": _candidate_lock_reference(
                spec, candidate_lock),
            "candidate_detector": candidate_lock.detector,
            "candidate_predictions": list(candidate_rows),
            "scorer": {
                "file": "scripts/score_iteration_4a_development_ab.py",
                "sha256": sha256_file(Path(__file__)),
            },
            "films": [
                {
                    "film_id": film_id,
                    "duration_ms": int(film["duration_ms"]),
                    "source_sha256": str(film["source_sha256"]),
                    "resolution": film.get("resolution"),
                    "frame_rate": film.get("frame_rate"),
                }
                for film_id, film in sorted(frozen.film_specs.items())
            ],
        },
        "integrity": {
            "passed": True,
            "frozen_artifact_hashes_valid": True,
            "state_truth_exact_mirror": True,
            "locked_root_count": len(frozen.roots),
            "verified_truth_count": sum(
                item.status == "verified"
                for item in frozen.final_items),
            "excluded_terminal_count": sum(
                item.status == "excluded"
                for item in frozen.final_items),
            "baseline_v2_exactly_reproduced": True,
            "candidate_lock_valid": True,
            "candidate_detector_source_hash_valid": True,
            "candidate_configuration_identity_valid": True,
            "candidate_prediction_hashes_valid": True,
            "candidate_file_set_complete": True,
            "candidate_payload_hashes_recorded": True,
            "candidate_full_output_structure_valid": True,
        },
        "results": {
            "truth_centric": comparisons,
            "one_to_one_assigned_iou": assigned_iou,
            "reviewed_support_midpoint_diagnostic": reviewed_support,
            "full_output_structure": {
                "baseline": baseline_structural,
                "candidate": candidate_structural,
                "candidate_minus_baseline": {
                    key: candidate_structural[key] - baseline_structural[key]
                    for key in (
                        "play_count",
                        "unclassified_count",
                        "invalid_play_count",
                        "invalid_unclassified_count",
                        "out_of_bounds_count",
                        "overlap_pair_count",
                    )
                },
            },
        },
    }
    json_text = _json_text(report)
    markdown_text = render_markdown(report)
    forbidden_paths = (
        str(spec.root.resolve()),
        str(candidate_dir.resolve()),
        str(candidate_lock_path.resolve()),
    )
    for path in forbidden_paths:
        if path.casefold() in json_text.casefold() \
                or path.casefold() in markdown_text.casefold():
            raise DevelopmentABError(
                "Report contains a machine-specific absolute path")
    return ReportBundle(report, json_text, markdown_text)


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    primary = report["results"]["truth_centric"]["iou_0_75"]
    diagnostic = report["results"]["truth_centric"]["iou_0_50"]
    assigned = report["results"]["one_to_one_assigned_iou"]
    support = report["results"][
        "reviewed_support_midpoint_diagnostic"]["iou_0_75"]
    structure = report["results"]["full_output_structure"]
    lines = [
        "# TapeSift Iteration 4A Development A/B",
        "",
        f"> **{report['scope']['warning']}**",
        "",
        "## Status",
        "",
        f"- Candidate: `{report['candidate_id']}`",
        "- Frozen artifact validation: **PASS**",
        "- Baseline v2 reproduction: **EXACT**",
        "- Public performance claim eligible: **NO**",
        "",
        "No film-wide false-positive, precision, F1, accuracy, or general "
        "coverage claim is computed from this partial truth.",
        "",
        "## Truth-centric recovery",
        "",
        "| IoU | Baseline | Candidate | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, row in (("0.75", primary), ("0.50", diagnostic)):
        baseline = row["baseline"]
        candidate = row["candidate"]
        lines.append(
            f"| {label} | "
            f"{baseline['matched_truth_count']}/{baseline['truth_count']} "
            f"({_percent(baseline['truth_recovery_rate'])}) | "
            f"{candidate['matched_truth_count']}/{candidate['truth_count']} "
            f"({_percent(candidate['truth_recovery_rate'])}) | "
            f"{row['delta']['matched_truth_count']:+d} |"
        )

    lines.extend([
        "",
        "### Per film at IoU 0.75",
        "",
        "| Film | Truth | Baseline | Candidate | Delta |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for film_id, row in primary["by_film_delta"].items():
        lines.append(
            f"| {film_id} | {row['truth_count']} | "
            f"{row['baseline_matched_truth_count']} | "
            f"{row['candidate_matched_truth_count']} | "
            f"{row['matched_truth_count_delta']:+d} |"
        )

    lines.extend([
        "",
        "## One-to-one assigned-IoU comparison",
        "",
        f"- Wins / ties / losses: **{assigned['wins']} / "
        f"{assigned['ties']} / {assigned['losses']}**",
        f"- Mean assigned IoU: "
        f"{assigned['baseline_mean_assigned_iou']:.4f} baseline, "
        f"{assigned['candidate_mean_assigned_iou']:.4f} candidate",
        "",
        "## Reviewed-support midpoint diagnostic at IoU 0.75",
        "",
        "This is a local, baseline-conditioned diagnostic. It is not "
        "film-wide precision or accuracy.",
        "",
        "| Arm | Owned candidates | Matches | Unmatched owned | "
        "Ignored outside | Candidate pass rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for arm in ("baseline", "candidate"):
        row = support[arm]
        lines.append(
            f"| {arm.title()} | {row['owned_prediction_count']} | "
            f"{row['matched_count']} | "
            f"{row['unmatched_owned_prediction_count']} | "
            f"{row['ignored_outside_support_count']} | "
            f"{_percent(row['candidate_iou_pass_rate'])} |"
        )

    lines.extend([
        "",
        "## Full-output structural diagnostics",
        "",
        "| Arm | Plays | Unclassified | Out of bounds | Overlap pairs |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for arm in ("baseline", "candidate"):
        row = structure[arm]
        lines.append(
            f"| {arm.title()} | {row['play_count']} | "
            f"{row['unclassified_count']} | "
            f"{row['out_of_bounds_count']} | "
            f"{row['overlap_pair_count']} |"
        )
    lines.extend([
        "",
        "Predictions outside the reviewed roots remain part of these "
        "full-output structural counts, but they are never labeled as false "
        "positives.",
        "",
        "## Pairing limitation",
        "",
        "The frozen roots validate sampled play boundaries, not whether two "
        "camera angles belong to the same snap. Better boundary recovery does "
        "not establish pairing precision. A new blind review of candidate "
        "pair proposals is required for that claim.",
        "",
    ])
    return "\n".join(lines)


def output_paths(
    output_dir: Path,
    candidate_id: str,
) -> tuple[Path, Path]:
    base = f"ab_results_{candidate_id}_v1"
    return output_dir / f"{base}.json", output_dir / f"{base}.md"


def write_reports(
    json_path: Path,
    markdown_path: Path,
    bundle: ReportBundle,
) -> None:
    """Publish both reports without replacing either existing artifact."""

    outputs = (
        (json_path, bundle.json_text),
        (markdown_path, bundle.markdown_text),
    )
    existing = [str(path) for path, _ in outputs if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to replace Iteration 4A A/B report: "
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
        description=(
            "Compare candidate detector predictions with the frozen "
            "Iteration 3A v2 development truth"
        )
    )
    value.add_argument(
        "--candidate-dir",
        type=Path,
        required=True,
        help="Directory containing exactly one <film_id>.json per frozen film.",
    )
    value.add_argument(
        "--candidate-lock",
        type=Path,
        required=True,
        help=(
            "Explicit candidate lock JSON pinning detector source, "
            "configuration, and every prediction file."
        ),
    )
    value.add_argument(
        "--candidate-id",
        required=True,
        help="Stable lowercase artifact id, ideally commit/config derived.",
    )
    value.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    value.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and score in memory without creating reports.",
    )
    return value


def main() -> int:
    args = parser().parse_args()
    spec = default_spec()
    bundle = build_report(
        spec, args.candidate_dir, args.candidate_lock, args.candidate_id)
    json_path, markdown_path = output_paths(
        args.output_dir.resolve(), args.candidate_id)
    if not args.check_only:
        write_reports(json_path, markdown_path, bundle)
    primary = bundle.report["results"][
        "truth_centric"]["iou_0_75"]
    support = bundle.report["results"][
        "reviewed_support_midpoint_diagnostic"]["iou_0_75"]["candidate"]
    print(_json_text({
        "status": "validated" if args.check_only else "scored",
        "scope": "development_only_non_public",
        "candidate_id": args.candidate_id,
        "baseline_matches_iou_0_75": (
            primary["baseline"]["matched_truth_count"]),
        "candidate_matches_iou_0_75": (
            primary["candidate"]["matched_truth_count"]),
        "match_delta_iou_0_75": primary["delta"]["matched_truth_count"],
        "ignored_outside_reviewed_support": (
            support["ignored_outside_support_count"]),
        "json_report": str(json_path),
        "markdown_report": str(markdown_path),
    }).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
