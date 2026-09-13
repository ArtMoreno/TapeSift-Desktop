"""Selective, game-level Temporal Run/Pass baseline behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tapesift.research.run_pass_temporal_baseline import (
    evaluate_temporal_feature_manifests,
    evaluate_temporal_holdout,
)
from tapesift.research.run_pass_temporal_features import (
    TEMPORAL_CLASSIFIER_FEATURES,
)


def _record(
        cohort: str,
        project: str,
        clip_number: int,
        label: str,
        value: float,
        *,
        eligible: bool = True,
) -> dict:
    return {
        "schema_version": "2.1",
        "dataset_kind": "tapesift_run_pass_temporal_features",
        "research_cohort_id": cohort,
        "source_project": project,
        "clip_number": clip_number,
        "label": label,
        "classifier_eligible": eligible,
        "abstain_reasons": (
            [] if eligible else ["expected_two_usable_angles_found_1"]
        ),
        "feature_extractor": {
            "name": "ffmpeg-temporal-cadence",
            "version": "3",
            "sample_fps": 8.0,
            "sample_width": 320,
        },
        "features": (
            {name: value for name in TEMPORAL_CLASSIFIER_FEATURES}
            if eligible else {}
        ),
        "play_type": "",
        "play_action": "",
    }


def _training() -> list[dict]:
    return [
        _record("train", "train.tapesift", 1, "run", 1.0),
        _record("train", "train.tapesift", 2, "run", 2.0),
        _record("train", "train.tapesift", 3, "run", 3.0),
        _record("train", "train.tapesift", 4, "pass", 8.0),
        _record("train", "train.tapesift", 5, "pass", 9.0),
        _record("train", "train.tapesift", 6, "pass", 10.0),
        _record(
            "train", "train.tapesift", 7, "run", 4.0,
            eligible=False,
        ),
    ]


def _holdout() -> list[dict]:
    return [
        _record("holdout", "holdout.tapesift", 11, "run", 1.5),
        _record("holdout", "holdout.tapesift", 12, "pass", 8.5),
        _record(
            "holdout", "holdout.tapesift", 13, "run", 2.0,
            eligible=False,
        ),
    ]


def test_temporal_rule_preserves_abstention_and_reports_coverage() -> None:
    result = evaluate_temporal_holdout(_training(), _holdout())

    assert result["rule"]["feature"] in TEMPORAL_CLASSIFIER_FEATURES
    assert result["holdout_metrics"]["accuracy"] == 1.0
    assert result["holdout_coverage"] == {
        "eligible": 2,
        "abstained": 1,
        "total": 3,
        "coverage": round(2 / 3, 6),
    }
    assert result["holdout_metrics"]["covered_correct_share"] == \
        round(2 / 3, 6)
    assert result["abstentions"] == [{
        "clip_number": 13,
        "actual": "run",
        "reasons": ["expected_two_usable_angles_found_1"],
    }]
    assert result["protocol"]["holdout_used_for_rule_selection"] is False


def test_holdout_answers_cannot_change_the_frozen_rule() -> None:
    original = evaluate_temporal_holdout(_training(), _holdout())
    swapped = [
        {
            **record,
            "label": (
                "pass" if record["label"] == "run" else "run"
            ),
        }
        for record in _holdout()
    ]
    changed = evaluate_temporal_holdout(_training(), swapped)

    assert changed["rule"] == original["rule"]
    assert changed["ranked_training_rules"] == \
        original["ranked_training_rules"]
    assert changed["holdout_metrics"] != original["holdout_metrics"]


def test_only_five_predeclared_temporal_features_can_compete() -> None:
    training = _training()
    for index, record in enumerate(training):
        record.setdefault("features", {})["onset_confidence"] = float(index)
        record["features"]["duration_ms"] = float(index * 1000)

    result = evaluate_temporal_holdout(training, _holdout())

    assert result["protocol"]["candidate_features"] == \
        list(TEMPORAL_CLASSIFIER_FEATURES)
    assert result["rule"]["feature"] not in {
        "onset_confidence", "duration_ms"}


def test_file_evaluation_hashes_inputs_and_writes_decision(
        tmp_path: Path) -> None:
    train = tmp_path / "train.jsonl"
    holdout = tmp_path / "holdout.jsonl"
    train.write_text(
        "".join(json.dumps(record) + "\n" for record in _training()),
        encoding="utf-8",
    )
    holdout.write_text(
        "".join(json.dumps(record) + "\n" for record in _holdout()),
        encoding="utf-8",
    )
    output = tmp_path / "result.json"
    report = tmp_path / "report.md"

    result = evaluate_temporal_feature_manifests(
        train, holdout, output, report)

    assert result["protocol"]["training_manifest_sha256"]
    assert result["protocol"]["holdout_manifest_sha256"]
    assert json.loads(output.read_text(encoding="utf-8"))["rule"] == \
        result["rule"]
    text = report.read_text(encoding="utf-8")
    assert "Cross-Game Run/Pass Baseline" in text
    assert "Coverage" in text
    assert "DO NOT DEPLOY" in text

    with pytest.raises(FileExistsError):
        evaluate_temporal_feature_manifests(
            train, holdout, output, report)
