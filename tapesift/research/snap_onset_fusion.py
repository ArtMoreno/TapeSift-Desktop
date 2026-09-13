"""Iteration 7C.1 bounded fusion of global and player-motion onset.

The fusion uses the frozen 7B.1 and 7C development candidates. When their
predictions disagree beyond a fixed threshold, the camera-compensated
player-motion prediction replaces the global-motion prediction.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tapesift.research.snap_onset_refiner import (
    _gate,
    _metric_summary,
    sha256_file,
)


ITERATION_ID = "iteration-7c1-global-player-disagreement-fusion-v1"
SCHEMA_VERSION = "1.0"
DISAGREEMENT_THRESHOLDS_MS = (
    250,
    500,
    750,
    1000,
    1250,
    1500,
    2000,
    3000,
)


@dataclass(frozen=True, order=True)
class FusionPolicy:
    disagreement_threshold_ms: int

    @property
    def policy_id(self) -> str:
        return (
            "player-replaces-global-if-disagreement-exceeds-"
            f"{self.disagreement_threshold_ms:04d}ms"
        )


def policy_grid() -> tuple[FusionPolicy, ...]:
    return tuple(
        FusionPolicy(threshold)
        for threshold in DISAGREEMENT_THRESHOLDS_MS
    )


def _load_report(path: Path, iteration_id: str) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("iteration_id") != iteration_id:
        raise ValueError(f"Unexpected iteration report: {path}")
    return report


def _validate_inputs(
    global_report: dict[str, Any],
    player_report: dict[str, Any],
) -> None:
    global_frozen = global_report.get("frozen_input", {})
    player_frozen = player_report.get("frozen_input", {})
    if (
        global_frozen.get("judgments_sha256")
        != player_frozen.get("judgments_sha256")
    ):
        raise ValueError("Fusion judgment fingerprints do not match")
    if (
        global_frozen.get("source_manifest_fingerprints")
        != player_frozen.get("source_manifest_fingerprints")
    ):
        raise ValueError("Fusion manifest fingerprints do not match")


def _rows_by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, int], dict]:
    keyed = {
        (str(row["clip_id"]), int(row["angle"])): row
        for row in rows
    }
    if len(keyed) != len(rows):
        raise ValueError("Duplicate candidate rows in fusion input")
    return keyed


def score_fusion_policy(
    global_rows: list[dict[str, Any]],
    player_rows: list[dict[str, Any]],
    policy: FusionPolicy,
) -> dict[str, Any]:
    players = _rows_by_key(player_rows)
    rows = []
    for global_row in global_rows:
        key = (str(global_row["clip_id"]), int(global_row["angle"]))
        player_row = players.get(key)
        if player_row is None:
            raise ValueError(f"Missing player-motion row: {key}")
        global_onset = int(global_row["candidate_onset_ms"])
        player_onset = int(player_row["candidate_onset_ms"])
        disagreement = abs(global_onset - player_onset)
        use_player = disagreement > policy.disagreement_threshold_ms
        onset = player_onset if use_player else global_onset
        rows.append({
            "item_id": str(global_row["item_id"]),
            "cohort_id": str(global_row["cohort_id"]),
            "clip_id": key[0],
            "clip_number": int(global_row["clip_number"]),
            "angle": key[1],
            "actual_snap_ms": int(global_row["actual_snap_ms"]),
            "temporal_v21_onset_ms": int(
                global_row["temporal_v21_onset_ms"]
            ),
            "iteration_7b1_onset_ms": global_onset,
            "iteration_7c_onset_ms": player_onset,
            "candidate_onset_ms": onset,
            "error_ms": int(global_row["actual_snap_ms"]) - onset,
            "prediction_disagreement_ms": disagreement,
            "selection_source": (
                "iteration_7c_player_motion"
                if use_player
                else "iteration_7b1_global_motion"
            ),
        })
    if len(rows) != len(players):
        raise ValueError("Fusion candidate row sets do not match")
    cohorts = {
        cohort: _metric_summary([
            row for row in rows if row["cohort_id"] == cohort
        ])
        for cohort in sorted({row["cohort_id"] for row in rows})
    }
    overall = _metric_summary(rows)
    overall["player_replacement_count"] = sum(
        row["selection_source"] == "iteration_7c_player_motion"
        for row in rows
    )
    return {
        "policy": asdict(policy) | {"policy_id": policy.policy_id},
        "overall": overall,
        "by_cohort": cohorts,
        "rows": rows,
    }


def _cohort_rank(result: dict[str, Any], cohort: str) -> tuple[Any, ...]:
    metrics = result["by_cohort"][cohort]
    return (
        -metrics["both_angles_within_500_ms_share"],
        -metrics["within_500_ms_share"],
        metrics["median_absolute_error_ms"],
        metrics["p90_absolute_error_ms"],
        abs(metrics["median_signed_bias_ms"]),
        result["policy"]["policy_id"],
    )


def _robust_rank(result: dict[str, Any]) -> tuple[Any, ...]:
    cohorts = list(result["by_cohort"].values())
    overall = result["overall"]
    return (
        -min(row["both_angles_within_500_ms_share"] for row in cohorts),
        -min(row["within_500_ms_share"] for row in cohorts),
        max(row["median_absolute_error_ms"] for row in cohorts),
        overall["median_absolute_error_ms"],
        overall["p90_absolute_error_ms"],
        abs(overall["median_signed_bias_ms"]),
        result["policy"]["policy_id"],
    )


def select_fusion_policy(
    global_rows: list[dict[str, Any]],
    player_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    results = [
        score_fusion_policy(global_rows, player_rows, policy)
        for policy in policy_grid()
    ]
    selected = min(results, key=_robust_rank)
    cohorts = sorted(selected["by_cohort"])
    transfer = []
    for training in cohorts:
        winner = min(
            results,
            key=lambda result: _cohort_rank(result, training),
        )
        validation = next(cohort for cohort in cohorts if cohort != training)
        transfer.append({
            "training_cohort": training,
            "validation_cohort": validation,
            "selected_policy": winner["policy"],
            "training_metrics": winner["by_cohort"][training],
            "validation_metrics": winner["by_cohort"][validation],
        })
    selected["development_gate"] = _gate(selected["overall"])
    return {
        "selected": selected,
        "film_separated_transfer": transfer,
        "policy_count": len(results),
        "selection_rule": (
            "minimize worst-cohort paired and angle failures, then boundary "
            "error, overall tail error, bias, and stable policy id"
        ),
    }


def evaluate_iteration_7c1(
    iteration_7b1_report_path: Path,
    iteration_7c_report_path: Path,
) -> dict[str, Any]:
    iteration_7b1_report_path = iteration_7b1_report_path.resolve()
    iteration_7c_report_path = iteration_7c_report_path.resolve()
    global_report = _load_report(
        iteration_7b1_report_path,
        "iteration-7b-earliest-qualified-rise-v1",
    )
    player_report = _load_report(
        iteration_7c_report_path,
        "iteration-7c-camera-compensated-player-motion-v1",
    )
    _validate_inputs(global_report, player_report)
    global_selected = global_report["development"]["selected"]
    player_selected = player_report["development"]["selected"]
    development = select_fusion_policy(
        global_selected["rows"],
        player_selected["rows"],
    )
    development["temporal_v21_baseline"] = global_report[
        "development"
    ]["temporal_v21_baseline"]
    development["iteration_7b1_baseline"] = {
        "policy": global_selected["policy"],
        "overall": global_selected["overall"],
        "by_cohort": global_selected["by_cohort"],
    }
    development["iteration_7c_baseline"] = {
        "policy": player_selected["policy"],
        "overall": player_selected["overall"],
        "by_cohort": player_selected["by_cohort"],
    }
    selected = development["selected"]
    gate_passed = selected["development_gate"]["passed"]
    frozen = global_report["frozen_input"]
    return {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": ITERATION_ID,
        "status": (
            "development_candidate_ready_for_third_game"
            if gate_passed
            else "development_hold_fusion_not_ready"
        ),
        "frozen_input": {
            "judgments_path": frozen["judgments_path"],
            "judgments_sha256": frozen["judgments_sha256"],
            "source_manifest_fingerprints": frozen[
                "source_manifest_fingerprints"
            ],
            "iteration_7b1_report": {
                "path": str(iteration_7b1_report_path),
                "sha256": sha256_file(iteration_7b1_report_path),
            },
            "iteration_7c_report": {
                "path": str(iteration_7c_report_path),
                "sha256": sha256_file(iteration_7c_report_path),
            },
        },
        "methodology": {
            "fusion_rule": (
                "use 7C player-motion onset only when its disagreement with "
                "7B.1 exceeds the selected threshold"
            ),
            "policy_grid_is_predeclared": True,
            "run_pass_labels_used": False,
            "snap_labels_used_for_development_selection": True,
            "promotion_holdout": "third untouched game required",
        },
        "development": development,
    }


def render_markdown(report: dict[str, Any]) -> str:
    development = report["development"]
    selected = development["selected"]
    metrics = selected["overall"]
    global_metrics = development["iteration_7b1_baseline"]["overall"]
    player_metrics = development["iteration_7c_baseline"]["overall"]
    gate = selected["development_gate"]
    lines = [
        "# TapeSift Iteration 7C.1 - Global/Player Onset Fusion",
        "",
        (
            "This bounded development fusion uses player motion only when "
            "it materially disagrees with the 7B.1 global-motion candidate. "
            "It does not modify a production detector."
        ),
        "",
        "## Selected deterministic policy",
        "",
        f"- Policy: `{selected['policy']['policy_id']}`",
        (
            "- Player-motion replacements: "
            f"**{metrics['player_replacement_count']} / "
            f"{metrics['angle_judgments']}**"
        ),
        f"- Threshold policies evaluated: **{development['policy_count']}**",
        "",
        "## Development comparison",
        "",
        "| Measure | 7B.1 global | 7C player | 7C.1 fusion |",
        "| --- | ---: | ---: | ---: |",
        (
            f"| Median absolute error | "
            f"{global_metrics['median_absolute_error_ms']} ms | "
            f"{player_metrics['median_absolute_error_ms']} ms | "
            f"{metrics['median_absolute_error_ms']} ms |"
        ),
        (
            f"| Within 500 ms | "
            f"{global_metrics['within_500_ms_share']:.1%} | "
            f"{player_metrics['within_500_ms_share']:.1%} | "
            f"{metrics['within_500_ms_share']:.1%} |"
        ),
        (
            f"| P90 absolute error | "
            f"{global_metrics['p90_absolute_error_ms']} ms | "
            f"{player_metrics['p90_absolute_error_ms']} ms | "
            f"{metrics['p90_absolute_error_ms']} ms |"
        ),
        (
            f"| Median signed bias | "
            f"{global_metrics['median_signed_bias_ms']} ms | "
            f"{player_metrics['median_signed_bias_ms']} ms | "
            f"{metrics['median_signed_bias_ms']} ms |"
        ),
        (
            f"| Both angles within 500 ms | "
            f"{global_metrics['both_angles_within_500_ms_share']:.1%} | "
            f"{player_metrics['both_angles_within_500_ms_share']:.1%} | "
            f"{metrics['both_angles_within_500_ms_share']:.1%} |"
        ),
        "",
        f"- Development gate passed: **{gate['passed']}**",
        "",
        "## Decision",
        "",
        *(
            [
                (
                    "**THIRD GAME REQUIRED.** Freeze this fusion policy "
                    "before evaluating one untouched game."
                )
            ]
            if gate["passed"]
            else [
                (
                    "**HOLD.** Fusion improves the development scorecard but "
                    "still misses the required angle-coverage gates. Do not "
                    "consume a third game or promote it."
                )
            ]
        ),
        "",
    ]
    return "\n".join(lines)


def write_iteration_7c1_report(
    report: dict[str, Any],
    output_json: Path,
    output_markdown: Path,
) -> None:
    output_json = output_json.resolve()
    output_markdown = output_markdown.resolve()
    for path in (output_json, output_markdown):
        if path.exists():
            raise FileExistsError(f"Refusing to replace frozen output: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    output_markdown.write_text(
        render_markdown(report),
        encoding="utf-8",
        newline="\n",
    )
