"""Tests for freezing completed Iteration 3A correction layers."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from scripts.freeze_iteration_3a_correction_layers import (
    FreezeSpec,
    LayerFreezeSpec,
    LockedFile,
    _truth_record,
    build_freeze,
    sha256_file,
    write_freeze,
)
from tapesift.research.segmentation_verification import VerificationItem


def _item(
    item_id: str,
    *,
    start_ms: int,
    end_ms: int,
    status: str = "verified",
    decision: str = "revised",
) -> VerificationItem:
    return VerificationItem(
        item_id=item_id,
        film_id="film",
        film_name="Film",
        source_file="C:/film.mp4",
        analysis_source="C:/film.mp4",
        frame_rate=60.0,
        film_duration_ms=10_000,
        start_ms=start_ms,
        end_ms=end_ms,
        original_start_ms=start_ms,
        original_end_ms=end_ms,
        candidate_kind="play",
        candidate_index=0,
        prediction_indices=[0],
        angle_starts_ms=[start_ms],
        detector_needs_review=True,
        detector_reason="review",
        detector_signal="scene",
        angle_count=1,
        candidate_stratum="other_review",
        status=status,
        decision=decision,
        edit_history=["reviewed"] if status == "verified" else [],
        verified_at=(
            "2026-07-25T01:00:00+00:00"
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
        "updated_at": "2026-07-25T01:00:00+00:00",
        "ground_truth_path": "truth.jsonl",
        "cursor": 0,
        "cursor_item_id": items[0].item_id,
        "last_action": "fixture",
        "items": [asdict(item) for item in items],
    }


def _layer(
    root: Path,
    layer_id: str,
    items: list[VerificationItem],
    *,
    preexisting_frozen: bool,
) -> LayerFreezeSpec:
    folder = root / layer_id
    state = folder / "state.json"
    truth = folder / "truth.jsonl"
    queue = folder / "queue.json"
    queue_id = f"queue-{layer_id}"
    queued = [
        replace(
            item,
            status="pending",
            decision="",
            edit_history=[],
            verified_at="",
        )
        for item in items
    ]
    _json(state, _state(queue_id, items))
    truth.write_text(
        "".join(
            json.dumps(
                _truth_record(item, queue_id),
                ensure_ascii=False,
                sort_keys=True,
            ) + "\n"
            for item in items
        ),
        encoding="utf-8",
    )
    _json(queue, _state(queue_id, queued))
    frozen_state = folder / "completed.state.json"
    frozen_truth = folder / "completed.truth.jsonl"
    if preexisting_frozen:
        frozen_state.write_bytes(state.read_bytes())
        frozen_truth.write_bytes(truth.read_bytes())
    return LayerFreezeSpec(
        layer_id=layer_id,
        queue_id=queue_id,
        item_ids=tuple(item.item_id for item in items),
        source_state=LockedFile(state, sha256_file(state)),
        source_truth=LockedFile(truth, sha256_file(truth)),
        locked_queue=LockedFile(queue, sha256_file(queue)),
        frozen_state=frozen_state,
        frozen_truth=frozen_truth,
        completion_lock=folder / "completion.lock.json",
        frozen_copies_already_exist=preexisting_frozen,
    )


@pytest.fixture
def spec(tmp_path: Path) -> FreezeSpec:
    seven = _layer(
        tmp_path,
        "seven",
        [_item("a", start_ms=0, end_ms=1_000),
         _item("b", start_ms=2_000, end_ms=3_000)],
        preexisting_frozen=True,
    )
    guided = _layer(
        tmp_path,
        "guided",
        [_item("a", start_ms=100, end_ms=900)],
        preexisting_frozen=False,
    )
    return FreezeSpec(tmp_path, (seven, guided))


def test_builds_complete_locks_and_independent_guided_copies(
        spec: FreezeSpec) -> None:
    bundle = build_freeze(spec)
    targets = [path for path, _data in bundle.writes]
    seven, guided = spec.layers

    assert seven.frozen_state not in targets
    assert seven.frozen_truth not in targets
    assert guided.frozen_state in targets
    assert guided.frozen_truth in targets
    assert seven.completion_lock in targets
    assert guided.completion_lock in targets

    lock_bytes = dict(bundle.writes)[guided.completion_lock]
    lock = json.loads(lock_bytes)
    assert lock["schema_version"] == "1.0"
    assert lock["status"] == "complete"
    assert lock["layer_id"] == "guided"
    assert lock["item_ids"] == ["a"]
    assert lock["state_sha256"] == guided.source_state.sha256
    assert lock["truth_sha256"] == guided.source_truth.sha256


def test_write_publishes_exact_bytes_and_hashes(spec: FreezeSpec) -> None:
    bundle = build_freeze(spec)
    write_freeze(bundle)
    seven, guided = spec.layers

    assert guided.frozen_state.read_bytes() == \
        guided.source_state.path.read_bytes()
    assert guided.frozen_truth.read_bytes() == \
        guided.source_truth.path.read_bytes()
    for lock_path, expected_hash in bundle.lock_hashes.items():
        assert sha256_file(lock_path) == expected_hash
    assert seven.frozen_state.read_bytes() == \
        seven.source_state.path.read_bytes()


def test_hash_drift_fails_before_any_output(spec: FreezeSpec) -> None:
    guided = spec.layers[1]
    guided.source_state.path.write_text(
        guided.source_state.path.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        build_freeze(spec)

    assert not guided.frozen_state.exists()
    assert not guided.completion_lock.exists()


def test_state_truth_mismatch_and_nonterminal_state_are_rejected(
        spec: FreezeSpec) -> None:
    guided = spec.layers[1]
    guided.source_truth.path.write_text("", encoding="utf-8")
    guided = replace(
        guided,
        source_truth=LockedFile(
            guided.source_truth.path, sha256_file(guided.source_truth.path)),
    )
    mismatched = FreezeSpec(spec.root, (spec.layers[0], guided))
    with pytest.raises(ValueError, match="do not mirror"):
        build_freeze(mismatched)

    payload = json.loads(guided.source_state.path.read_text(encoding="utf-8"))
    payload["items"][0]["status"] = "pending"
    payload["items"][0]["decision"] = ""
    payload["items"][0]["verified_at"] = ""
    _json(guided.source_state.path, payload)
    guided = replace(
        guided,
        source_state=LockedFile(
            guided.source_state.path, sha256_file(guided.source_state.path)),
    )
    nonterminal = FreezeSpec(spec.root, (spec.layers[0], guided))
    with pytest.raises(ValueError, match="not terminal"):
        build_freeze(nonterminal)


def test_wrong_ids_and_existing_outputs_are_rejected(
        spec: FreezeSpec) -> None:
    guided = replace(spec.layers[1], item_ids=("wrong",))
    with pytest.raises(ValueError, match="item IDs"):
        build_freeze(FreezeSpec(spec.root, (spec.layers[0], guided)))

    guided = spec.layers[1]
    guided.frozen_state.write_text("do not replace", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing"):
        build_freeze(spec)
    assert guided.frozen_state.read_text(encoding="utf-8") == "do not replace"
