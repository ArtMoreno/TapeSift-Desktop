"""Transparent Virginia-to-Alabama run/pass holdout evaluation.

This module deliberately fits one threshold rule. The rule is small enough
to read aloud, uses only normalized FFmpeg motion measurements, and is chosen
without consulting the holdout game.
"""

from __future__ import annotations

import json
import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from tapesift.research.run_pass_features import reject_quarantined_input


BASELINE_SCHEMA_VERSION = "1.0"
BINARY_LABELS = ("run", "pass")

# Deliberately omit duration, sampled-frame count, brightness, saturation,
# and motion maximum. Those can reveal editor/camera behavior or scale with
# clip length—the wrong shortcuts for learning football.
CANDIDATE_FEATURES = (
    "motion_y_mean",
    "motion_y_std",
    "motion_y_p50",
    "motion_y_p90",
    "motion_y_p95",
    "motion_uv_mean",
    "still_frame_share",
    "high_motion_share",
    "motion_q1_mean",
    "motion_q2_mean",
    "motion_q3_mean",
    "motion_q4_mean",
    "motion_late_early_ratio",
    "motion_peak_position",
)


@dataclass(frozen=True)
class ThresholdRule:
    """One human-readable split learned from the training game only."""

    feature: str
    threshold: float
    low_label: str
    high_label: str
    training_minimum: float
    training_maximum: float
    training_accuracy: float
    training_balanced_accuracy: float
    minimum_leaf_records: int

    def predict(self, features: dict[str, Any]) -> str:
        value = _finite_number(features.get(self.feature), self.feature)
        return self.low_label if value <= self.threshold else self.high_label

    def describe(self) -> str:
        return (
            f"If {self.feature} <= {self.threshold:.6f}, predict "
            f"{self.low_label.upper()}; otherwise predict "
            f"{self.high_label.upper()}."
        )


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Feature {name!r} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Feature {name!r} must be finite")
    return number


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on {path.name} line {line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise ValueError(
                f"Expected an object on {path.name} line {line_number}")
        records.append(value)
    if not records:
        raise ValueError(f"Feature manifest is empty: {path}")
    return records


def _binary_records(
        records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record for record in records
        if str(record.get("label", "")).casefold() in BINARY_LABELS
    ]


def classification_metrics(
        actual: Iterable[str], predicted: Iterable[str]) -> dict[str, Any]:
    """Return accuracy, balanced accuracy, and an explicit confusion matrix."""
    truth = [value.casefold() for value in actual]
    guesses = [value.casefold() for value in predicted]
    if not truth or len(truth) != len(guesses):
        raise ValueError("Metrics require equal non-empty label sequences")
    if any(value not in BINARY_LABELS for value in truth + guesses):
        raise ValueError("Metrics accept only run and pass labels")

    confusion = {
        label: {guess: 0 for guess in BINARY_LABELS}
        for label in BINARY_LABELS
    }
    for expected, guess in zip(truth, guesses):
        confusion[expected][guess] += 1
    correct = sum(
        confusion[label][label] for label in BINARY_LABELS)
    recalls: dict[str, float] = {}
    precisions: dict[str, float] = {}
    f1_scores: dict[str, float] = {}
    recall_intervals: dict[str, list[float]] = {}
    for label in BINARY_LABELS:
        total = sum(confusion[label].values())
        if not total:
            raise ValueError(
                f"Metrics require at least one {label} example")
        recalls[label] = confusion[label][label] / total
        predicted_total = sum(
            confusion[actual_label][label]
            for actual_label in BINARY_LABELS
        )
        precisions[label] = (
            confusion[label][label] / predicted_total
            if predicted_total else 0.0
        )
        denominator = precisions[label] + recalls[label]
        f1_scores[label] = (
            2 * precisions[label] * recalls[label] / denominator
            if denominator else 0.0
        )
        recall_intervals[label] = [
            round(value, 6)
            for value in _wilson_interval(confusion[label][label], total)
        ]
    accuracy = correct / len(truth)
    true_pass = confusion["pass"]["pass"]
    true_run = confusion["run"]["run"]
    false_pass = confusion["run"]["pass"]
    false_run = confusion["pass"]["run"]
    mcc_denominator = math.sqrt(
        (true_pass + false_pass)
        * (true_pass + false_run)
        * (true_run + false_pass)
        * (true_run + false_run)
    )
    mcc = (
        (true_pass * true_run - false_pass * false_run) / mcc_denominator
        if mcc_denominator else 0.0
    )
    return {
        "records": len(truth),
        "correct": correct,
        "accuracy": round(accuracy, 6),
        "balanced_accuracy": round(
            sum(recalls.values()) / len(recalls), 6),
        "recall": {
            label: round(recalls[label], 6) for label in BINARY_LABELS
        },
        "precision": {
            label: round(precisions[label], 6) for label in BINARY_LABELS
        },
        "f1": {
            label: round(f1_scores[label], 6) for label in BINARY_LABELS
        },
        "macro_f1": round(
            sum(f1_scores.values()) / len(f1_scores), 6),
        "matthews_correlation": round(mcc, 6),
        "coverage": 1.0,
        "recall_wilson_95": recall_intervals,
        "confusion": confusion,
        "accuracy_wilson_95": [
            round(value, 6)
            for value in _wilson_interval(correct, len(truth))
        ],
    }


def _wilson_interval(
        successes: int, total: int, z: float = 1.959964) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("Wilson interval requires at least one record")
    proportion = successes / total
    denominator = 1 + (z * z / total)
    center = (
        proportion + (z * z / (2 * total))
    ) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _feature_values(
        records: list[dict[str, Any]], feature: str) -> list[float]:
    values: list[float] = []
    for record in records:
        features = record.get("features")
        if not isinstance(features, dict):
            raise ValueError("Every record must contain a features object")
        values.append(_finite_number(features.get(feature), feature))
    return values


def fit_threshold_rule(
        training_records: Iterable[dict[str, Any]],
        *,
        candidate_features: tuple[str, ...] = CANDIDATE_FEATURES,
        minimum_leaf_records: int | None = None,
) -> tuple[ThresholdRule, list[ThresholdRule]]:
    """Fit and rank one threshold per feature using training labels only."""
    records = _binary_records(training_records)
    labels = [str(record["label"]).casefold() for record in records]
    if not records or any(labels.count(label) == 0 for label in BINARY_LABELS):
        raise ValueError("Training data needs both run and pass examples")
    if minimum_leaf_records is None:
        minimum_leaf_records = max(2, math.ceil(len(records) * 0.15))
    if minimum_leaf_records < 1:
        raise ValueError("Minimum leaf records must be positive")

    per_feature: list[ThresholdRule] = []
    for feature in candidate_features:
        values = _feature_values(records, feature)
        unique = sorted(set(values))
        feature_rules: list[tuple[tuple[float, ...], ThresholdRule]] = []
        for low, high in zip(unique, unique[1:]):
            threshold = (low + high) / 2
            low_count = sum(value <= threshold for value in values)
            high_count = len(values) - low_count
            if min(low_count, high_count) < minimum_leaf_records:
                continue
            for low_label, high_label in (
                    ("run", "pass"), ("pass", "run")):
                predictions = [
                    low_label if value <= threshold else high_label
                    for value in values
                ]
                metrics = classification_metrics(labels, predictions)
                normalized_gap = (
                    (high - low) / (max(values) - min(values))
                    if max(values) > min(values) else 0.0
                )
                rule = ThresholdRule(
                    feature=feature,
                    threshold=round(threshold, 9),
                    low_label=low_label,
                    high_label=high_label,
                    training_minimum=min(values),
                    training_maximum=max(values),
                    training_accuracy=metrics["accuracy"],
                    training_balanced_accuracy=metrics[
                        "balanced_accuracy"],
                    minimum_leaf_records=minimum_leaf_records,
                )
                rank = (
                    metrics["balanced_accuracy"],
                    metrics["accuracy"],
                    normalized_gap,
                    -abs(threshold),
                )
                feature_rules.append((rank, rule))
        if feature_rules:
            per_feature.append(max(feature_rules, key=lambda item: item[0])[1])
    if not per_feature:
        raise ValueError("No candidate feature produced a valid split")

    feature_order = {
        feature: index for index, feature in enumerate(candidate_features)}
    ranked = sorted(
        per_feature,
        key=lambda rule: (
            -rule.training_balanced_accuracy,
            -rule.training_accuracy,
            feature_order[rule.feature],
            rule.threshold,
        ),
    )
    return ranked[0], ranked


def _extractor_signature(records: list[dict[str, Any]]) -> dict[str, Any]:
    signatures: set[str] = set()
    values: list[dict[str, Any]] = []
    for record in records:
        extractor = record.get("feature_extractor")
        if not isinstance(extractor, dict):
            raise ValueError("Feature record is missing extractor provenance")
        signature = {
            key: extractor.get(key)
            for key in ("name", "version", "sample_fps", "sample_width")
        }
        signatures.add(json.dumps(signature, sort_keys=True))
        values.append(signature)
    if len(signatures) != 1:
        raise ValueError("Manifest mixes incompatible feature extractors")
    return values[0]


def _one_value(records: list[dict[str, Any]], key: str) -> str:
    values = {str(record.get(key, "")).strip() for record in records}
    if "" in values or len(values) != 1:
        raise ValueError(f"Manifest must contain one non-empty {key}")
    return next(iter(values))


def _modifier_flags(record: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    play_type = str(record.get("play_type", "")).strip()
    play_action = str(record.get("play_action", "")).strip()
    if play_type:
        flags.append(play_type)
    if play_action:
        flags.append(play_action)
    return flags or ["Plain"]


def _slice_metrics(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for prediction in predictions:
        for modifier in prediction["modifiers"]:
            groups.setdefault(modifier, []).append(prediction)
    return {
        name: classification_metrics(
            [item["actual"] for item in items],
            [item["predicted"] for item in items],
        )
        for name, items in sorted(groups.items())
        if {item["actual"] for item in items} == set(BINARY_LABELS)
    }


def evaluate_holdout(
        training_records: Iterable[dict[str, Any]],
        holdout_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Fit on one cohort, then open and score a distinct holdout cohort."""
    train = _binary_records(training_records)
    holdout = _binary_records(holdout_records)
    if not train or not holdout:
        raise ValueError("Training and holdout need binary records")
    train_cohort = _one_value(train, "research_cohort_id")
    holdout_cohort = _one_value(holdout, "research_cohort_id")
    if train_cohort == holdout_cohort:
        raise ValueError("Training and holdout cohorts must be distinct")
    train_project = _one_value(train, "source_project")
    holdout_project = _one_value(holdout, "source_project")
    if train_project == holdout_project:
        raise ValueError("Training and holdout projects must be distinct")
    train_extractor = _extractor_signature(train)
    holdout_extractor = _extractor_signature(holdout)
    if train_extractor != holdout_extractor:
        raise ValueError("Training and holdout extractors must match")

    rule, ranked = fit_threshold_rule(train)
    train_predictions = [
        rule.predict(record["features"]) for record in train]
    train_metrics = classification_metrics(
        [str(record["label"]) for record in train],
        train_predictions,
    )

    predictions: list[dict[str, Any]] = []
    training_span = max(
        rule.training_maximum - rule.training_minimum, 1e-12)
    for record in holdout:
        features = record["features"]
        value = _finite_number(features.get(rule.feature), rule.feature)
        actual = str(record["label"]).casefold()
        predicted = rule.predict(features)
        predictions.append({
            "clip_number": int(record.get("clip_number", 0)),
            "actual": actual,
            "predicted": predicted,
            "correct": actual == predicted,
            "feature": rule.feature,
            "feature_value": round(value, 9),
            "threshold_margin_fraction": round(
                abs(value - rule.threshold) / training_span, 6),
            "duration_seconds": round(
                _finite_number(
                    features.get("duration_ms"), "duration_ms") / 1000,
                3,
            ),
            "play_type": str(record.get("play_type", "")),
            "play_action": str(record.get("play_action", "")),
            "modifiers": _modifier_flags(record),
        })
    holdout_metrics = classification_metrics(
        [item["actual"] for item in predictions],
        [item["predicted"] for item in predictions],
    )
    training_counts = {
        label: sum(
            str(record["label"]).casefold() == label for record in train)
        for label in BINARY_LABELS
    }
    majority_label = max(
        BINARY_LABELS,
        key=lambda label: (training_counts[label], -BINARY_LABELS.index(label)),
    )
    majority_reference = classification_metrics(
        [item["actual"] for item in predictions],
        [majority_label] * len(predictions),
    )
    majority_reference["predicted_label"] = majority_label

    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "protocol": {
            "training_cohort": train_cohort,
            "holdout_cohort": holdout_cohort,
            "training_project": train_project,
            "holdout_project": holdout_project,
            "training_records": len(train),
            "holdout_records": len(holdout),
            "holdout_used_for_rule_selection": False,
            "candidate_features": list(CANDIDATE_FEATURES),
            "excluded_shortcut_features": [
                "duration_ms",
                "sampled_frames",
                "sample_fps",
                "luma_mean",
                "luma_std",
                "saturation_mean",
                "motion_y_max",
            ],
            "extractor": train_extractor,
        },
        "rule": asdict(rule),
        "rule_description": rule.describe(),
        "training_metrics": train_metrics,
        "holdout_metrics": holdout_metrics,
        "majority_reference": majority_reference,
        "modifier_slices": _slice_metrics(predictions),
        "ranked_training_rules": [
            asdict(item) for item in ranked[:5]
        ],
        "predictions": predictions,
    }


def render_markdown_report(result: dict[str, Any]) -> str:
    protocol = result["protocol"]
    rule = result["rule"]
    training = result["training_metrics"]
    holdout = result["holdout_metrics"]
    majority = result["majority_reference"]
    confusion = holdout["confusion"]
    interval = holdout["accuracy_wilson_95"]
    predictions = result["predictions"]
    errors = [item for item in predictions if not item["correct"]]

    lines = [
        "# TapeSift Run/Pass Baseline — First Cross-Game Holdout",
        "",
        "## Protocol",
        "",
        f"- Training cohort: `{protocol['training_cohort']}` "
        f"({protocol['training_records']} Run/Pass plays)",
        f"- Untuned holdout: `{protocol['holdout_cohort']}` "
        f"({protocol['holdout_records']} Run/Pass plays)",
        "- Alabama labels were not used to select the feature, threshold, "
        "or direction.",
        "- Duration, sample count, brightness, saturation, and motion maximum "
        "were excluded to reduce editing and camera-style shortcuts.",
        "",
        "## Learned rule",
        "",
        f"> {result['rule_description']}",
        "",
        f"The split requires at least {rule['minimum_leaf_records']} Virginia "
        "plays on each side.",
        "",
        "## Results",
        "",
        "| Split | Correct | Accuracy | Balanced accuracy |",
        "| --- | ---: | ---: | ---: |",
        f"| Virginia training (descriptive) | {training['correct']}/"
        f"{training['records']} | {training['accuracy']:.1%} | "
        f"{training['balanced_accuracy']:.1%} |",
        f"| Alabama untuned holdout | {holdout['correct']}/"
        f"{holdout['records']} | {holdout['accuracy']:.1%} | "
        f"{holdout['balanced_accuracy']:.1%} |",
        f"| Majority reference ({majority['predicted_label'].title()}) | "
        f"{majority['correct']}/{majority['records']} | "
        f"{majority['accuracy']:.1%} | "
        f"{majority['balanced_accuracy']:.1%} |",
        "",
        f"Holdout macro-F1: **{holdout['macro_f1']:.3f}**; "
        f"Matthews correlation: "
        f"**{holdout['matthews_correlation']:.3f}**; coverage: "
        f"**{holdout['coverage']:.0%}**.",
        "",
        f"Alabama accuracy 95% Wilson interval: "
        f"**{interval[0]:.1%}–{interval[1]:.1%}**. The interval is wide "
        "because this is only 20 plays.",
        "",
        "### Alabama confusion matrix",
        "",
        "| Actual \\ Predicted | Run | Pass |",
        "| --- | ---: | ---: |",
        f"| Run | {confusion['run']['run']} | "
        f"{confusion['run']['pass']} |",
        f"| Pass | {confusion['pass']['run']} | "
        f"{confusion['pass']['pass']} |",
        "",
        "## Misclassified Alabama plays",
        "",
    ]
    if errors:
        lines.extend([
            "| Clip | Actual | Predicted | Rule value | Duration | Modifiers |",
            "| ---: | --- | --- | ---: | ---: | --- |",
        ])
        for item in errors:
            modifiers = ", ".join(item["modifiers"])
            lines.append(
                f"| {item['clip_number']} | {item['actual'].title()} | "
                f"{item['predicted'].title()} | "
                f"{item['feature_value']:.6f} | "
                f"{item['duration_seconds']:.1f}s | {modifiers} |"
            )
    else:
        lines.append("No holdout errors.")
    lines.extend([
        "",
        "## Interpretation guardrail",
        "",
        "This is a deterministic measurement rule, not visual football "
        "recognition and not an AI model. One game cannot establish an 80% "
        "production claim. The domain-level evidence is one training game "
        "and one holdout game—not 20 independent games. The holdout result "
        "decides whether the current "
        "motion-only direction deserves refinement or needs richer "
        "non-AI measurements.",
        "",
    ])
    return "\n".join(lines)


def evaluate_feature_manifests(
        training_path: Path,
        holdout_path: Path,
        output_json: Path,
        output_markdown: Path,
        *,
        overwrite: bool = False,
) -> dict[str, Any]:
    """Evaluate two frozen manifests and atomically persist both reports."""
    training_path = reject_quarantined_input(training_path)
    holdout_path = reject_quarantined_input(holdout_path)
    output_json = output_json.resolve()
    output_markdown = output_markdown.resolve()
    for path in (training_path, holdout_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    for path in (output_json, output_markdown):
        if path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to replace report: {path}")

    result = evaluate_holdout(
        _read_jsonl(training_path),
        _read_jsonl(holdout_path),
    )
    result["protocol"]["training_manifest"] = str(training_path)
    result["protocol"]["holdout_manifest"] = str(holdout_path)
    result["protocol"]["training_manifest_sha256"] = hashlib.sha256(
        training_path.read_bytes()).hexdigest()
    result["protocol"]["holdout_manifest_sha256"] = hashlib.sha256(
        holdout_path.read_bytes()).hexdigest()

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    json_temp = output_json.with_suffix(output_json.suffix + ".tmp")
    markdown_temp = output_markdown.with_suffix(
        output_markdown.suffix + ".tmp")
    json_temp.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    markdown_temp.write_text(
        render_markdown_report(result), encoding="utf-8")
    json_temp.replace(output_json)
    markdown_temp.replace(output_markdown)
    return result
