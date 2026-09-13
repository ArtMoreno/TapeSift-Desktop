"""Selective cross-game baseline for Temporal v2 Run/Pass features.

The extractor may abstain when it cannot establish two trustworthy camera
angles and snap proxies.  This evaluator preserves those abstentions, fits
one readable threshold using only the training game, and opens the holdout
labels only after the rule is frozen.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from tapesift.research.run_pass_baseline import (
    BINARY_LABELS,
    classification_metrics,
    fit_threshold_rule,
)
from tapesift.research.run_pass_features import reject_quarantined_input
from tapesift.research.run_pass_temporal_features import (
    TEMPORAL_CLASSIFIER_FEATURES,
)


TEMPORAL_BASELINE_SCHEMA_VERSION = "1.0"
TEMPORAL_DATASET_KIND = "tapesift_run_pass_temporal_features"


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
                f"Invalid JSON on {path.name} line {line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(
                f"Expected an object on {path.name} line {line_number}")
        records.append(value)
    if not records:
        raise ValueError(f"Temporal feature manifest is empty: {path}")
    return records


def _binary_records(
        records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record for record in records
        if str(record.get("label", "")).casefold() in BINARY_LABELS
    ]


def _eligible_records(
        records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    for record in _binary_records(records):
        if record.get("classifier_eligible") is not True:
            continue
        features = record.get("features")
        if not isinstance(features, dict):
            raise ValueError(
                "An eligible Temporal record has no features object")
        missing = [
            name for name in TEMPORAL_CLASSIFIER_FEATURES
            if name not in features
        ]
        if missing:
            raise ValueError(
                "An eligible Temporal record is missing classifier "
                f"features: {', '.join(missing)}")
        eligible.append(record)
    return eligible


def _one_value(records: list[dict[str, Any]], key: str) -> str:
    values = {str(record.get(key, "")).strip() for record in records}
    if "" in values or len(values) != 1:
        raise ValueError(f"Manifest must contain one non-empty {key}")
    return next(iter(values))


def _extractor_signature(
        records: list[dict[str, Any]]) -> dict[str, Any]:
    signatures: set[str] = set()
    values: list[dict[str, Any]] = []
    for record in records:
        if record.get("dataset_kind") != TEMPORAL_DATASET_KIND:
            raise ValueError("Manifest is not a Temporal feature dataset")
        extractor = record.get("feature_extractor")
        if not isinstance(extractor, dict):
            raise ValueError(
                "Temporal record is missing extractor provenance")
        signature = {
            key: extractor.get(key)
            for key in ("name", "version", "sample_fps", "sample_width")
        }
        signatures.add(json.dumps(signature, sort_keys=True))
        values.append(signature)
    if len(signatures) != 1:
        raise ValueError("Manifest mixes incompatible Temporal extractors")
    return values[0]


def _abstain_reasons(record: Mapping[str, Any]) -> list[str]:
    values = record.get("abstain_reasons")
    if not isinstance(values, list):
        diagnostics = record.get("temporal_diagnostics")
        values = (
            diagnostics.get("abstain_reasons", [])
            if isinstance(diagnostics, dict)
            else []
        )
    return [str(value) for value in values if str(value).strip()]


def _coverage(eligible: int, total: int) -> dict[str, Any]:
    return {
        "eligible": eligible,
        "abstained": total - eligible,
        "total": total,
        "coverage": round(eligible / total, 6) if total else 0.0,
    }


def evaluate_temporal_holdout(
        training_records: Iterable[dict[str, Any]],
        holdout_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Fit on eligible training rows, then score eligible holdout rows."""

    train_all = _binary_records(training_records)
    holdout_all = _binary_records(holdout_records)
    train = _eligible_records(train_all)
    holdout = _eligible_records(holdout_all)
    if not train or not holdout:
        raise ValueError(
            "Training and holdout need classifier-eligible binary records")
    train_cohort = _one_value(train_all, "research_cohort_id")
    holdout_cohort = _one_value(holdout_all, "research_cohort_id")
    if train_cohort == holdout_cohort:
        raise ValueError("Training and holdout cohorts must be distinct")
    train_project = _one_value(train_all, "source_project")
    holdout_project = _one_value(holdout_all, "source_project")
    if train_project == holdout_project:
        raise ValueError("Training and holdout projects must be distinct")
    train_extractor = _extractor_signature(train_all)
    holdout_extractor = _extractor_signature(holdout_all)
    if train_extractor != holdout_extractor:
        raise ValueError("Training and holdout extractors must match")

    rule, ranked = fit_threshold_rule(
        train,
        candidate_features=TEMPORAL_CLASSIFIER_FEATURES,
    )
    training_predictions = [
        rule.predict(record["features"]) for record in train]
    training_metrics = classification_metrics(
        [str(record["label"]) for record in train],
        training_predictions,
    )

    eligible_predictions: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []
    eligible_ids = {id(record) for record in holdout}
    for record in holdout_all:
        if id(record) not in eligible_ids:
            abstentions.append({
                "clip_number": int(record.get("clip_number", 0)),
                "actual": str(record.get("label", "")).casefold(),
                "reasons": _abstain_reasons(record),
            })
            continue
        value = float(record["features"][rule.feature])
        actual = str(record["label"]).casefold()
        predicted = rule.predict(record["features"])
        eligible_predictions.append({
            "clip_number": int(record.get("clip_number", 0)),
            "actual": actual,
            "predicted": predicted,
            "correct": actual == predicted,
            "feature": rule.feature,
            "feature_value": round(value, 9),
            "play_type": str(record.get("play_type", "")),
            "play_action": str(record.get("play_action", "")),
        })
    holdout_metrics = classification_metrics(
        [item["actual"] for item in eligible_predictions],
        [item["predicted"] for item in eligible_predictions],
    )
    holdout_coverage = _coverage(len(holdout), len(holdout_all))
    training_coverage = _coverage(len(train), len(train_all))
    holdout_metrics["coverage"] = holdout_coverage["coverage"]
    holdout_metrics["covered_correct_share"] = round(
        holdout_metrics["correct"] / len(holdout_all), 6)

    training_counts = {
        label: sum(
            str(record["label"]).casefold() == label for record in train)
        for label in BINARY_LABELS
    }
    majority_label = max(
        BINARY_LABELS,
        key=lambda label: (
            training_counts[label],
            -BINARY_LABELS.index(label),
        ),
    )
    majority_reference = classification_metrics(
        [item["actual"] for item in eligible_predictions],
        [majority_label] * len(eligible_predictions),
    )
    majority_reference["predicted_label"] = majority_label
    majority_reference["coverage"] = holdout_coverage["coverage"]

    acceptance = {
        "minimum_accuracy": 0.80,
        "minimum_balanced_accuracy": 0.75,
        "minimum_coverage": 0.75,
    }
    accepted = (
        holdout_metrics["accuracy"] >= acceptance["minimum_accuracy"]
        and holdout_metrics["balanced_accuracy"]
        >= acceptance["minimum_balanced_accuracy"]
        and holdout_coverage["coverage"]
        >= acceptance["minimum_coverage"]
    )
    return {
        "schema_version": TEMPORAL_BASELINE_SCHEMA_VERSION,
        "protocol": {
            "training_cohort": train_cohort,
            "holdout_cohort": holdout_cohort,
            "training_project": train_project,
            "holdout_project": holdout_project,
            "holdout_used_for_rule_selection": False,
            "candidate_features": list(TEMPORAL_CLASSIFIER_FEATURES),
            "excluded_diagnostics": [
                "clip_duration_seconds",
                "sampled_frames",
                "raw_motion",
                "angle_split_location",
                "onset_seconds",
                "onset_confidence",
                "angle_disagreement",
            ],
            "extractor": train_extractor,
        },
        "rule": asdict(rule),
        "rule_description": rule.describe(),
        "training_coverage": training_coverage,
        "holdout_coverage": holdout_coverage,
        "training_metrics": training_metrics,
        "holdout_metrics": holdout_metrics,
        "majority_reference": majority_reference,
        "ranked_training_rules": [
            asdict(item) for item in ranked],
        "predictions": eligible_predictions,
        "abstentions": abstentions,
        "acceptance_gate": {
            **acceptance,
            "passed": accepted,
        },
    }


