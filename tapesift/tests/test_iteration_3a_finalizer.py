"""Focused tests for the fail-closed Iteration 3A finalizer."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pytest

import scripts.finalize_iteration_3a_holdout as finalizer
from scripts.finalize_iteration_3a_holdout import (
    FinalizationSpec,
    LockedFile,
    build_finalization,
    default_spec,
    sha256_file,
    verification_record,
    write_finalization,
)
from tapesift.research.segmentation_verification import VerificationItem


def _item(
    item_id: str,
    *,
    film_id: str = "film-a",
    start_ms: int,
    end_ms: int,
    status: str = "verified",
    decision: str = "accepted",
    verified_at: str = "2026-07-25T01:00:00+00:00",
    angle_starts: list[int] | None = None,
    edit_history: list[str] | None = None,
) -> VerificationItem:
    return VerificationItem(
        item_id=item_id,
        film_id=film_id,
        film_name=film_id,
        source_file=f"C:/{film_id}.mp4",
        analysis_source=f"C:/{film_id}.mp4",
        frame_rate=60.0,
        film_duration_ms=10_000,
        start_ms=start_ms,
        end_ms=end_ms,
        original_start_ms=start_ms,
        original_end_ms=end_ms,
        candidate_kind="play",
        candidate_index=0,
        prediction_indices=[0],
        angle_starts_ms=list(angle_starts or [start_ms]),
        detector_needs_review=False,
        detector_reason="",
        detector_signal="scene",
        angle_count=len(angle_starts or [start_ms]),
        candidate_stratum="confident",
        status=status,
        decision=decision,
        edit_history=list(edit_history or []),
        verified_at=verified_at if status in {"verified", "excluded"} else "",
    )


def _state_payload(
    queue_id: str,
    items: list[VerificationItem],
    truth_path: Path,
    *,
    updated_at: str = "2026-07-25T02:00:00+00:00",
) -> dict:
    return {
        "schema_version": "1.0",
        "queue_id": queue_id,
        "created_at": "2026-07-25T00:00:00+00:00",
        "updated_at": updated_at,
        "ground_truth_path": str(truth_path),
        "cursor": 0,
        "cursor_item_id": items[0].item_id if items else "",
        "last_action": "test fixture",
        "items": [asdict(item) for item in items],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_pair(
    state_path: Path,
    truth_path: Path,
    queue_id: str,
    items: list[VerificationItem],
    *,
    updated_at: str = "2026-07-25T02:00:00+00:00",
) -> None:
    payload = _state_payload(
        queue_id, items, truth_path, updated_at=updated_at)
    _write_json(state_path, payload)
    records = [
        verification_record(item, queue_id)
        for item in items
        if item.status in {"verified", "excluded"}
    ]
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    truth_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _locked(path: Path) -> LockedFile:
    return LockedFile(path, sha256_file(path))


@dataclass
class Harness:
    root: Path
    raw_items: list[VerificationItem]
    raw_state: Path
    raw_truth: Path
    original_queue: Path
    cleaned_state: Path
    cleaned_truth: Path
    boundary_lock: Path
    qa_state: Path
    qa_truth: Path
    qa_queue: Path
    qa_ids: tuple[str, ...]
    exclusion_ids: tuple[str, ...]
    correction_id: str
    merged_id: str
    forbidden_ids: frozenset[str]

    def make_layer(
        self,
        layer_id: str,
        item: VerificationItem,
        *,
        queue_item: VerificationItem | None = None,
        updated_at: str = "2026-07-25T04:00:00+00:00",
    ) -> LockedFile:
        folder = self.root / "layers" / layer_id
        state = folder / "state.json"
        truth = folder / "truth.jsonl"
        queue = folder / "queue.locked.json"
        queued = queue_item or replace(
            item,
            status="pending",
            decision="",
            verified_at="",
            edit_history=[],
        )
        _write_pair(
            state, truth, f"queue-{layer_id}", [item],
            updated_at=updated_at)
        _write_json(
            queue,
            _state_payload(
                f"queue-{layer_id}",
                [queued],
                folder / "never-written.jsonl",
                updated_at="2026-07-25T03:00:00+00:00",
            ),
        )
        lock = folder / "completion.lock.json"
        _write_json(lock, {
            "schema_version": "1.0",
            "status": "complete",
            "layer_id": layer_id,
            "queue_id": f"queue-{layer_id}",
            "state_file": str(state.relative_to(self.root)),
            "state_sha256": sha256_file(state),
            "truth_file": str(truth.relative_to(self.root)),
            "truth_sha256": sha256_file(truth),
            "locked_queue_file": str(queue.relative_to(self.root)),
            "locked_queue_sha256": sha256_file(queue),
            "item_ids": [item.item_id],
        })
        return _locked(lock)

    def spec(
        self,
        layers: list[LockedFile],
    ) -> FinalizationSpec:
        return FinalizationSpec(
            root=self.root,
            raw_state=_locked(self.raw_state),
            raw_truth=_locked(self.raw_truth),
            original_queue=_locked(self.original_queue),
            cleaned_state=_locked(self.cleaned_state),
            cleaned_truth=_locked(self.cleaned_truth),
            boundary_lock=_locked(self.boundary_lock),
            boundary_qa_state=_locked(self.qa_state),
            boundary_qa_truth=_locked(self.qa_truth),
            boundary_qa_queue=_locked(self.qa_queue),
            correction_locks=tuple(layers),
            final_state=self.root / "final" / "state.json",
            final_truth=self.root / "final" / "truth.jsonl",
            final_lock=self.root / "reports" / "finalization.lock.json",
            expected_raw_count=len(self.raw_items),
            expected_boundary_count=len(self.qa_ids),
            expected_exclusion_count=len(self.exclusion_ids),
            required_correction_ids=frozenset({self.correction_id}),
            preserved_item_id=self.merged_id,
            forbidden_reintroduced_ids=self.forbidden_ids,
        )


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    root = tmp_path
    normal = _item("normal", start_ms=0, end_ms=1_000)
    merged = _item(
        "approved:merge:abc",
        film_id="film-b",
        start_ms=0,
        end_ms=2_000,
        edit_history=["merged:approved-left+approved-right"],
    )
    qa_a_raw = _item(
        "qa-a",
        start_ms=2_000,
        end_ms=3_000,
        decision="revised",
        angle_starts=[2_000, 3_500],
        edit_history=["bad-loop-edit"],
    )
    qa_b_raw = _item("qa-b", start_ms=4_000, end_ms=5_000)
    excluded_raw = _item(
        "exclude-a",
        start_ms=6_000,
        end_ms=7_000,
        status="pending",
        decision="",
    )
    raw_items = [normal, merged, qa_a_raw, qa_b_raw, excluded_raw]

    raw_state = root / "raw.state.json"
    raw_truth = root / "raw.truth.jsonl"
    _write_pair(raw_state, raw_truth, "holdout", raw_items)
    original_queue = root / "original.queue.json"
    _write_json(
        original_queue,
        _state_payload(
            "holdout",
            raw_items,
            root / "unused-original.jsonl",
        ),
    )

    qa_a_queue = replace(
        qa_a_raw,
        status="pending",
        decision="",
        verified_at="",
        edit_history=[],
    )
    qa_b_queue = replace(
        qa_b_raw,
        status="pending",
        decision="",
        verified_at="",
        edit_history=[],
    )
    qa_a = replace(
        qa_a_queue,
        start_ms=1_800,
        end_ms=3_200,
        status="verified",
        decision="revised",
        verified_at="2026-07-25T03:00:00+00:00",
        edit_history=["start@1800", "end@3200"],
    )
    qa_b = replace(
        qa_b_queue,
        status="verified",
        decision="accepted",
        verified_at="2026-07-25T03:01:00+00:00",
    )
    qa_state = root / "qa.state.json"
    qa_truth = root / "qa.truth.jsonl"
    qa_queue = root / "qa.queue.json"
    _write_pair(qa_state, qa_truth, "boundary-qa", [qa_a, qa_b])
    _write_json(
        qa_queue,
        _state_payload(
            "boundary-qa",
            [qa_a_queue, qa_b_queue],
            root / "unused-qa.jsonl",
        ),
    )

    excluded = replace(
        excluded_raw,
        status="excluded",
        decision="excluded",
        verified_at="2026-07-25T02:30:00+00:00",
        edit_history=[
            "qa_excluded:frozen tail or repeated terminal frame; no new snap"
        ],
    )
    cleaned_by_id = {item.item_id: item for item in raw_items}
    cleaned_by_id["qa-a"] = qa_a_queue
    cleaned_by_id["qa-b"] = qa_b_queue
    cleaned_by_id["exclude-a"] = excluded
    cleaned_items = [cleaned_by_id[item.item_id] for item in raw_items]
    cleaned_state = root / "cleaned.state.json"
    cleaned_truth = root / "cleaned.truth.jsonl"
    _write_pair(cleaned_state, cleaned_truth, "holdout", cleaned_items)

    boundary_lock = root / "boundary.lock.json"
    _write_json(boundary_lock, {
        "schema_version": "1.0",
        "boundary_qa_ids": ["qa-a", "qa-b"],
        "frozen_tail_exclusions": ["exclude-a"],
    })
    return Harness(
        root=root,
        raw_items=raw_items,
        raw_state=raw_state,
        raw_truth=raw_truth,
        original_queue=original_queue,
        cleaned_state=cleaned_state,
        cleaned_truth=cleaned_truth,
        boundary_lock=boundary_lock,
        qa_state=qa_state,
        qa_truth=qa_truth,
        qa_queue=qa_queue,
        qa_ids=("qa-a", "qa-b"),
        exclusion_ids=("exclude-a",),
        correction_id="qa-a",
        merged_id="approved:merge:abc",
        forbidden_ids=frozenset({"approved-left", "approved-right"}),
    )


def _valid_correction(
    *,
    end_ms: int = 3_200,
    angle_starts: list[int] | None = None,
) -> VerificationItem:
    item = _item(
        "qa-a",
        start_ms=1_800,
        end_ms=end_ms,
        status="verified",
        decision="revised",
        verified_at="2026-07-25T04:00:00+00:00",
        angle_starts=angle_starts or [2_000, 3_500],
        edit_history=["corrected"],
    )
    return replace(
        item,
        original_start_ms=2_000,
        original_end_ms=3_000,
    )


def test_exact_allowlisted_diff_prunes_angles_and_preserves_merge(
        harness: Harness) -> None:
    correction = _valid_correction(angle_starts=[2_000, 3_500])
    layer = harness.make_layer("seven-completed", correction)
    spec = harness.spec([layer])

    first = build_finalization(spec)
    second = build_finalization(spec)

    assert first.state_text == second.state_text
    assert first.truth_text == second.truth_text
    assert first.lock_text == second.lock_text
    assert first.changed_item_ids == ("exclude-a", "qa-a", "qa-b")

    payload = json.loads(first.state_text)
    final_by_id = {item["item_id"]: item for item in payload["items"]}
    raw_by_id = {item.item_id: asdict(item) for item in harness.raw_items}
    assert [item["item_id"] for item in payload["items"]] == \
        [item.item_id for item in harness.raw_items]
    assert final_by_id[harness.merged_id] == raw_by_id[harness.merged_id]
    assert not (harness.forbidden_ids & set(final_by_id))
    assert final_by_id["qa-a"]["angle_starts_ms"] == [2_000]
    assert final_by_id["qa-a"]["edit_history"][-1] == \
        "finalizer_pruned_angle_starts:3500"
    assert first.angle_pruning[0]["removed"] == [3_500]
    assert final_by_id["exclude-a"]["status"] == "excluded"

    lock = json.loads(first.lock_text)
    assert lock["validation"] == {
        "all_inputs_hash_locked": True,
        "all_items_terminal": True,
        "bounds_valid": True,
        "no_overlaps": True,
        "off_review_allowlist_changes_limited_to_angle_starts": True,
        "semantic_diff_exactly_allowlisted": True,
        "state_truth_mirror": True,
    }
    assert lock["counts"]["changed_items"] == 3


def test_completed_raw_revised_item_prunes_only_angle_starts_and_locks_audit(
        harness: Harness) -> None:
    revised_raw = replace(
        harness.raw_items[0],
        decision="revised",
        angle_starts_ms=[0, 1_500],
        angle_count=2,
        edit_history=["raw_revised_before_boundary_qa"],
    )
    harness.raw_items[0] = revised_raw
    _write_pair(
        harness.raw_state,
        harness.raw_truth,
        "holdout",
        harness.raw_items,
    )
    layer = harness.make_layer("complete", _valid_correction())

    bundle = build_finalization(harness.spec([layer]))

    payload = json.loads(bundle.state_text)
    final = next(item for item in payload["items"] if item["item_id"] == "normal")
    baseline = asdict(revised_raw)
    changed_fields = {
        key for key, value in final.items()
        if baseline[key] != value
    }
    assert changed_fields == {"angle_starts_ms"}
    assert final["angle_starts_ms"] == [0]
    assert final["edit_history"] == ["raw_revised_before_boundary_qa"]

    lock = json.loads(bundle.lock_text)
    pruning = next(
        entry for entry in lock["angle_start_pruning"]
        if entry["item_id"] == "normal"
    )
    assert pruning == {
        "after": [0],
        "audit": "finalizer_pruned_angle_starts:1500",
        "before": [0, 1_500],
        "edit_history_entry": None,
        "item_id": "normal",
        "range": [0, 1_000],
        "removed": [1_500],
        "source": "completed_raw",
    }
    approved = lock["approved_changes"]
    assert approved["derived_angle_start_field_allowlist"] == [
        "angle_starts_ms",
    ]
    assert approved[
        "derived_angle_start_pruning_outside_review_allowlists"
    ] == ["normal"]
    assert approved["source_by_item_id"]["normal"] == "completed_raw"


def test_off_review_allowlist_pruning_rejects_every_other_mutation(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    revised_raw = replace(
        harness.raw_items[0],
        decision="revised",
        angle_starts_ms=[0, 1_500],
        angle_count=2,
    )
    harness.raw_items[0] = revised_raw
    _write_pair(
        harness.raw_state,
        harness.raw_truth,
        "holdout",
        harness.raw_items,
    )
    original_pruner = finalizer._prune_angle_starts

    def contaminated_pruner(
        item: VerificationItem,
        *,
        source: str,
        record_in_edit_history: bool,
    ) -> tuple[VerificationItem, dict | None]:
        pruned, audit = original_pruner(
            item,
            source=source,
            record_in_edit_history=record_in_edit_history,
        )
        if item.item_id == "normal":
            pruned = replace(
                pruned,
                detector_reason="off-allowlist contamination",
            )
        return pruned, audit

    monkeypatch.setattr(
        finalizer,
        "_prune_angle_starts",
        contaminated_pruner,
    )
    layer = harness.make_layer("complete", _valid_correction())

    with pytest.raises(
            ValueError,
            match="outside the boundary/exclusion allowlists.*detector_reason"):
        build_finalization(harness.spec([layer]))


def test_later_guided_layer_overrides_overlapping_earlier_layer(
        harness: Harness) -> None:
    overlapping = _valid_correction(end_ms=4_500)
    first_layer = harness.make_layer("seven-completed", overlapping)
    with pytest.raises(ValueError, match="overlaps"):
        build_finalization(harness.spec([first_layer]))

    corrected = _valid_correction(end_ms=3_300)
    guided_layer = harness.make_layer(
        "guided-five-completed",
        corrected,
        updated_at="2026-07-25T05:00:00+00:00",
    )
    bundle = build_finalization(harness.spec([first_layer, guided_layer]))

    payload = json.loads(bundle.state_text)
    final = next(item for item in payload["items"] if item["item_id"] == "qa-a")
    assert final["end_ms"] == 3_300
    lock = json.loads(bundle.lock_text)
    assert lock["approved_changes"]["source_by_item_id"]["qa-a"] == \
        "correction:guided-five-completed"
    assert lock["finalized_at"] == "2026-07-25T05:00:00+00:00"


def test_v2_owner_layer_timestamp_controls_final_state_and_lock(
        harness: Harness) -> None:
    earlier = harness.make_layer(
        "seven-v1",
        _valid_correction(end_ms=3_250),
        updated_at="2026-07-25T23:59:00Z",
    )
    owner_v2 = harness.make_layer(
        "reviewer-verified-final-five-v2",
        _valid_correction(end_ms=3_300),
        updated_at="2026-07-26T00:07:51Z",
    )

    bundle = build_finalization(harness.spec([earlier, owner_v2]))

    state = json.loads(bundle.state_text)
    lock = json.loads(bundle.lock_text)
    assert state["updated_at"] == "2026-07-26T00:07:51Z"
    assert lock["finalized_at"] == "2026-07-26T00:07:51Z"
    assert lock["correction_layers"][-1]["layer_id"] == \
        "reviewer-verified-final-five-v2"


def test_default_finalizer_spec_targets_only_v2_outputs() -> None:
    spec = default_spec(())

    assert spec.final_state.name == \
        "iteration_3a_independent_holdout_v1.final_v2.state.json"
    assert spec.final_truth.name == \
        "iteration_3a_independent_holdout_v1.final_v2.verified.jsonl"
    assert spec.final_lock.name == "finalization_lock_v2.json"
    assert spec.final_state.name != \
        "iteration_3a_independent_holdout_v1.final.state.json"
    assert spec.final_truth.name != \
        "iteration_3a_independent_holdout_v1.final.verified.jsonl"
    assert spec.final_lock.name != "finalization_lock.json"


def test_hash_mismatch_fails_closed(harness: Harness) -> None:
    layer = harness.make_layer("complete", _valid_correction())
    spec = harness.spec([layer])
    harness.raw_state.write_text(
        harness.raw_state.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        build_finalization(spec)
    assert not spec.final_state.exists()
    assert not spec.final_truth.exists()
    assert not spec.final_lock.exists()


def test_state_truth_mismatch_is_rejected(harness: Harness) -> None:
    layer = harness.make_layer("complete", _valid_correction())
    spec = harness.spec([layer])
    harness.qa_truth.write_text("", encoding="utf-8")
    spec = replace(spec, boundary_qa_truth=_locked(harness.qa_truth))

    with pytest.raises(ValueError, match="do not mirror"):
        build_finalization(spec)


def test_missing_or_unexpected_correction_ids_are_rejected(
        harness: Harness) -> None:
    no_layers = harness.spec([])
    with pytest.raises(ValueError, match="At least one"):
        build_finalization(no_layers)

    unexpected = _item(
        "not-allowlisted",
        start_ms=8_000,
        end_ms=9_000,
        decision="revised",
    )
    layer = harness.make_layer("unexpected", unexpected)
    with pytest.raises(ValueError, match="unexpected IDs"):
        build_finalization(harness.spec([layer]))


def test_out_of_bounds_correction_is_rejected(harness: Harness) -> None:
    correction = _valid_correction(end_ms=10_500)
    layer = harness.make_layer("bad-bounds", correction)

    with pytest.raises(ValueError, match="invalid bounds"):
        build_finalization(harness.spec([layer]))


def test_exclusion_may_not_change_boundaries(harness: Harness) -> None:
    cleaned = json.loads(harness.cleaned_state.read_text(encoding="utf-8"))
    excluded = next(
        item for item in cleaned["items"] if item["item_id"] == "exclude-a")
    excluded["start_ms"] += 100
    _write_json(harness.cleaned_state, cleaned)
    # Keep state/truth internally mirrored so the provenance check is reached.
    items = [VerificationItem.from_dict(item) for item in cleaned["items"]]
    _write_pair(
        harness.cleaned_state,
        harness.cleaned_truth,
        "holdout",
        items,
    )
    layer = harness.make_layer("complete", _valid_correction())
    spec = harness.spec([layer])

    with pytest.raises(ValueError, match="immutable fields"):
        build_finalization(spec)


def test_write_refuses_overwrite_without_changing_existing_file(
        harness: Harness) -> None:
    layer = harness.make_layer("complete", _valid_correction())
    spec = harness.spec([layer])
    bundle = build_finalization(spec)
    spec.final_state.parent.mkdir(parents=True, exist_ok=True)
    spec.final_state.write_text("keep-me", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Refusing"):
        write_finalization(spec, bundle)

    assert spec.final_state.read_text(encoding="utf-8") == "keep-me"
    assert not spec.final_truth.exists()
    assert not spec.final_lock.exists()


def test_written_state_truth_and_lock_match_frozen_hashes(
        harness: Harness) -> None:
    layer = harness.make_layer("complete", _valid_correction())
    spec = harness.spec([layer])
    bundle = build_finalization(spec)

    write_finalization(spec, bundle)

    assert sha256_file(spec.final_state) == bundle.state_sha256
    assert sha256_file(spec.final_truth) == bundle.truth_sha256
    lock = json.loads(spec.final_lock.read_text(encoding="utf-8"))
    assert lock["outputs"]["state"]["sha256"] == bundle.state_sha256
    assert lock["outputs"]["truth"]["sha256"] == bundle.truth_sha256
    final_state = json.loads(spec.final_state.read_text(encoding="utf-8"))
    truth_records = [
        json.loads(line)
        for line in spec.final_truth.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["item_id"] for record in truth_records] == [
        item["item_id"] for item in final_state["items"]
    ]


def test_effective_correction_must_match_boundary_qa_provenance(
        harness: Harness) -> None:
    contaminated = replace(
        _valid_correction(),
        detector_reason="contaminated correction provenance",
    )
    contaminated_queue = replace(
        contaminated,
        status="pending",
        decision="",
        verified_at="",
        edit_history=[],
    )
    layer = harness.make_layer(
        "self-consistent-contamination",
        contaminated,
        queue_item=contaminated_queue,
    )

    with pytest.raises(
            ValueError, match="effective correction layer.*immutable fields"):
        build_finalization(harness.spec([layer]))
