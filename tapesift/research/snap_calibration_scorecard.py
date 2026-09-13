"""Frozen, label-blind scorecard for exact-snap calibration judgments.

Iteration 7A measures the fixed Temporal v2.1 onset proposals against the
completed human snap marks.  It neither extracts new features nor changes the
Cadence Segmentation Engine or Temporal extractor.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


SCORECARD_SCHEMA_VERSION = "1.0"
CALIBRATION_DATASET_KIND = "tapesift_temporal_snap_calibration"
CALIBRATION_SCHEMA_VERSION = "1.0"
FROZEN_PLAY_COUNT = 45
FROZEN_ANGLE_COUNT = 90
EXACT_SNAP_STATUS = "marked"
UNAVAILABLE_SNAP_STATUSES = frozenset({"not_visible", "unsure"})

LOCALIZATION_GATES = {
    "maximum_median_absolute_error_ms": 250,
    "minimum_within_500_ms_share": 0.90,
    "maximum_p90_absolute_error_ms": 750,
    "maximum_median_signed_bias_abs_ms": 125,
    "minimum_two_eligible_angle_play_share": 0.80,
    "minimum_both_angles_within_500_ms_share": 0.80,
}


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
        raise ValueError(f"Snap calibration judgment file is empty: {path}")
    return records


def _integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if result != value:
        raise ValueError(f"{field_name} must be an integer")
    return result


def _required_text(record: Mapping[str, Any], key: str) -> str:
    value = str(record.get(key, "")).strip()
    if not value:
        raise ValueError(f"Calibration record is missing {key}")
    return value


def _share(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _median(values: list[int]) -> float:
    if not values:
        raise ValueError("Cannot calculate a median for no values")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _nearest_rank(values: list[int], percentile: float) -> int:
    """Return a percentile using the predeclared nearest-rank convention."""

    if not values:
        raise ValueError("Cannot calculate a percentile for no values")
    return sorted(values)[math.ceil(percentile * len(values)) - 1]


def _normalized_manifest_fingerprints(
        records: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    fingerprints: set[tuple[str, str]] = set()
    expected: set[tuple[str, str]] | None = None
    for record in records:
        values = record.get("source_manifests")
        if not isinstance(values, list) or not values:
            raise ValueError("Calibration record is missing source_manifests")
        current: set[tuple[str, str]] = set()
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("source_manifests entries must be objects")
            name = str(value.get("name", "")).strip()
            sha256 = str(value.get("sha256", "")).strip().lower()
            if not name or len(sha256) != 64 or any(
                    char not in "0123456789abcdef" for char in sha256):
                raise ValueError("Invalid source manifest fingerprint")
            current.add((name, sha256))
        if expected is None:
            expected = current
        elif current != expected:
            raise ValueError(
                "Calibration records do not share one frozen manifest set")
        fingerprints.update(current)
    return [
        {"name": name, "sha256": sha256}
        for name, sha256 in sorted(fingerprints)
    ]


def _validate_and_collect(
        records: Iterable[Mapping[str, Any]]) -> tuple[
            list[dict[str, Any]], list[dict[str, str]]]:
    values = list(records)
    if not values:
        raise ValueError("Snap calibration records are empty")
    fingerprints = _normalized_manifest_fingerprints(values)
    seen_items: set[str] = set()
    angles: list[dict[str, Any]] = []
    for record in values:
        if record.get("dataset_kind") != CALIBRATION_DATASET_KIND:
            raise ValueError("Record is not a snap calibration judgment")
        if record.get("schema_version") != CALIBRATION_SCHEMA_VERSION:
            raise ValueError("Calibration schema version does not match")
        if record.get("workflow") != "snap_calibration":
            raise ValueError("Record is not from the snap calibration workflow")
        item_id = _required_text(record, "item_id")
        if item_id in seen_items:
            raise ValueError(f"Duplicate calibration item_id: {item_id}")
        seen_items.add(item_id)
        cohort = _required_text(record, "research_cohort_id")
        project = _required_text(record, "project_name")
        clip_number = _integer(record.get("clip_number"),
                               field_name="clip_number")
        record_angles = record.get("angles")
        if not isinstance(record_angles, list) or len(record_angles) != 2:
            raise ValueError(f"{item_id} must contain exactly two angles")
        angle_numbers: set[int] = set()
        for raw_angle in record_angles:
            if not isinstance(raw_angle, dict):
                raise ValueError(f"{item_id} has an invalid angle")
            angle_number = _integer(raw_angle.get("angle"),
                                    field_name="angle")
            if angle_number not in {1, 2} or angle_number in angle_numbers:
                raise ValueError(f"{item_id} must contain angles 1 and 2")
            angle_numbers.add(angle_number)
            proposed = _integer(raw_angle.get("proposed_onset_ms"),
                                field_name="proposed_onset_ms")
            status = str(raw_angle.get("snap_status", "")).strip()
            actual_value = raw_angle.get("actual_snap_ms")
            delta_value = raw_angle.get("delta_ms")
            if status == EXACT_SNAP_STATUS:
                actual = _integer(actual_value, field_name="actual_snap_ms")
                delta = _integer(delta_value, field_name="delta_ms")
                if delta != actual - proposed:
                    raise ValueError(
                        f"{item_id} angle {angle_number} has a bad delta_ms")
            elif status in UNAVAILABLE_SNAP_STATUSES:
                if actual_value is not None or delta_value is not None:
                    raise ValueError(
                        f"{item_id} angle {angle_number} has unavailable "
                        "status with a timestamp")
                actual = None
                delta = None
            else:
                raise ValueError(
                    f"{item_id} angle {angle_number} has incomplete status")
            angles.append({
                "item_id": item_id,
                "research_cohort_id": cohort,
                "project_name": project,
                "clip_number": clip_number,
                "angle": angle_number,
                "snap_status": status,
                "proposed_onset_ms": proposed,
                "actual_snap_ms": actual,
                "delta_ms": delta,
            })
        if angle_numbers != {1, 2}:
            raise ValueError(f"{item_id} must contain angles 1 and 2")
    return angles, fingerprints


def _metric_block(angle_rows: list[dict[str, Any]]) -> dict[str, Any]:
    marked = [row for row in angle_rows if row["snap_status"] == EXACT_SNAP_STATUS]
    errors = [int(row["delta_ms"]) for row in marked]
    absolute_errors = [abs(error) for error in errors]
    counts = Counter(row["snap_status"] for row in angle_rows)
    if not errors:
        return {
            "angle_judgments": len(angle_rows),
            "emitted_onsets": 0,
            "unavailable": len(angle_rows),
            "status_counts": dict(sorted(counts.items())),
            "median_absolute_error_ms": None,
            "within_500_ms": 0,
            "within_500_ms_share": 0.0,
            "p90_absolute_error_ms": None,
            "median_signed_bias_ms": None,
        }
    within_500 = sum(error <= 500 for error in absolute_errors)
    return {
        "angle_judgments": len(angle_rows),
        "emitted_onsets": len(marked),
        "unavailable": len(angle_rows) - len(marked),
        "status_counts": dict(sorted(counts.items())),
        "median_absolute_error_ms": _median(absolute_errors),
        "within_500_ms": within_500,
        "within_500_ms_share": _share(within_500, len(marked)),
        "p90_absolute_error_ms": _nearest_rank(absolute_errors, 0.90),
        "median_signed_bias_ms": _median(errors),
    }


def _pair_metrics(angle_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in angle_rows:
        by_item[row["item_id"]].append(row)
    eligible = [
        rows for rows in by_item.values()
        if all(row["snap_status"] == EXACT_SNAP_STATUS for row in rows)
    ]
    both_within = sum(
        all(abs(int(row["delta_ms"])) <= 500 for row in rows)
        for rows in eligible
    )
    return {
        "plays": len(by_item),
        "two_eligible_angle_plays": len(eligible),
        "two_eligible_angle_play_share": _share(len(eligible), len(by_item)),
        "both_angles_within_500_ms": both_within,
        "both_angles_within_500_ms_share": _share(both_within, len(eligible)),
    }


def _gate_result(metrics: Mapping[str, Any], pairs: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        "median_absolute_error": (
            metrics["median_absolute_error_ms"] is not None
            and metrics["median_absolute_error_ms"]
            <= LOCALIZATION_GATES["maximum_median_absolute_error_ms"]
        ),
        "within_500_ms_share": (
            metrics["within_500_ms_share"]
            >= LOCALIZATION_GATES["minimum_within_500_ms_share"]
        ),
        "p90_absolute_error": (
            metrics["p90_absolute_error_ms"] is not None
            and metrics["p90_absolute_error_ms"]
            <= LOCALIZATION_GATES["maximum_p90_absolute_error_ms"]
        ),
        "median_signed_bias": (
            metrics["median_signed_bias_ms"] is not None
            and abs(metrics["median_signed_bias_ms"])
            <= LOCALIZATION_GATES["maximum_median_signed_bias_abs_ms"]
        ),
        "two_eligible_angle_play_share": (
            pairs["two_eligible_angle_play_share"]
            >= LOCALIZATION_GATES["minimum_two_eligible_angle_play_share"]
        ),
        "both_angles_within_500_ms_share": (
            pairs["both_angles_within_500_ms_share"]
            >= LOCALIZATION_GATES[
                "minimum_both_angles_within_500_ms_share"]
        ),
    }
    return {**LOCALIZATION_GATES, "checks": checks, "passed": all(checks.values())}


def score_snap_calibration(
        records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Calculate a scorecard from label-blind calibration judgment records."""

    rows, fingerprints = _validate_and_collect(records)
    overall = _metric_block(rows)
    pairs = _pair_metrics(rows)
    cohorts: dict[str, dict[str, Any]] = {}
    for cohort in sorted({str(row["research_cohort_id"]) for row in rows}):
        cohort_rows = [
            row for row in rows if row["research_cohort_id"] == cohort]
        cohorts[cohort] = {
            "localization": _metric_block(cohort_rows),
            "pair_coverage": _pair_metrics(cohort_rows),
        }
    return {
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "measurement": {
            "dataset_kind": CALIBRATION_DATASET_KIND,
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "review_workflow": "snap_calibration",
            "error_definition": "actual_snap_ms minus proposed_onset_ms",
            "p90_definition": "nearest_rank",
            "source_manifest_fingerprints": fingerprints,
        },
        "localization": overall,
        "pair_coverage": pairs,
        "cohorts": cohorts,
        "localization_gate": _gate_result(overall, pairs),
    }


