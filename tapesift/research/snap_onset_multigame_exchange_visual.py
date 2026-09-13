"""Direct exchange-area visual scoring for snap candidate ranking."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

import cv2
import numpy as np

from tapesift.research.snap_multigame_center_anchor_locator import (
    AnchorExample,
    LocatorModel,
    _fingerprint,
    build_or_load_features as build_or_load_exact_anchor_features,
    fit_locator_ensemble,
    load_anchor_examples,
)
from tapesift.research.snap_onset_detected_players import (
    detected_player_policy_grid,
)
from tapesift.research.snap_onset_multigame_candidate_ranker import (
    CandidateGroup,
    RandomFeatureRanker,
    _apply_ranker_fallback_counts,
    _model_scores,
    build_candidate_groups,
    load_angle_contexts,
    predict_groups,
    save_model_bundle,
    train_ranker_ensemble,
)
from tapesift.research.snap_onset_multigame_detected_players import (
    MarkedAngleSpec,
    _transfer_decision,
    build_or_load_traces,
    frozen_policy,
    load_marked_angle_specs,
)
from tapesift.research.snap_onset_multigame_policy_replacement import (
    _prediction_scores,
)
from tapesift.research.snap_onset_multigame_spatial_interaction import (
    AnchorEstimate,
    RuntimeAnchorFeatures,
    _resolve,
    augment_groups,
    build_or_load_motion_grids,
    build_or_load_runtime_anchor_features,
    crossfit_anchor_estimates,
    global_anchor_estimates,
    interaction_feature_names,
)
from tapesift.research.snap_onset_spatial_refiner import (
    SpatialAngleTrace,
    SpatialPolicy,
)


VISUAL_FRAME_VERSION = "exchange-visual-frames-v1"


@dataclass(frozen=True)
class VisualFrames:
    times_seconds: np.ndarray
    frames: np.ndarray


@dataclass(frozen=True)
class VisibilityModel:
    seed: int
    prior: float
    mean: np.ndarray
    scale: np.ndarray
    projection: np.ndarray
    hidden_bias: np.ndarray
    coefficients: np.ndarray
    training_rmse: float


def _source_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _frame_signature(
    spec: MarkedAngleSpec,
    config: dict[str, Any],
) -> str:
    payload = {
        "version": VISUAL_FRAME_VERSION,
        "source": _source_signature(Path(spec.source_video_path)),
        "range_start_ms": spec.range_start_ms,
        "range_end_ms": spec.range_end_ms,
        "config": config,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def measure_visual_frames(
    spec: MarkedAngleSpec,
    config: dict[str, Any],
) -> VisualFrames:
    capture = cv2.VideoCapture(spec.source_video_path)
    if not capture.isOpened():
        raise FileNotFoundError(spec.source_video_path)
    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    if source_fps <= 0:
        source_fps = 30.0
    start_frame = max(0, int(np.floor(spec.range_start_ms * source_fps / 1000)))
    end_frame = int(np.ceil(spec.range_end_ms * source_fps / 1000))
    step = source_fps / float(config["sample_fps"])
    targets = []
    value = float(start_frame)
    while value <= end_frame:
        targets.append(int(round(value)))
        value += step
    targets = sorted(set(targets))
    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    target_position = 0
    frame_index = start_frame
    times = []
    frames = []
    try:
        while target_position < len(targets) and frame_index <= end_frame:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            if frame_index >= targets[target_position]:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(
                    gray,
                    (int(config["sample_width"]), int(config["sample_height"])),
                    interpolation=cv2.INTER_AREA,
                )
                frames.append(gray)
                times.append(
                    frame_index / source_fps
                    - spec.range_start_ms / 1000
                )
                target_position += 1
            frame_index += 1
    finally:
        capture.release()
    if len(frames) < 16:
        raise RuntimeError(
            f"Only {len(frames)} visual samples for "
            f"{spec.cohort_id}:{spec.clip_id}:angle-{spec.angle}"
        )
    return VisualFrames(
        times_seconds=np.asarray(times, dtype=np.float32),
        frames=np.asarray(frames, dtype=np.uint8),
    )


def build_or_load_visual_frames(
    specs: list[MarkedAngleSpec],
    cache_directory: Path,
    config: dict[str, Any],
) -> tuple[dict[tuple[str, str, int], VisualFrames], dict[str, Any]]:
    cache_directory.mkdir(parents=True, exist_ok=True)
    output = {}
    hits = 0
    for index, spec in enumerate(specs, start=1):
        key = (spec.cohort_id, spec.clip_id, spec.angle)
        signature = _frame_signature(spec, config)
        name = hashlib.sha256(
            "|".join(map(str, key)).encode("utf-8")
        ).hexdigest()[:24] + ".npz"
        path = cache_directory / name
        record = None
        if path.exists():
            with np.load(path) as payload:
                if str(payload["signature"].item()) == signature:
                    record = VisualFrames(
                        times_seconds=payload["times_seconds"].copy(),
                        frames=payload["frames"].copy(),
                    )
                    hits += 1
        if record is None:
            record = measure_visual_frames(spec, config)
            np.savez_compressed(
                path,
                signature=np.asarray(signature),
                times_seconds=record.times_seconds,
                frames=record.frames,
            )
            if index == 1 or index % 10 == 0 or index == len(specs):
                print(
                    f"[{index}/{len(specs)}] cached exchange-area visual frames",
                    flush=True,
                )
        output[key] = record
    return output, {
        "version": VISUAL_FRAME_VERSION,
        "angles": len(output),
        "cache_hits": hits,
        "configuration": config,
    }


def _design(
    standardized: np.ndarray,
    projection: np.ndarray,
    hidden_bias: np.ndarray,
) -> np.ndarray:
    return np.column_stack([
        np.ones(len(standardized), dtype=np.float64),
        standardized,
        np.tanh(standardized @ projection + hidden_bias),
    ])


def fit_visibility_ensemble(
    examples: list[AnchorExample],
    exact_features: dict[tuple[str, str, int], np.ndarray],
    config: dict[str, Any],
) -> list[VisibilityModel]:
    if len(examples) < 20:
        raise ValueError("Insufficient visibility examples")
    matrix = np.stack([exact_features[example.key] for example in examples])
    targets = np.asarray([
        float(example.status == "visible") for example in examples
    ], dtype=np.float64)
    positives = max(int(np.sum(targets == 1)), 1)
    negatives = max(int(np.sum(targets == 0)), 1)
    row_weights = np.where(
        targets == 1, 0.5 / positives, 0.5 / negatives
    )
    prior = float(np.average(targets, weights=row_weights))
    centered_targets = targets - prior
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale[scale < 1e-8] = 1.0
    standardized = np.clip((matrix - mean) / scale, -6, 6)
    hidden_features = int(config["random_hidden_features"])
    ridge_penalty = float(config["ridge_penalty"])
    models = []
    for seed in config["ensemble_seeds"]:
        random = np.random.default_rng(int(seed))
        projection = random.normal(
            0,
            1 / np.sqrt(matrix.shape[1]),
            size=(matrix.shape[1], hidden_features),
        )
        hidden_bias = random.normal(0, 0.5, size=hidden_features)
        design = _design(standardized, projection, hidden_bias)
        system = design.T @ (design * row_weights[:, None])
        penalty = np.eye(system.shape[0]) * ridge_penalty
        penalty[0, 0] = 0.0
        response = design.T @ (row_weights * centered_targets)
        coefficients = np.linalg.solve(system + penalty, response)
        prediction = np.clip(prior + design @ coefficients, 0, 1)
        rmse = float(np.sqrt(np.average(
            (prediction - targets) ** 2, weights=row_weights
        )))
        models.append(VisibilityModel(
            seed=int(seed),
            prior=prior,
            mean=mean.copy(),
            scale=scale.copy(),
            projection=projection,
            hidden_bias=hidden_bias,
            coefficients=coefficients,
            training_rmse=round(rmse, 6),
        ))
    return models


def _predict_visibility_one(
    models: list[VisibilityModel],
    feature: np.ndarray,
) -> tuple[float, float]:
    predictions = []
    for model in models:
        standardized = np.clip((feature - model.mean) / model.scale, -6, 6)
        design = _design(
            standardized[None, :], model.projection, model.hidden_bias
        )
        predictions.append(float(np.clip(
            model.prior + (design @ model.coefficients)[0], 0, 1
        )))
    return float(np.mean(predictions)), float(np.std(predictions))


def _runtime_visibility(
    models: list[VisibilityModel],
    features: RuntimeAnchorFeatures,
) -> tuple[float, float]:
    temporal, temporal_uncertainty = _predict_visibility_one(
        models, features.temporal
    )
    frozen, frozen_uncertainty = _predict_visibility_one(
        models, features.frozen_7e
    )
    return (
        round((temporal + frozen) / 2, 6),
        round(
            (temporal_uncertainty + frozen_uncertainty) / 2
            + abs(temporal - frozen) / 2,
            6,
        ),
    )


def crossfit_visibility(
    groups: list[CandidateGroup],
    examples: list[AnchorExample],
    exact_features: dict[tuple[str, str, int], np.ndarray],
    runtime_features: dict[tuple[str, str, int], RuntimeAnchorFeatures],
    config: dict[str, Any],
    outer_holdout_game: str | None,
    model_cache: dict[frozenset[str], list[VisibilityModel]],
) -> dict[tuple[str, str, int], tuple[float, float]]:
    output = {}
    games = sorted({group.game_id for group in groups})
    for target_game in games:
        excluded = {target_game}
        if outer_holdout_game is not None:
            excluded.add(outer_holdout_game)
        key = frozenset(excluded)
        models = model_cache.get(key)
        if models is None:
            models = fit_visibility_ensemble(
                [example for example in examples if example.game_id not in excluded],
                exact_features,
                config,
            )
            model_cache[key] = models
        for group in groups:
            if group.game_id == target_game:
                output[group.key] = _runtime_visibility(
                    models, runtime_features[group.key]
                )
    return output


def global_visibility(
    groups: list[CandidateGroup],
    examples: list[AnchorExample],
    exact_features: dict[tuple[str, str, int], np.ndarray],
    runtime_features: dict[tuple[str, str, int], RuntimeAnchorFeatures],
    config: dict[str, Any],
) -> tuple[
    dict[tuple[str, str, int], tuple[float, float]],
    list[VisibilityModel],
]:
    models = fit_visibility_ensemble(examples, exact_features, config)
    return {
        group.key: _runtime_visibility(models, runtime_features[group.key])
        for group in groups
    }, models


def _crop_frame(
    frame: np.ndarray,
    anchor: AnchorEstimate,
    radius: float,
    output_size: int,
) -> np.ndarray:
    height, width = frame.shape
    crop_width = max(4, round(width * radius * 2))
    crop_height = max(4, round(height * radius * 2))
    crop = cv2.getRectSubPix(
        frame,
        (crop_width, crop_height),
        (anchor.x * (width - 1), anchor.y * (height - 1)),
    )
    crop = cv2.resize(
        crop, (output_size, output_size), interpolation=cv2.INTER_AREA
    ).astype(np.float32) / 255
    return np.clip(
        (crop - float(crop.mean())) / max(float(crop.std()), 0.05),
        -3,
        3,
    ) / 3


def _block_means(image: np.ndarray) -> np.ndarray:
    size = image.shape[0]
    block = size // 4
    trimmed = image[:block * 4, :block * 4]
    return trimmed.reshape(4, block, 4, block).mean(axis=(1, 3)).ravel()


def visual_descriptor_names(config: dict[str, Any]) -> list[str]:
    names = []
    for radius in config["crop_radii"]:
        radius_id = str(radius).replace(".", "_")
        for interval in range(4):
            names.extend(
                f"r{radius_id}_diff_{interval}_block_{index}"
                for index in range(16)
            )
            names.extend(
                f"r{radius_id}_diff_{interval}_dct_{index}"
                for index in range(16)
            )
            names.extend([
                f"r{radius_id}_diff_{interval}_mean",
                f"r{radius_id}_diff_{interval}_std",
            ])
        for frame_name in ("pre", "candidate"):
            names.extend(
                f"r{radius_id}_{frame_name}_block_{index}"
                for index in range(16)
            )
        names.extend(
            f"r{radius_id}_candidate_gradient_{index}" for index in range(16)
        )
    names.extend([
        "runtime_visibility_probability",
        "runtime_visibility_uncertainty",
        "anchor_uncertainty",
        "anchor_reference_disagreement",
    ])
    return names


def _candidate_visual_descriptor(
    record: VisualFrames,
    anchor: AnchorEstimate,
    candidate_time: float,
    visibility: tuple[float, float],
    config: dict[str, Any],
) -> list[float]:
    offsets = [float(value) for value in config["time_offsets_seconds"]]
    indexes = [
        int(np.argmin(np.abs(record.times_seconds - (candidate_time + offset))))
        for offset in offsets
    ]
    values = []
    output_size = int(config["crop_size"])
    for radius in map(float, config["crop_radii"]):
        crops = [
            _crop_frame(record.frames[index], anchor, radius, output_size)
            for index in indexes
        ]
        for left, right in zip(crops[:-1], crops[1:], strict=True):
            difference = np.abs(right - left).astype(np.float32)
            dct = cv2.dct(difference)[:4, :4].ravel()
            values.extend(_block_means(difference).tolist())
            values.extend(np.clip(dct, -4, 4).tolist())
            values.extend([
                float(difference.mean()),
                float(difference.std()),
            ])
        values.extend(_block_means(crops[1]).tolist())
        values.extend(_block_means(crops[2]).tolist())
        gradient_x = cv2.Sobel(crops[2], cv2.CV_32F, 1, 0, ksize=3)
        gradient_y = cv2.Sobel(crops[2], cv2.CV_32F, 0, 1, ksize=3)
        gradient = cv2.magnitude(gradient_x, gradient_y)
        values.extend(_block_means(gradient).tolist())
    values.extend([
        visibility[0],
        visibility[1],
        anchor.uncertainty,
        anchor.reference_disagreement,
    ])
    return values


def build_visual_groups(
    groups: list[CandidateGroup],
    traces_by_key: dict[tuple[str, str, int], SpatialAngleTrace],
    frames: dict[tuple[str, str, int], VisualFrames],
    anchors: dict[tuple[str, str, int], AnchorEstimate],
    visibility: dict[tuple[str, str, int], tuple[float, float]],
    config: dict[str, Any],
) -> list[CandidateGroup]:
    output = []
    for group in groups:
        trace = traces_by_key[group.key]
        descriptors = np.asarray([
            _candidate_visual_descriptor(
                frames[group.key],
                anchors[group.key],
                (int(onset_ms) - trace.range_start_ms) / 1000,
                visibility[group.key],
                config,
            )
            for onset_ms in group.candidate_onsets_ms
        ], dtype=np.float64)
        output.append(CandidateGroup(
            key=group.key,
            game_id=group.game_id,
            game_name=group.game_name,
            actual_snap_ms=group.actual_snap_ms,
            temporal_onset_ms=group.temporal_onset_ms,
            frozen_7e_onset_ms=group.frozen_7e_onset_ms,
            candidate_onsets_ms=group.candidate_onsets_ms,
            features=descriptors,
            absolute_errors_ms=group.absolute_errors_ms,
        ))
    return output


def append_visual_scores(
    fusion_groups: list[CandidateGroup],
    visual_groups: list[CandidateGroup],
    models: list[RandomFeatureRanker],
) -> list[CandidateGroup]:
    visual_by_key = {group.key: group for group in visual_groups}
    output = []
    for group in fusion_groups:
        visual = visual_by_key[group.key]
        member_scores = [
            _model_scores(model, visual.features) for model in models
        ]
        normalized = []
        member_indexes = []
        for scores in member_scores:
            scale = float(scores.std())
            normalized.append(
                (scores - scores.mean()) / (scale if scale > 1e-8 else 1.0)
            )
            member_indexes.append(int(np.argmax(scores)))
        score = np.mean(normalized, axis=0)
        order = np.argsort(score)
        ranks = np.empty_like(score)
        ranks[order] = np.linspace(0, 1, len(score), endpoint=True)
        maximum = float(score.max())
        agreement = np.asarray([
            sum(index == candidate_index for index in member_indexes) / len(models)
            for candidate_index in range(len(score))
        ])
        additions = np.column_stack([
            score,
            ranks,
            maximum - score,
            agreement,
        ])
        output.append(CandidateGroup(
            key=group.key,
            game_id=group.game_id,
            game_name=group.game_name,
            actual_snap_ms=group.actual_snap_ms,
            temporal_onset_ms=group.temporal_onset_ms,
            frozen_7e_onset_ms=group.frozen_7e_onset_ms,
            candidate_onsets_ms=group.candidate_onsets_ms,
            features=np.column_stack([group.features, additions]),
            absolute_errors_ms=group.absolute_errors_ms,
        ))
    return output


def _visual_score_feature_names() -> list[str]:
    return [
        "visual_classifier_score",
        "visual_classifier_rank",
        "visual_classifier_distance_from_max",
        "visual_classifier_member_agreement",
    ]


def nested_visual_fusion_groups(
    base_fusion_groups: list[CandidateGroup],
    visual_groups: list[CandidateGroup],
    outer_holdout_game: str | None,
    visual_model_config: dict[str, Any],
) -> list[CandidateGroup]:
    games = sorted({group.game_id for group in visual_groups})
    fusion_by_key = {group.key: group for group in base_fusion_groups}
    scored = []
    for target_game in games:
        excluded = {target_game}
        if outer_holdout_game is not None:
            excluded.add(outer_holdout_game)
        training = [
            group for group in visual_groups if group.game_id not in excluded
        ]
        target_visual = [
            group for group in visual_groups if group.game_id == target_game
        ]
        target_fusion = [fusion_by_key[group.key] for group in target_visual]
        models = train_ranker_ensemble(training, visual_model_config)
        scored.extend(append_visual_scores(
            target_fusion, target_visual, models
        ))
    scored.sort(key=lambda group: group.key)
    return scored


def _score_predictions(
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


def leave_one_game_out_visual(
    traces: list[SpatialAngleTrace],
    base_groups: list[CandidateGroup],
    examples: list[AnchorExample],
    exact_anchor_features: dict[tuple[str, str, int], np.ndarray],
    runtime_anchor_features: dict[tuple[str, str, int], RuntimeAnchorFeatures],
    motion_grids: dict[tuple[str, str, int], Any],
    visual_frames: dict[tuple[str, str, int], VisualFrames],
    locator_config: dict[str, Any],
    interaction_config: dict[str, Any],
    descriptor_config: dict[str, Any],
    visibility_config: dict[str, Any],
    visual_model_config: dict[str, Any],
    fusion_model_config: dict[str, Any],
    specs_by_angle: dict[tuple[str, str, int], MarkedAngleSpec],
    old_policy: SpatialPolicy,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    traces_by_key = {
        (trace.cohort_id, trace.clip_id, trace.angle): trace for trace in traces
    }
    games = sorted({group.game_id for group in base_groups})
    anchor_cache: dict[frozenset[str], list[LocatorModel]] = {}
    visibility_cache: dict[frozenset[str], list[VisibilityModel]] = {}
    all_predictions = {}
    all_diagnostics = {}
    folds = []
    hard_negative_count = sum(
        int(np.sum(
            group.absolute_errors_ms
            >= float(descriptor_config["hard_negative_minimum_error_ms"])
        ))
        for group in base_groups
    )
    for fold_index, holdout_game in enumerate(games, start=1):
        print(
            f"[{fold_index}/{len(games)}] exchange visual model holding out "
            f"{holdout_game}",
            flush=True,
        )
        anchors = crossfit_anchor_estimates(
            base_groups,
            examples,
            exact_anchor_features,
            runtime_anchor_features,
            locator_config,
            holdout_game,
            anchor_cache,
        )
        visibility = crossfit_visibility(
            base_groups,
            examples,
            exact_anchor_features,
            runtime_anchor_features,
            visibility_config,
            holdout_game,
            visibility_cache,
        )
        spatial_groups = augment_groups(
            base_groups,
            traces_by_key,
            motion_grids,
            anchors,
            interaction_config,
        )
        visual_groups = build_visual_groups(
            base_groups,
            traces_by_key,
            visual_frames,
            anchors,
            visibility,
            descriptor_config,
        )
        fusion_groups = nested_visual_fusion_groups(
            spatial_groups,
            visual_groups,
            holdout_game,
            visual_model_config,
        )
        training = [
            group for group in fusion_groups if group.game_id != holdout_game
        ]
        holdout = [
            group for group in fusion_groups if group.game_id == holdout_game
        ]
        holdout_traces = [traces_by_key[group.key] for group in holdout]
        fusion_models = train_ranker_ensemble(training, fusion_model_config)
        scores, diagnostics = _score_predictions(
            holdout_traces,
            holdout,
            fusion_models,
            fusion_model_config,
            specs_by_angle,
            old_policy,
        )
        for key, item in diagnostics.items():
            all_predictions[key] = int(item["predicted_onset_ms"])
            all_diagnostics[key] = item
        folds.append({
            "holdout_game": holdout_game,
            "training_angles": len(training),
            "holdout_angles": len(holdout),
            "holdout_overall": scores["overall"],
        })
        print(
            f"    {scores['overall']['within_500_ms']}/"
            f"{scores['overall']['angle_judgments']} within 500 ms",
            flush=True,
        )
    scores = _prediction_scores(
        traces, all_predictions, specs_by_angle, old_policy
    )
    _apply_ranker_fallback_counts(scores, all_diagnostics, specs_by_angle)
    return scores, folds, {
        "hard_negative_candidates": hard_negative_count,
        "candidate_rows": sum(len(group.features) for group in base_groups),
    }


def _acceptance(
    protocol: dict[str, Any],
    transfer_decision: dict[str, Any],
    visual: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    config = protocol["acceptance"]
    improvement = (
        float(visual["overall"]["within_500_ms_share"])
        - float(baseline["overall"]["within_500_ms_share"])
    )
    paired_delta = (
        float(visual["overall"]["both_angles_within_500_ms_share"] or 0)
        - float(baseline["overall"]["both_angles_within_500_ms_share"] or 0)
    )
    game_deltas = {
        game_id: round(
            float(visual["by_game"][game_id]["within_500_ms_share"])
            - float(baseline["by_game"][game_id]["within_500_ms_share"]),
            6,
        )
        for game_id in visual["by_game"]
    }
    tolerance = float(config["game_regression_tolerance_share"])
    regressions = sorted(
        game_id for game_id, delta in game_deltas.items() if delta < -tolerance
    )
    checks = {
        "transfer_gate": (
            bool(transfer_decision["passed"])
            if config["require_transfer_gate"] else True
        ),
        "within_500_ms_improvement": (
            improvement >= float(config[
                "minimum_within_500_ms_share_improvement_vs_spatial"
            ])
        ),
        "median_absolute_error_not_worse": (
            float(visual["overall"]["median_absolute_error_ms"])
            <= float(baseline["overall"]["median_absolute_error_ms"])
            if config["require_median_absolute_error_not_worse"] else True
        ),
        "paired_success_not_materially_worse": (
            paired_delta >= float(config[
                "minimum_paired_success_share_change_vs_spatial"
            ])
        ),
        "game_regressions_bounded": (
            len(regressions)
            <= int(config["maximum_games_regressing_beyond_tolerance"])
        ),
    }
    passed = all(checks.values())
    return {
        "decision": (
            "exchange_visual_ranker_supported"
            if passed else "exchange_visual_ranker_not_supported"
        ),
        "passed": passed,
        "checks": checks,
        "within_500_ms_share_change": round(improvement, 6),
        "paired_success_share_change": round(paired_delta, 6),
        "game_within_500_ms_share_changes": game_deltas,
        "games_regressing_beyond_tolerance": regressions,
    }


def save_visibility_bundle(
    path: Path,
    models: list[VisibilityModel],
    protocol: dict[str, Any],
    approved: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": "1.0",
        "iteration_id": protocol["iteration_id"],
        "model": protocol["visibility_model"],
        "approved_for_sealed_validation": approved,
    }
    np.savez_compressed(
        path,
        metadata_json=np.asarray(json.dumps(metadata, sort_keys=True)),
        seeds=np.asarray([model.seed for model in models], dtype=np.int64),
        priors=np.asarray([model.prior for model in models]),
        means=np.stack([model.mean for model in models]),
        scales=np.stack([model.scale for model in models]),
        projections=np.stack([model.projection for model in models]),
        hidden_biases=np.stack([model.hidden_bias for model in models]),
        coefficients=np.stack([model.coefficients for model in models]),
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def _percent(value: Any) -> str:
    return f"{float(value) * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    baseline = report["spatial_baseline"]["overall"]
    visual = report["lofo_exchange_visual"]["overall"]
    decision = report["acceptance"]
    lines = [
        "# Multi-game Exchange-area Visual Classifier",
        "",
        f"**Decision:** `{decision['decision']}`",
        "",
        "Visible and occluded exchange-area labels, hard temporal negatives, "
        "visual scoring, and final fusion were all evaluated with nested "
        "game-level exclusions. Validation and final holdout data were not loaded.",
        "",
        "## Unseen-game comparison",
        "",
        "| Method | Within 500 ms | Median abs error | P90 abs error | Both paired angles |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| Spatial interaction | {baseline['within_500_ms']}/"
        f"{baseline['angle_judgments']} "
        f"({_percent(baseline['within_500_ms_share'])}) | "
        f"{baseline['median_absolute_error_ms']:.1f} ms | "
        f"{baseline['p90_absolute_error_ms']:.1f} ms | "
        f"{_percent(baseline['both_angles_within_500_ms_share'])} |",
        f"| Exchange visual | {visual['within_500_ms']}/"
        f"{visual['angle_judgments']} "
        f"({_percent(visual['within_500_ms_share'])}) | "
        f"{visual['median_absolute_error_ms']:.1f} ms | "
        f"{visual['p90_absolute_error_ms']:.1f} ms | "
        f"{_percent(visual['both_angles_within_500_ms_share'])} |",
        "",
        "## Held-out games",
        "",
        "| Game | Within 500 ms | Median abs error |",
        "| --- | ---: | ---: |",
    ]
    for fold in report["folds"]:
        metrics = fold["holdout_overall"]
        lines.append(
            f"| {fold['holdout_game']} | {metrics['within_500_ms']}/"
            f"{metrics['angle_judgments']} "
            f"({_percent(metrics['within_500_ms_share'])}) | "
            f"{metrics['median_absolute_error_ms']:.1f} ms |"
        )
    lines.extend(["", "## Acceptance checks", ""])
    for name, passed in decision["checks"].items():
        lines.append(f"- `{name}`: {passed}")
    lines.extend(["", "## Next iteration", ""])
    if decision["passed"]:
        lines.append(
            "Freeze the locator, visibility, visual classifier, and fusion "
            "artifacts, then run them once on sealed Miami-Ohio State validation."
        )
    else:
        lines.append(
            "Keep validation sealed. Add independent development games or replace "
            "the handcrafted crop descriptor with a stronger pretrained visual "
            "embedding before another gate attempt."
        )
    return "\n".join(lines) + "\n"


def run_exchange_visual(
    protocol_path: Path,
    report_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    protocol_path = protocol_path.resolve()
    repository_root = protocol_path.parents[1]
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    spatial_protocol_path = _resolve(
        protocol["spatial_protocol"], repository_root
    )
    spatial_protocol = json.loads(
        spatial_protocol_path.read_text(encoding="utf-8")
    )
    spatial_report_path = _resolve(protocol["spatial_report"], repository_root)
    spatial_report = json.loads(
        spatial_report_path.read_text(encoding="utf-8")
    )
    transfer_path = _resolve(
        spatial_protocol["transfer_protocol"], repository_root
    )
    transfer = json.loads(transfer_path.read_text(encoding="utf-8"))
    locator_protocol_path = _resolve(
        spatial_protocol["anchor_locator_protocol"], repository_root
    )
    locator_protocol = json.loads(
        locator_protocol_path.read_text(encoding="utf-8")
    )
    specs, counts = load_marked_angle_specs(transfer, repository_root)
    specs_by_angle = {
        (spec.cohort_id, spec.clip_id, spec.angle): spec for spec in specs
    }
    model_path = _resolve(spatial_protocol["detector_model"], repository_root)
    traces, measurement = build_or_load_traces(
        specs,
        model_path,
        _resolve(spatial_protocol["detected_player_cache"], repository_root),
    )
    traces_by_key = {
        (trace.cohort_id, trace.clip_id, trace.angle): trace for trace in traces
    }
    old_policy = frozen_policy(transfer)
    contexts = load_angle_contexts(transfer, repository_root, specs)
    base_groups, base_feature_names, candidate_diagnostics = build_candidate_groups(
        traces,
        specs_by_angle,
        contexts,
        old_policy,
        spatial_protocol["candidate_generation"],
    )
    examples, statuses, _ = load_anchor_examples(locator_protocol, repository_root)
    exact_features, _ = build_or_load_exact_anchor_features(
        examples,
        model_path,
        _resolve(locator_protocol["feature_cache"], repository_root),
        locator_protocol["feature_extractor"],
    )
    runtime_features, runtime_measurement = build_or_load_runtime_anchor_features(
        specs,
        traces,
        old_policy,
        model_path,
        _resolve(
            spatial_protocol["runtime_anchor_feature_cache"], repository_root
        ),
        locator_protocol["feature_extractor"],
    )
    motion_grids, motion_measurement = build_or_load_motion_grids(
        specs,
        _resolve(
            spatial_protocol["motion_grid_cache_directory"], repository_root
        ),
        spatial_protocol["motion_grid"],
    )
    visual_frames, visual_measurement = build_or_load_visual_frames(
        specs,
        _resolve(protocol["visual_frame_cache_directory"], repository_root),
        protocol["visual_frames"],
    )
    visual_scores, folds, training_diagnostics = leave_one_game_out_visual(
        traces,
        base_groups,
        examples,
        exact_features,
        runtime_features,
        motion_grids,
        visual_frames,
        locator_protocol["locator_model"],
        spatial_protocol["interaction_features"],
        protocol["visual_descriptor"],
        protocol["visibility_model"],
        protocol["visual_model"],
        protocol["fusion_model"],
        specs_by_angle,
        old_policy,
    )
    transfer_decision = _transfer_decision(transfer, visual_scores)
    baseline = spatial_report["lofo_spatial_interaction"]
    acceptance = _acceptance(
        protocol, transfer_decision, visual_scores, baseline
    )

    global_anchors, _ = global_anchor_estimates(
        base_groups,
        examples,
        exact_features,
        runtime_features,
        locator_protocol["locator_model"],
    )
    global_visibility_values, visibility_models = global_visibility(
        base_groups,
        examples,
        exact_features,
        runtime_features,
        protocol["visibility_model"],
    )
    global_spatial = augment_groups(
        base_groups,
        traces_by_key,
        motion_grids,
        global_anchors,
        spatial_protocol["interaction_features"],
    )
    global_visual = build_visual_groups(
        base_groups,
        traces_by_key,
        visual_frames,
        global_anchors,
        global_visibility_values,
        protocol["visual_descriptor"],
    )
    visual_models = train_ranker_ensemble(
        global_visual, protocol["visual_model"]
    )
    global_fusion = append_visual_scores(
        global_spatial, global_visual, visual_models
    )
    fusion_models = train_ranker_ensemble(
        global_fusion, protocol["fusion_model"]
    )
    policies = sorted(
        detected_player_policy_grid(), key=lambda policy: policy.policy_id
    )
    grid_sha256 = hashlib.sha256(
        "\n".join(policy.policy_id for policy in policies).encode("utf-8")
    ).hexdigest()
    approved = bool(acceptance["passed"])
    visual_artifact = _resolve(
        protocol["visual_classifier_artifact"], repository_root
    )
    fusion_artifact = _resolve(
        protocol["fusion_ranker_artifact"], repository_root
    )
    visibility_artifact = _resolve(
        protocol["visibility_artifact"], repository_root
    )
    visual_feature_names = visual_descriptor_names(
        protocol["visual_descriptor"]
    )
    fusion_feature_names = (
        base_feature_names
        + interaction_feature_names(spatial_protocol["interaction_features"])
        + _visual_score_feature_names()
    )
    visual_bundle_protocol = {
        **protocol,
        "candidate_generation": spatial_protocol["candidate_generation"],
        "model": protocol["visual_model"],
    }
    fusion_bundle_protocol = {
        **protocol,
        "candidate_generation": spatial_protocol["candidate_generation"],
        "model": protocol["fusion_model"],
    }
    save_model_bundle(
        visual_artifact,
        visual_models,
        visual_feature_names,
        visual_bundle_protocol,
        grid_sha256,
        approved,
    )
    save_model_bundle(
        fusion_artifact,
        fusion_models,
        fusion_feature_names,
        fusion_bundle_protocol,
        grid_sha256,
        approved,
    )
    save_visibility_bundle(
        visibility_artifact, visibility_models, protocol, approved
    )
    report = {
        "schema_version": "1.0",
        "iteration_id": protocol["iteration_id"],
        "dataset": counts,
        "anchor_labels": statuses,
        "candidate_generation": candidate_diagnostics,
        "training_diagnostics": training_diagnostics,
        "measurement": measurement,
        "runtime_anchor_measurement": runtime_measurement,
        "motion_grid_measurement": motion_measurement,
        "visual_frame_measurement": visual_measurement,
        "spatial_baseline": baseline,
        "lofo_exchange_visual": visual_scores,
        "folds": folds,
        "transfer_decision": transfer_decision,
        "acceptance": acceptance,
        "artifacts": {
            "visibility": _fingerprint(visibility_artifact),
            "visual_classifier": _fingerprint(visual_artifact),
            "fusion_ranker": _fingerprint(fusion_artifact),
            "status": (
                "approved_for_sealed_validation"
                if approved else "experimental_not_approved"
            ),
        },
        "inputs": {
            "protocol": _fingerprint(protocol_path),
            "spatial_protocol": _fingerprint(spatial_protocol_path),
            "spatial_report": _fingerprint(spatial_report_path),
            "transfer_protocol": _fingerprint(transfer_path),
            "anchor_locator_protocol": _fingerprint(locator_protocol_path),
        },
        "sealed_data": protocol["sealed_data_policy"],
    }
    report_path = _resolve(report_path, repository_root)
    markdown_path = _resolve(markdown_path, repository_root)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(
        report, indent=2, sort_keys=True, default=_json_default
    ) + "\n", encoding="utf-8")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    old = baseline["overall"]
    new = visual_scores["overall"]
    print(
        f"LOFO exchange visual: {new['within_500_ms']}/"
        f"{new['angle_judgments']} ({new['within_500_ms_share'] * 100:.1f}%), "
        f"versus {old['within_500_ms']}/{old['angle_judgments']} "
        f"({old['within_500_ms_share'] * 100:.1f}%) spatial baseline.",
        flush=True,
    )
    print(
        f"Release gate: {transfer_decision['passed']}; games passing "
        f"{transfer_decision['game_consistency']['games_passing']}/"
        f"{transfer_decision['game_consistency']['games_total']}",
        flush=True,
    )
    print(f"Decision: {acceptance['decision']}", flush=True)
    return report
