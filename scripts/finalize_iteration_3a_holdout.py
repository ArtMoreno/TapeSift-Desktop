"""Fail-closed finalization of the Iteration 3A independent holdout.

This script never edits a verifier working file.  It starts from the immutable
124-item ``completed.raw`` state, overlays the frozen 15-item boundary review,
then applies one or more externally frozen correction layers.  Later layers
may replace an earlier correction for the same allowlisted item.

Correction layers are supplied as ``LOCK_PATH=LOCK_SHA256``.  A completion
lock has this deliberately small schema::

    {
      "schema_version": "1.0",
      "status": "complete",
      "layer_id": "guided-five-v1",
      "queue_id": "the verifier queue id",
      "state_file": "research/.../completed.state.json",
      "state_sha256": "...",
      "truth_file": "research/.../completed.verified.jsonl",
      "truth_sha256": "...",
      "locked_queue_file": "research/.../queue.locked.json",
      "locked_queue_sha256": "...",
      "item_ids": ["..."]
    }

The lock itself is also hash-pinned on the command line.  A pending or mutable
queue therefore cannot accidentally become benchmark truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tapesift.research.segmentation_benchmark import (  # noqa: E402
    SCHEMA_VERSION,
)
from tapesift.research.segmentation_verification import (  # noqa: E402
    MIN_SEGMENT_MS,
    PILOT_SCHEMA_VERSION,
    VerificationItem,
)


VERIFICATION_DIR = (
    ROOT / "research" / "segmentation_benchmark" / "verification")
REPORT_DIR = (
    ROOT / "research" / "segmentation_benchmark" / "reports"
    / "iteration_3a_holdout_v1")

RAW_STATE = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.completed.raw.state.json")
RAW_TRUTH = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.completed.raw.verified.jsonl")
ORIGINAL_QUEUE = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.queue.locked.json")
CLEANED_STATE = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.cleaned.state.json")
CLEANED_TRUTH = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.cleaned.verified.jsonl")
BOUNDARY_LOCK = REPORT_DIR / "boundary_qa_lock.json"
BOUNDARY_QA_STATE = (
    VERIFICATION_DIR
    / "iteration_3a_boundary_qa_v1.completed.raw.state.json")
BOUNDARY_QA_TRUTH = (
    VERIFICATION_DIR
    / "iteration_3a_boundary_qa_v1.completed.raw.verified.jsonl")
BOUNDARY_QA_QUEUE = (
    VERIFICATION_DIR / "iteration_3a_boundary_qa_v1.queue.locked.json")

FINAL_STATE = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.final_v2.state.json")
FINAL_TRUTH = (
    VERIFICATION_DIR
    / "iteration_3a_independent_holdout_v1.final_v2.verified.jsonl")
FINAL_LOCK = REPORT_DIR / "finalization_lock_v2.json"

BASE_HASHES = {
    RAW_STATE: (
        "e5ef2ed55a0eaace2d62b98b228531a34440bd296076c7d7416269ffc4dca2ef"),
    RAW_TRUTH: (
        "d1e739787c0f2f8246e833973667f859e7b0def73ca6702b5ec36c9e609cb5fb"),
    ORIGINAL_QUEUE: (
        "ce057e42b063e7d81f866ef3406f392db92f644e5febafab7ba87013e515538d"),
    CLEANED_STATE: (
        "ad3c6d5a12ec61c8a01f206ee5d5e99c95fb69cc56894570e1cc507401363795"),
    CLEANED_TRUTH: (
        "c27670317e8010893a140c518cb2626eaefd8815718d3f31f69636fcd2133259"),
    BOUNDARY_LOCK: (
        "98f0493bfdf5419236a6c231ee44bcfee67f2e0316e165f7e7895666bd6b88d6"),
    BOUNDARY_QA_STATE: (
        "ff6e6ba1fc6ec1164be8d9b860bfd42e63b585e98f7c67152e11cbaedb6a0c2c"),
    BOUNDARY_QA_TRUTH: (
        "bdd08e1130303f21cd9c142056148ddbea85b75bbc8288a586724ef9618f32f3"),
    BOUNDARY_QA_QUEUE: (
        "dbec58877235d16933a286747ab211a0026cdd8d45b1d05060d3b4e9172f33dd"),
}

REQUIRED_CORRECTION_IDS = frozenset({
    (
        "south_carolina_o_vs_vanderbilt_d_holdout_3a_v1:"
        "unclassified:0131"
    ),
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0023",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0032",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0035",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0042",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0062",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0065",
})

BOSTON_MERGED_ID = (
    "sample_game_002:"
    "play:0007:merge:5c185493")
BOSTON_REPLACED_IDS = frozenset({
    "sample_game_002:play:0007",
    "sample_game_002:unclassified:0001",
})

REVIEW_MUTABLE_FIELDS = frozenset({
    "start_ms",
    "end_ms",
    "status",
    "decision",
    "edit_history",
    "verified_at",
})
EXCLUSION_MUTABLE_FIELDS = frozenset({
    "status",
    "decision",
    "edit_history",
    "verified_at",
})
DERIVED_ANGLE_MUTABLE_FIELDS = frozenset({"angle_starts_ms"})
ITEM_FIELD_NAMES = frozenset(entry.name for entry in fields(VerificationItem))


@dataclass(frozen=True)
class LockedFile:
    path: Path
    sha256: str


@dataclass(frozen=True)
class FinalizationSpec:
    root: Path
    raw_state: LockedFile
    raw_truth: LockedFile
    original_queue: LockedFile
    cleaned_state: LockedFile
    cleaned_truth: LockedFile
    boundary_lock: LockedFile
    boundary_qa_state: LockedFile
    boundary_qa_truth: LockedFile
    boundary_qa_queue: LockedFile
    correction_locks: tuple[LockedFile, ...]
    final_state: Path
    final_truth: Path
    final_lock: Path
    expected_raw_count: int = 124
    expected_boundary_count: int = 15
    expected_exclusion_count: int = 6
    required_correction_ids: frozenset[str] = REQUIRED_CORRECTION_IDS
    preserved_item_id: str = BOSTON_MERGED_ID
    forbidden_reintroduced_ids: frozenset[str] = BOSTON_REPLACED_IDS


@dataclass
class CorrectionLayer:
    layer_id: str
    queue_id: str
    lock: LockedFile
    state: LockedFile
    truth: LockedFile
    queue: LockedFile
    item_ids: tuple[str, ...]
    items: dict[str, VerificationItem]
    updated_at: str


@dataclass
class FinalizationBundle:
    state_text: str
    truth_text: str
    lock_text: str
    state_sha256: str
    truth_sha256: str
    changed_item_ids: tuple[str, ...]
    angle_pruning: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _json_text(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _jsonl_text(records: Iterable[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )


def _require_hash(artifact: LockedFile, label: str) -> None:
    if not artifact.path.is_file():
        raise FileNotFoundError(f"{label} is missing: {artifact.path}")
    actual = sha256_file(artifact.path)
    if actual != artifact.sha256:
        raise ValueError(
            f"{label} hash mismatch for {artifact.path}: "
            f"expected {artifact.sha256}, found {actual}")


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _resolve_inside(root: Path, raw_path: str, label: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes the repository root: {candidate}") \
            from exc
    return candidate


def _items_from_state(
    payload: dict[str, Any],
    label: str,
) -> list[VerificationItem]:
    if payload.get("schema_version") != PILOT_SCHEMA_VERSION:
        raise ValueError(f"{label} has an unsupported state schema")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError(f"{label} does not contain an item list")
    result = []
    seen = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict) \
                or set(raw_item) - ITEM_FIELD_NAMES:
            raise ValueError(f"{label} contains an invalid item schema")
        item = VerificationItem.from_dict(raw_item)
        if item.item_id in seen:
            raise ValueError(f"{label} contains duplicate id {item.item_id}")
        seen.add(item.item_id)
        result.append(item)
    return result


def verification_record(
    item: VerificationItem,
    queue_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "queue_id": queue_id,
        "item_id": item.item_id,
        "film_id": item.film_id,
        "source_file": item.source_file,
        "start_ms": item.start_ms,
        "end_ms": item.end_ms,
        "angle_starts_ms": item.angle_starts_ms,
        "verification_status": item.status,
        "decision": item.decision,
        "edit_history": item.edit_history,
        "verified_at": item.verified_at,
        "detector": {
            "candidate_kind": item.candidate_kind,
            "candidate_stratum": item.candidate_stratum,
            "candidate_index": item.candidate_index,
            "prediction_indices": item.prediction_indices,
            "original_start_ms": item.original_start_ms,
            "original_end_ms": item.original_end_ms,
            "needs_review": item.detector_needs_review,
            "reason": item.detector_reason,
            "signal": item.detector_signal,
            "angle_count": item.angle_count,
        },
    }


def _require_state_truth_mirror(
    state_payload: dict[str, Any],
    truth_records: list[dict[str, Any]],
    label: str,
) -> list[VerificationItem]:
    items = _items_from_state(state_payload, label)
    queue_id = state_payload.get("queue_id")
    if not isinstance(queue_id, str) or not queue_id:
        raise ValueError(f"{label} has no queue id")
    expected = [
        verification_record(item, queue_id)
        for item in items
        if item.status in {"verified", "excluded"}
    ]
    if truth_records != expected:
        raise ValueError(f"{label} state and truth do not mirror each other")
    return items


def _require_same_except(
    baseline: VerificationItem,
    candidate: VerificationItem,
    allowed_fields: frozenset[str],
    label: str,
) -> None:
    left = asdict(baseline)
    right = asdict(candidate)
    changed = {
        key for key in ITEM_FIELD_NAMES
        if left.get(key) != right.get(key)
    }
    unexpected = sorted(changed - allowed_fields)
    if unexpected:
        raise ValueError(
            f"{label} changes immutable fields for {candidate.item_id}: "
            f"{', '.join(unexpected)}")


def _require_bounds(items: Iterable[VerificationItem], label: str) -> None:
    for item in items:
        if item.start_ms < 0 \
                or item.end_ms > item.film_duration_ms \
                or item.end_ms - item.start_ms < MIN_SEGMENT_MS:
            raise ValueError(
                f"{label} has invalid bounds for {item.item_id}: "
                f"{item.start_ms}-{item.end_ms} of "
                f"{item.film_duration_ms}")


def _require_no_overlaps(items: Iterable[VerificationItem]) -> None:
    by_film: dict[str, list[VerificationItem]] = {}
    for item in items:
        if item.status == "excluded":
            continue
        by_film.setdefault(item.film_id, []).append(item)
    for film_id, film_items in by_film.items():
        ordered = sorted(
            film_items, key=lambda item: (item.start_ms, item.end_ms, item.item_id))
        for left, right in zip(ordered, ordered[1:]):
            if right.start_ms < left.end_ms:
                raise ValueError(
                    f"Final truth overlaps in {film_id}: {left.item_id} "
                    f"({left.start_ms}-{left.end_ms}) and {right.item_id} "
                    f"({right.start_ms}-{right.end_ms})")


def _load_pair(
    state_artifact: LockedFile,
    truth_artifact: LockedFile,
    label: str,
) -> tuple[dict[str, Any], list[VerificationItem]]:
    _require_hash(state_artifact, f"{label} state")
    _require_hash(truth_artifact, f"{label} truth")
    payload = _read_json(state_artifact.path)
    items = _require_state_truth_mirror(
        payload, _read_jsonl(truth_artifact.path), label)
    return payload, items


def _load_correction_layer(
    root: Path,
    lock_artifact: LockedFile,
) -> CorrectionLayer:
    _require_hash(lock_artifact, "correction completion lock")
    lock = _read_json(lock_artifact.path)
    if lock.get("schema_version") != "1.0" \
            or lock.get("status") != "complete":
        raise ValueError(
            f"Correction lock is not complete: {lock_artifact.path}")
    required = {
        "layer_id",
        "queue_id",
        "state_file",
        "state_sha256",
        "truth_file",
        "truth_sha256",
        "locked_queue_file",
        "locked_queue_sha256",
        "item_ids",
    }
    missing = sorted(required - set(lock))
    if missing:
        raise ValueError(
            f"Correction lock is missing: {', '.join(missing)}")
    if not isinstance(lock["item_ids"], list) \
            or not all(isinstance(item_id, str) and item_id
                       for item_id in lock["item_ids"]):
        raise ValueError("Correction lock item_ids must be a list of strings")
    item_ids = tuple(lock["item_ids"])
    if not item_ids or len(item_ids) != len(set(item_ids)):
        raise ValueError("Correction lock item IDs are empty or duplicated")

    state = LockedFile(
        _resolve_inside(root, lock["state_file"], "correction state"),
        str(lock["state_sha256"]).lower(),
    )
    truth = LockedFile(
        _resolve_inside(root, lock["truth_file"], "correction truth"),
        str(lock["truth_sha256"]).lower(),
    )
    queue = LockedFile(
        _resolve_inside(root, lock["locked_queue_file"], "correction queue"),
        str(lock["locked_queue_sha256"]).lower(),
    )
    state_payload, items = _load_pair(
        state, truth, f"correction layer {lock['layer_id']}")
    _require_hash(queue, f"correction layer {lock['layer_id']} queue")
    queue_payload = _read_json(queue.path)
    queue_items = _items_from_state(
        queue_payload, f"correction layer {lock['layer_id']} queue")

    state_ids = tuple(item.item_id for item in items)
    queue_ids = tuple(item.item_id for item in queue_items)
    if state_ids != item_ids or queue_ids != item_ids:
        raise ValueError(
            f"Correction layer {lock['layer_id']} ID order does not match lock")
    if state_payload.get("queue_id") != lock["queue_id"] \
            or queue_payload.get("queue_id") != lock["queue_id"]:
        raise ValueError(
            f"Correction layer {lock['layer_id']} queue ID mismatch")
    queue_by_id = {item.item_id: item for item in queue_items}
    for item in items:
        if item.status != "verified" \
                or item.decision not in {"accepted", "revised"}:
            raise ValueError(
                f"Correction layer {lock['layer_id']} is incomplete at "
                f"{item.item_id}")
        _require_same_except(
            queue_by_id[item.item_id],
            item,
            REVIEW_MUTABLE_FIELDS,
            f"correction layer {lock['layer_id']}",
        )
    _require_bounds(items, f"correction layer {lock['layer_id']}")
    return CorrectionLayer(
        layer_id=lock["layer_id"],
        queue_id=lock["queue_id"],
        lock=lock_artifact,
        state=state,
        truth=truth,
        queue=queue,
        item_ids=item_ids,
        items={item.item_id: item for item in items},
        updated_at=str(state_payload.get("updated_at") or ""),
    )


def _prune_angle_starts(
    item: VerificationItem,
    *,
    source: str,
    record_in_edit_history: bool,
) -> tuple[VerificationItem, dict[str, Any] | None]:
    kept = [
        value for value in item.angle_starts_ms
        if item.start_ms <= value < item.end_ms
    ]
    removed = [
        value for value in item.angle_starts_ms
        if value not in kept
    ]
    if not removed:
        return item, None
    payload = asdict(item)
    audit_text = "finalizer_pruned_angle_starts:" + ",".join(
        str(value) for value in removed)
    payload["angle_starts_ms"] = kept
    if record_in_edit_history:
        payload["edit_history"] = [*item.edit_history, audit_text]
    return VerificationItem.from_dict(payload), {
        "item_id": item.item_id,
        "source": source,
        "range": [item.start_ms, item.end_ms],
        "before": list(item.angle_starts_ms),
        "after": kept,
        "removed": removed,
        "audit": audit_text,
        "edit_history_entry": (
            audit_text if record_in_edit_history else None),
    }


def _locked_entry(root: Path, artifact: LockedFile) -> dict[str, str]:
    return {
        "file": _relative(root, artifact.path),
        "sha256": artifact.sha256,
    }


def build_finalization(spec: FinalizationSpec) -> FinalizationBundle:
    """Validate every input and build deterministic output bytes in memory."""
    _require_hash(spec.original_queue, "original locked queue")
    _require_hash(spec.boundary_lock, "boundary QA lock")
    raw_payload, raw_items = _load_pair(
        spec.raw_state, spec.raw_truth, "completed raw holdout")
    cleaned_payload, cleaned_items = _load_pair(
        spec.cleaned_state, spec.cleaned_truth, "cleaned holdout")
    qa_payload, qa_items = _load_pair(
        spec.boundary_qa_state, spec.boundary_qa_truth, "completed boundary QA")
    _require_hash(spec.boundary_qa_queue, "boundary QA locked queue")

    if len(raw_items) != spec.expected_raw_count:
        raise ValueError(
            f"Expected {spec.expected_raw_count} raw items, found "
            f"{len(raw_items)}")
    raw_ids = tuple(item.item_id for item in raw_items)
    if len(cleaned_items) != len(raw_items) \
            or tuple(item.item_id for item in cleaned_items) != raw_ids:
        raise ValueError("Cleaned state does not preserve raw ID order")
    if spec.preserved_item_id:
        if raw_ids.count(spec.preserved_item_id) != 1:
            raise ValueError("The approved Boston merge is missing")
        reintroduced = sorted(set(raw_ids) & spec.forbidden_reintroduced_ids)
        if reintroduced:
            raise ValueError(
                f"Pre-merge Boston IDs were reintroduced: {reintroduced}")

    boundary = _read_json(spec.boundary_lock.path)
    boundary_ids = tuple(boundary.get("boundary_qa_ids", []))
    exclusion_ids = tuple(boundary.get("frozen_tail_exclusions", []))
    if len(boundary_ids) != spec.expected_boundary_count \
            or len(boundary_ids) != len(set(boundary_ids)):
        raise ValueError("Boundary QA allowlist is the wrong size or duplicated")
    if len(exclusion_ids) != spec.expected_exclusion_count \
            or len(exclusion_ids) != len(set(exclusion_ids)):
        raise ValueError("Exclusion allowlist is the wrong size or duplicated")
    if set(boundary_ids) & set(exclusion_ids):
        raise ValueError("Boundary QA and exclusion allowlists overlap")
    if not set(boundary_ids).issubset(raw_ids) \
            or not set(exclusion_ids).issubset(raw_ids):
        raise ValueError("An allowlisted item is absent from completed raw")

    qa_queue_payload = _read_json(spec.boundary_qa_queue.path)
    qa_queue_items = _items_from_state(
        qa_queue_payload, "boundary QA locked queue")
    if tuple(item.item_id for item in qa_items) != boundary_ids \
            or tuple(item.item_id for item in qa_queue_items) != boundary_ids:
        raise ValueError("Frozen boundary QA IDs do not match the allowlist")
    qa_queue_by_id = {item.item_id: item for item in qa_queue_items}
    for item in qa_items:
        if item.status != "verified" \
                or item.decision not in {"accepted", "revised"}:
            raise ValueError(f"Boundary QA is incomplete at {item.item_id}")
        _require_same_except(
            qa_queue_by_id[item.item_id],
            item,
            REVIEW_MUTABLE_FIELDS,
            "completed boundary QA",
        )
    _require_bounds(qa_items, "completed boundary QA")

    raw_by_id = {item.item_id: item for item in raw_items}
    cleaned_by_id = {item.item_id: item for item in cleaned_items}
    for item_id in exclusion_ids:
        item = cleaned_by_id[item_id]
        if item.status != "excluded" or item.decision != "excluded":
            raise ValueError(f"Approved exclusion is not excluded: {item_id}")
        _require_same_except(
            raw_by_id[item_id],
            item,
            EXCLUSION_MUTABLE_FIELDS,
            "cleaned exclusion",
        )
        audit_entries = [
            entry for entry in item.edit_history
            if entry.startswith("qa_excluded:")
        ]
        if len(audit_entries) != 1:
            raise ValueError(
                f"Approved exclusion has no unique QA audit: {item_id}")

    layers = [
        _load_correction_layer(spec.root, artifact)
        for artifact in spec.correction_locks
    ]
    if not layers:
        raise ValueError("At least one frozen correction layer is required")
    effective_corrections: dict[str, tuple[VerificationItem, str]] = {}
    covered = set()
    boundary_set = set(boundary_ids)
    for layer in layers:
        layer_ids = set(layer.item_ids)
        if not layer_ids.issubset(spec.required_correction_ids):
            raise ValueError(
                f"Correction layer {layer.layer_id} contains unexpected IDs: "
                f"{sorted(layer_ids - spec.required_correction_ids)}")
        if not layer_ids.issubset(boundary_set):
            raise ValueError(
                f"Correction layer {layer.layer_id} is outside boundary QA")
        covered.update(layer_ids)
        for item_id in layer.item_ids:
            effective_corrections[item_id] = (
                layer.items[item_id], layer.layer_id)
    if covered != set(spec.required_correction_ids):
        raise ValueError(
            "Frozen correction layers do not cover exactly the required IDs. "
            f"Missing={sorted(spec.required_correction_ids - covered)}; "
            f"extra={sorted(covered - spec.required_correction_ids)}")
    # A correction layer can have a self-consistent but contaminated locked
    # queue.  The original boundary-QA queue is the authority for detector
    # provenance, so validate the effective (last-wins) record against it too.
    for item_id, (item, layer_id) in effective_corrections.items():
        _require_same_except(
            qa_queue_by_id[item_id],
            item,
            REVIEW_MUTABLE_FIELDS,
            f"effective correction layer {layer_id}",
        )

    qa_by_id = {item.item_id: item for item in qa_items}
    angle_pruning = []
    final_items = []
    source_by_id: dict[str, str] = {}
    review_change_allowlist = boundary_set | set(exclusion_ids)
    for raw_item in raw_items:
        item_id = raw_item.item_id
        if item_id in exclusion_ids:
            candidate = cleaned_by_id[item_id]
            source_by_id[item_id] = "cleaned_exclusion"
        elif item_id in boundary_set:
            if item_id in effective_corrections:
                candidate, layer_id = effective_corrections[item_id]
                source_by_id[item_id] = f"correction:{layer_id}"
            else:
                candidate = qa_by_id[item_id]
                source_by_id[item_id] = "completed_boundary_qa"
        else:
            candidate = raw_item
            source_by_id[item_id] = "completed_raw"
        candidate, audit = _prune_angle_starts(
            candidate,
            source=source_by_id[item_id],
            record_in_edit_history=item_id in review_change_allowlist,
        )
        if audit:
            angle_pruning.append(audit)
        final_items.append(candidate)

    _require_bounds(final_items, "final holdout")
    for item in final_items:
        outside = [
            value for value in item.angle_starts_ms
            if not item.start_ms <= value < item.end_ms
        ]
        if outside:
            raise ValueError(
                f"Final angle markers are outside {item.item_id}: {outside}")
        if item.status not in {"verified", "excluded"}:
            raise ValueError(f"Final holdout is incomplete at {item.item_id}")
    actual_exclusions = {
        item.item_id for item in final_items if item.status == "excluded"
    }
    if actual_exclusions != set(exclusion_ids):
        raise ValueError(
            "Final exclusions differ from the approved allowlist. "
            f"Expected={sorted(exclusion_ids)}; "
            f"found={sorted(actual_exclusions)}")
    _require_no_overlaps(final_items)

    angle_pruned_ids = {
        entry["item_id"] for entry in angle_pruning
    }
    expected_changed = review_change_allowlist | (
        angle_pruned_ids - review_change_allowlist)
    changed = {
        raw.item_id
        for raw, final in zip(raw_items, final_items)
        if asdict(raw) != asdict(final)
    }
    if changed != expected_changed:
        raise ValueError(
            "Final semantic diff escaped the approved allowlists. "
            f"Expected={sorted(expected_changed)}; found={sorted(changed)}")
    for raw, final in zip(raw_items, final_items):
        if raw.item_id in review_change_allowlist:
            continue
        changed_fields = {
            key for key in ITEM_FIELD_NAMES
            if getattr(raw, key) != getattr(final, key)
        }
        unexpected = sorted(
            changed_fields - DERIVED_ANGLE_MUTABLE_FIELDS)
        if unexpected:
            raise ValueError(
                "Final semantic diff changed fields outside the boundary/"
                f"exclusion allowlists for {raw.item_id}: "
                f"{', '.join(unexpected)}")
        expected_angles = [
            value for value in raw.angle_starts_ms
            if raw.start_ms <= value < raw.end_ms
        ]
        if final.angle_starts_ms != expected_angles:
            raise ValueError(
                "Final angle normalization outside the boundary/exclusion "
                f"allowlists is not the exact stable filter for {raw.item_id}")
        if changed_fields and raw.item_id not in angle_pruned_ids:
            raise ValueError(
                "Final semantic diff has unaudited angle pruning for "
                f"{raw.item_id}")

    final_updated_at = layers[-1].updated_at
    if not final_updated_at:
        raise ValueError("Final correction layer has no frozen completion time")
    final_payload = {
        "schema_version": raw_payload["schema_version"],
        "queue_id": raw_payload["queue_id"],
        "created_at": raw_payload.get("created_at", ""),
        "updated_at": final_updated_at,
        # Keep archival bytes reproducible if the repository moves.
        "ground_truth_path": _relative(spec.root, spec.final_truth),
        "cursor": 0,
        "cursor_item_id": final_items[0].item_id if final_items else "",
        "last_action": "finalized Iteration 3A holdout from frozen QA layers",
        "items": [asdict(item) for item in final_items],
    }
    truth_records = [
        verification_record(item, final_payload["queue_id"])
        for item in final_items
    ]
    state_text = _json_text(final_payload)
    truth_text = _jsonl_text(truth_records)
    state_hash = sha256_text(state_text)
    truth_hash = sha256_text(truth_text)
    status_counts = Counter(item.status for item in final_items)
    decision_counts = Counter(item.decision for item in final_items)

    base_inputs = (
        spec.raw_state,
        spec.raw_truth,
        spec.original_queue,
        spec.cleaned_state,
        spec.cleaned_truth,
        spec.boundary_lock,
        spec.boundary_qa_state,
        spec.boundary_qa_truth,
        spec.boundary_qa_queue,
    )
    lock_payload = {
        "schema_version": "1.0",
        "status": "final",
        "finalized_at": final_updated_at,
        "method": (
            "124-item completed.raw base; 15-item frozen boundary QA overlay; "
            "six cleaned exclusions; ordered frozen correction layers"
        ),
        "inputs": [
            _locked_entry(spec.root, artifact) for artifact in base_inputs
        ],
        "correction_layers": [
            {
                "layer_id": layer.layer_id,
                "queue_id": layer.queue_id,
                "item_ids": list(layer.item_ids),
                "completion_lock": _locked_entry(spec.root, layer.lock),
                "state": _locked_entry(spec.root, layer.state),
                "truth": _locked_entry(spec.root, layer.truth),
                "locked_queue": _locked_entry(spec.root, layer.queue),
            }
            for layer in layers
        ],
        "outputs": {
            "state": {
                "file": _relative(spec.root, spec.final_state),
                "sha256": state_hash,
            },
            "truth": {
                "file": _relative(spec.root, spec.final_truth),
                "sha256": truth_hash,
            },
        },
        "counts": {
            "items": len(final_items),
            "statuses": dict(sorted(status_counts.items())),
            "decisions": dict(sorted(decision_counts.items())),
            "changed_items": len(changed),
            "angle_markers_pruned": sum(
                len(entry["removed"]) for entry in angle_pruning),
        },
        "approved_changes": {
            "boundary_qa_ids": list(boundary_ids),
            "frozen_tail_exclusions": list(exclusion_ids),
            "required_correction_ids": sorted(spec.required_correction_ids),
            "derived_angle_start_field_allowlist": [
                "angle_starts_ms",
            ],
            "derived_angle_start_pruning_ids": sorted(angle_pruned_ids),
            "derived_angle_start_pruning_outside_review_allowlists": sorted(
                angle_pruned_ids - review_change_allowlist),
            "changed_item_ids": sorted(changed),
            "source_by_item_id": {
                item_id: source_by_id[item_id]
                for item_id in sorted(changed)
            },
        },
        "angle_start_pruning": angle_pruning,
        "preserved_raw_decisions": {
            "required_item_id": spec.preserved_item_id,
            "forbidden_reintroduced_ids": sorted(
                spec.forbidden_reintroduced_ids),
        },
        "validation": {
            "all_inputs_hash_locked": True,
            "all_items_terminal": True,
            "bounds_valid": True,
            "no_overlaps": True,
            "state_truth_mirror": True,
            "semantic_diff_exactly_allowlisted": True,
            "off_review_allowlist_changes_limited_to_angle_starts": True,
        },
    }
    return FinalizationBundle(
        state_text=state_text,
        truth_text=truth_text,
        lock_text=_json_text(lock_payload),
        state_sha256=state_hash,
        truth_sha256=truth_hash,
        changed_item_ids=tuple(sorted(changed)),
        angle_pruning=tuple(angle_pruning),
    )


def write_finalization(
    spec: FinalizationSpec,
    bundle: FinalizationBundle,
) -> None:
    """Create the three final artifacts without replacing any existing file."""
    outputs = (
        (spec.final_state, bundle.state_text),
        (spec.final_truth, bundle.truth_text),
        (spec.final_lock, bundle.lock_text),
    )
    existing = [str(path) for path, _text in outputs if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to replace finalization artifacts: "
            + ", ".join(existing))

    staged: list[tuple[Path, Path]] = []
    created: list[Path] = []
    try:
        for target, text in outputs:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.{uuid.uuid4().hex}.tmp")
            # Bytes avoid platform newline translation, so the published
            # SHA-256 is identical on Windows and in headless CI.
            temporary.write_bytes(text.encode("utf-8"))
            staged.append((temporary, target))
        for temporary, target in staged:
            # Hard-link publication fails atomically if target now exists.
            os.link(temporary, target)
            created.append(target)
    except Exception:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise
    finally:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)


def parse_locked_file(value: str) -> LockedFile:
    try:
        raw_path, digest = value.rsplit("=", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use LOCK_PATH=LOCK_SHA256") from exc
    digest = digest.strip().lower()
    if not raw_path.strip() or len(digest) != 64 \
            or any(character not in "0123456789abcdef" for character in digest):
        raise argparse.ArgumentTypeError(
            "Use LOCK_PATH=LOCK_SHA256 with a 64-character SHA-256")
    path = Path(raw_path.strip())
    if not path.is_absolute():
        path = ROOT / path
    return LockedFile(path.resolve(), digest)


def default_spec(correction_locks: Iterable[LockedFile]) -> FinalizationSpec:
    locked = {
        path: LockedFile(path, digest)
        for path, digest in BASE_HASHES.items()
    }
    return FinalizationSpec(
        root=ROOT,
        raw_state=locked[RAW_STATE],
        raw_truth=locked[RAW_TRUTH],
        original_queue=locked[ORIGINAL_QUEUE],
        cleaned_state=locked[CLEANED_STATE],
        cleaned_truth=locked[CLEANED_TRUTH],
        boundary_lock=locked[BOUNDARY_LOCK],
        boundary_qa_state=locked[BOUNDARY_QA_STATE],
        boundary_qa_truth=locked[BOUNDARY_QA_TRUTH],
        boundary_qa_queue=locked[BOUNDARY_QA_QUEUE],
        correction_locks=tuple(correction_locks),
        final_state=FINAL_STATE,
        final_truth=FINAL_TRUTH,
        final_lock=FINAL_LOCK,
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Freeze the Iteration 3A holdout from hash-locked QA layers")
    value.add_argument(
        "--correction-lock",
        action="append",
        required=True,
        type=parse_locked_file,
        metavar="PATH=SHA256",
        help=(
            "Frozen correction completion lock. Repeat in overlay order; "
            "later locks replace earlier records for duplicate allowlisted IDs."
        ),
    )
    value.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and build deterministic bytes without writing outputs.",
    )
    return value


def main() -> int:
    args = parser().parse_args()
    spec = default_spec(args.correction_lock)
    bundle = build_finalization(spec)
    if not args.check_only:
        write_finalization(spec, bundle)
    print(json.dumps({
        "status": "validated" if args.check_only else "finalized",
        "state_file": str(spec.final_state),
        "state_sha256": bundle.state_sha256,
        "truth_file": str(spec.final_truth),
        "truth_sha256": bundle.truth_sha256,
        "finalization_lock": str(spec.final_lock),
        "changed_items": len(bundle.changed_item_ids),
        "angle_markers_pruned": sum(
            len(entry["removed"]) for entry in bundle.angle_pruning),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