def render_markdown_scorecard(result: Mapping[str, Any]) -> str:
    """Render a concise, deterministic decision report."""

    localization = result["localization"]
    pairs = result["pair_coverage"]
    gate = result["localization_gate"]
    lines = [
        "# TapeSift Iteration 7A — Frozen Snap Calibration Scorecard",
        "",
        "This label-blind scorecard evaluates the frozen Temporal v2.1 "
        "proposed onsets against human-marked exact snap frames. It does "
        "not modify CSE Beta 4D, Temporal v2.1, or any classifier rule.",
        "",
        "## Frozen evidence",
        "",
        f"- Plays: **{pairs['plays']}**",
        f"- Angle judgments: **{localization['angle_judgments']}**",
        f"- Exact marked angles: **{localization['emitted_onsets']}**",
        "- Error: `actual_snap_ms - proposed_onset_ms`",
        "- P90: nearest-rank percentile of absolute signed errors",
        "",
        "## Localization results",
        "",
        "| Measure | Result | Gate |",
        "| --- | ---: | ---: |",
        f"| Median absolute error | {localization['median_absolute_error_ms']:.1f} ms | ≤ {gate['maximum_median_absolute_error_ms']} ms |",
        f"| Within 500 ms | {localization['within_500_ms']}/{localization['emitted_onsets']} ({localization['within_500_ms_share']:.1%}) | ≥ {gate['minimum_within_500_ms_share']:.0%} |",
        f"| P90 absolute error | {localization['p90_absolute_error_ms']} ms | ≤ {gate['maximum_p90_absolute_error_ms']} ms |",
        f"| Median signed bias | {localization['median_signed_bias_ms']:.1f} ms | ±{gate['maximum_median_signed_bias_abs_ms']} ms |",
        f"| Two eligible-angle plays | {pairs['two_eligible_angle_plays']}/{pairs['plays']} ({pairs['two_eligible_angle_play_share']:.1%}) | ≥ {gate['minimum_two_eligible_angle_play_share']:.0%} |",
        f"| Both angles within 500 ms | {pairs['both_angles_within_500_ms']}/{pairs['two_eligible_angle_plays']} ({pairs['both_angles_within_500_ms_share']:.1%}) | ≥ {gate['minimum_both_angles_within_500_ms_share']:.0%} |",
        "",
        "## Cohort detail",
        "",
        "| Cohort | Plays | Median absolute error | Within 500 ms | P90 absolute error | Median bias |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cohort, values in result["cohorts"].items():
        metrics = values["localization"]
        pairs_for_cohort = values["pair_coverage"]
        lines.append(
            f"| {cohort} | {pairs_for_cohort['plays']} | "
            f"{metrics['median_absolute_error_ms']:.1f} ms | "
            f"{metrics['within_500_ms_share']:.1%} | "
            f"{metrics['p90_absolute_error_ms']} ms | "
            f"{metrics['median_signed_bias_ms']:.1f} ms |"
        )
    lines.extend([
        "",
        "## Decision",
        "",
        (
            "**PASS:** the frozen localization evidence clears every "
            "predeclared gate."
            if gate["passed"] else
            "**HOLD:** the frozen localization evidence does not clear every "
            "predeclared gate. Do not tune Temporal v2.1 from this scorecard."
        ),
        "",
        "The next experiment, if authorized, must be a separately frozen "
        "candidate onset refiner evaluated on a third untouched game.",
        "",
    ])
    return "\n".join(lines)


def build_frozen_snap_calibration_scorecard(
        judgments_path: Path,
        output_json: Path,
        output_markdown: Path,
) -> dict[str, Any]:
    """Validate the completed 45-play calibration and persist frozen outputs.

    Existing reports are never overwritten, ensuring a changed input requires
    a new explicit output location instead of silently replacing evidence.
    """

    judgments_path = judgments_path.resolve()
    output_json = output_json.resolve()
    output_markdown = output_markdown.resolve()
    if not judgments_path.is_file():
        raise FileNotFoundError(judgments_path)
    for path in (output_json, output_markdown):
        if path.exists():
            raise FileExistsError(f"Refusing to replace frozen report: {path}")
    records = _read_jsonl(judgments_path)
    result = score_snap_calibration(records)
    if result["pair_coverage"]["plays"] != FROZEN_PLAY_COUNT:
        raise ValueError(
            f"Frozen 7A requires {FROZEN_PLAY_COUNT} plays; found "
            f"{result['pair_coverage']['plays']}")
    if result["localization"]["angle_judgments"] != FROZEN_ANGLE_COUNT:
        raise ValueError(
            f"Frozen 7A requires {FROZEN_ANGLE_COUNT} angle judgments; found "
            f"{result['localization']['angle_judgments']}")
    if result["localization"]["emitted_onsets"] != FROZEN_ANGLE_COUNT:
        raise ValueError("Frozen 7A requires exact snap marks for every angle")
    result["frozen_input"] = {
        "judgments_path": str(judgments_path),
        "judgments_sha256": hashlib.sha256(judgments_path.read_bytes()).hexdigest(),
        "expected_plays": FROZEN_PLAY_COUNT,
        "expected_angle_judgments": FROZEN_ANGLE_COUNT,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    json_temp = output_json.with_suffix(output_json.suffix + ".tmp")
    markdown_temp = output_markdown.with_suffix(
        output_markdown.suffix + ".tmp")
    json_temp.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_temp.write_text(
        render_markdown_scorecard(result), encoding="utf-8")
    json_temp.replace(output_json)
    markdown_temp.replace(output_markdown)
    return result


__all__ = [
    "FROZEN_ANGLE_COUNT",
    "FROZEN_PLAY_COUNT",
    "LOCALIZATION_GATES",
    "build_frozen_snap_calibration_scorecard",
    "render_markdown_scorecard",
    "score_snap_calibration",
]
