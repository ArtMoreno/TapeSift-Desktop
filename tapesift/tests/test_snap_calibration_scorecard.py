"""Frozen scorecard behavior for Iteration 7A snap calibration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tapesift.research.snap_calibration_scorecard import (
    build_frozen_snap_calibration_scorecard,
    score_snap_calibration,
)


def _record(index: int, first_delta: int, second_delta: int) -> dict:
    cohort = "virginia" if index <= 25 else "alabama"
    return {
        "schema_version": "1.0",
        "dataset_kind": "tapesift_temporal_snap_calibration",
        "workflow": "snap_calibration",
        "item_id": f"{cohort}:clip-{index}",
        "research_cohort_id": cohort,
        "project_name": f"{cohort}.tapesift",
        "clip_number": index,
        "source_manifests": [
            {"name": "virginia.jsonl", "sha256": "a" * 64},
            {"name": "alabama.jsonl", "sha256": "b" * 64},
        ],
        "angles": [
            {
                "angle": 1,
                "proposed_onset_ms": index * 10_000,
                "actual_snap_ms": index * 10_000 + first_delta,
                "delta_ms": first_delta,
                "snap_status": "marked",
            },
            {
                "angle": 2,
                "proposed_onset_ms": index * 10_000 + 2_000,
                "actual_snap_ms": index * 10_000 + 2_000 + second_delta,
                "delta_ms": second_delta,
                "snap_status": "marked",
            },
        ],
    }


def _completed_records() -> list[dict]:
    return [_record(index, -100, 200) for index in range(1, 46)]


def test_scorecard_uses_predeclared_localization_metrics() -> None:
    result = score_snap_calibration(_completed_records())

    assert result["localization"] == {
        "angle_judgments": 90,
        "emitted_onsets": 90,
        "unavailable": 0,
        "status_counts": {"marked": 90},
        "median_absolute_error_ms": 150.0,
        "within_500_ms": 90,
        "within_500_ms_share": 1.0,
        "p90_absolute_error_ms": 200,
        "median_signed_bias_ms": 50.0,
    }
    assert result["pair_coverage"] == {
        "plays": 45,
        "two_eligible_angle_plays": 45,
        "two_eligible_angle_play_share": 1.0,
        "both_angles_within_500_ms": 45,
        "both_angles_within_500_ms_share": 1.0,
    }
    assert result["localization_gate"]["passed"] is True
    assert set(result["cohorts"]) == {"alabama", "virginia"}


def test_scorecard_rejects_tampered_delta_and_mixed_manifest_sets() -> None:
    records = _completed_records()
    records[0]["angles"][0]["delta_ms"] = 999
    with pytest.raises(ValueError, match="bad delta_ms"):
        score_snap_calibration(records)

    records = _completed_records()
    records[1]["source_manifests"] = [
        {"name": "different.jsonl", "sha256": "c" * 64},
    ]
    with pytest.raises(ValueError, match="one frozen manifest set"):
        score_snap_calibration(records)


def test_frozen_builder_hashes_input_and_refuses_replacement(
        tmp_path: Path) -> None:
    judgments = tmp_path / "judgments.jsonl"
    judgments.write_text(
        "".join(json.dumps(record) + "\n" for record in _completed_records()),
        encoding="utf-8",
    )
    output_json = tmp_path / "scorecard.json"
    output_report = tmp_path / "scorecard.md"

    result = build_frozen_snap_calibration_scorecard(
        judgments, output_json, output_report)

    assert result["frozen_input"]["judgments_sha256"]
    written = json.loads(output_json.read_text(encoding="utf-8"))
    assert written["localization_gate"]["passed"] is True
    report = output_report.read_text(encoding="utf-8")
    assert "Frozen Snap Calibration Scorecard" in report
    assert "does not modify CSE Beta 4D, Temporal v2.1" in report
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        build_frozen_snap_calibration_scorecard(
            judgments, output_json, output_report)


def test_frozen_builder_requires_the_completed_45_play_90_angle_set(
        tmp_path: Path) -> None:
    judgments = tmp_path / "short.jsonl"
    judgments.write_text(json.dumps(_record(1, 0, 0)) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="requires 45 plays"):
        build_frozen_snap_calibration_scorecard(
            judgments, tmp_path / "result.json", tmp_path / "result.md")
