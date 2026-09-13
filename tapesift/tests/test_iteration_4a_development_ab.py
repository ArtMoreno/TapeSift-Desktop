"""Focused tests for the Iteration 4A development A/B scorer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import pytest

import scripts.score_iteration_4a_development_ab as scorer
from scripts.score_iteration_4a_development_ab import (
    CANDIDATE_DETECTOR_SOURCE,
    CANDIDATE_LOCK_SCHEMA_VERSION,
    CANDIDATE_LOCK_STATUS,
    CandidateLockData,
    DevelopmentABError,
    LockedArtifact,
    ReportBundle,
    _load_candidate_lock,
    _load_candidate_predictions,
    _midpoint_owned_root,
    _reviewed_support_at_threshold,
    _structural_diagnostics,
    build_report,
    default_spec,
    load_frozen_development_data,
    output_paths,
    write_reports,
)
from tapesift.research.segmentation_sample_score import (
    load_locked_roots,
    resolve_final_items,
)


def _require_frozen_research_inputs() -> None:
    """Skip when the frozen benchmark inputs are not on this machine.

    These score a candidate detector against a locked holdout that lives
    under research/segmentation_benchmark/verification/ - a directory
    .gitignore excludes on purpose, as local research input. The files
    have never been in the repository, so on any clean clone these tests
    were not failing because something broke; they were asserting that an
    optional local artifact exists. That is a skip, not a failure, and
    reporting it as a failure buried the two real ones underneath it.
    """
    if not scorer.SELECTION.is_file():
        pytest.skip(
            "frozen benchmark inputs absent - see research/README.md "
            f"({scorer.SELECTION.name})")


def _copy_baseline_as_candidate(
    tmp_path: Path,
) -> Path:
    _require_frozen_research_inputs()
    spec = default_spec()
    frozen = load_frozen_development_data(spec)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    for row in frozen.baseline_prediction_rows:
        source = spec.root / row["file"]
        (candidate / f"{row['film_id']}.json").write_bytes(
            source.read_bytes())
    return candidate


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_candidate_lock(
    tmp_path: Path,
    candidate: Path,
    *,
    candidate_id: str = "same-as-v2",
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    spec = default_spec()
    rows = []
    parameter_sets = []
    for path in sorted(candidate.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        parameter_sets.append(payload["parameters"])
        rows.append({
            "film_id": payload["film_id"],
            "file": path.name,
            "sha256": _digest(path),
            "duration_ms": payload["duration_ms"],
            "play_count": len(payload["plays"]),
            "unclassified_count": len(payload.get("unclassified", [])),
        })
    assert parameter_sets
    assert all(parameters == parameter_sets[0] for parameters in parameter_sets)
    configuration = {
        "id": "play-detect-defaults-v1",
        "parameters": parameter_sets[0],
    }
    configuration["sha256"] = _canonical_digest(configuration)
    source_path = spec.root / CANDIDATE_DETECTOR_SOURCE
    payload = {
        "schema_version": CANDIDATE_LOCK_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "status": CANDIDATE_LOCK_STATUS,
        "detector": {
            "source_file": CANDIDATE_DETECTOR_SOURCE,
            "source_sha256": _digest(source_path),
            "source_commit": None,
            "source_commit_reason": "uncommitted test candidate source",
            "configuration": configuration,
        },
        "predictions": rows,
    }
    if mutate is not None:
        mutate(payload)
    path = tmp_path / f"{candidate_id}.candidate-lock.json"
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _candidate_lock_data(
    candidate: Path,
    film_specs: dict[str, dict],
) -> CandidateLockData:
    parameters = {
        "separator_max_s": 5.0,
        "min_play_s": 4.0,
        "max_play_s": 90.0,
        "scene_threshold": 0.35,
    }
    predictions = {}
    for film_id, film in film_specs.items():
        path = candidate / f"{film_id}.json"
        predictions[film_id] = {
            "film_id": film_id,
            "file": path.name,
            "sha256": _digest(path) if path.is_file() else "0" * 64,
            "duration_ms": film["duration_ms"],
            "play_count": 0,
            "unclassified_count": 0,
        }
    return CandidateLockData(
        path=candidate.parent / "unused.lock.json",
        sha256="0" * 64,
        candidate_id="test",
        detector={},
        parameters=parameters,
        predictions=predictions,
    )


def _root(
    item_id: str,
    *,
    start: int,
    end: int,
    prediction_index: int,
) -> dict:
    return {
        "item_id": item_id,
        "film_id": "film-a",
        "film_duration_ms": 100_000,
        "start_ms": start,
        "end_ms": end,
        "original_start_ms": start,
        "original_end_ms": end,
        "candidate_kind": "play",
        "candidate_stratum": "confident",
        "prediction_indices": [prediction_index],
        "status": "pending",
        "decision": "",
        "edit_history": [],
    }


def _final(root: dict, *, status: str = "verified") -> dict:
    return {
        **root,
        "status": status,
        "decision": "accepted" if status == "verified" else "excluded",
    }


def _payload(plays: list[dict], unclassified: list[dict] | None = None) -> dict:
    return {
        "film_id": "film-a",
        "duration_ms": 100_000,
        "plays": [
            {**row, "_prediction_index": index}
            for index, row in enumerate(plays)
        ],
        "unclassified": [
            {**row, "_prediction_index": index}
            for index, row in enumerate(unclassified or [])
        ],
    }


def test_equal_candidate_reproduces_baseline_and_all_truths_tie(
    tmp_path: Path,
) -> None:
    candidate = _copy_baseline_as_candidate(tmp_path)
    candidate_lock = _write_candidate_lock(tmp_path, candidate)

    bundle = build_report(
        default_spec(), candidate, candidate_lock, "same-as-v2")
    report = bundle.report
    primary = report["results"]["truth_centric"]["iou_0_75"]
    diagnostic = report["results"]["truth_centric"]["iou_0_50"]
    assigned = report["results"]["one_to_one_assigned_iou"]
    support = report["results"][
        "reviewed_support_midpoint_diagnostic"]["iou_0_75"]

    assert primary["baseline"]["matched_truth_count"] == 71
    assert primary["candidate"]["matched_truth_count"] == 71
    assert primary["delta"]["matched_truth_count"] == 0
    assert diagnostic["baseline"]["matched_truth_count"] == 72
    assert diagnostic["candidate"]["matched_truth_count"] == 72
    assert (assigned["wins"], assigned["ties"], assigned["losses"]) == (
        0, 118, 0)
    assert support["baseline"]["owned_prediction_count"] == 85
    assert support["candidate"]["owned_prediction_count"] == 85
    assert support["candidate"]["ignored_outside_support_count"] == 150
    assert report["scope"]["public_claim_eligible"] is False
    assert report["scope"]["independent_holdout"] is False
    assert "Development-only" in bundle.markdown_text
    assert "same snap" in bundle.markdown_text
    assert any(
        "blind human review" in limitation
        for limitation in report["methodology"]["limitations"]
    )
    assert report["integrity"]["candidate_lock_valid"] is True
    assert report["integrity"]["candidate_full_output_structure_valid"] is True
    assert report["provenance"]["candidate_lock"]["sha256"] == _digest(
        candidate_lock)
    assert report["provenance"]["candidate_detector"][
        "configuration"]["id"] == "play-detect-defaults-v1"
    assert str(candidate.resolve()) not in bundle.json_text


def test_reviewed_support_midpoint_ignores_predictions_outside_roots() -> None:
    first = _root(
        "film-a:play:0000", start=10_000, end=20_000,
        prediction_index=0)
    second = _root(
        "film-a:play:0001", start=40_000, end=50_000,
        prediction_index=1)
    roots = load_locked_roots([first, second], expected_root_count=2)
    items = tuple(resolve_final_items(
        [_final(first), _final(second)], roots))
    payloads = {
        "film-a": _payload([
            {"start_ms": 10_000, "end_ms": 20_000},
            {"start_ms": 25_000, "end_ms": 35_000},
            # Overlaps the first truth, but midpoint 25_000 is in the gap.
            {"start_ms": 15_000, "end_ms": 35_000},
        ])
    }

    score = _reviewed_support_at_threshold(
        roots, items, payloads, 0.75)

    assert score["owned_prediction_count"] == 1
    assert score["matched_count"] == 1
    assert score["unmatched_owned_prediction_count"] == 0
    assert score["ignored_outside_support_count"] == 2
    assert score["candidate_iou_pass_rate"] == 1.0
    assert "never counted as a false positive" in (
        score["outside_prediction_policy"])


def test_midpoint_ownership_is_half_open_and_exact() -> None:
    left_payload = _root(
        "film-a:play:0000", start=10, end=20, prediction_index=0)
    right_payload = _root(
        "film-a:play:0001", start=20, end=30, prediction_index=1)
    roots = load_locked_roots(
        [left_payload, right_payload], expected_root_count=2)
    ordered = [roots[left_payload["item_id"]], roots[right_payload["item_id"]]]

    # Exact midpoint 20 belongs to [20, 30), never [10, 20).
    owned = _midpoint_owned_root(
        {"start_ms": 15, "end_ms": 25}, ordered)
    assert owned is not None
    assert owned.root_id == right_payload["item_id"]

    # Exact midpoint at the final half-open end is outside support.
    assert _midpoint_owned_root(
        {"start_ms": 25, "end_ms": 35}, ordered) is None


def test_structural_diagnostics_cover_full_outputs() -> None:
    payloads = {
        "film-a": _payload(
            [
                {"start_ms": 1_000, "end_ms": 10_000},
                {"start_ms": 5_000, "end_ms": 12_000},
                {"start_ms": -1, "end_ms": 100},
                {"start_ms": 90_000, "end_ms": 101_000},
                {"start_ms": 50_000, "end_ms": 49_000},
            ],
            [
                {"start_ms": 8_000, "end_ms": 15_000},
                {"start_ms": 14_000, "end_ms": 16_000},
            ],
        )
    }

    result = _structural_diagnostics(payloads)

    assert result["play_count"] == 5
    assert result["unclassified_count"] == 2
    assert result["invalid_play_count"] == 3
    assert result["out_of_bounds_count"] == 3
    assert result["play_play_overlap_pair_count"] == 1
    assert result["unclassified_unclassified_overlap_pair_count"] == 1
    assert result["play_unclassified_overlap_pair_count"] == 2
    assert result["overlap_pair_count"] == 4
    assert "all full-film" in result["scope"]


def test_candidate_contract_fails_on_missing_extra_or_bad_duration(
    tmp_path: Path,
) -> None:
    film_specs = {
        "film-a": {"duration_ms": 100},
        "film-b": {"duration_ms": 200},
    }
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "film-a.json").write_text(json.dumps({
        "film_id": "film-a",
        "duration_ms": 100,
        "parameters": {
            "separator_max_s": 5.0,
            "min_play_s": 4.0,
            "max_play_s": 90.0,
            "scene_threshold": 0.35,
        },
        "plays": [],
    }), encoding="utf-8")

    with pytest.raises(DevelopmentABError, match="missing"):
        _load_candidate_predictions(
            candidate, film_specs,
            _candidate_lock_data(candidate, film_specs))

    (candidate / "film-b.json").write_text(json.dumps({
        "film_id": "film-b",
        "duration_ms": 201,
        "parameters": {
            "separator_max_s": 5.0,
            "min_play_s": 4.0,
            "max_play_s": 90.0,
            "scene_threshold": 0.35,
        },
        "plays": [],
    }), encoding="utf-8")
    with pytest.raises(DevelopmentABError, match="duration mismatch"):
        _load_candidate_predictions(
            candidate, film_specs,
            _candidate_lock_data(candidate, film_specs))

    (candidate / "film-b.json").write_text(json.dumps({
        "film_id": "film-b",
        "duration_ms": 200,
        "parameters": {
            "separator_max_s": 5.0,
            "min_play_s": 4.0,
            "max_play_s": 90.0,
            "scene_threshold": 0.35,
        },
        "plays": [],
    }), encoding="utf-8")
    (candidate / "rogue.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(DevelopmentABError, match="extra"):
        _load_candidate_predictions(
            candidate, film_specs,
            _candidate_lock_data(candidate, film_specs))


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("source_hash", "candidate detector source.*hash mismatch"),
        ("configuration_hash", "configuration sha256"),
        ("prediction_hash", "candidate prediction .*hash mismatch"),
        ("candidate_id", "candidate_id"),
    ],
)
def test_candidate_lock_fails_closed_on_provenance_tampering(
    tmp_path: Path,
    tamper: str,
    message: str,
) -> None:
    candidate = _copy_baseline_as_candidate(tmp_path)

    def mutate(lock: dict[str, Any]) -> None:
        if tamper == "source_hash":
            lock["detector"]["source_sha256"] = "0" * 64
        elif tamper == "configuration_hash":
            lock["detector"]["configuration"]["sha256"] = "0" * 64
        elif tamper == "prediction_hash":
            lock["predictions"][0]["sha256"] = "0" * 64
        elif tamper == "candidate_id":
            lock["candidate_id"] = "different-candidate"

    candidate_lock = _write_candidate_lock(
        tmp_path, candidate, mutate=mutate)

    with pytest.raises(DevelopmentABError, match=message):
        build_report(
            default_spec(), candidate, candidate_lock, "same-as-v2")


def test_candidate_lock_requires_explicit_commit_unavailability_reason(
    tmp_path: Path,
) -> None:
    candidate = _copy_baseline_as_candidate(tmp_path)

    def mutate(lock: dict[str, Any]) -> None:
        lock["detector"].pop("source_commit_reason")

    candidate_lock = _write_candidate_lock(
        tmp_path, candidate, mutate=mutate)
    _require_frozen_research_inputs()
    frozen = load_frozen_development_data(default_spec())

    with pytest.raises(
        DevelopmentABError,
        match="source_commit is null without a source_commit_reason",
    ):
        _load_candidate_lock(
            default_spec(),
            candidate_lock,
            "same-as-v2",
            frozen.film_specs,
        )


@pytest.mark.parametrize("failure_kind", ["out_of_bounds", "overlap"])
def test_invalid_candidate_structure_fails_before_any_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    candidate = _copy_baseline_as_candidate(tmp_path)
    target = next(
        path
        for path in sorted(candidate.glob("*.json"))
        if len(json.loads(path.read_text(encoding="utf-8"))["plays"]) >= 2
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    if failure_kind == "out_of_bounds":
        payload["plays"][0]["end_ms"] = payload["duration_ms"] + 1
    else:
        first = payload["plays"][0]
        second = payload["plays"][1]
        second["start_ms"] = first["start_ms"]
        second["end_ms"] = max(
            int(first["end_ms"]), int(second["end_ms"]))
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    candidate_lock = _write_candidate_lock(tmp_path, candidate)

    def scoring_must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("scoring ran before structural validation")

    monkeypatch.setattr(
        scorer, "_truth_recovery_at_threshold", scoring_must_not_run)

    with pytest.raises(
        DevelopmentABError,
        match=r"Candidate structural integrity failed before scoring",
    ) as error:
        build_report(
            default_spec(), candidate, candidate_lock, "same-as-v2")

    if failure_kind == "out_of_bounds":
        assert "out_of_bounds_count=1" in str(error.value)
    else:
        assert "overlap_pair_count=" in str(error.value)


def test_mutated_frozen_truth_fails_before_scoring(
    tmp_path: Path,
) -> None:
    # Reads the frozen truth directly, so the guard has to run before the
    # read rather than at the first helper call like the others.
    _require_frozen_research_inputs()
    spec = default_spec()
    mutated_truth = tmp_path / "mutated.verified.jsonl"
    mutated_truth.write_bytes(spec.final_truth.path.read_bytes() + b" ")
    bad_spec = replace(
        spec,
        final_truth=LockedArtifact(
            mutated_truth, spec.final_truth.sha256),
    )
    candidate = _copy_baseline_as_candidate(tmp_path)
    candidate_lock = _write_candidate_lock(tmp_path, candidate)

    with pytest.raises(DevelopmentABError, match="hash mismatch"):
        build_report(
            bad_spec, candidate, candidate_lock, "mutated-truth")


@pytest.mark.parametrize("existing_kind", ["json", "markdown"])
def test_write_reports_refuses_if_either_output_exists(
    tmp_path: Path,
    existing_kind: str,
) -> None:
    json_path, markdown_path = output_paths(tmp_path, "candidate")
    existing = json_path if existing_kind == "json" else markdown_path
    existing.write_text("keep me", encoding="utf-8")
    bundle = ReportBundle(
        report={},
        json_text='{"ok": true}\n',
        markdown_text="# Result\n",
    )

    with pytest.raises(FileExistsError, match="Refusing"):
        write_reports(json_path, markdown_path, bundle)

    assert existing.read_text(encoding="utf-8") == "keep me"
    other = markdown_path if existing_kind == "json" else json_path
    assert not other.exists()
