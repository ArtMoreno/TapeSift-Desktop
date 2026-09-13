"""Tests for the reviewer-verified Iteration 3A five-item overlay."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from scripts.build_iteration_3a_reviewed_layer import (
    LockedFile,
    OwnerLayerSpec,
    _truth_record,
    build_owner_layer,
    sha256_file,
    write_owner_layer,
)
from scripts.build_iteration_3a_reviewed_layer_v2 import (
    APPROVAL_RECORDED_AT,
    APPROVAL_RECORDED_AT_BASIS,
    OUTPUT_LAYER_ID as V2_LAYER_ID,
    OUTPUT_QUEUE_ID as V2_QUEUE_ID,
    default_spec as v2_default_spec,
)
from tapesift.research.segmentation_verification import VerificationItem


IDS = ("sc", "u23", "u32", "u35", "u42")


def _item(
    item_id: str,
    start_ms: int,
    end_ms: int,
    *,
    reason: str,
    status: str,
) -> VerificationItem:
    return VerificationItem(
        item_id=item_id,
        film_id="film",
        film_name="Film",
        source_file="C:/film.mp4",
        analysis_source="C:/film.mp4",
        frame_rate=60.0,
        film_duration_ms=20_000,
        start_ms=start_ms,
        end_ms=end_ms,
        original_start_ms=start_ms,
        original_end_ms=end_ms,
        candidate_kind="play",
        candidate_index=3,
        prediction_indices=[3],
        angle_starts_ms=[start_ms],
        detector_needs_review=True,
        detector_reason=reason,
        detector_signal="scene",
        angle_count=1,
        candidate_stratum="other_review",
        status=status,
        decision="revised" if status == "verified" else "",
        edit_history=["guided-edit"] if status == "verified" else [],
        verified_at=(
            "2026-07-25T05:00:00+00:00"
            if status == "verified" else ""
        ),
    )


def _json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _state(queue_id: str, items: list[VerificationItem]) -> dict:
    return {
        "schema_version": "1.0",
        "queue_id": queue_id,
        "created_at": "2026-07-25T00:00:00+00:00",
        "updated_at": "2026-07-25T05:00:00+00:00",
        "ground_truth_path": "truth.jsonl",
        "cursor": 0,
        "cursor_item_id": items[0].item_id,
        "last_action": "fixture",
        "items": [asdict(item) for item in items],
    }


@pytest.fixture
def spec(tmp_path: Path) -> OwnerLayerSpec:
    boundary_items = [
        _item(item_id, index * 3_000, index * 3_000 + 2_000,
              reason=f"boundary-reason-{item_id}", status="pending")
        for index, item_id in enumerate(IDS)
    ]
    boundary_queue = tmp_path / "boundary.queue.json"
    _json(boundary_queue, _state("boundary", boundary_items))

    guided_ranges = {
        "sc": (100, 900),
        "u23": (3_100, 4_900),
        "u32": (6_100, 7_000),
        "u35": (9_100, 10_000),
        "u42": (12_100, 13_000),
    }
    guided_items = []
    for baseline in boundary_items:
        start_ms, end_ms = guided_ranges[baseline.item_id]
        guided_items.append(replace(
            baseline,
            start_ms=start_ms,
            end_ms=end_ms,
            # Simulate the exact contamination this builder must discard.
            detector_reason="contaminated-guided-reason",
            status="verified",
            decision="revised",
            edit_history=["guided-edit"],
            verified_at="2026-07-25T05:00:00+00:00",
        ))
    guided_state = tmp_path / "guided.state.json"
    guided_truth = tmp_path / "guided.truth.jsonl"
    guided_queue = tmp_path / "guided.queue.json"
    _json(guided_state, _state("guided", guided_items))
    guided_truth.write_text(
        "".join(
            json.dumps(
                _truth_record(item, "guided"),
                ensure_ascii=False,
                sort_keys=True,
            ) + "\n"
            for item in guided_items
        ),
        encoding="utf-8",
    )
    _json(guided_queue, _state("guided", [
        replace(
            item,
            status="pending",
            decision="",
            edit_history=[],
            verified_at="",
        )
        for item in guided_items
    ]))
    completion = tmp_path / "guided.completion.lock.json"
    _json(completion, {
        "schema_version": "1.0",
        "status": "complete",
        "layer_id": "guided",
        "queue_id": "guided",
        "state_file": str(guided_state.relative_to(tmp_path)),
        "state_sha256": sha256_file(guided_state),
        "truth_file": str(guided_truth.relative_to(tmp_path)),
        "truth_sha256": sha256_file(guided_truth),
        "locked_queue_file": str(guided_queue.relative_to(tmp_path)),
        "locked_queue_sha256": sha256_file(guided_queue),
        "item_ids": list(IDS),
    })

    approval = tmp_path / "owner.json"
    _json(approval, {
        "schema_version": "1.0",
        "status": "approved",
        "approved_by": "owner",
        "guided_bounds_retained_for": ["sc", "u23"],
        "approved_ranges": [
            {"item_id": "u32", "start_ms": 6_000, "end_ms": 8_000},
            {"item_id": "u35", "start_ms": 9_000, "end_ms": 11_000},
            {"item_id": "u42", "start_ms": 12_000, "end_ms": 14_000},
        ],
    })
    return OwnerLayerSpec(
        root=tmp_path,
        guided_completion_lock=LockedFile(
            completion, sha256_file(completion)),
        review_approval=LockedFile(approval, sha256_file(approval)),
        boundary_queue=LockedFile(
            boundary_queue, sha256_file(boundary_queue)),
        output_state=tmp_path / "out" / "state.json",
        output_truth=tmp_path / "out" / "truth.jsonl",
        output_queue=tmp_path / "out" / "queue.json",
        output_lock=tmp_path / "out" / "completion.lock.json",
        expected_ids=IDS,
        guided_bound_ids=frozenset({"sc", "u23"}),
        owner_range_ids=frozenset({"u32", "u35", "u42"}),
        output_queue_id="owner-final",
    )


def test_restores_boundary_provenance_and_applies_exact_ranges(
        spec: OwnerLayerSpec) -> None:
    first = build_owner_layer(spec)
    second = build_owner_layer(spec)
    assert first.writes == second.writes
    assert first.completion_lock_sha256 == second.completion_lock_sha256

    outputs = dict(first.writes)
    state = json.loads(outputs[spec.output_state])
    items = {item["item_id"]: item for item in state["items"]}
    assert (items["sc"]["start_ms"], items["sc"]["end_ms"]) == (100, 900)
    assert (items["u23"]["start_ms"], items["u23"]["end_ms"]) == \
        (3_100, 4_900)
    assert (items["u32"]["start_ms"], items["u32"]["end_ms"]) == \
        (6_000, 8_000)
    assert (items["u35"]["start_ms"], items["u35"]["end_ms"]) == \
        (9_000, 11_000)
    assert (items["u42"]["start_ms"], items["u42"]["end_ms"]) == \
        (12_000, 14_000)
    for item_id, item in items.items():
        assert item["detector_reason"] == f"boundary-reason-{item_id}"
        assert item["status"] == "verified"
        assert item["decision"] == "revised"
        assert item["edit_history"] == [
            "guided-edit",
            f"reviewer_verified_range:{item['start_ms']}-{item['end_ms']}",
        ]

    queue = json.loads(outputs[spec.output_queue])
    assert all(item["status"] == "pending" for item in queue["items"])
    assert all(item["detector_reason"].startswith("boundary-reason-")
               for item in queue["items"])
    lock = json.loads(outputs[spec.output_lock])
    assert lock["status"] == "complete"
    assert lock["input_locks"]["review_approval"]["sha256"] == \
        spec.review_approval.sha256


def test_written_outputs_match_reported_hashes(spec: OwnerLayerSpec) -> None:
    bundle = build_owner_layer(spec)
    write_owner_layer(bundle)

    assert sha256_file(spec.output_state) == bundle.state_sha256
    assert sha256_file(spec.output_truth) == bundle.truth_sha256
    assert sha256_file(spec.output_queue) == bundle.queue_sha256
    assert sha256_file(spec.output_lock) == bundle.completion_lock_sha256


def test_v2_uses_recorded_approval_time_for_records_and_layer(
        spec: OwnerLayerSpec) -> None:
    approval = json.loads(
        spec.review_approval.path.read_text(encoding="utf-8"))
    approval.update({
        "approval_recorded_at": APPROVAL_RECORDED_AT,
        "approval_recorded_at_basis": APPROVAL_RECORDED_AT_BASIS,
    })
    _json(spec.review_approval.path, approval)
    spec = replace(
        spec,
        review_approval=LockedFile(
            spec.review_approval.path, sha256_file(spec.review_approval.path)),
        output_state=spec.root / "v2" / "completed.state.json",
        output_truth=spec.root / "v2" / "completed.verified.jsonl",
        output_queue=spec.root / "v2" / "queue.locked.json",
        output_lock=spec.root / "v2" / "completion.lock.json",
        output_queue_id=V2_QUEUE_ID,
        output_layer_id=V2_LAYER_ID,
        required_approval_recorded_at=APPROVAL_RECORDED_AT,
        required_approval_recorded_at_basis=APPROVAL_RECORDED_AT_BASIS,
    )

    bundle = build_owner_layer(spec)
    outputs = dict(bundle.writes)
    state = json.loads(outputs[spec.output_state])
    queue = json.loads(outputs[spec.output_queue])
    truth = [
        json.loads(line)
        for line in outputs[spec.output_truth].decode("utf-8").splitlines()
    ]
    lock = json.loads(outputs[spec.output_lock])

    assert state["updated_at"] == APPROVAL_RECORDED_AT
    assert queue["updated_at"] == APPROVAL_RECORDED_AT
    assert all(
        item["verified_at"] == APPROVAL_RECORDED_AT
        for item in state["items"]
    )
    assert all(
        record["verified_at"] == APPROVAL_RECORDED_AT
        for record in truth
    )
    assert lock["layer_id"] == V2_LAYER_ID
    assert lock["queue_id"] == V2_QUEUE_ID
    assert lock["approval_recorded_at"] == APPROVAL_RECORDED_AT
    assert lock["approval_recorded_at_basis"] == \
        APPROVAL_RECORDED_AT_BASIS
    assert lock["input_locks"]["review_approval"]["sha256"] == \
        spec.review_approval.sha256


def test_v2_rejects_missing_or_relabelled_recorded_timestamp(
        spec: OwnerLayerSpec) -> None:
    spec = replace(
        spec,
        output_layer_id=V2_LAYER_ID,
        required_approval_recorded_at=APPROVAL_RECORDED_AT,
        required_approval_recorded_at_basis=APPROVAL_RECORDED_AT_BASIS,
    )
    with pytest.raises(ValueError, match="recorded timestamp does not match"):
        build_owner_layer(spec)

    approval = json.loads(
        spec.review_approval.path.read_text(encoding="utf-8"))
    approval.update({
        "approval_recorded_at": APPROVAL_RECORDED_AT,
        "approval_recorded_at_basis": "Claimed exact click time",
    })
    _json(spec.review_approval.path, approval)
    spec = replace(
        spec,
        review_approval=LockedFile(
            spec.review_approval.path, sha256_file(spec.review_approval.path)),
    )
    with pytest.raises(ValueError, match="timestamp basis does not match"):
        build_owner_layer(spec)


def test_v2_default_paths_and_ids_do_not_target_v1_artifacts() -> None:
    spec = v2_default_spec()

    assert spec.review_approval.path.name == \
        "reviewer_verified_final_ranges_v2.json"
    assert spec.review_approval.sha256 == \
        "6c074b9d2d1f6469e98470b618eba3596603c48689f9ae0ed6453a26ab3f14ae"
    assert spec.required_approval_recorded_at == APPROVAL_RECORDED_AT
    assert spec.required_approval_recorded_at_basis == \
        APPROVAL_RECORDED_AT_BASIS
    assert spec.output_state.name == \
        "iteration_3a_reviewer_verified_final_five_v2.completed.raw.state.json"
    assert spec.output_truth.name == \
        "iteration_3a_reviewer_verified_final_five_v2.completed.raw.verified.jsonl"
    assert spec.output_queue.name == \
        "iteration_3a_reviewer_verified_final_five_v2.queue.locked.json"
    assert spec.output_lock.name == \
        "reviewer_verified_final_five_v2.completion.lock.json"
    assert spec.output_layer_id == "reviewer-verified-final-five-v2"
    assert spec.output_queue_id.endswith("reviewer-verified-final-five-v2")
    assert all("_v2" in path.name for path in (
        spec.output_state,
        spec.output_truth,
        spec.output_queue,
        spec.output_lock,
    ))


def test_input_hash_drift_and_wrong_approval_ids_fail_closed(
        spec: OwnerLayerSpec) -> None:
    spec.review_approval.path.write_text(
        spec.review_approval.path.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        build_owner_layer(spec)
    assert not spec.output_state.exists()

    approval = json.loads(
        spec.review_approval.path.read_text(encoding="utf-8"))
    approval["guided_bounds_retained_for"] = ["sc", "wrong"]
    _json(spec.review_approval.path, approval)
    spec = replace(
        spec,
        review_approval=LockedFile(
            spec.review_approval.path, sha256_file(spec.review_approval.path)),
    )
    with pytest.raises(ValueError, match="IDs do not match"):
        build_owner_layer(spec)


def test_refuses_to_replace_any_owner_layer_output(
        spec: OwnerLayerSpec) -> None:
    spec.output_truth.parent.mkdir(parents=True, exist_ok=True)
    spec.output_truth.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Refusing"):
        build_owner_layer(spec)

    assert spec.output_truth.read_text(encoding="utf-8") == "keep"
