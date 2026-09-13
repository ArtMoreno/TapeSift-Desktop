"""Evaluate the frozen Iteration 7E snap policy across development games."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from tapesift.research.snap_onset_detected_players import (
    CHANNELS,
    MAX_MATCH_DISTANCE,
    MODEL_CONFIDENCE,
    MODEL_IMAGE_SIZE,
    MOVING_DISTANCE,
    PERSON_CLASS_ID,
    SAMPLE_FPS,
    configure_inference,
    measure_detected_player_angle,
)
from tapesift.research.snap_onset_refiner import (
    RiseCandidate,
    _gate,
    build_rise_candidates,
    sha256_file,
)
from tapesift.research.snap_onset_spatial_refiner import (
    SpatialAngleTrace,
    SpatialPolicy,
    score_spatial_policy,
)


MEASUREMENT_VERSION = "multigame-detected-player-v1"


@dataclass(frozen=True)
class MarkedAngleSpec:
    item_id: str
    cohort_id: str
    game_id: str
    game_name: str
    clip_id: str
    clip_number: int
    angle: int
    source_video_path: str
    range_start_ms: int
    range_end_ms: int
    actual_snap_ms: int
    temporal_v21_onset_ms: int


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_number}") from exc
    return records


def _resolve_repo_path(value: str, repository_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repository_root / path


def _cohort_configuration(
    protocol: dict[str, Any],
    repository_root: Path,
) -> dict[str, dict[str, Any]]:
    cohorts: dict[str, dict[str, Any]] = {}
    for entry in protocol["development_cohorts"]:
        cohort_id = str(entry["cohort_id"])
        if cohort_id in cohorts:
            raise ValueError(f"Duplicate development cohort: {cohort_id}")
        cohorts[cohort_id] = {
            **entry,
            "feature_path": _resolve_repo_path(
                str(entry["feature_path"]), repository_root
            ),
        }
    return cohorts


def load_marked_angle_specs(
    protocol: dict[str, Any],
    repository_root: Path,
) -> tuple[list[MarkedAngleSpec], dict[str, Any]]:
    """Join marked judgments to temporal angle diagnostics without holdout access."""

    cohorts = _cohort_configuration(protocol, repository_root)
    features: dict[tuple[str, str], dict[str, Any]] = {}
    for cohort_id, entry in cohorts.items():
        feature_path = Path(entry["feature_path"])
        for record in _read_jsonl(feature_path):
            record_cohort = str(record["research_cohort_id"])
            if record_cohort != cohort_id:
                raise ValueError(
                    f"Unexpected cohort {record_cohort} in {feature_path}"
                )
            key = (cohort_id, str(record["clip_id"]))
            if key in features:
                raise ValueError(f"Duplicate temporal feature record: {key}")
            features[key] = record

    judgments: list[dict[str, Any]] = []
    judgment_paths = [
        _resolve_repo_path(str(value), repository_root)
        for value in protocol["judgment_paths"]
    ]
    for path in judgment_paths:
        judgments.extend(_read_jsonl(path))

    specs: list[MarkedAngleSpec] = []
    unsure_angles = 0
    play_keys: set[tuple[str, str]] = set()
    seen_angles: set[tuple[str, str, int]] = set()
    for judgment in judgments:
        cohort_id = str(judgment["research_cohort_id"])
        if cohort_id not in cohorts:
            raise ValueError(f"Judgment references undeclared cohort: {cohort_id}")
        clip_id = str(judgment["clip_id"])
        key = (cohort_id, clip_id)
        feature = features.get(key)
        if feature is None:
            raise ValueError(f"No temporal feature record for judgment: {key}")
        diagnostics_by_angle = {
            int(angle["angle"]): angle
            for angle in feature["temporal_diagnostics"]["angles"]
        }
        play_keys.add(key)
        cohort = cohorts[cohort_id]
        for judgment_angle in judgment["angles"]:
            status = str(judgment_angle["snap_status"])
            if status == "unsure":
                unsure_angles += 1
                continue
            if status != "marked":
                raise ValueError(
                    f"Incomplete snap status for {key}: {status}"
                )
            angle_number = int(judgment_angle["angle"])
            angle_key = (cohort_id, clip_id, angle_number)
            if angle_key in seen_angles:
                raise ValueError(f"Duplicate marked angle: {angle_key}")
            seen_angles.add(angle_key)
            diagnostic = diagnostics_by_angle.get(angle_number)
            if diagnostic is None:
                raise ValueError(f"No temporal diagnostic for marked angle: {angle_key}")
            clip_start_ms = int(feature["start_ms"])
            calculated_onset_ms = clip_start_ms + round(
                (
                    float(diagnostic["start_seconds"])
                    + float(diagnostic["onset_seconds"])
                ) * 1000
            )
            proposed_onset_ms = int(
                judgment_angle.get("proposed_onset_ms", calculated_onset_ms)
            )
            if abs(calculated_onset_ms - proposed_onset_ms) > 2:
                raise ValueError(
                    f"Temporal onset changed after calibration for {angle_key}"
                )
            range_start_ms = int(judgment_angle["range_start_ms"])
            range_end_ms = int(judgment_angle["range_end_ms"])
            actual_snap_ms = int(judgment_angle["actual_snap_ms"])
            if not range_start_ms <= actual_snap_ms <= range_end_ms:
                raise ValueError(f"Snap label is outside its angle range: {angle_key}")
            specs.append(MarkedAngleSpec(
                item_id=str(judgment["item_id"]),
                cohort_id=cohort_id,
                game_id=str(cohort["game_id"]),
                game_name=str(cohort["game_name"]),
                clip_id=clip_id,
                clip_number=int(judgment["clip_number"]),
                angle=angle_number,
                source_video_path=str(judgment["source_video_path"]),
                range_start_ms=range_start_ms,
                range_end_ms=range_end_ms,
                actual_snap_ms=actual_snap_ms,
                temporal_v21_onset_ms=proposed_onset_ms,
            ))

    specs.sort(key=lambda item: (
        item.game_id,
        item.cohort_id,
        item.clip_number,
        item.angle,
    ))
    counts = {
        "development_plays": len(play_keys),
        "marked_angles": len(specs),
        "unsure_angles": unsure_angles,
        "game_groups": len({spec.game_id for spec in specs}),
        "cohorts": len({spec.cohort_id for spec in specs}),
    }
    for name, expected in protocol["expected_counts"].items():
        actual = counts[name]
        if actual != int(expected):
            raise ValueError(
                f"Expected {expected} {name}, found {actual}; refusing audit"
            )
    return specs, counts


def frozen_policy(protocol: dict[str, Any]) -> SpatialPolicy:
    values = protocol["frozen_policy"]
    return SpatialPolicy(
        channel=str(values["channel"]),
        relative_score_floor=float(values["relative_score_floor"]),
        minimum_score=float(values["minimum_score"]),
        minimum_post_pre_ratio=float(values["minimum_post_pre_ratio"]),
        output_lag_seconds=float(values["output_lag_seconds"]),
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    return value


def _measurement_signature(
    spec: MarkedAngleSpec,
    model_sha256: str,
    inference_configuration: dict[str, str],
) -> str:
    source = Path(spec.source_video_path)
    stat = source.stat()
    payload = {
        "measurement_version": MEASUREMENT_VERSION,
        "source_video_path": str(source.resolve()),
        "source_video_size": stat.st_size,
        "source_video_mtime_ns": stat.st_mtime_ns,
        "range_start_ms": spec.range_start_ms,
        "range_end_ms": spec.range_end_ms,
        "model_sha256": model_sha256,
        "sample_fps": SAMPLE_FPS,
        "model_image_size": MODEL_IMAGE_SIZE,
        "model_confidence": MODEL_CONFIDENCE,
        "person_class_id": PERSON_CLASS_ID,
        "max_match_distance": MAX_MATCH_DISTANCE,
        "moving_distance": MOVING_DISTANCE,
        "inference_configuration": inference_configuration,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for record in _read_jsonl(path):
        records[str(record["measurement_signature"])] = record
    return records


def _cached_candidates(
    record: dict[str, Any],
) -> dict[str, tuple[RiseCandidate, ...]]:
    return {
        channel: tuple(RiseCandidate(**candidate) for candidate in candidates)
        for channel, candidates in record["candidates_by_channel"].items()
    }


def _trace_from_measurement(
    spec: MarkedAngleSpec,
    candidates_by_channel: dict[str, tuple[RiseCandidate, ...]],
) -> SpatialAngleTrace:
    return SpatialAngleTrace(
        item_id=spec.item_id,
        cohort_id=spec.cohort_id,
        clip_id=spec.clip_id,
        clip_number=spec.clip_number,
        angle=spec.angle,
        source_video_path=spec.source_video_path,
        range_start_ms=spec.range_start_ms,
        range_end_ms=spec.range_end_ms,
        actual_snap_ms=spec.actual_snap_ms,
        temporal_v21_onset_ms=spec.temporal_v21_onset_ms,
        candidates_by_channel=candidates_by_channel,
    )


def build_or_load_traces(
    specs: list[MarkedAngleSpec],
    model_path: Path,
    cache_path: Path,
) -> tuple[list[SpatialAngleTrace], dict[str, Any]]:
    """Measure each angle once and append a reusable record immediately."""

    model_sha256 = sha256_file(model_path)
    inference_configuration = configure_inference()
    cache = _load_cache(cache_path)
    signatures = [
        _measurement_signature(spec, model_sha256, inference_configuration)
        for spec in specs
    ]
    cache_hits = sum(signature in cache for signature in signatures)
    cache_misses = len(specs) - cache_hits
    print(
        f"Prepared {len(specs)} marked angles: "
        f"{cache_hits} cached, {cache_misses} to measure.",
        flush=True,
    )

    model: Any | None = None
    append_handle = None
    if cache_misses:
        from ultralytics import YOLO

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        append_handle = cache_path.open("a", encoding="utf-8")
        model = YOLO(str(model_path))

    traces: list[SpatialAngleTrace] = []
    try:
        for index, (spec, signature) in enumerate(
            zip(specs, signatures, strict=True), start=1
        ):
            record = cache.get(signature)
            if record is None:
                print(
                    f"[{index}/{len(specs)}] measuring {spec.cohort_id} "
                    f"clip {spec.clip_number} angle {spec.angle}",
                    flush=True,
                )
                frames_by_channel, diagnostics = measure_detected_player_angle(
                    model,
                    Path(spec.source_video_path),
                    spec.range_start_ms,
                    spec.range_end_ms,
                )
                candidates_by_channel = {
                    channel: build_rise_candidates(frames_by_channel[channel])
                    for channel in CHANNELS
                }
                record = {
                    "schema_version": "1.0",
                    "measurement_signature": signature,
                    "measurement_version": MEASUREMENT_VERSION,
                    "source_video_path": spec.source_video_path,
                    "range_start_ms": spec.range_start_ms,
                    "range_end_ms": spec.range_end_ms,
                    "candidates_by_channel": {
                        channel: [asdict(candidate) for candidate in candidates]
                        for channel, candidates in candidates_by_channel.items()
                    },
                    "diagnostics": _jsonable(diagnostics),
                }
                assert append_handle is not None
                append_handle.write(json.dumps(
                    record, sort_keys=True, separators=(",", ":")
                ) + "\n")
                append_handle.flush()
                cache[signature] = record
            elif index == 1 or index == len(specs) or index % 25 == 0:
                print(
                    f"[{index}/{len(specs)}] restored cached measurements",
                    flush=True,
                )
            candidates = _cached_candidates(record)
            missing_channels = set(CHANNELS) - set(candidates)
            if missing_channels:
                raise ValueError(
                    f"Cache record missing channels: {sorted(missing_channels)}"
                )
            traces.append(_trace_from_measurement(spec, candidates))
    finally:
        if append_handle is not None:
            append_handle.close()

    measurement = {
        "version": MEASUREMENT_VERSION,
        "model_sha256": model_sha256,
        "sample_fps": SAMPLE_FPS,
        "model_image_size": MODEL_IMAGE_SIZE,
        "model_confidence": MODEL_CONFIDENCE,
        "person_class_id": PERSON_CLASS_ID,
        "max_match_distance": MAX_MATCH_DISTANCE,
        "moving_distance": MOVING_DISTANCE,
        "inference_configuration": inference_configuration,
        "completed_angles": len(traces),
    }
    return traces, measurement


def _pair_aware_metrics(
    metrics: dict[str, Any],
    rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(
            (str(row["cohort_id"]), str(row["clip_id"])), []
        ).append(row)
    paired = [
        group
        for group in grouped.values()
        if len({int(row["angle"]) for row in group}) >= 2
    ]
    both_within = sum(
        all(abs(int(row["error_ms"])) <= 500 for row in group)
        for group in paired
    )
    result = dict(metrics)
    result["paired_plays"] = len(paired)
    result["single_view_plays"] = len(grouped) - len(paired)
    result["both_angles_within_500_ms"] = both_within
    result["both_angles_within_500_ms_share"] = (
        round(both_within / len(paired), 6) if paired else None
    )
    result["paired_play_share"] = (
        round(len(paired) / len(grouped), 6) if grouped else 0.0
    )
    return result


def _score_subset(
    traces: list[SpatialAngleTrace],
    policy: SpatialPolicy,
    baseline: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    selected_traces = traces
    if baseline:
        selected_traces = [
            replace(
                trace,
                candidates_by_channel={policy.channel: ()},
            )
            for trace in traces
        ]
    result = score_spatial_policy(selected_traces, policy)
    rows = list(result["rows"])
    return _pair_aware_metrics(result["overall"], rows), rows


def _grouped_scores(
    traces: list[SpatialAngleTrace],
    specs_by_angle: dict[tuple[str, str, int], MarkedAngleSpec],
    policy: SpatialPolicy,
    baseline: bool = False,
) -> dict[str, Any]:
    overall, rows = _score_subset(traces, policy, baseline=baseline)
    by_cohort = {}
    for cohort_id in sorted({trace.cohort_id for trace in traces}):
        subset = [trace for trace in traces if trace.cohort_id == cohort_id]
        by_cohort[cohort_id], _ = _score_subset(
            subset, policy, baseline=baseline
        )

    game_ids = {
        specs_by_angle[(trace.cohort_id, trace.clip_id, trace.angle)].game_id
        for trace in traces
    }
    by_game = {}
    for game_id in sorted(game_ids):
        subset = [
            trace
            for trace in traces
            if specs_by_angle[
                (trace.cohort_id, trace.clip_id, trace.angle)
            ].game_id == game_id
        ]
        game_metrics, _ = _score_subset(subset, policy, baseline=baseline)
        sample_spec = specs_by_angle[
            (subset[0].cohort_id, subset[0].clip_id, subset[0].angle)
        ]
        by_game[game_id] = {
            "game_name": sample_spec.game_name,
            **game_metrics,
        }
    return {
        "overall": overall,
        "by_cohort": by_cohort,
        "by_game": by_game,
        "rows": rows,
    }


def _comparison(
    baseline: dict[str, Any],
    detector: dict[str, Any],
) -> dict[str, float | int]:
    return {
        "additional_angles_within_500_ms": (
            int(detector["within_500_ms"]) - int(baseline["within_500_ms"])
        ),
        "within_500_ms_share_change": round(
            float(detector["within_500_ms_share"])
            - float(baseline["within_500_ms_share"]),
            6,
        ),
        "median_absolute_error_reduction_ms": round(
            float(baseline["median_absolute_error_ms"])
            - float(detector["median_absolute_error_ms"]),
            3,
        ),
        "p90_absolute_error_reduction_ms": round(
            float(baseline["p90_absolute_error_ms"])
            - float(detector["p90_absolute_error_ms"]),
            3,
        ),
    }


def _transfer_decision(
    protocol: dict[str, Any],
    detector_scores: dict[str, Any],
) -> dict[str, Any]:
    overall = detector_scores["overall"]
    release_metrics = dict(overall)
    if release_metrics["both_angles_within_500_ms_share"] is None:
        release_metrics["both_angles_within_500_ms_share"] = 0.0
    release_gate = _gate(release_metrics)

    thresholds = protocol["transfer_gate"]
    game_checks = {}
    for game_id, metrics in detector_scores["by_game"].items():
        checks = {
            "within_500_ms_share": (
                float(metrics["within_500_ms_share"])
                >= float(thresholds["minimum_game_within_500_ms_share"])
            ),
            "median_absolute_error": (
                float(metrics["median_absolute_error_ms"])
                <= float(thresholds["maximum_game_median_absolute_error_ms"])
            ),
        }
        game_checks[game_id] = {
            "passed": all(checks.values()),
            "checks": checks,
        }
    games_passing = sum(item["passed"] for item in game_checks.values())
    game_consistency_passed = (
        games_passing >= int(thresholds["minimum_games_passing"])
    )
    passed = bool(release_gate["passed"] and game_consistency_passed)
    return {
        "decision": (
            "frozen_7e_transfer_supported"
            if passed
            else "multigame_replacement_candidate_required"
        ),
        "passed": passed,
        "release_gate": release_gate,
        "game_consistency": {
            "passed": game_consistency_passed,
            "games_passing": games_passing,
            "games_total": len(game_checks),
            "minimum_games_passing": int(
                thresholds["minimum_games_passing"]
            ),
            "checks": game_checks,
        },
    }


def _fingerprint(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _percent(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    detector = report["frozen_7e"]["overall"]
    baseline = report["baseline_temporal_v21"]["overall"]
    decision = report["transfer_decision"]
    lines = [
        "# Multi-game Snap 7E Transfer Audit",
        "",
        f"**Decision:** `{decision['decision']}`",
        "",
        "The frozen Iteration 7E policy was applied without retuning. "
        "Validation and final holdout data were not loaded by this audit.",
        "",
        "## Dataset",
        "",
        f"- Plays: {report['dataset']['development_plays']}",
        f"- Marked camera angles: {report['dataset']['marked_angles']}",
        f"- True paired-view plays: {detector['paired_plays']}",
        f"- Single-view plays: {detector['single_view_plays']}",
        f"- Game groups: {report['dataset']['game_groups']}",
        "",
        "## Overall comparison",
        "",
        "| Method | Within 500 ms | Median abs error | P90 abs error | "
        "Signed bias | Both paired angles | Fallbacks |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| Temporal v2.1 | "
        f"{baseline['within_500_ms']}/{baseline['angle_judgments']} "
        f"({_percent(baseline['within_500_ms_share'])}) | "
        f"{baseline['median_absolute_error_ms']:.1f} ms | "
        f"{baseline['p90_absolute_error_ms']:.1f} ms | "
        f"{baseline['median_signed_bias_ms']:.1f} ms | "
        f"{_percent(baseline['both_angles_within_500_ms_share'])} | "
        f"{baseline['fallback_count']} |",
        "| Frozen 7E | "
        f"{detector['within_500_ms']}/{detector['angle_judgments']} "
        f"({_percent(detector['within_500_ms_share'])}) | "
        f"{detector['median_absolute_error_ms']:.1f} ms | "
        f"{detector['p90_absolute_error_ms']:.1f} ms | "
        f"{detector['median_signed_bias_ms']:.1f} ms | "
        f"{_percent(detector['both_angles_within_500_ms_share'])} | "
        f"{detector['fallback_count']} |",
        "",
        "## Frozen 7E by game",
        "",
        "| Game | Plays | Angles | Within 500 ms | Median abs error | "
        "P90 abs error | Paired plays | Both paired angles |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for game_id, metrics in report["frozen_7e"]["by_game"].items():
        lines.append(
            f"| {metrics['game_name']} (`{game_id}`) | {metrics['plays']} | "
            f"{metrics['angle_judgments']} | "
            f"{metrics['within_500_ms']}/{metrics['angle_judgments']} "
            f"({_percent(metrics['within_500_ms_share'])}) | "
            f"{metrics['median_absolute_error_ms']:.1f} ms | "
            f"{metrics['p90_absolute_error_ms']:.1f} ms | "
            f"{metrics['paired_plays']} | "
            f"{_percent(metrics['both_angles_within_500_ms_share'])} |"
        )
    lines.extend([
        "",
        "## Predeclared transfer gate",
        "",
        f"- Existing release gate passed: "
        f"{decision['release_gate']['passed']}",
        f"- Games passing consistency checks: "
        f"{decision['game_consistency']['games_passing']}/"
        f"{decision['game_consistency']['games_total']}",
        f"- Required games passing: "
        f"{decision['game_consistency']['minimum_games_passing']}",
        "",
        "## Next iteration",
        "",
    ])
    if decision["passed"]:
        lines.append(
            "Keep 7E as the coarse detector, train a leave-one-game-out "
            "correction selector on development games, freeze it, then open "
            "the Miami-Ohio State validation set once."
        )
    else:
        lines.append(
            "Train a leave-one-game-out replacement candidate on development "
            "games rather than stacking another correction on 7E. Keep the "
            "Miami-Ohio State validation set sealed until that model is frozen."
        )
    return "\n".join(lines) + "\n"


def run_transfer_audit(
    protocol_path: Path,
    model_path: Path,
    cache_path: Path,
    report_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    protocol_path = protocol_path.resolve()
    repository_root = protocol_path.parents[1]
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    specs, counts = load_marked_angle_specs(protocol, repository_root)
    policy = frozen_policy(protocol)
    if policy.policy_id != (
        "detected_moving_share__rel-0.800_score-0.120_"
        "ratio-2.000_lag-0.250"
    ):
        raise ValueError(f"Unexpected frozen policy: {policy.policy_id}")

    model_path = _resolve_repo_path(str(model_path), repository_root)
    traces, measurement = build_or_load_traces(
        specs,
        model_path,
        cache_path,
    )
    specs_by_angle = {
        (spec.cohort_id, spec.clip_id, spec.angle): spec for spec in specs
    }
    detector_scores = _grouped_scores(
        traces, specs_by_angle, policy, baseline=False
    )
    baseline_scores = _grouped_scores(
        traces, specs_by_angle, policy, baseline=True
    )
    decision = _transfer_decision(protocol, detector_scores)

    cohorts = _cohort_configuration(protocol, repository_root)
    judgment_paths = [
        _resolve_repo_path(str(value), repository_root)
        for value in protocol["judgment_paths"]
    ]
    feature_paths = [Path(entry["feature_path"]) for entry in cohorts.values()]
    report = {
        "schema_version": "1.0",
        "audit_id": str(protocol["audit_id"]),
        "dataset": counts,
        "frozen_policy": asdict(policy) | {"policy_id": policy.policy_id},
        "measurement": measurement,
        "inputs": {
            "protocol": _fingerprint(protocol_path),
            "model": _fingerprint(model_path),
            "judgments": [_fingerprint(path) for path in judgment_paths],
            "temporal_features": [
                _fingerprint(path) for path in feature_paths
            ],
        },
        "baseline_temporal_v21": {
            key: value
            for key, value in baseline_scores.items()
            if key != "rows"
        },
        "frozen_7e": detector_scores,
        "comparison": _comparison(
            baseline_scores["overall"], detector_scores["overall"]
        ),
        "transfer_decision": decision,
        "sealed_data": {
            "validation": protocol["sealed_validation"],
            "final_holdout": protocol["sealed_final_holdout"],
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        f"Audit complete: {decision['decision']} "
        f"({detector_scores['overall']['within_500_ms']}/"
        f"{detector_scores['overall']['angle_judgments']} within 500 ms).",
        flush=True,
    )
    return report
