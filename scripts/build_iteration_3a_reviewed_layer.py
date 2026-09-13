"""Build the reviewer-verified final five-item Iteration 3A correction layer.

The layer is reconstructed from the immutable boundary-QA queue so detector
provenance cannot leak in from a later verifier queue.  Two accepted bounds
come from the frozen guided-five completion.  Three bounds come from the
hash-pinned owner-approval sidecar.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, fields
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

GUIDED_COMPLETION_LOCK = (
    REPORT_DIR / "guided_final_five_v1.completion.lock.json")
REVIEW_APPROVAL = REPORT_DIR / "reviewer_verified_final_ranges_v1.json"
BOUNDARY_QA_QUEUE = (
    VERIFICATION_DIR / "iteration_3a_boundary_qa_v1.queue.locked.json")

OUTPUT_STATE = (
    VERIFICATION_DIR
    / "iteration_3a_reviewer_verified_final_five_v1.completed.raw.state.json")
OUTPUT_TRUTH = (
    VERIFICATION_DIR
    / "iteration_3a_reviewer_verified_final_five_v1.completed.raw.verified.jsonl")
OUTPUT_QUEUE = (
    VERIFICATION_DIR
    / "iteration_3a_reviewer_verified_final_five_v1.queue.locked.json")
OUTPUT_LOCK = (
    REPORT_DIR / "reviewer_verified_final_five_v1.completion.lock.json")

GUIDED_COMPLETION_LOCK_SHA256 = (
    "a876c3f96d6762c4645b8511df04a16a8f79282f5f93106a5b226c0c5c769621")
REVIEW_APPROVAL_SHA256 = (
    "f2ba97ed8c1f25682b65d83d994b3d9051623a25d1b72b0bd2561442ffa5c626")
BOUNDARY_QA_QUEUE_SHA256 = (
    "dbec58877235d16933a286747ab211a0026cdd8d45b1d05060d3b4e9172f33dd")

GUIDED_IDS = (
    (
        "south_carolina_o_vs_vanderbilt_d_holdout_3a_v1:"
        "unclassified:0131"
    ),
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0023",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0032",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0035",
    "utsa_o_vs_texas_a_m_d_holdout_3a_v1:play:0042",
)
GUIDED_BOUND_IDS = frozenset(GUIDED_IDS[:2])
OWNER_RANGE_IDS = frozenset(GUIDED_IDS[2:])
OUTPUT_QUEUE_ID = (
    "segmentation-iteration-3a-holdout-v1-reviewer-verified-final-five")
ITEM_FIELDS = frozenset(entry.name for entry in fields(VerificationItem))


@dataclass(frozen=True)
class LockedFile:
    path: Path
    sha256: str


@dataclass(frozen=True)
class OwnerLayerSpec:
    root: Path
    guided_completion_lock: LockedFile
    review_approval: LockedFile
    boundary_queue: LockedFile
    output_state: Path
    output_truth: Path
    output_queue: Path
    output_lock: Path
    expected_ids: tuple[str, ...] = GUIDED_IDS
    guided_bound_ids: frozenset[str] = GUIDED_BOUND_IDS
    owner_range_ids: frozenset[str] = OWNER_RANGE_IDS
    output_queue_id: str = OUTPUT_QUEUE_ID
    output_layer_id: str = "reviewer-verified-final-five-v1"
    required_approval_recorded_at: str = ""
    required_approval_recorded_at_basis: str = ""


@dataclass
class OwnerLayerBundle:
    writes: tuple[tuple[Path, bytes], ...]
    completion_lock_sha256: str
    state_sha256: str
    truth_sha256: str
    queue_sha256: str


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _require_hash(value: LockedFile, label: str) -> None:
    if not value.path.is_file():
        raise FileNotFoundError(f"{label} is missing: {value.path}")
    actual = sha256_file(value.path)
    if actual != value.sha256:
        raise ValueError(
            f"{label} hash mismatch: expected {value.sha256}, found {actual}")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _jsonl_bytes(records: Iterable[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    ).encode("utf-8")


def _relative(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


def _resolve_inside(root: Path, value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes repository root: {path}") from exc
    return path


def _items(payload: dict[str, Any], label: str) -> list[VerificationItem]:
    if payload.get("schema_version") != PILOT_SCHEMA_VERSION:
        raise ValueError(f"{label} has an unsupported verifier schema")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError(f"{label} does not contain items")
    result = []
    seen = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict) or set(raw_item) - ITEM_FIELDS:
            raise ValueError(f"{label} contains an invalid item")
        item = VerificationItem.from_dict(raw_item)
        if item.item_id in seen:
            raise ValueError(f"{label} duplicates {item.item_id}")
        seen.add(item.item_id)
        result.append(item)
    return result


def _truth_record(item: VerificationItem, queue_id: str) -> dict[str, Any]:
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


def _load_guided(
    spec: OwnerLayerSpec,
) -> tuple[dict[str, Any], list[VerificationItem], list[dict[str, Any]]]:
    _require_hash(spec.guided_completion_lock, "guided completion lock")
    completion = _read_json(spec.guided_completion_lock.path)
    if completion.get("schema_version") != "1.0" \
            or completion.get("status") != "complete":
        raise ValueError("Guided completion lock is not complete")
    state_path = _resolve_inside(
        spec.root, completion["state_file"], "guided state")
    truth_path = _resolve_inside(
        spec.root, completion["truth_file"], "guided truth")
    queue_path = _resolve_inside(
        spec.root, completion["locked_queue_file"], "guided queue")
    state = LockedFile(state_path, completion["state_sha256"])
    truth = LockedFile(truth_path, completion["truth_sha256"])
    queue = LockedFile(queue_path, completion["locked_queue_sha256"])
    _require_hash(state, "guided frozen state")
    _require_hash(truth, "guided frozen truth")
    _require_hash(queue, "guided locked queue")

    state_payload = _read_json(state.path)
    guided_items = _items(state_payload, "guided state")
    ids = tuple(item.item_id for item in guided_items)
    if ids != spec.expected_ids \
            or tuple(completion.get("item_ids", [])) != spec.expected_ids:
        raise ValueError("Guided completion IDs do not match exact five")
    if state_payload.get("queue_id") != completion.get("queue_id"):
        raise ValueError("Guided completion queue ID mismatch")
    for item in guided_items:
        if item.status != "verified" \
                or item.decision not in {"accepted", "revised"}:
            raise ValueError(f"Guided item is not terminal: {item.item_id}")
    truth_records = _read_jsonl(truth.path)
    expected_truth = [
        _truth_record(item, state_payload["queue_id"])
        for item in guided_items
    ]
    if truth_records != expected_truth:
        raise ValueError("Guided state and truth do not mirror")
    return state_payload, guided_items, truth_records


def _load_owner_ranges(
    spec: OwnerLayerSpec,
) -> tuple[dict[str, tuple[int, int]], str, str]:
    _require_hash(spec.review_approval, "review approval sidecar")
    approval = _read_json(spec.review_approval.path)
    if approval.get("schema_version") != "1.0" \
            or approval.get("status") != "approved" \
            or approval.get("approved_by") != "owner":
        raise ValueError("Review approval sidecar is not approved")
    retained = tuple(approval.get("guided_bounds_retained_for", []))
    if set(retained) != spec.guided_bound_ids \
            or len(retained) != len(spec.guided_bound_ids):
        raise ValueError("Guided-bound review approval IDs do not match")
    ranges = {}
    for entry in approval.get("approved_ranges", []):
        item_id = entry.get("item_id")
        if item_id in ranges:
            raise ValueError(f"Review approval duplicates {item_id}")
        ranges[item_id] = (int(entry["start_ms"]), int(entry["end_ms"]))
    if set(ranges) != spec.owner_range_ids:
        raise ValueError("Reviewer-verified range IDs do not match exact three")
    recorded_at = str(approval.get("approval_recorded_at") or "")
    recorded_at_basis = str(
        approval.get("approval_recorded_at_basis") or "")
    if spec.required_approval_recorded_at:
        if recorded_at != spec.required_approval_recorded_at:
            raise ValueError(
                "Review approval recorded timestamp does not match the "
                "hash-pinned v2 specification")
        if recorded_at_basis != spec.required_approval_recorded_at_basis:
            raise ValueError(
                "Review approval recorded timestamp basis does not match the "
                "hash-pinned v2 specification")
    return ranges, recorded_at, recorded_at_basis


def build_owner_layer(spec: OwnerLayerSpec) -> OwnerLayerBundle:
    """Prepare a deterministic five-item completion layer in memory."""
    _require_hash(spec.boundary_queue, "boundary QA locked queue")
    guided_payload, guided_items, _truth = _load_guided(spec)
    owner_ranges, approval_recorded_at, approval_recorded_at_basis = \
        _load_owner_ranges(spec)
    boundary_payload = _read_json(spec.boundary_queue.path)
    boundary_items = _items(boundary_payload, "boundary QA locked queue")
    boundary_by_id = {item.item_id: item for item in boundary_items}
    missing = sorted(set(spec.expected_ids) - set(boundary_by_id))
    if missing:
        raise ValueError(f"Boundary QA provenance is missing: {missing}")

    guided_by_id = {item.item_id: item for item in guided_items}
    final_items = []
    queue_items = []
    range_sources = {}
    for item_id in spec.expected_ids:
        immutable = boundary_by_id[item_id]
        guided = guided_by_id[item_id]
        if item_id in spec.guided_bound_ids:
            start_ms, end_ms = guided.start_ms, guided.end_ms
            range_sources[item_id] = "completed_guided_review"
        else:
            start_ms, end_ms = owner_ranges[item_id]
            range_sources[item_id] = "reviewer_verified_sidecar"
        if start_ms < 0 \
                or end_ms > immutable.film_duration_ms \
                or end_ms - start_ms < MIN_SEGMENT_MS:
            raise ValueError(f"Approved bounds are invalid for {item_id}")

        audit = f"reviewer_verified_range:{start_ms}-{end_ms}"
        history = [
            entry for entry in guided.edit_history
            if not entry.startswith("reviewer_verified_range:")
        ]
        completed_payload = asdict(immutable)
        completed_payload.update({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "status": "verified",
            "decision": "revised",
            "verified_at": approval_recorded_at or guided.verified_at,
            "edit_history": [*history, audit],
        })
        final_items.append(VerificationItem.from_dict(completed_payload))

        queue_payload = asdict(immutable)
        queue_payload.update({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "status": "pending",
            "decision": "",
            "verified_at": "",
            "edit_history": [],
        })
        queue_items.append(VerificationItem.from_dict(queue_payload))

    created_at = guided_payload.get("created_at", "")
    updated_at = (
        approval_recorded_at or guided_payload.get("updated_at", ""))
    if not updated_at:
        raise ValueError("Owner layer has no frozen timestamp")

    def state_payload(
        items: list[VerificationItem],
        truth_path: Path,
        last_action: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": PILOT_SCHEMA_VERSION,
            "queue_id": spec.output_queue_id,
            "created_at": created_at,
            "updated_at": updated_at,
            "ground_truth_path": _relative(spec.root, truth_path),
            "cursor": 0,
            "cursor_item_id": items[0].item_id,
            "last_action": last_action,
            "items": [asdict(item) for item in items],
        }

    state_bytes = _json_bytes(state_payload(
        final_items,
        spec.output_truth,
        "frozen reviewer-verified final five",
    ))
    truth_bytes = _jsonl_bytes(
        _truth_record(item, spec.output_queue_id)
        for item in final_items
    )
    queue_bytes = _json_bytes(state_payload(
        queue_items,
        spec.output_truth,
        "locked reviewer-verified final five queue",
    ))
    state_hash = sha256_bytes(state_bytes)
    truth_hash = sha256_bytes(truth_bytes)
    queue_hash = sha256_bytes(queue_bytes)
    lock_payload = {
        "schema_version": "1.0",
        "status": "complete",
        "layer_id": spec.output_layer_id,
        "queue_id": spec.output_queue_id,
        "state_file": _relative(spec.root, spec.output_state),
        "state_sha256": state_hash,
        "truth_file": _relative(spec.root, spec.output_truth),
        "truth_sha256": truth_hash,
        "locked_queue_file": _relative(spec.root, spec.output_queue),
        "locked_queue_sha256": queue_hash,
        "item_ids": list(spec.expected_ids),
        "authority": "Reviewer verified final ranges after guided review.",
        "input_locks": {
            "guided_completion": {
                "file": _relative(
                    spec.root, spec.guided_completion_lock.path),
                "sha256": spec.guided_completion_lock.sha256,
            },
            "review_approval": {
                "file": _relative(spec.root, spec.review_approval.path),
                "sha256": spec.review_approval.sha256,
            },
            "boundary_qa_queue": {
                "file": _relative(spec.root, spec.boundary_queue.path),
                "sha256": spec.boundary_queue.sha256,
            },
        },
        "range_source_by_item_id": range_sources,
    }
    if approval_recorded_at:
        lock_payload["approval_recorded_at"] = approval_recorded_at
        lock_payload[
            "approval_recorded_at_basis"
        ] = approval_recorded_at_basis
    lock_bytes = _json_bytes(lock_payload)
    writes = (
        (spec.output_state, state_bytes),
        (spec.output_truth, truth_bytes),
        (spec.output_queue, queue_bytes),
        (spec.output_lock, lock_bytes),
    )
    if len({path for path, _data in writes}) != len(writes):
        raise ValueError("Owner-layer output paths are duplicated")
    existing = [str(path) for path, _data in writes if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to replace owner-layer artifacts: "
            + ", ".join(existing))
    return OwnerLayerBundle(
        writes=writes,
        completion_lock_sha256=sha256_bytes(lock_bytes),
        state_sha256=state_hash,
        truth_sha256=truth_hash,
        queue_sha256=queue_hash,
    )


def write_owner_layer(bundle: OwnerLayerBundle) -> None:
    """Publish all four prepared artifacts without overwriting a target."""
    staged: list[tuple[Path, Path]] = []
    created: list[Path] = []
    try:
        for target, data in bundle.writes:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(data)
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


def default_spec() -> OwnerLayerSpec:
    return OwnerLayerSpec(
        root=ROOT,
        guided_completion_lock=LockedFile(
            GUIDED_COMPLETION_LOCK, GUIDED_COMPLETION_LOCK_SHA256),
        review_approval=LockedFile(
            REVIEW_APPROVAL, REVIEW_APPROVAL_SHA256),
        boundary_queue=LockedFile(
            BOUNDARY_QA_QUEUE, BOUNDARY_QA_QUEUE_SHA256),
        output_state=OUTPUT_STATE,
        output_truth=OUTPUT_TRUTH,
        output_queue=OUTPUT_QUEUE,
        output_lock=OUTPUT_LOCK,
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Build the reviewer-verified Iteration 3A final-five layer")
    value.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and build bytes without writing artifacts.",
    )
    return value


def main() -> int:
    args = parser().parse_args()
    spec = default_spec()
    bundle = build_owner_layer(spec)
    if not args.check_only:
        write_owner_layer(bundle)
    print(json.dumps({
        "status": "validated" if args.check_only else "frozen",
        "state_sha256": bundle.state_sha256,
        "truth_sha256": bundle.truth_sha256,
        "locked_queue_sha256": bundle.queue_sha256,
        "completion_lock": str(spec.output_lock),
        "completion_lock_sha256": bundle.completion_lock_sha256,
        "finalizer_argument": (
            f"{spec.output_lock}={bundle.completion_lock_sha256}"
        ),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
