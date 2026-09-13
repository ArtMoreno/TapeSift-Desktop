"""Learn to rank temporal and detected-player snap candidates across games."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

import numpy as np

from tapesift.research.snap_onset_detected_players import (
    CHANNELS,
    detected_player_policy_grid,
)
from tapesift.research.snap_onset_multigame_detected_players import (
    MarkedAngleSpec,
    _fingerprint,
    _grouped_scores,
    _read_jsonl,
    _transfer_decision,
    build_or_load_traces,
    frozen_policy,
    load_marked_angle_specs,
)
from tapesift.research.snap_onset_multigame_policy_replacement import (
    _acceptance_decision,
    _prediction_scores,
)
from tapesift.research.snap_onset_refiner import RiseCandidate
from tapesift.research.snap_onset_spatial_refiner import (
    SpatialAngleTrace,
    SpatialPolicy,
    score_spatial_policy,
)


@dataclass
class CandidateSupport:
    is_temporal: bool = False
    is_frozen_7e: bool = False
    policy_count: int = 0
    fallback_policy_count: int = 0
    policy_channels: dict[str, int] = field(default_factory=dict)
    policy_lags: dict[int, int] = field(default_factory=dict)
    raw_count: int = 0
    raw_channels: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AngleContext:
    onset_confidence: float
    onset_sustained_rise_score: float
    onset_prominence: float
    onset_pre_post_ratio: float
    angle_split_confidence: float
    classifier_usable: bool
    onset_method: str
    analysis_mode: str
    duration_seconds: float
    paired_play: bool


@dataclass(frozen=True)
class CandidateGroup:
    key: tuple[str, str, int]
    game_id: str
    game_name: str
    actual_snap_ms: int
    temporal_onset_ms: int
    frozen_7e_onset_ms: int
    candidate_onsets_ms: np.ndarray
    features: np.ndarray
    absolute_errors_ms: np.ndarray


@dataclass(frozen=True)
class RandomFeatureRanker:
    mean: np.ndarray
    scale: np.ndarray
    projection: np.ndarray
    hidden_bias: np.ndarray
    coefficients: np.ndarray
    seed: int
    training_weighted_rmse: float


def _resolve(value: str | Path, repository_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repository_root / path


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if np.isfinite(result) else default


def load_angle_contexts(
    transfer_protocol: dict[str, Any],
    repository_root: Path,
    specs: list[MarkedAngleSpec],
) -> dict[tuple[str, str, int], AngleContext]:
    wanted = {
        (spec.cohort_id, spec.clip_id, spec.angle) for spec in specs
    }
    angles_per_play: dict[tuple[str, str], int] = {}
    for spec in specs:
        play_key = (spec.cohort_id, spec.clip_id)
        angles_per_play[play_key] = angles_per_play.get(play_key, 0) + 1

    contexts: dict[tuple[str, str, int], AngleContext] = {}
    for cohort in transfer_protocol["development_cohorts"]:
        feature_path = _resolve(cohort["feature_path"], repository_root)
        for record in _read_jsonl(feature_path):
            cohort_id = str(record["research_cohort_id"])
            clip_id = str(record["clip_id"])
            root = record["temporal_diagnostics"]
            for angle in root["angles"]:
                key = (cohort_id, clip_id, int(angle["angle"]))
                if key not in wanted:
                    continue
                contexts[key] = AngleContext(
                    onset_confidence=_safe_float(angle.get("onset_confidence")),
                    onset_sustained_rise_score=_safe_float(
                        angle.get("onset_sustained_rise_score")
                    ),
                    onset_prominence=_safe_float(angle.get("onset_prominence")),
                    onset_pre_post_ratio=_safe_float(
                        angle.get("onset_pre_post_ratio"), 1.0
                    ),
                    angle_split_confidence=_safe_float(
                        root.get("angle_split_confidence")
                    ),
                    classifier_usable=bool(angle.get("classifier_usable")),
                    onset_method=str(angle.get("onset_method", "unknown")),
                    analysis_mode=str(root.get("analysis_mode", "unknown")),
                    duration_seconds=_safe_float(angle.get("duration_seconds")),
                    paired_play=(
                        angles_per_play[(cohort_id, clip_id)] >= 2
                    ),
                )
    missing = wanted - set(contexts)
    if missing:
        raise ValueError(f"Missing temporal contexts for {len(missing)} angles")
    return contexts


def _support_for(
    supports: dict[int, CandidateSupport],
    onset_ms: int,
) -> CandidateSupport:
    if onset_ms not in supports:
        supports[onset_ms] = CandidateSupport()
    return supports[onset_ms]


def _top_raw_candidates(
    candidates: tuple[RiseCandidate, ...],
    count: int,
) -> list[RiseCandidate]:
    by_score = sorted(
        candidates,
        key=lambda item: (-item.score, -item.post_pre_ratio, item.time_seconds),
    )[:count]
    by_ratio = sorted(
        candidates,
        key=lambda item: (-item.post_pre_ratio, -item.score, item.time_seconds),
    )[:count]
    unique = {
        (item.time_seconds, item.score, item.post_pre_ratio): item
        for item in by_score + by_ratio
    }
    return sorted(unique.values(), key=lambda item: item.time_seconds)


def _nearest_candidate(
    candidates: tuple[RiseCandidate, ...],
    times_ms: list[int],
    target_ms: int,
    tolerance_ms: int,
) -> RiseCandidate | None:
    if not times_ms:
        return None
    position = bisect_left(times_ms, target_ms)
    indexes = [position]
    if position:
        indexes.append(position - 1)
    if position + 1 < len(times_ms):
        indexes.append(position + 1)
    best_index = min(
        (index for index in indexes if 0 <= index < len(times_ms)),
        key=lambda index: abs(times_ms[index] - target_ms),
    )
    if abs(times_ms[best_index] - target_ms) > tolerance_ms:
        return None
    return candidates[best_index]


def _feature_names(
    policy_lags_ms: list[int],
) -> list[str]:
    names = [
        "is_temporal_v21",
        "is_frozen_7e",
        "has_policy_support",
        "has_raw_support",
        "policy_support_share",
        "fallback_policy_support_share",
        "policy_channel_count_share",
        "raw_support_share",
        "raw_channel_count_share",
        "is_policy_vote_mode",
        "distance_from_policy_vote_median_s",
        "absolute_distance_from_policy_vote_median_s",
        "delta_from_temporal_s",
        "absolute_delta_from_temporal_s",
        "squared_delta_from_temporal",
        "delta_from_frozen_7e_s",
        "absolute_delta_from_frozen_7e_s",
        "range_position_centered",
        "seconds_from_range_start",
        "seconds_to_range_end",
    ]
    names.extend(f"policy_support_{channel}" for channel in CHANNELS)
    names.extend(f"policy_support_lag_{lag_ms}ms" for lag_ms in policy_lags_ms)
    for channel in CHANNELS:
        names.extend([
            f"signal_score_{channel}",
            f"signal_relative_score_{channel}",
            f"signal_log_ratio_{channel}",
            f"signal_score_rank_{channel}",
            f"signal_low_gate_{channel}",
            f"context_max_score_{channel}",
            f"context_max_log_ratio_{channel}",
        ])
    names.extend([
        "signal_max_score",
        "signal_mean_positive_score",
        "signal_max_relative_score",
        "signal_max_log_ratio",
        "signal_channels_low_gate_share",
        "signal_channels_strict_gate_share",
        "angle_duration_seconds",
        "temporal_onset_confidence",
        "temporal_sustained_rise_score",
        "temporal_onset_prominence",
        "temporal_log_pre_post_ratio",
        "temporal_method_low_confidence",
        "temporal_method_sustained",
        "analysis_one_angle",
        "analysis_abstain",
        "analysis_paired",
        "classifier_usable",
        "angle_two",
        "paired_play",
        "angle_split_confidence",
        "temporal_range_position",
        "frozen_7e_delta_from_temporal_s",
        "frozen_7e_absolute_delta_from_temporal_s",
    ])
    return names


def _candidate_features(
    trace: SpatialAngleTrace,
    spec: MarkedAngleSpec,
    context: AngleContext,
    onset_ms: int,
    support: CandidateSupport,
    frozen_onset_ms: int,
    vote_median_ms: float,
    vote_mode_ms: int,
    policies_per_channel: dict[str, int],
    policies_per_lag: dict[int, int],
    raw_lags_ms: list[int],
    total_policies: int,
    tolerance_ms: int,
) -> list[float]:
    duration_ms = max(trace.range_end_ms - trace.range_start_ms, 1)
    policy_channels = sum(value > 0 for value in support.policy_channels.values())
    raw_channels = sum(value > 0 for value in support.raw_channels.values())
    delta_temporal_s = (onset_ms - trace.temporal_v21_onset_ms) / 1000
    delta_frozen_s = (onset_ms - frozen_onset_ms) / 1000
    vote_delta_s = (onset_ms - vote_median_ms) / 1000
    values = [
        float(support.is_temporal),
        float(support.is_frozen_7e),
        float(support.policy_count > 0),
        float(support.raw_count > 0),
        support.policy_count / max(total_policies, 1),
        support.fallback_policy_count / max(total_policies, 1),
        policy_channels / max(len(CHANNELS), 1),
        min(support.raw_count / max(len(CHANNELS) * len(raw_lags_ms), 1), 1.0),
        raw_channels / max(len(CHANNELS), 1),
        float(onset_ms == vote_mode_ms),
        float(np.clip(vote_delta_s / 10, -2, 2)),
        float(np.clip(abs(vote_delta_s) / 10, 0, 2)),
        float(np.clip(delta_temporal_s / 10, -2, 2)),
        float(np.clip(abs(delta_temporal_s) / 10, 0, 2)),
        float(np.clip((delta_temporal_s / 10) ** 2, 0, 4)),
        float(np.clip(delta_frozen_s / 10, -2, 2)),
        float(np.clip(abs(delta_frozen_s) / 10, 0, 2)),
        float(np.clip((onset_ms - trace.range_start_ms) / duration_ms - 0.5, -1, 1)),
        float(np.clip((onset_ms - trace.range_start_ms) / 20000, -0.5, 2)),
        float(np.clip((trace.range_end_ms - onset_ms) / 20000, -0.5, 2)),
    ]
    values.extend(
        support.policy_channels.get(channel, 0)
        / max(policies_per_channel[channel], 1)
        for channel in CHANNELS
    )
    values.extend(
        support.policy_lags.get(lag_ms, 0)
        / max(policies_per_lag[lag_ms], 1)
        for lag_ms in sorted(policies_per_lag)
    )

    signal_scores = []
    signal_relatives = []
    signal_log_ratios = []
    low_gate_count = 0
    strict_gate_count = 0
    for channel in CHANNELS:
        candidates = trace.candidates_by_channel[channel]
        times_ms = [round(item.time_seconds * 1000) for item in candidates]
        channel_scores = sorted(item.score for item in candidates)
        max_score = max(channel_scores, default=0.0)
        max_ratio = max((item.post_pre_ratio for item in candidates), default=1.0)
        aligned = []
        for lag_ms in raw_lags_ms:
            candidate = _nearest_candidate(
                candidates,
                times_ms,
                onset_ms - trace.range_start_ms - lag_ms,
                tolerance_ms,
            )
            if candidate is not None:
                aligned.append(candidate)
        best = max(
            aligned,
            key=lambda item: (item.score, item.post_pre_ratio),
            default=None,
        )
        if best is None:
            score = relative = log_ratio = rank = low_gate = 0.0
        else:
            score = float(np.clip(best.score, -2, 2))
            relative = float(np.clip(best.score / max(max_score, 0.001), -2, 2))
            log_ratio = float(np.clip(
                np.log(max(best.post_pre_ratio, 0.02)) / np.log(50), -1, 1
            ))
            rank = bisect_right(channel_scores, best.score) / max(
                len(channel_scores), 1
            )
            low_gate = float(best.score >= 0.08 and best.post_pre_ratio >= 1.5)
            low_gate_count += int(low_gate)
            strict_gate_count += int(
                best.score >= 0.12 and best.post_pre_ratio >= 2.0
            )
        signal_scores.append(score)
        signal_relatives.append(relative)
        signal_log_ratios.append(log_ratio)
        values.extend([
            score,
            relative,
            log_ratio,
            rank,
            low_gate,
            float(np.clip(max_score, -2, 2)),
            float(np.clip(np.log(max(max_ratio, 0.02)) / np.log(50), -1, 1)),
        ])

    positive_scores = [value for value in signal_scores if value > 0]
    frozen_delta_s = (
        frozen_onset_ms - trace.temporal_v21_onset_ms
    ) / 1000
    temporal_position = (
        trace.temporal_v21_onset_ms - trace.range_start_ms
    ) / duration_ms
    method = context.onset_method.lower()
    mode = context.analysis_mode.lower()
    values.extend([
        max(signal_scores, default=0.0),
        statistics.fmean(positive_scores) if positive_scores else 0.0,
        max(signal_relatives, default=0.0),
        max(signal_log_ratios, default=0.0),
        low_gate_count / max(len(CHANNELS), 1),
        strict_gate_count / max(len(CHANNELS), 1),
        float(np.clip(context.duration_seconds / 30, 0, 2)),
        float(np.clip(context.onset_confidence, 0, 1)),
        float(np.clip(context.onset_sustained_rise_score, -1, 2)),
        float(np.clip(context.onset_prominence, 0, 2)),
        float(np.clip(
            np.log(max(context.onset_pre_post_ratio, 0.02)) / np.log(50),
            -1,
            1,
        )),
        float("low_confidence" in method),
        float("sustained_motion_rise" in method and "low_confidence" not in method),
        float("one_angle" in mode),
        float("abstain" in mode),
        float("paired" in mode),
        float(context.classifier_usable),
        float(spec.angle == 2),
        float(context.paired_play),
        float(np.clip(context.angle_split_confidence, 0, 1)),
        float(np.clip(temporal_position - 0.5, -1, 1)),
        float(np.clip(frozen_delta_s / 10, -2, 2)),
        float(np.clip(abs(frozen_delta_s) / 10, 0, 2)),
    ])
    return values


def build_candidate_groups(
    traces: list[SpatialAngleTrace],
    specs_by_angle: dict[tuple[str, str, int], MarkedAngleSpec],
    contexts: dict[tuple[str, str, int], AngleContext],
    old_policy: SpatialPolicy,
    candidate_config: dict[str, Any],
) -> tuple[list[CandidateGroup], list[str], dict[str, Any]]:
    policies = sorted(
        detected_player_policy_grid(), key=lambda policy: policy.policy_id
    )
    policy_lags_ms = sorted({round(policy.output_lag_seconds * 1000) for policy in policies})
    policies_per_channel = {
        channel: sum(policy.channel == channel for policy in policies)
        for channel in CHANNELS
    }
    policies_per_lag = {
        lag_ms: sum(
            round(policy.output_lag_seconds * 1000) == lag_ms
            for policy in policies
        )
        for lag_ms in policy_lags_ms
    }
    supports_by_key: dict[
        tuple[str, str, int], dict[int, CandidateSupport]
    ] = {}
    frozen_onsets: dict[tuple[str, str, int], int] = {}
    traces_by_key = {
        (trace.cohort_id, trace.clip_id, trace.angle): trace for trace in traces
    }
    for key, trace in traces_by_key.items():
        supports: dict[int, CandidateSupport] = {}
        _support_for(supports, trace.temporal_v21_onset_ms).is_temporal = True
        supports_by_key[key] = supports

    for policy_index, policy in enumerate(policies, start=1):
        result = score_spatial_policy(traces, policy)
        for trace, row in zip(traces, result["rows"], strict=True):
            key = (trace.cohort_id, trace.clip_id, trace.angle)
            onset_ms = int(row["candidate_onset_ms"])
            support = _support_for(supports_by_key[key], onset_ms)
            if str(row["selection_status"]).startswith("temporal_v21_fallback"):
                support.fallback_policy_count += 1
            else:
                support.policy_count += 1
                support.policy_channels[policy.channel] = (
                    support.policy_channels.get(policy.channel, 0) + 1
                )
                lag_ms = round(policy.output_lag_seconds * 1000)
                support.policy_lags[lag_ms] = support.policy_lags.get(lag_ms, 0) + 1
            if policy.policy_id == old_policy.policy_id:
                support.is_frozen_7e = True
                frozen_onsets[key] = onset_ms
        if policy_index % 144 == 0:
            print(
                f"Candidate voting: {policy_index}/{len(policies)} policies",
                flush=True,
            )

    if len(frozen_onsets) != len(traces):
        frozen_result = score_spatial_policy(traces, old_policy)
        for trace, row in zip(traces, frozen_result["rows"], strict=True):
            key = (trace.cohort_id, trace.clip_id, trace.angle)
            onset_ms = int(row["candidate_onset_ms"])
            _support_for(supports_by_key[key], onset_ms).is_frozen_7e = True
            frozen_onsets[key] = onset_ms

    raw_lags_ms = [int(value) for value in candidate_config["raw_output_lags_ms"]]
    raw_count = int(candidate_config["raw_candidates_per_channel_per_ranking"])
    maximum_candidates = int(candidate_config["maximum_candidates_per_angle"])
    tolerance_ms = int(candidate_config["nearest_signal_tolerance_ms"])
    feature_names = _feature_names(policy_lags_ms)
    groups = []
    candidate_counts = []
    for trace in traces:
        key = (trace.cohort_id, trace.clip_id, trace.angle)
        spec = specs_by_angle[key]
        supports = supports_by_key[key]
        frozen_onset_ms = frozen_onsets[key]
        for channel in CHANNELS:
            selected = _top_raw_candidates(
                trace.candidates_by_channel[channel], raw_count
            )
            for candidate in selected:
                for lag_ms in raw_lags_ms:
                    onset_ms = (
                        trace.range_start_ms
                        + round(candidate.time_seconds * 1000)
                        + lag_ms
                    )
                    support = _support_for(supports, onset_ms)
                    support.raw_count += 1
                    support.raw_channels[channel] = (
                        support.raw_channels.get(channel, 0) + 1
                    )

        mandatory = {
            onset_ms
            for onset_ms, support in supports.items()
            if support.is_temporal or support.is_frozen_7e
        }
        remaining = sorted(
            (onset_ms for onset_ms in supports if onset_ms not in mandatory),
            key=lambda onset_ms: (
                -supports[onset_ms].policy_count,
                -len(supports[onset_ms].policy_channels),
                -supports[onset_ms].raw_count,
                abs(onset_ms - trace.temporal_v21_onset_ms),
                onset_ms,
            ),
        )
        retained = sorted(
            mandatory | set(remaining[:max(0, maximum_candidates - len(mandatory))])
        )
        policy_votes = [
            onset_ms
            for onset_ms, support in supports.items()
            for _ in range(support.policy_count)
        ]
        vote_median_ms = (
            statistics.median(policy_votes)
            if policy_votes
            else trace.temporal_v21_onset_ms
        )
        vote_mode_ms = min(
            retained,
            key=lambda onset_ms: (
                -supports[onset_ms].policy_count,
                abs(onset_ms - vote_median_ms),
                onset_ms,
            ),
        )
        rows = [
            _candidate_features(
                trace,
                spec,
                contexts[key],
                onset_ms,
                supports[onset_ms],
                frozen_onset_ms,
                vote_median_ms,
                vote_mode_ms,
                policies_per_channel,
                policies_per_lag,
                raw_lags_ms,
                len(policies),
                tolerance_ms,
            )
            for onset_ms in retained
        ]
        features = np.asarray(rows, dtype=np.float64)
        if features.shape[1] != len(feature_names):
            raise ValueError(
                f"Feature width {features.shape[1]} != {len(feature_names)}"
            )
        onsets = np.asarray(retained, dtype=np.int64)
        errors = np.abs(onsets - spec.actual_snap_ms).astype(np.float64)
        groups.append(CandidateGroup(
            key=key,
            game_id=spec.game_id,
            game_name=spec.game_name,
            actual_snap_ms=spec.actual_snap_ms,
            temporal_onset_ms=spec.temporal_v21_onset_ms,
            frozen_7e_onset_ms=frozen_onset_ms,
            candidate_onsets_ms=onsets,
            features=features,
            absolute_errors_ms=errors,
        ))
        candidate_counts.append(len(retained))
    oracle_within = sum(
        float(group.absolute_errors_ms.min()) <= 500 for group in groups
    )
    diagnostics = {
        "angles": len(groups),
        "minimum_candidates": min(candidate_counts),
        "median_candidates": statistics.median(candidate_counts),
        "maximum_candidates": max(candidate_counts),
        "oracle_within_500_ms": oracle_within,
        "oracle_within_500_ms_share": round(oracle_within / len(groups), 6),
        "policy_candidates": len(policies),
        "policy_lags_ms": policy_lags_ms,
    }
    return groups, feature_names, diagnostics


def _design_matrix(
    standardized: np.ndarray,
    projection: np.ndarray,
    hidden_bias: np.ndarray,
) -> np.ndarray:
    hidden = np.tanh(standardized @ projection + hidden_bias)
    return np.column_stack([
        np.ones(len(standardized), dtype=np.float64),
        standardized,
        hidden,
    ])


def train_ranker_ensemble(
    groups: list[CandidateGroup],
    model_config: dict[str, Any],
) -> list[RandomFeatureRanker]:
    features = np.concatenate([group.features for group in groups], axis=0)
    errors = np.concatenate([
        group.absolute_errors_ms for group in groups
    ]).astype(np.float64)
    row_weights = np.concatenate([
        np.full(len(group.features), 1 / len(group.features), dtype=np.float64)
        for group in groups
    ])
    mean = features.mean(axis=0)
    scale = features.std(axis=0)
    scale[scale < 1e-8] = 1.0
    standardized = np.clip((features - mean) / scale, -6, 6)
    target = -np.minimum(
        errors,
        float(model_config["target_error_cap_ms"]),
    ) / 1000
    target += (
        errors <= 500
    ) * float(model_config["target_within_500_ms_bonus"])
    hidden_features = int(model_config["random_hidden_features"])
    ridge_penalty = float(model_config["ridge_penalty"])
    models = []
    for seed in model_config["ensemble_seeds"]:
        random = np.random.default_rng(int(seed))
        projection = random.normal(
            0,
            1 / np.sqrt(features.shape[1]),
            size=(features.shape[1], hidden_features),
        )
        hidden_bias = random.normal(0, 0.5, size=hidden_features)
        design = _design_matrix(standardized, projection, hidden_bias)
        weighted_design = design * row_weights[:, None]
        system = design.T @ weighted_design
        penalty = np.eye(system.shape[0], dtype=np.float64) * ridge_penalty
        penalty[0, 0] = 0.0
        response = design.T @ (row_weights * target)
        try:
            coefficients = np.linalg.solve(system + penalty, response)
        except np.linalg.LinAlgError:
            coefficients = np.linalg.lstsq(
                system + penalty, response, rcond=None
            )[0]
        prediction = design @ coefficients
        rmse = float(np.sqrt(
            np.sum(row_weights * (prediction - target) ** 2)
            / np.sum(row_weights)
        ))
        models.append(RandomFeatureRanker(
            mean=mean.copy(),
            scale=scale.copy(),
            projection=projection,
            hidden_bias=hidden_bias,
            coefficients=coefficients,
            seed=int(seed),
            training_weighted_rmse=round(rmse, 6),
        ))
    return models


def _model_scores(
    model: RandomFeatureRanker,
    features: np.ndarray,
) -> np.ndarray:
    standardized = np.clip((features - model.mean) / model.scale, -6, 6)
    return _design_matrix(
        standardized, model.projection, model.hidden_bias
    ) @ model.coefficients


def predict_groups(
    groups: list[CandidateGroup],
    models: list[RandomFeatureRanker],
    model_config: dict[str, Any],
) -> tuple[
    dict[tuple[str, str, int], int],
    dict[tuple[str, str, int], dict[str, Any]],
]:
    tolerance_ms = int(model_config["consensus_tolerance_ms"])
    minimum_members = int(model_config["minimum_consensus_members"])
    predictions = {}
    diagnostics = {}
    for group in groups:
        all_scores = [_model_scores(model, group.features) for model in models]
        normalized_scores = []
        member_onsets = []
        for scores in all_scores:
            scale = float(scores.std())
            normalized_scores.append(
                (scores - scores.mean()) / (scale if scale > 1e-8 else 1.0)
            )
            member_onsets.append(int(group.candidate_onsets_ms[int(np.argmax(scores))]))
        ensemble_scores = np.mean(normalized_scores, axis=0)
        ensemble_index = int(np.argmax(ensemble_scores))
        ensemble_onset = int(group.candidate_onsets_ms[ensemble_index])
        best_center = min(
            member_onsets,
            key=lambda center: (
                -sum(abs(value - center) <= tolerance_ms for value in member_onsets),
                abs(center - ensemble_onset),
                center,
            ),
        )
        cluster = [
            value for value in member_onsets if abs(value - best_center) <= tolerance_ms
        ]
        used_fallback = len(cluster) < minimum_members
        if used_fallback:
            predicted_onset = group.frozen_7e_onset_ms
        else:
            cluster_median = statistics.median(cluster)
            predicted_index = min(
                range(len(group.candidate_onsets_ms)),
                key=lambda index: (
                    abs(int(group.candidate_onsets_ms[index]) - cluster_median),
                    -float(ensemble_scores[index]),
                    int(group.candidate_onsets_ms[index]),
                ),
            )
            predicted_onset = int(group.candidate_onsets_ms[predicted_index])
        predictions[group.key] = predicted_onset
        diagnostics[group.key] = {
            "predicted_onset_ms": predicted_onset,
            "member_onsets_ms": member_onsets,
            "consensus_members": len(cluster),
            "used_frozen_7e_fallback": used_fallback,
        }
    return predictions, diagnostics


def _apply_ranker_fallback_counts(
    scores: dict[str, Any],
    diagnostics: dict[tuple[str, str, int], dict[str, Any]],
    specs_by_angle: dict[tuple[str, str, int], MarkedAngleSpec],
) -> None:
    scores["overall"]["fallback_count"] = sum(
        item["used_frozen_7e_fallback"] for item in diagnostics.values()
    )
    for game_id, metrics in scores["by_game"].items():
        metrics["fallback_count"] = sum(
            item["used_frozen_7e_fallback"]
            for key, item in diagnostics.items()
            if specs_by_angle[key].game_id == game_id
        )


def _ranker_scores(
    traces: list[SpatialAngleTrace],
    groups: list[CandidateGroup],
    models: list[RandomFeatureRanker],
    model_config: dict[str, Any],
    specs_by_angle: dict[tuple[str, str, int], MarkedAngleSpec],
    old_policy: SpatialPolicy,
) -> tuple[dict[str, Any], dict[tuple[str, str, int], dict[str, Any]]]:
    predictions, diagnostics = predict_groups(groups, models, model_config)
    scores = _prediction_scores(
        traces, predictions, specs_by_angle, old_policy
    )
    _apply_ranker_fallback_counts(scores, diagnostics, specs_by_angle)
    return scores, diagnostics


def leave_one_game_out(
    traces: list[SpatialAngleTrace],
    groups: list[CandidateGroup],
    model_config: dict[str, Any],
    specs_by_angle: dict[tuple[str, str, int], MarkedAngleSpec],
    old_policy: SpatialPolicy,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    dict[tuple[str, str, int], dict[str, Any]],
]:
    traces_by_key = {
        (trace.cohort_id, trace.clip_id, trace.angle): trace for trace in traces
    }
    games = sorted({group.game_id for group in groups})
    all_predictions = {}
    all_diagnostics = {}
    fold_reports = []
    for fold_index, holdout_game in enumerate(games, start=1):
        training_groups = [group for group in groups if group.game_id != holdout_game]
        holdout_groups = [group for group in groups if group.game_id == holdout_game]
        holdout_traces = [traces_by_key[group.key] for group in holdout_groups]
        print(
            f"[{fold_index}/{len(games)}] training ranker without {holdout_game}",
            flush=True,
        )
        models = train_ranker_ensemble(training_groups, model_config)
        fold_scores, fold_diagnostics = _ranker_scores(
            holdout_traces,
            holdout_groups,
            models,
            model_config,
            specs_by_angle,
            old_policy,
        )
        for key, item in fold_diagnostics.items():
            if key in all_predictions:
                raise ValueError(f"Duplicate LOFO ranker prediction: {key}")
            all_predictions[key] = int(item["predicted_onset_ms"])
            all_diagnostics[key] = item
        fold_reports.append({
            "holdout_game": holdout_game,
            "training_angles": len(training_groups),
            "holdout_angles": len(holdout_groups),
            "model_training_weighted_rmse": [
                model.training_weighted_rmse for model in models
            ],
            "holdout_overall": fold_scores["overall"],
        })
        print(
            f"    {fold_scores['overall']['within_500_ms']}/"
            f"{fold_scores['overall']['angle_judgments']} within 500 ms; "
            f"{fold_scores['overall']['fallback_count']} consensus fallbacks",
            flush=True,
        )
    if len(all_predictions) != len(groups):
        raise ValueError(
            f"Expected {len(groups)} ranker predictions, found {len(all_predictions)}"
        )
    scores = _prediction_scores(
        traces, all_predictions, specs_by_angle, old_policy
    )
    _apply_ranker_fallback_counts(scores, all_diagnostics, specs_by_angle)
    return scores, fold_reports, all_diagnostics


def _oracle_summary(groups: list[CandidateGroup]) -> dict[str, Any]:
    by_game = {}
    for game_id in sorted({group.game_id for group in groups}):
        subset = [group for group in groups if group.game_id == game_id]
        within = sum(group.absolute_errors_ms.min() <= 500 for group in subset)
        by_game[game_id] = {
            "angles": len(subset),
            "within_500_ms": within,
            "within_500_ms_share": round(within / len(subset), 6),
            "median_best_absolute_error_ms": float(statistics.median(
                group.absolute_errors_ms.min() for group in subset
            )),
        }
    within = sum(group.absolute_errors_ms.min() <= 500 for group in groups)
    return {
        "overall": {
            "angles": len(groups),
            "within_500_ms": within,
            "within_500_ms_share": round(within / len(groups), 6),
            "median_best_absolute_error_ms": float(statistics.median(
                group.absolute_errors_ms.min() for group in groups
            )),
        },
        "by_game": by_game,
    }


def save_model_bundle(
    path: Path,
    models: list[RandomFeatureRanker],
    feature_names: list[str],
    protocol: dict[str, Any],
    candidate_grid_sha256: str,
    approved: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": "1.0",
        "iteration_id": protocol["iteration_id"],
        "feature_names": feature_names,
        "candidate_generation": protocol["candidate_generation"],
        "model": protocol["model"],
        "candidate_grid_sha256": candidate_grid_sha256,
        "approved_for_sealed_validation": approved,
    }
    np.savez_compressed(
        path,
        metadata_json=np.asarray(json.dumps(metadata, sort_keys=True)),
        means=np.stack([model.mean for model in models]),
        scales=np.stack([model.scale for model in models]),
        projections=np.stack([model.projection for model in models]),
        hidden_biases=np.stack([model.hidden_bias for model in models]),
        coefficients=np.stack([model.coefficients for model in models]),
        seeds=np.asarray([model.seed for model in models], dtype=np.int64),
    )


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f}%"


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def render_markdown(report: dict[str, Any]) -> str:
    old = report["frozen_7e"]["overall"]
    new = report["lofo_candidate_ranker"]["overall"]
    oracle = report["candidate_oracle"]["overall"]
    decision = report["acceptance_decision"]
    lines = [
        "# Multi-game Snap Candidate Ranker",
        "",
        f"**Decision:** `{decision['decision']}`",
        "",
        "Every reported ranker prediction comes from a model trained without "
        "that prediction's game. Validation and final holdout data were not loaded.",
        "",
        "## Unseen-game result",
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
        f"| LOFO ranker | {new['within_500_ms']}/{new['angle_judgments']} "
        f"({_percent(new['within_500_ms_share'])}) | "
        f"{new['median_absolute_error_ms']:.1f} ms | "
        f"{new['p90_absolute_error_ms']:.1f} ms | "
        f"{_percent(new['both_angles_within_500_ms_share'])} | "
        f"{new['fallback_count']} |",
        f"| Candidate oracle | {oracle['within_500_ms']}/{oracle['angles']} "
        f"({_percent(oracle['within_500_ms_share'])}) | "
        f"{oracle['median_best_absolute_error_ms']:.1f} ms | n/a | n/a | n/a |",
        "",
        "## Leave-one-game-out folds",
        "",
        "| Held-out game | Within 500 ms | Median abs error | Fallbacks |",
        "| --- | ---: | ---: | ---: |",
    ]
    for fold in report["folds"]:
        metrics = fold["holdout_overall"]
        lines.append(
            f"| {fold['holdout_game']} | "
            f"{metrics['within_500_ms']}/{metrics['angle_judgments']} "
            f"({_percent(metrics['within_500_ms_share'])}) | "
            f"{metrics['median_absolute_error_ms']:.1f} ms | "
            f"{metrics['fallback_count']} |"
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
        "## Frozen artifact",
        "",
        f"- Path: `{report['global_model']['artifact']['path']}`",
        f"- SHA-256: `{report['global_model']['artifact']['sha256']}`",
        f"- Status: `{report['global_model']['status']}`",
        "",
        "## Next iteration",
        "",
    ])
    if decision["passed"]:
        lines.append(
            "Run this exact frozen artifact once on the sealed Miami-Ohio State "
            "validation set. Do not retrain or change thresholds first."
        )
    else:
        lines.append(
            "Do not open validation. The current motion candidate representation "
            "does not generalize sufficiently; the next model must add stronger "
            "spatial center/QB interaction features or more development games."
        )
    return "\n".join(lines) + "\n"


def run_candidate_ranker(
    protocol_path: Path,
    report_path: Path,
    markdown_path: Path,
    model_artifact_path: Path,
) -> dict[str, Any]:
    protocol_path = protocol_path.resolve()
    repository_root = protocol_path.parents[1]
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    transfer_protocol_path = _resolve(
        protocol["base_transfer_protocol"], repository_root
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
    contexts = load_angle_contexts(
        transfer_protocol, repository_root, specs
    )
    model_path = _resolve(protocol["model_path"], repository_root)
    cache_path = _resolve(protocol["measurement_cache_path"], repository_root)
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
    groups, feature_names, candidate_diagnostics = build_candidate_groups(
        traces,
        specs_by_angle,
        contexts,
        old_policy,
        protocol["candidate_generation"],
    )
    oracle = _oracle_summary(groups)
    print(
        f"Candidate oracle: {oracle['overall']['within_500_ms']}/"
        f"{oracle['overall']['angles']} within 500 ms "
        f"({oracle['overall']['within_500_ms_share'] * 100:.1f}%)",
        flush=True,
    )
    lofo_scores, folds, lofo_diagnostics = leave_one_game_out(
        traces,
        groups,
        protocol["model"],
        specs_by_angle,
        old_policy,
    )
    transfer_decision = _transfer_decision(
        transfer_protocol, lofo_scores
    )
    acceptance = _acceptance_decision(
        protocol,
        transfer_decision,
        lofo_scores,
        frozen_scores,
    )

    print("[global] fitting candidate ranker on all development games", flush=True)
    global_models = train_ranker_ensemble(groups, protocol["model"])
    global_scores, global_diagnostics = _ranker_scores(
        traces,
        groups,
        global_models,
        protocol["model"],
        specs_by_angle,
        old_policy,
    )
    policies = sorted(
        detected_player_policy_grid(), key=lambda policy: policy.policy_id
    )
    grid_sha256 = hashlib.sha256(
        "\n".join(policy.policy_id for policy in policies).encode("utf-8")
    ).hexdigest()
    model_artifact_path = _resolve(model_artifact_path, repository_root)
    save_model_bundle(
        model_artifact_path,
        global_models,
        feature_names,
        protocol,
        grid_sha256,
        bool(acceptance["passed"]),
    )
    artifact = _fingerprint(model_artifact_path)
    report = {
        "schema_version": "1.0",
        "iteration_id": protocol["iteration_id"],
        "dataset": counts,
        "inputs": {
            "ranker_protocol": _fingerprint(protocol_path),
            "transfer_protocol": _fingerprint(transfer_protocol_path),
            "detected_player_model": _fingerprint(model_path),
        },
        "measurement": measurement,
        "candidate_generation": candidate_diagnostics,
        "candidate_grid_sha256": grid_sha256,
        "candidate_oracle": oracle,
        "baseline_temporal_v21": {
            key: value for key, value in baseline_scores.items() if key != "rows"
        },
        "frozen_7e": frozen_scores,
        "lofo_candidate_ranker": lofo_scores,
        "folds": folds,
        "lofo_prediction_diagnostics": {
            "consensus_fallbacks": sum(
                item["used_frozen_7e_fallback"]
                for item in lofo_diagnostics.values()
            ),
        },
        "lofo_transfer_decision": transfer_decision,
        "acceptance_decision": acceptance,
        "global_model": {
            "artifact": artifact,
            "status": (
                "approved_for_sealed_validation"
                if acceptance["passed"]
                else "experimental_not_approved"
            ),
            "training_weighted_rmse": [
                model.training_weighted_rmse for model in global_models
            ],
            "all_development_apparent_metrics": global_scores["overall"],
            "all_development_consensus_fallbacks": sum(
                item["used_frozen_7e_fallback"]
                for item in global_diagnostics.values()
            ),
        },
        "sealed_data": {
            "validation": transfer_protocol["sealed_validation"],
            "final_holdout": transfer_protocol["sealed_final_holdout"],
        },
    }
    report_path = _resolve(report_path, repository_root)
    markdown_path = _resolve(markdown_path, repository_root)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
            default=_json_default,
        ) + "\n",
        encoding="utf-8",
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    old = frozen_scores["overall"]
    new = lofo_scores["overall"]
    print(
        f"LOFO ranker: {new['within_500_ms']}/{new['angle_judgments']} "
        f"within 500 ms ({new['within_500_ms_share'] * 100:.1f}%), versus "
        f"{old['within_500_ms']}/{old['angle_judgments']} "
        f"({old['within_500_ms_share'] * 100:.1f}%) for frozen 7E.",
        flush=True,
    )
    print(
        f"Release gate: {transfer_decision['passed']}; games passing: "
        f"{transfer_decision['game_consistency']['games_passing']}/"
        f"{transfer_decision['game_consistency']['games_total']}",
        flush=True,
    )
    print(f"Decision: {acceptance['decision']}", flush=True)
    return report
