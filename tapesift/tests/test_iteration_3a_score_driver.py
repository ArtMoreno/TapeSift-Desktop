"""Focused tests for the frozen Iteration 3A scoring driver."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from scripts.score_iteration_3a_holdout import (
    ExpectedCorrectionLayer,
    FrozenScoreError,
    FrozenScoreSpec,
    LockedArtifact,
    build_frozen_report,
    render_markdown,
    sha256_file,
    write_reports,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _ref(root: Path, path: Path) -> dict[str, str]:
    return {
        "file": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
    }


def _locked(path: Path) -> LockedArtifact:
    return LockedArtifact(path, sha256_file(path))


def _root(
    item_id: str,
    *,
    kind: str,
    start: int,
    end: int,
    prediction_indices: list[int],
) -> dict:
    return {
        "item_id": item_id,
        "film_id": "film-a",
        "film_name": "Film A",
        "source_file": "X:/private/film-a.mp4",
        "analysis_source": "X:/private/film-a.mp4",
        "frame_rate": 30.0,
        "film_duration_ms": 40_000,
        "start_ms": start,
        "end_ms": end,
        "original_start_ms": start,
        "original_end_ms": end,
        "candidate_kind": kind,
        "candidate_index": 0,
        "prediction_indices": prediction_indices,
        "angle_starts_ms": [start],
        "detector_needs_review": kind == "unclassified",
        "detector_reason": "",
        "detector_signal": "scene",
        "angle_count": 1,
        "candidate_stratum": (
            "unclassified" if kind == "unclassified" else "confident"),
        "status": "pending",
        "decision": "",
        "edit_history": [],
        "verified_at": "",
    }


def _truth_record(item: dict, queue_id: str) -> dict:
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


@dataclass
class Harness:
    root: Path
    selection: Path
    prediction_lock: Path
    queue_lock: Path
    finalization_lock: Path
    final_state: Path
    final_truth: Path
    required_inputs: tuple[Path, ...]
    correction_completion: Path
    correction_layer_id: str
    correction_queue_id: str
    correction_item_ids: tuple[str, ...]
    output_json: Path
    output_markdown: Path

    def spec(self) -> FrozenScoreSpec:
        return FrozenScoreSpec(
            root=self.root,
            benchmark_id="holdout-test",
            selection=_locked(self.selection),
            prediction_lock=_locked(self.prediction_lock),
            queue_lock=_locked(self.queue_lock),
            finalization_lock=_locked(self.finalization_lock),
            output_json=self.output_json,
            output_markdown=self.output_markdown,
            required_finalization_inputs=tuple(
                _locked(path) for path in self.required_inputs),
            required_correction_layers=(
                ExpectedCorrectionLayer(
                    layer_id=self.correction_layer_id,
                    queue_id=self.correction_queue_id,
                    item_ids=self.correction_item_ids,
                    completion_lock=_locked(self.correction_completion),
                ),
            ),
            expected_final_state=self.final_state,
            expected_final_truth=self.final_truth,
            expected_root_count=2,
            expected_film_count=1,
        )


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    root = tmp_path
    source = root / "external-source.mp4"
    source.write_bytes(b"small-source")
    detector = root / "detector.py"
    detector.write_text("# frozen detector\n", encoding="utf-8")
    play_root = _root(
        "film-a:play:0000",
        kind="play",
        start=1_000,
        end=11_000,
        prediction_indices=[0],
    )
    gap_root = _root(
        "film-a:unclassified:0000",
        kind="unclassified",
        start=20_000,
        end=30_000,
        prediction_indices=[],
    )
    roots = [play_root, gap_root]
    selection = root / "selection.json"
    gates = {
        "sampled_coverage_iou_0_75_min": 0.90,
        "per_film_sampled_coverage_iou_0_75_min": 0.80,
        "confident_prediction_match_min": 0.90,
        "weak_recovery_match_min": 0.80,
        "scene_angle_pair_precision_min": 0.90,
        "merge_rate_max": 0.05,
        "split_rate_max": 0.05,
        "median_boundary_error_ms_max": 1500,
        "p90_boundary_error_ms_max": 5000,
        "overlap_count_max": 0,
        "out_of_bounds_count_max": 0,
        "data_loss_allowed": False,
    }
    detector_lock = {
        "source_file": str(detector),
        "source_sha256": sha256_file(detector),
        "source_commit": "abc123",
        "settings": {
            "separator_max_s": 5.0,
            "min_play_s": 4.0,
            "max_play_s": 90.0,
            "scene_threshold": 0.35,
        },
    }
    _write_json(selection, {
        "schema_version": "1.0",
        "queue_id": "holdout-test",
        "status": "locked_before_detection",
        "selection_policy": {
            "film_count": 1,
            "samples_per_film": 2,
            "total_review_samples": 2,
        },
        "detector_lock": detector_lock,
        "predeclared_review": {"gates": gates},
        "films": [{
            "film_id": "film-a",
            "source_file": str(source),
            "source_bytes": source.stat().st_size,
            "source_sha256": sha256_file(source),
            "duration_ms": 40_000,
            "resolution": "1280x720",
            "frame_rate": "30/1",
        }],
    })

    prediction = root / "prediction.json"
    _write_json(prediction, {
        "schema_version": "1.0",
        "film_id": "film-a",
        "duration_ms": 40_000,
        "plays": [{"start_ms": 1_000, "end_ms": 11_000}],
        "unclassified": [{"start_ms": 20_000, "end_ms": 30_000}],
    })
    prediction_lock = root / "prediction-lock.json"
    _write_json(prediction_lock, {
        "schema_version": "1.0",
        "queue_id": "holdout-test",
        "status": "predictions_frozen_before_human_review",
        "selection": _ref(root, selection),
        "detector": {
            **detector_lock,
            "source_file": detector.relative_to(root).as_posix(),
        },
        "structural_checks": {"passed": True},
        "predictions": [{
            "film_id": "film-a",
            **_ref(root, prediction),
            "duration_ms": 40_000,
            "play_count": 1,
            "unclassified_count": 1,
        }],
    })

    queue_file = root / "queue.locked.json"
    _write_json(queue_file, {
        "schema_version": "1.0",
        "queue_id": "holdout-test",
        "items": roots,
    })
    queue_lock = root / "queue-lock.json"
    _write_json(queue_lock, {
        "schema_version": "1.0",
        "queue_id": "holdout-test",
        "status": "queue_frozen_before_human_review",
        "prediction_lock": _ref(root, prediction_lock),
        "queue": {
            "locked_selection_file": queue_file.relative_to(root).as_posix(),
            "locked_selection_sha256": sha256_file(queue_file),
            "initial_review_units": 2,
            "items_per_film": 2,
        },
        "actual_counts": {
            "all_films": {
                "confident": 1,
                "unclassified": 1,
                "scene_angle_pair": 0,
            },
        },
    })

    verified = {
        **play_root,
        "status": "verified",
        "decision": "accepted",
        "verified_at": "2026-07-25T01:00:00+00:00",
    }
    excluded = {
        **gap_root,
        "status": "excluded",
        "decision": "excluded",
        "verified_at": "2026-07-25T01:01:00+00:00",
    }
    final_state = root / "final.state.json"
    final_truth = root / "final.truth.jsonl"
    _write_json(final_state, {
        "schema_version": "1.0",
        "queue_id": "holdout-test",
        "items": [verified, excluded],
    })
    _write_jsonl(final_truth, [
        _truth_record(verified, "holdout-test"),
        _truth_record(excluded, "holdout-test"),
    ])
    base = root / "base.locked"
    base.write_text("locked input", encoding="utf-8")
    correction_layer_id = "reviewer-verified-test-v2"
    correction_queue_id = "holdout-test-reviewer-verified-v2"
    correction_item_ids = (
        play_root["item_id"],
        gap_root["item_id"],
    )
    correction_state = root / "correction.state.json"
    correction_truth = root / "correction.truth.jsonl"
    correction_queue = root / "correction.queue.locked.json"
    _write_json(correction_state, {"items": [verified]})
    _write_jsonl(correction_truth, [_truth_record(verified, "holdout-test")])
    _write_json(correction_queue, {
        "schema_version": "1.0",
        "queue_id": correction_queue_id,
        "items": [play_root],
    })
    correction_lock = root / "correction.completion.lock.json"
    _write_json(correction_lock, {
        "schema_version": "1.0",
        "status": "complete",
        "layer_id": correction_layer_id,
        "queue_id": correction_queue_id,
        "item_ids": list(correction_item_ids),
        "state_file": correction_state.relative_to(root).as_posix(),
        "state_sha256": sha256_file(correction_state),
        "truth_file": correction_truth.relative_to(root).as_posix(),
        "truth_sha256": sha256_file(correction_truth),
        "locked_queue_file": correction_queue.relative_to(root).as_posix(),
        "locked_queue_sha256": sha256_file(correction_queue),
        "input_locks": {},
    })
    finalization_lock = root / "finalization-lock.json"
    _write_json(finalization_lock, {
        "schema_version": "1.0",
        "status": "final",
        "finalized_at": "2026-07-25T02:00:00+00:00",
        "inputs": [_ref(root, base), _ref(root, queue_file)],
        "correction_layers": [{
            "layer_id": correction_layer_id,
            "queue_id": correction_queue_id,
            "item_ids": list(correction_item_ids),
            "completion_lock": _ref(root, correction_lock),
            "state": _ref(root, correction_state),
            "truth": _ref(root, correction_truth),
            "locked_queue": _ref(root, correction_queue),
        }],
        "approved_changes": {
            "required_correction_ids": list(correction_item_ids),
        },
        "outputs": {
            "state": _ref(root, final_state),
            "truth": _ref(root, final_truth),
        },
        "counts": {
            "items": 2,
            "statuses": {"excluded": 1, "verified": 1},
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
    })
    return Harness(
        root=root,
        selection=selection,
        prediction_lock=prediction_lock,
        queue_lock=queue_lock,
        finalization_lock=finalization_lock,
        final_state=final_state,
        final_truth=final_truth,
        required_inputs=(base, queue_file),
        correction_completion=correction_lock,
        correction_layer_id=correction_layer_id,
        correction_queue_id=correction_queue_id,
        correction_item_ids=correction_item_ids,
        output_json=root / "reports" / "results.json",
        output_markdown=root / "reports" / "results.md",
    )


def test_builds_hash_locked_public_report_without_absolute_paths(
    harness: Harness,
) -> None:
    bundle = build_frozen_report(harness.spec())

    assert bundle.report["results"]["population"]["root_count"] == 2
    assert bundle.report["results"]["aggregate"]["primary"]["coverage"] == 1.0
    assert bundle.report["gates"]["results"][
        "scene_angle_pair_precision"]["passed"] is None
    assert bundle.report["gates"]["results"][
        "scene_angle_pair_precision"]["status"] == "not_measurable"
    assert str(harness.root).casefold() not in bundle.json_text.casefold()
    assert "X:/private" not in bundle.json_text
    assert bundle.report["schema_version"] == "2.0"
    assert bundle.report["report_status"] == "complete"
    assert bundle.report["results"]["scoring_status"] == "complete"
    assert bundle.report["performance_gate_status"] == (
        bundle.report["gates"]["overall_status"])
    assert (
        "Internal engineering diagnostic"
        in bundle.markdown_text
    )
    assert "not eligible for a public performance" in bundle.markdown_text
    assert "Verified plays within sampled roots" in bundle.markdown_text
    assert "Sampled detector candidates" in bundle.markdown_text
    assert bundle.report["gates"]["results"][
        "median_boundary_error_ms"]["denominator"] == 1


def test_fails_closed_when_referenced_artifact_hash_changes(
    harness: Harness,
) -> None:
    spec = harness.spec()
    harness.final_state.write_text(
        harness.final_state.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )

    with pytest.raises(FrozenScoreError, match="hash mismatch"):
        build_frozen_report(spec)
    assert not spec.output_json.exists()
    assert not spec.output_markdown.exists()


def test_fails_closed_when_final_truth_is_not_state_mirror(
    harness: Harness,
) -> None:
    truth = harness.final_truth.read_text(encoding="utf-8")
    harness.final_truth.write_text(
        truth.replace('"accepted"', '"revised"', 1),
        encoding="utf-8",
    )
    lock = json.loads(
        harness.finalization_lock.read_text(encoding="utf-8"))
    lock["outputs"]["truth"]["sha256"] = sha256_file(harness.final_truth)
    _write_json(harness.finalization_lock, lock)

    with pytest.raises(FrozenScoreError, match="not exact mirrors"):
        build_frozen_report(harness.spec())


def test_atomic_write_refuses_overwrite(harness: Harness) -> None:
    spec = harness.spec()
    bundle = build_frozen_report(spec)
    write_reports(spec, bundle)
    original = spec.output_json.read_text(encoding="utf-8")

    with pytest.raises(FileExistsError, match="Refusing"):
        write_reports(spec, bundle)

    assert spec.output_json.read_text(encoding="utf-8") == original
    assert spec.output_markdown.is_file()


def test_top_level_lock_hash_is_required(harness: Harness) -> None:
    spec = harness.spec()
    bad = replace(
        spec,
        queue_lock=LockedArtifact(spec.queue_lock.path, "0" * 64),
    )

    with pytest.raises(FrozenScoreError, match="queue lock hash mismatch"):
        build_frozen_report(bad)


def test_finalization_base_input_must_match_required_identity(
    harness: Harness,
) -> None:
    rogue = harness.root / "rogue-base.locked"
    rogue.write_text("different but hash-valid input", encoding="utf-8")
    lock = json.loads(
        harness.finalization_lock.read_text(encoding="utf-8"))
    lock["inputs"][0] = _ref(harness.root, rogue)
    _write_json(harness.finalization_lock, lock)

    with pytest.raises(FrozenScoreError, match="required path and hash"):
        build_frozen_report(harness.spec())


def test_finalization_base_input_hash_cannot_be_redeclared(
    harness: Harness,
) -> None:
    spec = harness.spec()
    base = harness.required_inputs[0]
    base.write_text("mutated locked input", encoding="utf-8")
    lock = json.loads(
        harness.finalization_lock.read_text(encoding="utf-8"))
    lock["inputs"][0] = _ref(harness.root, base)
    _write_json(harness.finalization_lock, lock)
    spec = replace(
        spec,
        finalization_lock=_locked(harness.finalization_lock),
    )

    with pytest.raises(FrozenScoreError, match="required path and hash"):
        build_frozen_report(spec)


def test_finalization_queue_must_be_the_queue_lock_artifact(
    harness: Harness,
) -> None:
    queue_lock = json.loads(
        harness.queue_lock.read_text(encoding="utf-8"))
    original = harness.required_inputs[1]
    alternate = harness.root / "alternate.queue.locked.json"
    alternate.write_bytes(original.read_bytes())
    queue_lock["queue"]["locked_selection_file"] = (
        alternate.relative_to(harness.root).as_posix())
    queue_lock["queue"]["locked_selection_sha256"] = sha256_file(alternate)
    _write_json(harness.queue_lock, queue_lock)

    with pytest.raises(FrozenScoreError, match="not the queue_lock artifact"):
        build_frozen_report(harness.spec())


def test_correction_layer_item_order_is_part_of_contract(
    harness: Harness,
) -> None:
    lock = json.loads(
        harness.finalization_lock.read_text(encoding="utf-8"))
    lock["correction_layers"][0]["item_ids"].reverse()
    _write_json(harness.finalization_lock, lock)

    with pytest.raises(FrozenScoreError, match="identity or item IDs"):
        build_frozen_report(harness.spec())


def test_correction_layer_order_is_part_of_contract(
    harness: Harness,
) -> None:
    first_spec = harness.spec()
    first_expected = first_spec.required_correction_layers[0]
    second_id = "final-owner-overlay-test-v2"
    second_queue_id = "holdout-test-final-owner-overlay-v2"
    second_completion = harness.root / "second.completion.lock.json"
    completion = json.loads(
        harness.correction_completion.read_text(encoding="utf-8"))
    completion["layer_id"] = second_id
    completion["queue_id"] = second_queue_id
    _write_json(second_completion, completion)

    lock = json.loads(
        harness.finalization_lock.read_text(encoding="utf-8"))
    second_row = copy.deepcopy(lock["correction_layers"][0])
    second_row["layer_id"] = second_id
    second_row["queue_id"] = second_queue_id
    second_row["completion_lock"] = _ref(
        harness.root, second_completion)
    lock["correction_layers"].append(second_row)
    _write_json(harness.finalization_lock, lock)
    second_expected = ExpectedCorrectionLayer(
        layer_id=second_id,
        queue_id=second_queue_id,
        item_ids=harness.correction_item_ids,
        completion_lock=_locked(second_completion),
    )
    ordered_spec = replace(
        first_spec,
        finalization_lock=_locked(harness.finalization_lock),
        required_correction_layers=(first_expected, second_expected),
    )
    build_frozen_report(ordered_spec)

    lock["correction_layers"].reverse()
    _write_json(harness.finalization_lock, lock)
    swapped_spec = replace(
        ordered_spec,
        finalization_lock=_locked(harness.finalization_lock),
    )
    with pytest.raises(FrozenScoreError, match="identity or item IDs"):
        build_frozen_report(swapped_spec)


def test_correction_completion_lock_path_and_hash_are_pinned(
    harness: Harness,
) -> None:
    alternate = harness.root / "alternate.completion.lock.json"
    alternate.write_bytes(harness.correction_completion.read_bytes())
    lock = json.loads(
        harness.finalization_lock.read_text(encoding="utf-8"))
    lock["correction_layers"][0]["completion_lock"] = _ref(
        harness.root, alternate)
    _write_json(harness.finalization_lock, lock)

    with pytest.raises(FrozenScoreError, match="required path and hash"):
        build_frozen_report(harness.spec())


def test_correction_outputs_must_match_pinned_completion_lock(
    harness: Harness,
) -> None:
    alternate_state = harness.root / "alternate.correction.state.json"
    alternate_state.write_text('{"items": []}\n', encoding="utf-8")
    completion = json.loads(
        harness.correction_completion.read_text(encoding="utf-8"))
    completion["state_file"] = (
        alternate_state.relative_to(harness.root).as_posix())
    completion["state_sha256"] = sha256_file(alternate_state)
    _write_json(harness.correction_completion, completion)
    lock = json.loads(
        harness.finalization_lock.read_text(encoding="utf-8"))
    lock["correction_layers"][0]["completion_lock"] = _ref(
        harness.root, harness.correction_completion)
    _write_json(harness.finalization_lock, lock)

    with pytest.raises(FrozenScoreError, match="required path and hash"):
        build_frozen_report(harness.spec())


def test_final_output_paths_are_pinned_not_self_asserted(
    harness: Harness,
) -> None:
    alternate = harness.root / "alternate.final.state.json"
    alternate.write_bytes(harness.final_state.read_bytes())
    lock = json.loads(
        harness.finalization_lock.read_text(encoding="utf-8"))
    lock["outputs"]["state"] = _ref(harness.root, alternate)
    _write_json(harness.finalization_lock, lock)

    with pytest.raises(FrozenScoreError, match="required v2 paths"):
        build_frozen_report(harness.spec())


@pytest.mark.parametrize("mutation", ["missing", "extra", "false"])
def test_finalization_validation_flags_are_exact(
    harness: Harness,
    mutation: str,
) -> None:
    lock = json.loads(
        harness.finalization_lock.read_text(encoding="utf-8"))
    if mutation == "missing":
        del lock["validation"]["bounds_valid"]
    elif mutation == "extra":
        lock["validation"]["invented_check"] = True
    else:
        lock["validation"]["bounds_valid"] = False
    _write_json(harness.finalization_lock, lock)

    with pytest.raises(FrozenScoreError, match="validation is incomplete"):
        build_frozen_report(harness.spec())


def test_boundary_gates_use_all_one_to_one_components(
    harness: Harness,
) -> None:
    state = json.loads(harness.final_state.read_text(encoding="utf-8"))
    state["items"][0]["end_ms"] = 31_000
    _write_json(harness.final_state, state)
    _write_jsonl(harness.final_truth, [
        _truth_record(item, "holdout-test") for item in state["items"]
    ])
    lock = json.loads(
        harness.finalization_lock.read_text(encoding="utf-8"))
    lock["outputs"]["state"] = _ref(harness.root, harness.final_state)
    lock["outputs"]["truth"] = _ref(harness.root, harness.final_truth)
    _write_json(harness.finalization_lock, lock)

    bundle = build_frozen_report(harness.spec())

    primary = bundle.report["results"]["aggregate"]["primary"]
    boundary = bundle.report["results"]["aggregate"][
        "all_one_to_one_boundary"]
    assert primary["matched_count"] == 0
    assert primary["boundary_error_denominator"] == 0
    assert boundary["denominator"] == 1
    assert boundary["p90_max_boundary_error_ms"] == 20_000
    gate = bundle.report["gates"]["results"]["p90_boundary_error_ms"]
    assert gate["value"] == 20_000
    assert gate["denominator"] == 1
    assert gate["passed"] is False


def test_markdown_uses_na_for_zero_candidate_precision(
    harness: Harness,
) -> None:
    report = copy.deepcopy(build_frozen_report(harness.spec()).report)
    film = report["results"]["by_film"]["film-a"]
    film["primary"]["prediction_count"] = 0
    film["primary"]["matched_count"] = 0
    film["primary"]["precision"] = 0.0
    film["primary"]["coverage"] = 0.0
    report["provenance"]["predictions"][0]["play_count"] = 0

    markdown = render_markdown(report)

    film_row = next(
        line for line in markdown.splitlines()
        if line.startswith("| film-a |"))
    assert film_row.endswith("| 0.0% | N/A |")
    assert "Candidate pass rate is N/A." in markdown
