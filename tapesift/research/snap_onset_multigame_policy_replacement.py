"""Train and evaluate a leave-one-game-out detected-player policy replacement."""

from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from typing import Any

from tapesift.research.snap_onset_detected_players import (
    detected_player_policy_grid,
)
from tapesift.research.snap_onset_multigame_detected_players import (
    MarkedAngleSpec,
    _fingerprint,
    _grouped_scores,
    _transfer_decision,
    build_or_load_traces,
    frozen_policy,
    load_marked_angle_specs,
)
from tapesift.research.snap_onset_spatial_refiner import (
    SpatialAngleTrace,
    SpatialPolicy,
)


def _resolve(value: str | Path, repository_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repository_root / path


def _policy_selection_key(
    scores: dict[str, Any],
    transfer_protocol: dict[str, Any],
) -> tuple[float | int, ...]:
    threshold = transfer_protocol["transfer_gate"]
    game_metrics = list(scores["by_game"].values())
    games_passing = sum(
        float(metrics["within_500_ms_share"])
        >= float(threshold["minimum_game_within_500_ms_share"])
        and float(metrics["median_absolute_error_ms"])
        <= float(threshold["maximum_game_median_absolute_error_ms"])
        for metrics in game_metrics
    )
    paired_shares = [
        float(metrics["both_angles_within_500_ms_share"])
        for metrics in game_metrics
        if metrics["both_angles_within_500_ms_share"] is not None
    ]
    overall = scores["overall"]
    return (
        games_passing,
        min(float(item["within_500_ms_share"]) for item in game_metrics),
        sum(float(item["within_500_ms_share"]) for item in game_metrics)
        / len(game_metrics),
        float(overall["within_500_ms_share"]),
        sum(paired_shares) / len(paired_shares) if paired_shares else 0.0,
        -float(overall["median_absolute_error_ms"]),
        -float(overall["p90_absolute_error_ms"]),
        -abs(float(overall["median_signed_bias_ms"])),
        -int(overall["fallback_count"]),
    )


def select_policy(
    traces: list[SpatialAngleTrace],
    specs_by_angle: dict[tuple[str, str, int], MarkedAngleSpec],
    transfer_protocol: dict[str, Any],
) -> tuple[SpatialPolicy, dict[str, Any]]:
    best_policy: SpatialPolicy | None = None
    best_scores: dict[str, Any] | None = None
    best_key: tuple[float | int, ...] | None = None
    policies = sorted(
        detected_player_policy_grid(), key=lambda policy: policy.policy_id
    )
    for policy in policies:
        scores = _grouped_scores(
            traces, specs_by_angle, policy, baseline=False
        )
        selection_key = _policy_selection_key(scores, transfer_protocol)
        if best_key is None or selection_key > best_key:
            best_policy = policy
            best_scores = scores
            best_key = selection_key
    assert best_policy is not None
    assert best_scores is not None
    return best_policy, {
        "selection_key": list(best_key or ()),
        "training_overall": best_scores["overall"],
        "training_by_game": best_scores["by_game"],
    }


def _prediction_scores(
    traces: list[SpatialAngleTrace],
    predictions: dict[tuple[str, str, int], int],
    specs_by_angle: dict[tuple[str, str, int], MarkedAngleSpec],
    reference_policy: SpatialPolicy,
) -> dict[str, Any]:
    predicted_traces = []
    for trace in traces:
        key = (trace.cohort_id, trace.clip_id, trace.angle)
        predicted_traces.append(replace(
            trace,
            temporal_v21_onset_ms=int(predictions[key]),
            candidates_by_channel={reference_policy.channel: ()},
        ))
    return _grouped_scores(
        predicted_traces,
        specs_by_angle,
        reference_policy,
        baseline=False,
    )


def leave_one_game_out(
    traces: list[SpatialAngleTrace],
    specs_by_angle: dict[tuple[str, str, int], MarkedAngleSpec],
    transfer_protocol: dict[str, Any],
    reference_policy: SpatialPolicy,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    game_for_trace = {
        (trace.cohort_id, trace.clip_id, trace.angle): specs_by_angle[
            (trace.cohort_id, trace.clip_id, trace.angle)
        ].game_id
        for trace in traces
    }
    games = sorted(set(game_for_trace.values()))
    predictions: dict[tuple[str, str, int], int] = {}
    fold_reports = []
    for fold_index, holdout_game in enumerate(games, start=1):
        training = [
            trace
            for trace in traces
            if game_for_trace[
                (trace.cohort_id, trace.clip_id, trace.angle)
            ] != holdout_game
        ]
        holdout = [
            trace
            for trace in traces
            if game_for_trace[
                (trace.cohort_id, trace.clip_id, trace.angle)
            ] == holdout_game
        ]
        print(
            f"[{fold_index}/{len(games)}] selecting on {len(games) - 1} "
            f"games; holding out {holdout_game}",
            flush=True,
        )
        selected_policy, training_report = select_policy(
            training, specs_by_angle, transfer_protocol
        )
        holdout_scores = _grouped_scores(
            holdout, specs_by_angle, selected_policy, baseline=False
        )
        for row in holdout_scores["rows"]:
            key = (
                str(row["cohort_id"]),
                str(row["clip_id"]),
                int(row["angle"]),
            )
            if key in predictions:
                raise ValueError(f"Duplicate LOFO prediction: {key}")
            predictions[key] = int(row["candidate_onset_ms"])
        fold_reports.append({
            "holdout_game": holdout_game,
            "selected_policy": asdict(selected_policy) | {
                "policy_id": selected_policy.policy_id
            },
            "training": training_report,
            "holdout_overall": holdout_scores["overall"],
        })
        print(
            f"    {selected_policy.policy_id}: "
            f"{holdout_scores['overall']['within_500_ms']}/"
            f"{holdout_scores['overall']['angle_judgments']} within 500 ms",
            flush=True,
        )
    if len(predictions) != len(traces):
        raise ValueError(
            f"Expected {len(traces)} LOFO predictions, found {len(predictions)}"
        )
    return _prediction_scores(
        traces,
        predictions,
        specs_by_angle,
        reference_policy,
    ), fold_reports


def _acceptance_decision(
    replacement_protocol: dict[str, Any],
    transfer_decision: dict[str, Any],
    lofo: dict[str, Any],
    frozen_7e: dict[str, Any],
) -> dict[str, Any]:
    criteria = replacement_protocol["acceptance"]
    overall_delta = (
        float(lofo["overall"]["within_500_ms_share"])
        - float(frozen_7e["overall"]["within_500_ms_share"])
    )
    median_not_worse = (
        float(lofo["overall"]["median_absolute_error_ms"])
        <= float(frozen_7e["overall"]["median_absolute_error_ms"])
    )
    lofo_paired = lofo["overall"]["both_angles_within_500_ms_share"]
    frozen_paired = frozen_7e["overall"][
        "both_angles_within_500_ms_share"
    ]
    paired_delta = float(lofo_paired or 0.0) - float(frozen_paired or 0.0)
    tolerance = float(criteria["game_regression_tolerance_share"])
    game_deltas = {
        game_id: round(
            float(lofo["by_game"][game_id]["within_500_ms_share"])
            - float(frozen_7e["by_game"][game_id]["within_500_ms_share"]),
            6,
        )
        for game_id in lofo["by_game"]
    }
    regressed_games = sorted(
        game_id for game_id, delta in game_deltas.items() if delta < -tolerance
    )
    checks = {
        "transfer_gate": (
            bool(transfer_decision["passed"])
            if criteria["require_transfer_gate"]
            else True
        ),
        "within_500_ms_improvement": (
            overall_delta
            >= float(criteria[
                "minimum_within_500_ms_share_improvement_vs_7e"
            ])
        ),
        "median_absolute_error_not_worse": (
            median_not_worse
            if criteria["require_median_absolute_error_not_worse"]
            else True
        ),
        "paired_success_not_materially_worse": (
            paired_delta
            >= float(criteria[
                "minimum_paired_success_share_change_vs_7e"
            ])
        ),
        "game_regressions_bounded": (
            len(regressed_games)
            <= int(criteria["maximum_games_regressing_beyond_tolerance"])
        ),
    }
    passed = all(checks.values())
    return {
        "decision": (
            "multigame_policy_replacement_supported"
            if passed
            else "learned_candidate_ranker_required"
        ),
        "passed": passed,
        "checks": checks,
        "within_500_ms_share_change_vs_7e": round(overall_delta, 6),
        "paired_success_share_change_vs_7e": round(paired_delta, 6),
        "game_within_500_ms_share_changes_vs_7e": game_deltas,
        "games_regressing_beyond_tolerance": regressed_games,
    }


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    old = report["frozen_7e"]["overall"]
    new = report["lofo_replacement"]["overall"]
    decision = report["acceptance_decision"]
    lines = [
        "# Multi-game Snap Policy Replacement",
        "",
        f"**Decision:** `{decision['decision']}`",
        "",
        "Six leave-one-game-out folds selected policies using only the other "
        "five development games. Validation and final holdout data were not "
        "loaded.",
        "",
        "## Unseen-game comparison",
        "",
        "| Method | Within 500 ms | Median abs error | P90 abs error | "
        "Both paired angles | Fallbacks |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| Frozen 7E | {old['within_500_ms']}/{old['angle_judgments']} "
        f"({_percent(old['within_500_ms_share'])}) | "
        f"{old['median_absolute_error_ms']:.1f} ms | "
        f"{old['p90_absolute_error_ms']:.1f} ms | "
        f"{_percent(old['both_angles_within_500_ms_share'])} | "
        f"{old['fallback_count']} |",
        f"| LOFO replacement | {new['within_500_ms']}/"
        f"{new['angle_judgments']} ({_percent(new['within_500_ms_share'])}) | "
        f"{new['median_absolute_error_ms']:.1f} ms | "
        f"{new['p90_absolute_error_ms']:.1f} ms | "
        f"{_percent(new['both_angles_within_500_ms_share'])} | "
        f"{new['fallback_count']} |",
        "",
        "## Unseen game folds",
        "",
        "| Held-out game | Selected policy | Within 500 ms | Median abs error |",
        "| --- | --- | ---: | ---: |",
    ]
    for fold in report["folds"]:
        metrics = fold["holdout_overall"]
        lines.append(
            f"| {fold['holdout_game']} | "
            f"`{fold['selected_policy']['policy_id']}` | "
            f"{metrics['within_500_ms']}/{metrics['angle_judgments']} "
            f"({_percent(metrics['within_500_ms_share'])}) | "
            f"{metrics['median_absolute_error_ms']:.1f} ms |"
        )
    lines.extend([
        "",
        "## Acceptance checks",
        "",
    ])
    for name, passed in decision["checks"].items():
        lines.append(f"- `{name}`: {passed}")
    lines.extend([
        "",
        "## All-development fit",
        "",
        f"Selected policy: "
        f"`{report['all_development_fit']['policy']['policy_id']}`",
        "",
        "This all-development result is descriptive only. The acceptance "
        "decision uses the six unseen-game folds above.",
        "",
        "## Next iteration",
        "",
    ])
    if decision["passed"]:
        lines.append(
            "Freeze the all-development policy, then run it once against the "
            "sealed Miami-Ohio State validation set."
        )
    else:
        lines.append(
            "The threshold-policy family is exhausted. Train a candidate-level "
            "ranker that can choose among temporal v2.1, 7E, and multi-channel "
            "motion candidates, using the same leave-one-game-out protocol."
        )
    return "\n".join(lines) + "\n"


def run_policy_replacement(
    protocol_path: Path,
    report_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    protocol_path = protocol_path.resolve()
    repository_root = protocol_path.parents[1]
    replacement_protocol = json.loads(
        protocol_path.read_text(encoding="utf-8")
    )
    transfer_protocol_path = _resolve(
        replacement_protocol["base_transfer_protocol"], repository_root
    )
    transfer_protocol = json.loads(
        transfer_protocol_path.read_text(encoding="utf-8")
    )
    specs, counts = load_marked_angle_specs(
        transfer_protocol, repository_root
    )
    specs_by_angle = {
        (spec.cohort_id, spec.clip_id, spec.angle): spec for spec in specs
    }
    model_path = _resolve(replacement_protocol["model_path"], repository_root)
    cache_path = _resolve(
        replacement_protocol["measurement_cache_path"], repository_root
    )
    traces, measurement = build_or_load_traces(
        specs, model_path, cache_path
    )
    old_policy = frozen_policy(transfer_protocol)
    frozen_scores = _grouped_scores(
        traces, specs_by_angle, old_policy, baseline=False
    )
    baseline_scores = _grouped_scores(
        traces, specs_by_angle, old_policy, baseline=True
    )
    lofo_scores, folds = leave_one_game_out(
        traces,
        specs_by_angle,
        transfer_protocol,
        old_policy,
    )
    lofo_transfer_decision = _transfer_decision(
        transfer_protocol, lofo_scores
    )
    acceptance = _acceptance_decision(
        replacement_protocol,
        lofo_transfer_decision,
        lofo_scores,
        frozen_scores,
    )

    print("[global] selecting replacement on all development games", flush=True)
    global_policy, global_training = select_policy(
        traces, specs_by_angle, transfer_protocol
    )
    policy_ids = [
        policy.policy_id
        for policy in sorted(
            detected_player_policy_grid(), key=lambda item: item.policy_id
        )
    ]
    grid_sha256 = hashlib.sha256(
        "\n".join(policy_ids).encode("utf-8")
    ).hexdigest()
    report = {
        "schema_version": "1.0",
        "iteration_id": replacement_protocol["iteration_id"],
        "dataset": counts,
        "inputs": {
            "replacement_protocol": _fingerprint(protocol_path),
            "transfer_protocol": _fingerprint(transfer_protocol_path),
            "model": _fingerprint(model_path),
        },
        "measurement": measurement,
        "candidate_grid": {
            "policies": len(policy_ids),
            "policy_ids_sha256": grid_sha256,
        },
        "baseline_temporal_v21": {
            key: value for key, value in baseline_scores.items() if key != "rows"
        },
        "frozen_7e": frozen_scores,
        "lofo_replacement": lofo_scores,
        "folds": folds,
        "lofo_transfer_decision": lofo_transfer_decision,
        "acceptance_decision": acceptance,
        "all_development_fit": {
            "policy": asdict(global_policy) | {
                "policy_id": global_policy.policy_id
            },
            "training": global_training,
            "freeze_status": (
                "approved_for_sealed_validation"
                if acceptance["passed"]
                else "not_approved"
            ),
        },
        "sealed_data": {
            "validation": transfer_protocol["sealed_validation"],
            "final_holdout": transfer_protocol["sealed_final_holdout"],
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    old = frozen_scores["overall"]
    new = lofo_scores["overall"]
    print(
        f"LOFO replacement: {new['within_500_ms']}/"
        f"{new['angle_judgments']} within 500 ms "
        f"({float(new['within_500_ms_share']) * 100:.1f}%), versus "
        f"{old['within_500_ms']}/{old['angle_judgments']} "
        f"({float(old['within_500_ms_share']) * 100:.1f}%) for frozen 7E.",
        flush=True,
    )
    print(
        f"Release gate: {lofo_transfer_decision['passed']}; "
        f"games passing: "
        f"{lofo_transfer_decision['game_consistency']['games_passing']}/"
        f"{lofo_transfer_decision['game_consistency']['games_total']}",
        flush=True,
    )
    print(
        f"Decision: {acceptance['decision']}; global policy: "
        f"{global_policy.policy_id}",
        flush=True,
    )
    return report