def render_markdown_report(result: dict[str, Any]) -> str:
    protocol = result["protocol"]
    training = result["training_metrics"]
    holdout = result["holdout_metrics"]
    coverage = result["holdout_coverage"]
    majority = result["majority_reference"]
    interval = holdout["accuracy_wilson_95"]
    errors = [
        item for item in result["predictions"] if not item["correct"]]
    gate = result["acceptance_gate"]

    lines = [
        "# TapeSift Temporal v2 — Cross-Game Run/Pass Baseline",
        "",
        "This is a deterministic, selective threshold test. It is not an AI "
        "model and does not inspect video content beyond the frozen FFmpeg "
        "Temporal v2 measurements.",
        "",
        "## Protocol",
        "",
        f"- Training cohort: `{protocol['training_cohort']}`",
        f"- Untuned holdout: `{protocol['holdout_cohort']}`",
        "- The feature, threshold, and direction were selected from the "
        "training game only.",
        "- Clips rejected by the Temporal quality gate remain abstentions; "
        "they are not silently scored or discarded.",
        "",
        "## Frozen rule",
        "",
        f"> {result['rule_description']}",
        "",
        "## Results",
        "",
        "| Split | Eligible | Coverage | Correct | Accuracy | "
        "Balanced accuracy |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| Training | {result['training_coverage']['eligible']}/"
        f"{result['training_coverage']['total']} | "
        f"{result['training_coverage']['coverage']:.1%} | "
        f"{training['correct']}/{training['records']} | "
        f"{training['accuracy']:.1%} | "
        f"{training['balanced_accuracy']:.1%} |",
        f"| Holdout | {coverage['eligible']}/{coverage['total']} | "
        f"{coverage['coverage']:.1%} | "
        f"{holdout['correct']}/{holdout['records']} | "
        f"{holdout['accuracy']:.1%} | "
        f"{holdout['balanced_accuracy']:.1%} |",
        f"| Holdout majority reference "
        f"({majority['predicted_label'].title()}) | "
        f"{coverage['eligible']}/{coverage['total']} | "
        f"{coverage['coverage']:.1%} | "
        f"{majority['correct']}/{majority['records']} | "
        f"{majority['accuracy']:.1%} | "
        f"{majority['balanced_accuracy']:.1%} |",
        "",
        f"Holdout accuracy 95% Wilson interval: "
        f"**{interval[0]:.1%}–{interval[1]:.1%}**.",
        "",
        f"Correct predictions across all holdout plays, counting abstentions "
        f"as uncovered: **{holdout['covered_correct_share']:.1%}**.",
        "",
        "## Holdout errors on covered plays",
        "",
    ]
    if errors:
        lines.extend([
            "| Clip | Actual | Predicted | Rule value | Modifiers |",
            "| ---: | --- | --- | ---: | --- |",
        ])
        for item in errors:
            modifiers = ", ".join(
                value for value in (
                    item["play_type"], item["play_action"])
                if value
            ) or "Plain"
            lines.append(
                f"| {item['clip_number']} | {item['actual'].title()} | "
                f"{item['predicted'].title()} | "
                f"{item['feature_value']:.6f} | {modifiers} |"
            )
    else:
        lines.append("No covered holdout errors.")

    lines.extend([
        "",
        "## Abstentions",
        "",
    ])
    if result["abstentions"]:
        for item in result["abstentions"]:
            reasons = ", ".join(item["reasons"]) or "quality gate"
            lines.append(
                f"- Clip {item['clip_number']}: {reasons}")
    else:
        lines.append("- None")

    lines.extend([
        "",
        "## Decision",
        "",
        (
            "**PASS:** the frozen rule cleared the current personal-use "
            "accuracy, balance, and coverage gates."
            if gate["passed"]
            else
            "**DO NOT DEPLOY:** the frozen rule did not clear the current "
            "personal-use accuracy, balance, and coverage gates."
        ),
        "",
        "The next experiment must add a new predeclared measurement family "
        "rather than tune this threshold against Alabama. Alabama has now "
        "served as the opened holdout for this Temporal v2 hypothesis.",
        "",
    ])
    return "\n".join(lines)


def evaluate_temporal_feature_manifests(
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

    result = evaluate_temporal_holdout(
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
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    markdown_temp.write_text(
        render_markdown_report(result), encoding="utf-8")
    json_temp.replace(output_json)
    markdown_temp.replace(output_markdown)
    return result


__all__ = [
    "TEMPORAL_BASELINE_SCHEMA_VERSION",
    "evaluate_temporal_feature_manifests",
    "evaluate_temporal_holdout",
    "render_markdown_report",
]
