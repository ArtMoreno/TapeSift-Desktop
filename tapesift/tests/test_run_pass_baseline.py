import json
from pathlib import Path

import pytest

from tapesift.research.run_pass_baseline import (
    CANDIDATE_FEATURES,
    classification_metrics,
    evaluate_feature_manifests,
    evaluate_holdout,
    fit_threshold_rule,
)


def _record(
        cohort: str,
        project: str,
        clip_number: int,
        label: str,
        motion: float,
        *,
        duration_ms: int = 30_000,
) -> dict[str, object]:
    features = {
        feature: motion for feature in CANDIDATE_FEATURES
    }
    features["duration_ms"] = duration_ms
    return {
        "research_cohort_id": cohort,
        "source_project": project,
        "clip_number": clip_number,
        "label": label,
        "feature_extractor": {
            "name": "ffmpeg-signalstats",
            "version": "1",
            "sample_fps": 4.0,
            "sample_width": 320,
        },
        "features": features,
        "play_type": "",
        "play_action": "",
    }


def _training() -> list[dict[str, object]]:
    return [
        _record("train", "train.tapesift", 1, "run", 1.0),
        _record("train", "train.tapesift", 2, "run", 2.0),
        _record("train", "train.tapesift", 3, "pass", 8.0),
        _record("train", "train.tapesift", 4, "pass", 9.0),
    ]


def _holdout() -> list[dict[str, object]]:
    return [
        _record("test", "test.tapesift", 10, "run", 1.5),
        _record("test", "test.tapesift", 11, "pass", 8.5),
    ]


def test_threshold_rule_is_small_and_readable():
    rule, ranked = fit_threshold_rule(
        _training(),
        candidate_features=("motion_y_mean",),
        minimum_leaf_records=2,
    )

    assert rule.feature == "motion_y_mean"
    assert rule.threshold == 5.0
    assert rule.low_label == "run"
    assert rule.high_label == "pass"
    assert rule.predict({"motion_y_mean": 2.5}) == "run"
    assert "otherwise predict PASS" in rule.describe()
    assert ranked == [rule]


def test_holdout_labels_cannot_change_the_learned_rule():
    original = evaluate_holdout(_training(), _holdout())
    swapped = [
        {**record, "label": "pass" if record["label"] == "run" else "run"}
        for record in _holdout()
    ]
    changed = evaluate_holdout(_training(), swapped)

    assert original["rule"] == changed["rule"]
    assert original["protocol"]["holdout_used_for_rule_selection"] is False
    assert original["holdout_metrics"] != changed["holdout_metrics"]


def test_duration_is_not_a_candidate_shortcut():
    training = _training()
    training[0]["features"]["duration_ms"] = 1
    training[1]["features"]["duration_ms"] = 2
    training[2]["features"]["duration_ms"] = 100
    training[3]["features"]["duration_ms"] = 200

    rule, _ranked = fit_threshold_rule(training)

    assert rule.feature != "duration_ms"
    assert "duration_ms" not in CANDIDATE_FEATURES


def test_metrics_report_both_classes_and_wilson_interval():
    metrics = classification_metrics(
        ["run", "run", "pass", "pass"],
        ["run", "pass", "pass", "pass"],
    )

    assert metrics["accuracy"] == 0.75
    assert metrics["balanced_accuracy"] == 0.75
    assert metrics["confusion"]["run"] == {"run": 1, "pass": 1}
    assert metrics["recall"] == {"run": 0.5, "pass": 1.0}
    assert metrics["precision"] == {
        "run": 1.0, "pass": round(2 / 3, 6)}
    assert metrics["macro_f1"] == round((2 / 3 + 0.8) / 2, 6)
    assert metrics["matthews_correlation"] > 0
    assert metrics["coverage"] == 1.0
    assert metrics["accuracy_wilson_95"][0] < 0.75
    assert metrics["accuracy_wilson_95"][1] > 0.75


def test_same_cohort_cannot_masquerade_as_holdout():
    holdout = [
        {**record, "research_cohort_id": "train"}
        for record in _holdout()
    ]

    with pytest.raises(ValueError, match="cohorts must be distinct"):
        evaluate_holdout(_training(), holdout)


def test_file_evaluation_writes_json_and_readable_report(tmp_path: Path):
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

    result = evaluate_feature_manifests(
        train, holdout, output, report)

    assert result["holdout_metrics"]["accuracy"] == 1.0
    assert json.loads(output.read_text(encoding="utf-8"))["rule"] \
        == result["rule"]
    report_text = report.read_text(encoding="utf-8")
    assert "First Cross-Game Holdout" in report_text
    assert "Alabama labels were not used" in report_text
    assert result["protocol"]["training_manifest_sha256"]
    assert result["protocol"]["holdout_manifest_sha256"]
    assert result["majority_reference"]["predicted_label"] == "run"
