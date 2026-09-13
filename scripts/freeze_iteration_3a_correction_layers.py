"""Freeze the two completed correction layers used by Iteration 3A.

This is intentionally narrower than the holdout finalizer.  It validates two
known verifier queues byte-for-byte, creates independent byte copies of the
guided-five completion, and emits the ``status=complete`` lock schema consumed
by ``finalize_iteration_3a_holdout.py``.  It never writes final benchmark truth.
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

SEVEN_IDS = (
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
)
GUIDED_FIVE_IDS = SEVEN_IDS[:5]

REVIEW_MUTABLE_FIELDS = frozenset({
    "start_ms",
    "end_ms",
    "status",
    "decision",
    "edit_history",
    "verified_at",
})
ITEM_FIELDS = frozenset(entry.name for entry in fields(VerificationItem))


@dataclass(frozen=True)
class LockedFile:
    path: Path
    sha256: str


@dataclass(frozen=True)
class LayerFreezeSpec:
    layer_id: str
    queue_id: str
    item_ids: tuple[str, ...]
    source_state: LockedFile
    source_truth: LockedFile
    locked_queue: LockedFile
    frozen_state: Path
    frozen_truth: Path
    completion_lock: Path
    frozen_copies_already_exist: bool = False


@dataclass(frozen=True)
class FreezeSpec:
    root: Path
    layers: tuple[LayerFreezeSpec, ...]


@dataclass
class FreezeBundle:
    writes: tuple[tuple[Path, bytes], ...]
    lock_hashes: dict[Path, str]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _require_hash(artifact: LockedFile, label: str) -> None:
    if not artifact.path.is_file():
        raise FileNotFoundError(f"{label} is missing: {artifact.path}")
    actual = sha256_file(artifact.path)
    if actual != artifact.sha256:
        raise ValueError(
            f"{label} hash mismatch for {artifact.path}: "
            f"expected {artifact.sha256}, found {actual}")


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


def _relative(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")


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
            raise ValueError(f"{label} contains an invalid item schema")
        item = VerificationItem.from_dict(raw_item)
        if item.item_id in seen:
            raise ValueError(f"{label} duplicates item {item.item_id}")
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


def _same_except(
    queued: VerificationItem,
    completed: VerificationItem,
    label: str,
) -> None:
    before = asdict(queued)
    after = asdict(completed)
    changed = {
        name for name in ITEM_FIELDS if before[name] != after[name]
    }
    unexpected = sorted(changed - REVIEW_MUTABLE_FIELDS)
    if unexpected:
        raise ValueError(
            f"{label} changes immutable fields for {completed.item_id}: "
            f"{', '.join(unexpected)}")


def _validate_layer(layer: LayerFreezeSpec) -> None:
    _require_hash(layer.source_state, f"{layer.layer_id} state")
    _require_hash(layer.source_truth, f"{layer.layer_id} truth")
    _require_hash(layer.locked_queue, f"{layer.layer_id} locked queue")
    if layer.frozen_copies_already_exist:
        _require_hash(
            LockedFile(layer.frozen_state, layer.source_state.sha256),
            f"{layer.layer_id} frozen state",
        )
        _require_hash(
            LockedFile(layer.frozen_truth, layer.source_truth.sha256),
            f"{layer.layer_id} frozen truth",
        )
    elif layer.frozen_state.exists() or layer.frozen_truth.exists():
        raise FileExistsError(
            f"Refusing to replace guided frozen copies for {layer.layer_id}")

    state_payload = _read_json(layer.source_state.path)
    queue_payload = _read_json(layer.locked_queue.path)
    state_items = _items(state_payload, f"{layer.layer_id} state")
    queue_items = _items(queue_payload, f"{layer.layer_id} queue")
    state_ids = tuple(item.item_id for item in state_items)
    queue_ids = tuple(item.item_id for item in queue_items)
    if state_payload.get("queue_id") != layer.queue_id \
            or queue_payload.get("queue_id") != layer.queue_id:
        raise ValueError(f"{layer.layer_id} queue ID mismatch")
    if state_ids != layer.item_ids or queue_ids != layer.item_ids:
        raise ValueError(f"{layer.layer_id} item IDs do not match exact lock")
    if len(set(layer.item_ids)) != len(layer.item_ids):
        raise ValueError(f"{layer.layer_id} expected IDs are duplicated")

    queue_by_id = {item.item_id: item for item in queue_items}
    for item in state_items:
        if item.status != "verified" \
                or item.decision not in {"accepted", "revised"}:
            raise ValueError(
                f"{layer.layer_id} is not terminal at {item.item_id}")
        if item.start_ms < 0 \
                or item.end_ms > item.film_duration_ms \
                or item.end_ms - item.start_ms < MIN_SEGMENT_MS:
            raise ValueError(
                f"{layer.layer_id} has invalid bounds at {item.item_id}")
        _same_except(
            queue_by_id[item.item_id], item, layer.layer_id)
    if any(item.status != "pending" for item in queue_items):
        raise ValueError(f"{layer.layer_id} locked queue is not pristine")

    expected_truth = [
        _truth_record(item, layer.queue_id) for item in state_items
    ]
    if _read_jsonl(layer.source_truth.path) != expected_truth:
        raise ValueError(
            f"{layer.layer_id} state and truth do not mirror each other")


def build_freeze(spec: FreezeSpec) -> FreezeBundle:
    """Validate both layers and prepare all new bytes before any write."""
    if not spec.layers:
        raise ValueError("No correction layers were configured")
    completion_paths = [layer.completion_lock for layer in spec.layers]
    if len(completion_paths) != len(set(completion_paths)):
        raise ValueError("Completion lock paths are duplicated")
    for layer in spec.layers:
        _validate_layer(layer)
        if layer.completion_lock.exists():
            raise FileExistsError(
                f"Refusing to replace completion lock: "
                f"{layer.completion_lock}")

    writes: list[tuple[Path, bytes]] = []
    lock_hashes = {}
    for layer in spec.layers:
        if not layer.frozen_copies_already_exist:
            writes.extend([
                (layer.frozen_state, layer.source_state.path.read_bytes()),
                (layer.frozen_truth, layer.source_truth.path.read_bytes()),
            ])
        lock_payload = {
            "schema_version": "1.0",
            "status": "complete",
            "layer_id": layer.layer_id,
            "queue_id": layer.queue_id,
            "state_file": _relative(spec.root, layer.frozen_state),
            "state_sha256": layer.source_state.sha256,
            "truth_file": _relative(spec.root, layer.frozen_truth),
            "truth_sha256": layer.source_truth.sha256,
            "locked_queue_file": _relative(
                spec.root, layer.locked_queue.path),
            "locked_queue_sha256": layer.locked_queue.sha256,
            "item_ids": list(layer.item_ids),
        }
        lock_bytes = _json_bytes(lock_payload)
        writes.append((layer.completion_lock, lock_bytes))
        lock_hashes[layer.completion_lock] = sha256_bytes(lock_bytes)
    targets = [path for path, _data in writes]
    if len(targets) != len(set(targets)):
        raise ValueError("Freeze output paths are duplicated")
    existing = [str(path) for path in targets if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to replace freeze outputs: " + ", ".join(existing))
    return FreezeBundle(tuple(writes), lock_hashes)


def write_freeze(bundle: FreezeBundle) -> None:
    """Publish all prepared bytes without overwriting an existing target."""
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


def default_spec() -> FreezeSpec:
    seven_state = (
        VERIFICATION_DIR / "iteration_3a_final_corrections_v1.state.json")
    seven_truth = (
        VERIFICATION_DIR
        / "iteration_3a_final_corrections_v1.verified.jsonl")
    seven_queue = (
        VERIFICATION_DIR
        / "iteration_3a_final_corrections_v1.queue.locked.json")
    seven_frozen_state = (
        VERIFICATION_DIR
        / "iteration_3a_final_corrections_v1.completed.raw.state.json")
    seven_frozen_truth = (
        VERIFICATION_DIR
        / "iteration_3a_final_corrections_v1.completed.raw.verified.jsonl")

    guided_state = (
        VERIFICATION_DIR / "iteration_3a_guided_final_five_v1.state.json")
    guided_truth = (
        VERIFICATION_DIR
        / "iteration_3a_guided_final_five_v1.verified.jsonl")
    guided_queue = (
        VERIFICATION_DIR
        / "iteration_3a_guided_final_five_v1.queue.locked.json")
    guided_frozen_state = (
        VERIFICATION_DIR
        / "iteration_3a_guided_final_five_v1.completed.raw.state.json")
    guided_frozen_truth = (
        VERIFICATION_DIR
        / "iteration_3a_guided_final_five_v1.completed.raw.verified.jsonl")

    return FreezeSpec(ROOT, (
        LayerFreezeSpec(
            layer_id="final-corrections-seven-v1",
            queue_id=(
                "segmentation-iteration-3a-holdout-v1-final-corrections"),
            item_ids=SEVEN_IDS,
            source_state=LockedFile(
                seven_state,
                "367ab4184aadba694bb4883ba7c65716e680de36f14f65a09cf3dd8c9e055b03",
            ),
            source_truth=LockedFile(
                seven_truth,
                "ef3cb4c57a3b0d93546f7c36f530daa753b9c359ac1ac4ebcd5e513203e77b0f",
            ),
            locked_queue=LockedFile(
                seven_queue,
                "636b2b62b04ac9976ce5076d4831d57f35c5202e106d50c8f1c2484462f581a0",
            ),
            frozen_state=seven_frozen_state,
            frozen_truth=seven_frozen_truth,
            completion_lock=(
                REPORT_DIR / "final_corrections_seven_v1.completion.lock.json"),
            frozen_copies_already_exist=True,
        ),
        LayerFreezeSpec(
            layer_id="guided-final-five-v1",
            queue_id=(
                "segmentation-iteration-3a-holdout-v1-guided-final-five"),
            item_ids=GUIDED_FIVE_IDS,
            source_state=LockedFile(
                guided_state,
                "c0fd03ccbf166e3d1520ad5e596449e9153b6cebe18d5e698a089abcf2879349",
            ),
            source_truth=LockedFile(
                guided_truth,
                "8bc7b62dc002c2c8de314ff39b50911a20e5ae03eb7e71b8f89302d0ba096f39",
            ),
            locked_queue=LockedFile(
                guided_queue,
                "e0731dea15393539ceb23d3dd94f8d63c12b5a5425e7821cc63d8ed2b279fc0d",
            ),
            frozen_state=guided_frozen_state,
            frozen_truth=guided_frozen_truth,
            completion_lock=(
                REPORT_DIR / "guided_final_five_v1.completion.lock.json"),
        ),
    ))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Freeze completed Iteration 3A correction layers")
    value.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and prepare deterministic bytes without writing them.",
    )
    return value


def main() -> int:
    args = parser().parse_args()
    bundle = build_freeze(default_spec())
    if not args.check_only:
        write_freeze(bundle)
    print(json.dumps({
        "status": "validated" if args.check_only else "frozen",
        "completion_locks": [
            {
                "file": str(path),
                "sha256": digest,
                "finalizer_argument": f"{path}={digest}",
            }
            for path, digest in bundle.lock_hashes.items()
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
