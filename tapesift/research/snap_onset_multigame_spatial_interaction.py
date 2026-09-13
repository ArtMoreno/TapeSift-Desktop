"""Center/QB-local motion features for multi-game snap candidate ranking."""

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
    _predict_one,
    build_or_load_features as build_or_load_exact_anchor_features,
    extract_anchor_features,
    fit_locator_ensemble,
    load_anchor_examples,
)
from tapesift.research.snap_onset_detected_players import (
    configure_inference,
    detected_player_policy_grid,
)
from tapesift.research.snap_onset_multigame_candidate_ranker import (
    CandidateGroup,
    _apply_ranker_fallback_counts,
    _ranker_scores,
    build_candidate_groups,
    load_angle_contexts,
    predict_groups,
    save_model_bundle,
    train_ranker_ensemble,
)
from tapesift.research.snap_onset_multigame_detected_players import (
    MarkedAngleSpec,
    _grouped_scores,
    _transfer_decision,
    build_or_load_traces,
    frozen_policy,
    load_marked_angle_specs,
)
from tapesift.research.snap_onset_multigame_policy_replacement import (
    _prediction_scores,
)
from tapesift.research.snap_onset_refiner import sha256_file
from tapesift.research.snap_onset_spatial_refiner import (
    SpatialAngleTrace,
    SpatialPolicy,
    score_spatial_policy,
)


RUNTIME_ANCHOR_FEATURE_VERSION = "runtime-anchor-layout-v1"
MOTION_GRID_VERSION = "center-interaction-grid-v1"


@dataclass(frozen=True)
class RuntimeAnchorFeatures:
    temporal: np.ndarray
    frozen_7e: np.ndarray
    temporal_timestamp_ms: int
    frozen_7e_timestamp_ms: int


@dataclass(frozen=True)
class AnchorEstimate:
    x: float
    y: float
    uncertainty: float
    reference_disagreement: float


@dataclass(frozen=True)
class MotionGrid:
    times_seconds: np.ndarray
    difference: np.ndarray
    flow_x: np.ndarray
    flow_y: np.ndarray


def _resolve(value: str | Path, repository_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repository_root / path


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


def _source_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _runtime_signature(
    spec: MarkedAngleSpec,
    temporal_timestamp_ms: int,
    frozen_timestamp_ms: int,
    model_sha256: str,
    extractor_config: dict[str, Any],
) -> str:
    payload = {
        "version": RUNTIME_ANCHOR_FEATURE_VERSION,
        "source": _source_signature(Path(spec.source_video_path)),
        "angle": spec.angle,
        "temporal_timestamp_ms": temporal_timestamp_ms,
        "frozen_timestamp_ms": frozen_timestamp_ms,
        "model_sha256": model_sha256,
        "extractor": extractor_config,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def build_or_load_runtime_anchor_features(
    specs: list[MarkedAngleSpec],
    traces: list[SpatialAngleTrace],
    old_policy: SpatialPolicy,
    model_path: Path,
    cache_path: Path,
    extractor_config: dict[str, Any],
) -> tuple[dict[tuple[str, str, int], RuntimeAnchorFeatures], dict[str, Any]]:
    frozen_result = score_spatial_policy(traces, old_policy)
    frozen_onsets = {
        (str(row["cohort_id"]), str(row["clip_id"]), int(row["angle"])):
        int(row["candidate_onset_ms"])
        for row in frozen_result["rows"]
    }
    model_sha256 = sha256_file(model_path)
    signatures = {}
    timestamps = {}
    for spec in specs:
        key = (spec.cohort_id, spec.clip_id, spec.angle)
        temporal_timestamp = int(np.clip(
            spec.temporal_v21_onset_ms - 250,
            spec.range_start_ms,
            spec.range_end_ms,
        ))
        frozen_timestamp = int(np.clip(
            frozen_onsets[key] - 250,
            spec.range_start_ms,
            spec.range_end_ms,
        ))
        timestamps[key] = (temporal_timestamp, frozen_timestamp)
        signatures[key] = _runtime_signature(
            spec,
            temporal_timestamp,
            frozen_timestamp,
            model_sha256,
            extractor_config,
        )
    cache = {}
    if cache_path.exists():
        cache = {
            str(row["measurement_signature"]): row
            for row in _read_jsonl(cache_path)
        }
    hits = sum(signature in cache for signature in signatures.values())
    print(
        f"Runtime anchor frames: {hits} cached, {len(specs) - hits} to measure.",
        flush=True,
    )
    model = None
    append_handle = None
    captures: dict[str, Any] = {}
    if hits != len(specs):
        from ultralytics import YOLO

        configure_inference()
        model = YOLO(str(model_path))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        append_handle = cache_path.open("a", encoding="utf-8")
    output = {}
    try:
        for index, spec in enumerate(specs, start=1):
            key = (spec.cohort_id, spec.clip_id, spec.angle)
            signature = signatures[key]
            record = cache.get(signature)
            if record is None:
                capture = captures.get(spec.source_video_path)
                if capture is None:
                    capture = cv2.VideoCapture(spec.source_video_path)
                    if not capture.isOpened():
                        raise FileNotFoundError(spec.source_video_path)
                    captures[spec.source_video_path] = capture
                vectors = []
                diagnostics = []
                for timestamp_ms in timestamps[key]:
                    capture.set(cv2.CAP_PROP_POS_MSEC, timestamp_ms)
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        raise RuntimeError(
                            f"Could not decode {spec.source_video_path} "
                            f"at {timestamp_ms} ms"
                        )
                    vector, diagnostic = extract_anchor_features(
                        model, frame, spec.angle, extractor_config
                    )
                    vectors.append(vector)
                    diagnostics.append(diagnostic)
                record = {
                    "schema_version": "1.0",
                    "measurement_signature": signature,
                    "key": list(key),
                    "temporal_timestamp_ms": timestamps[key][0],
                    "frozen_7e_timestamp_ms": timestamps[key][1],
                    "temporal_features": vectors[0].tolist(),
                    "frozen_7e_features": vectors[1].tolist(),
                    "diagnostics": diagnostics,
                }
                assert append_handle is not None
                append_handle.write(json.dumps(
                    record, sort_keys=True, separators=(",", ":")
                ) + "\n")
                append_handle.flush()
                cache[signature] = record
                if index == 1 or index % 20 == 0 or index == len(specs):
                    print(
                        f"[{index}/{len(specs)}] measured runtime anchor frames",
                        flush=True,
                    )
            output[key] = RuntimeAnchorFeatures(
                temporal=np.asarray(record["temporal_features"], dtype=np.float64),
                frozen_7e=np.asarray(record["frozen_7e_features"], dtype=np.float64),
                temporal_timestamp_ms=int(record["temporal_timestamp_ms"]),
                frozen_7e_timestamp_ms=int(record["frozen_7e_timestamp_ms"]),
            )
    finally:
        if append_handle is not None:
            append_handle.close()
        for capture in captures.values():
            capture.release()
    return output, {
        "version": RUNTIME_ANCHOR_FEATURE_VERSION,
        "model_sha256": model_sha256,
        "angles": len(output),
    }


def _motion_signature(
    spec: MarkedAngleSpec,
    config: dict[str, Any],
) -> str:
    payload = {
        "version": MOTION_GRID_VERSION,
        "source": _source_signature(Path(spec.source_video_path)),
        "range_start_ms": spec.range_start_ms,
        "range_end_ms": spec.range_end_ms,
        "config": config,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _grid_means(values: np.ndarray, grid_height: int, grid_width: int) -> np.ndarray:
    height, width = values.shape
    cell_height = height // grid_height
    cell_width = width // grid_width
    trimmed = values[:cell_height * grid_height, :cell_width * grid_width]
    return trimmed.reshape(
        grid_height, cell_height, grid_width, cell_width
    ).mean(axis=(1, 3))


def measure_motion_grid(
    spec: MarkedAngleSpec,
    config: dict[str, Any],
) -> MotionGrid:
    capture = cv2.VideoCapture(spec.source_video_path)
    if not capture.isOpened():
        raise FileNotFoundError(spec.source_video_path)
    source_fps = float(capture.get(cv2.CAP_PROP_FPS))
    if source_fps <= 0:
        source_fps = 30.0
    start_frame = max(0, int(np.floor(spec.range_start_ms * source_fps / 1000)))
    end_frame = int(np.ceil(spec.range_end_ms * source_fps / 1000))
    sample_step = source_fps / float(config["sample_fps"])
    sample_targets = []
    value = float(start_frame)
    while value <= end_frame:
        sample_targets.append(int(round(value)))
        value += sample_step
    sample_targets = sorted(set(sample_targets))
    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    target_position = 0
    frame_index = start_frame
    previous = None
    times = []
    differences = []
    flow_x_values = []
    flow_y_values = []
    try:
        while target_position < len(sample_targets) and frame_index <= end_frame:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            target_frame = sample_targets[target_position]
            if frame_index >= target_frame:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(
                    gray,
                    (int(config["sample_width"]), int(config["sample_height"])),
                    interpolation=cv2.INTER_AREA,
                )
                if previous is None:
                    difference = np.zeros_like(gray, dtype=np.float32)
                    flow_x = np.zeros_like(gray, dtype=np.float32)
                    flow_y = np.zeros_like(gray, dtype=np.float32)
                else:
                    difference = cv2.absdiff(previous, gray).astype(np.float32) / 255
                    flow = cv2.calcOpticalFlowFarneback(
                        previous,
                        gray,
                        None,
                        0.5,
                        int(config["farneback_levels"]),
                        int(config["farneback_window"]),
                        2,
                        5,
                        1.1,
                        0,
                    )
                    flow_x = flow[..., 0] - np.median(flow[..., 0])
                    flow_y = flow[..., 1] - np.median(flow[..., 1])
                grid_height = int(config["grid_height"])
                grid_width = int(config["grid_width"])
                differences.append(_grid_means(
                    difference, grid_height, grid_width
                ))
                flow_x_values.append(_grid_means(
                    flow_x, grid_height, grid_width
                ))
                flow_y_values.append(_grid_means(
                    flow_y, grid_height, grid_width
                ))
                times.append(
                    frame_index / source_fps
                    - spec.range_start_ms / 1000
                )
                previous = gray
                target_position += 1
            frame_index += 1
    finally:
        capture.release()
    if len(times) < 16:
        raise RuntimeError(f"Only {len(times)} motion samples for {spec.key}")
    return MotionGrid(
        times_seconds=np.asarray(times, dtype=np.float32),
        difference=np.asarray(differences, dtype=np.float32),
        flow_x=np.asarray(flow_x_values, dtype=np.float32),
        flow_y=np.asarray(flow_y_values, dtype=np.float32),
    )


def build_or_load_motion_grids(
    specs: list[MarkedAngleSpec],
    cache_directory: Path,
    config: dict[str, Any],
) -> tuple[dict[tuple[str, str, int], MotionGrid], dict[str, Any]]:
    cache_directory.mkdir(parents=True, exist_ok=True)
    output = {}
    hits = 0
    for index, spec in enumerate(specs, start=1):
        key = (spec.cohort_id, spec.clip_id, spec.angle)
        signature = _motion_signature(spec, config)
        cache_name = hashlib.sha256(
            "|".join(map(str, key)).encode("utf-8")
        ).hexdigest()[:24] + ".npz"
        cache_path = cache_directory / cache_name
        grid = None
        if cache_path.exists():
            with np.load(cache_path) as payload:
                if str(payload["signature"].item()) == signature:
                    grid = MotionGrid(
                        times_seconds=payload["times_seconds"].copy(),
                        difference=payload["difference"].copy(),
                        flow_x=payload["flow_x"].copy(),
                        flow_y=payload["flow_y"].copy(),
                    )
                    hits += 1
        if grid is None:
            grid = measure_motion_grid(spec, config)
            np.savez_compressed(
                cache_path,
                signature=np.asarray(signature),
                times_seconds=grid.times_seconds,
                difference=grid.difference,
                flow_x=grid.flow_x,
                flow_y=grid.flow_y,
            )
            if index == 1 or index % 10 == 0 or index == len(specs):
                print(
                    f"[{index}/{len(specs)}] measured local motion grids",
                    flush=True,
                )
        output[key] = grid
    return output, {
        "version": MOTION_GRID_VERSION,
        "angles": len(output),
        "cache_hits": hits,
        "configuration": config,
    }


def _runtime_anchor_estimate(
    models: list[LocatorModel],
    features: RuntimeAnchorFeatures,
    angle: int,
) -> AnchorEstimate:
    temporal, temporal_uncertainty = _predict_one(
        models, features.temporal, angle
    )
    frozen, frozen_uncertainty = _predict_one(
        models, features.frozen_7e, angle
    )
    disagreement = float(np.linalg.norm(temporal - frozen))
    if disagreement > 0.25:
        combined = temporal if temporal_uncertainty <= frozen_uncertainty else frozen
    else:
        temporal_weight = 1 / max(temporal_uncertainty + 0.02, 0.02)
        frozen_weight = 1 / max(frozen_uncertainty + 0.02, 0.02)
        combined = (
            temporal * temporal_weight + frozen * frozen_weight
        ) / (temporal_weight + frozen_weight)
    return AnchorEstimate(
        x=float(combined[0]),
        y=float(combined[1]),
        uncertainty=round(
            (temporal_uncertainty + frozen_uncertainty) / 2, 6
        ),
        reference_disagreement=round(disagreement, 6),
    )


def crossfit_anchor_estimates(
    groups: list[CandidateGroup],
    examples: list[AnchorExample],
    exact_features: dict[tuple[str, str, int], np.ndarray],
    runtime_features: dict[tuple[str, str, int], RuntimeAnchorFeatures],
    locator_config: dict[str, Any],
    outer_holdout_game: str | None,
    model_cache: dict[frozenset[str], list[LocatorModel]],
) -> dict[tuple[str, str, int], AnchorEstimate]:
    estimates = {}
    games = sorted({group.game_id for group in groups})
    for target_game in games:
        excluded = {target_game}
        if outer_holdout_game is not None:
            excluded.add(outer_holdout_game)
        frozen_excluded = frozenset(excluded)
        models = model_cache.get(frozen_excluded)
        if models is None:
            training_examples = [
                example for example in examples
                if example.status == "visible" and example.game_id not in excluded
            ]
            models = fit_locator_ensemble(
                training_examples, exact_features, locator_config
            )
            model_cache[frozen_excluded] = models
        for group in groups:
            if group.game_id != target_game:
                continue
            estimates[group.key] = _runtime_anchor_estimate(
                models, runtime_features[group.key], group.key[2]
            )
    return estimates


def global_anchor_estimates(
    groups: list[CandidateGroup],
    examples: list[AnchorExample],
    exact_features: dict[tuple[str, str, int], np.ndarray],
    runtime_features: dict[tuple[str, str, int], RuntimeAnchorFeatures],
    locator_config: dict[str, Any],
) -> tuple[dict[tuple[str, str, int], AnchorEstimate], list[LocatorModel]]:
    models = fit_locator_ensemble(
        [example for example in examples if example.status == "visible"],
        exact_features,
        locator_config,
    )
    return {
        group.key: _runtime_anchor_estimate(
            models, runtime_features[group.key], group.key[2]
        )
        for group in groups
    }, models


def _series_features(
    series: np.ndarray,
    times: np.ndarray,
    candidate_time: float,
    config: dict[str, Any],
) -> list[float]:
    pre_start, pre_end = map(float, config["pre_window_seconds"])
    early_start, early_end = map(float, config["early_window_seconds"])
    late_start, late_end = map(float, config["late_window_seconds"])

    def median(start: float, end: float) -> float:
        values = series[
            (times >= candidate_time + start)
            & (times < candidate_time + end)
        ]
        return float(np.median(values)) if len(values) else 0.0

    pre = median(pre_start, pre_end)
    early = median(early_start, early_end)
    late = median(late_start, late_end)
    near_values = series[
        (times >= candidate_time - 0.125)
        & (times <= candidate_time + 0.375)
    ]
    near_peak = float(np.max(near_values)) if len(near_values) else 0.0
    scale = float(np.percentile(series, 90) - np.percentile(series, 10))
    scale = max(scale, 1e-5)
    nearest = int(np.argmin(np.abs(times - candidate_time)))
    percentile = float(np.mean(series <= series[nearest]))
    return [
        float(np.clip(pre / scale, -5, 5)),
        float(np.clip((early - pre) / scale, -5, 5)),
        float(np.clip((late - pre) / scale, -5, 5)),
        float(np.clip((near_peak - pre) / scale, -5, 5)),
        percentile,
        float(np.clip((min(early, late) - pre) / scale, -5, 5)),
    ]


def interaction_feature_names(config: dict[str, Any]) -> list[str]:
    names = []
    statistic_names = (
        "pre", "early_delta", "late_delta", "near_peak_delta", "percentile",
        "sustained_delta",
    )
    for radius in config["local_radii"]:
        radius_id = str(radius).replace(".", "_")
        for signal in ("difference", "flow_magnitude", "radial_flow"):
            names.extend(
                f"anchor_r{radius_id}_{signal}_{statistic}"
                for statistic in statistic_names
            )
    for signal in ("global_difference", "global_flow_magnitude"):
        names.extend(f"{signal}_{statistic}" for statistic in statistic_names)
    for radius in config["local_radii"]:
        radius_id = str(radius).replace(".", "_")
        names.extend([
            f"anchor_r{radius_id}_difference_concentration",
            f"anchor_r{radius_id}_flow_concentration",
        ])
    names.extend([
        "anchor_x",
        "anchor_y",
        "anchor_uncertainty",
        "anchor_reference_disagreement",
        "anchor_edge_distance",
    ])
    return names


def _candidate_interaction_features(
    grid: MotionGrid,
    estimate: AnchorEstimate,
    candidate_time: float,
    config: dict[str, Any],
) -> list[float]:
    grid_height, grid_width = grid.difference.shape[1:]
    cell_x = (np.arange(grid_width) + 0.5) / grid_width
    cell_y = (np.arange(grid_height) + 0.5) / grid_height
    xx, yy = np.meshgrid(cell_x, cell_y)
    dx = xx - estimate.x
    dy = yy - estimate.y
    distance = np.sqrt(dx * dx + dy * dy)
    flow_magnitude = np.sqrt(grid.flow_x ** 2 + grid.flow_y ** 2)
    radial = (
        grid.flow_x * dx[None, :, :] + grid.flow_y * dy[None, :, :]
    ) / np.maximum(distance[None, :, :], 0.02)
    global_difference = grid.difference.mean(axis=(1, 2))
    global_flow = flow_magnitude.mean(axis=(1, 2))
    values = []
    local_summaries = []
    for radius in map(float, config["local_radii"]):
        weights = np.exp(-0.5 * (distance / radius) ** 2)
        weights /= max(float(weights.sum()), 1e-6)
        local_difference = np.sum(
            grid.difference * weights[None, :, :], axis=(1, 2)
        )
        local_flow = np.sum(
            flow_magnitude * weights[None, :, :], axis=(1, 2)
        )
        local_radial = np.sum(
            radial * weights[None, :, :], axis=(1, 2)
        )
        values.extend(_series_features(
            local_difference, grid.times_seconds, candidate_time, config
        ))
        values.extend(_series_features(
            local_flow, grid.times_seconds, candidate_time, config
        ))
        values.extend(_series_features(
            local_radial, grid.times_seconds, candidate_time, config
        ))
        local_summaries.append((local_difference, local_flow))
    values.extend(_series_features(
        global_difference, grid.times_seconds, candidate_time, config
    ))
    values.extend(_series_features(
        global_flow, grid.times_seconds, candidate_time, config
    ))
    early_mask = (
        (grid.times_seconds >= candidate_time)
        & (grid.times_seconds < candidate_time + 0.5)
    )
    for local_difference, local_flow in local_summaries:
        if np.any(early_mask):
            difference_concentration = float(
                np.median(local_difference[early_mask])
                / max(np.median(global_difference[early_mask]), 1e-5)
            )
            flow_concentration = float(
                np.median(local_flow[early_mask])
                / max(np.median(global_flow[early_mask]), 1e-5)
            )
        else:
            difference_concentration = flow_concentration = 0.0
        values.extend([
            float(np.clip(difference_concentration, 0, 10)),
            float(np.clip(flow_concentration, 0, 10)),
        ])
    edge_distance = min(
        estimate.x, 1 - estimate.x, estimate.y, 1 - estimate.y
    )
    values.extend([
        estimate.x,
        estimate.y,
        float(np.clip(estimate.uncertainty, 0, 1)),
        float(np.clip(estimate.reference_disagreement, 0, 1)),
        float(np.clip(edge_distance, 0, 0.5)),
    ])
    return values


def augment_groups(
    groups: list[CandidateGroup],
    traces_by_key: dict[tuple[str, str, int], SpatialAngleTrace],
    motion_grids: dict[tuple[str, str, int], MotionGrid],
    estimates: dict[tuple[str, str, int], AnchorEstimate],
    config: dict[str, Any],
) -> list[CandidateGroup]:
    augmented = []
    for group in groups:
        trace = traces_by_key[group.key]
        grid = motion_grids[group.key]
        estimate = estimates[group.key]
        interaction = np.asarray([
            _candidate_interaction_features(
                grid,
                estimate,
                (int(onset_ms) - trace.range_start_ms) / 1000,
                config,
            )
            for onset_ms in group.candidate_onsets_ms
        ], dtype=np.float64)
        augmented.append(CandidateGroup(
            key=group.key,
            game_id=group.game_id,
            game_name=group.game_name,
            actual_snap_ms=group.actual_snap_ms,
            temporal_onset_ms=group.temporal_onset_ms,
            frozen_7e_onset_ms=group.frozen_7e_onset_ms,
            candidate_onsets_ms=group.candidate_onsets_ms,
            features=np.column_stack([group.features, interaction]),
            absolute_errors_ms=group.absolute_errors_ms,
        ))
    return augmented


def leave_one_game_out_spatial(
    traces: list[SpatialAngleTrace],
    base_groups: list[CandidateGroup],
    examples: list[AnchorExample],
    exact_anchor_features: dict[tuple[str, str, int], np.ndarray],
    runtime_anchor_features: dict[tuple[str, str, int], RuntimeAnchorFeatures],
    motion_grids: dict[tuple[str, str, int], MotionGrid],
    locator_config: dict[str, Any],
    interaction_config: dict[str, Any],
    ranker_config: dict[str, Any],
    specs_by_angle: dict[tuple[str, str, int], MarkedAngleSpec],
    old_policy: SpatialPolicy,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    traces_by_key = {
        (trace.cohort_id, trace.clip_id, trace.angle): trace for trace in traces
    }
    games = sorted({group.game_id for group in base_groups})
    all_predictions = {}
    all_diagnostics = {}
    folds = []
    locator_model_cache: dict[frozenset[str], list[LocatorModel]] = {}
    for fold_index, holdout_game in enumerate(games, start=1):
        print(
            f"[{fold_index}/{len(games)}] spatial ranker holding out {holdout_game}",
            flush=True,
        )
        estimates = crossfit_anchor_estimates(
            base_groups,
            examples,
            exact_anchor_features,
            runtime_anchor_features,
            locator_config,
            holdout_game,
            locator_model_cache,
        )
        augmented = augment_groups(
            base_groups,
            traces_by_key,
            motion_grids,
            estimates,
            interaction_config,
        )
        training_groups = [
            group for group in augmented if group.game_id != holdout_game
        ]
        holdout_groups = [
            group for group in augmented if group.game_id == holdout_game
        ]
        holdout_traces = [traces_by_key[group.key] for group in holdout_groups]
        models = train_ranker_ensemble(training_groups, ranker_config)
        predictions, diagnostics = predict_groups(
            holdout_groups, models, ranker_config
        )
        scores = _prediction_scores(
            holdout_traces, predictions, specs_by_angle, old_policy
        )
        _apply_ranker_fallback_counts(scores, diagnostics, specs_by_angle)
        for key, onset in predictions.items():
            all_predictions[key] = onset
            all_diagnostics[key] = diagnostics[key]
        folds.append({
            "holdout_game": holdout_game,
            "holdout_overall": scores["overall"],
            "training_angles": len(training_groups),
            "holdout_angles": len(holdout_groups),
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
    return scores, folds


def _acceptance(
    protocol: dict[str, Any],
    transfer_decision: dict[str, Any],
    locator_passed: bool,
    spatial: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    config = protocol["acceptance"]
    improvement = (
        float(spatial["overall"]["within_500_ms_share"])
        - float(baseline["overall"]["within_500_ms_share"])
    )
    paired_delta = (
        float(spatial["overall"]["both_angles_within_500_ms_share"] or 0)
        - float(baseline["overall"]["both_angles_within_500_ms_share"] or 0)
    )
    game_deltas = {
        game_id: round(
            float(spatial["by_game"][game_id]["within_500_ms_share"])
            - float(baseline["by_game"][game_id]["within_500_ms_share"]),
            6,
        )
        for game_id in spatial["by_game"]
    }
    tolerance = float(config["game_regression_tolerance_share"])
    regressions = [
        game_id for game_id, delta in game_deltas.items() if delta < -tolerance
    ]
    checks = {
        "anchor_locator_gate": (
            locator_passed if config["require_anchor_locator_gate"] else True
        ),
        "transfer_gate": (
            bool(transfer_decision["passed"])
            if config["require_transfer_gate"] else True
        ),
        "within_500_ms_improvement": (
            improvement >= float(config[
                "minimum_within_500_ms_share_improvement_vs_candidate_ranker"
            ])
        ),
        "median_absolute_error_not_worse": (
            float(spatial["overall"]["median_absolute_error_ms"])
            <= float(baseline["overall"]["median_absolute_error_ms"])
            if config["require_median_absolute_error_not_worse"] else True
        ),
        "paired_success_not_materially_worse": (
            paired_delta >= float(config[
                "minimum_paired_success_share_change_vs_candidate_ranker"
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
            "spatial_interaction_ranker_supported"
            if passed else "spatial_interaction_ranker_not_supported"
        ),
        "passed": passed,
        "checks": checks,
        "within_500_ms_share_change": round(improvement, 6),
        "paired_success_share_change": round(paired_delta, 6),
        "game_within_500_ms_share_changes": game_deltas,
        "games_regressing_beyond_tolerance": sorted(regressions),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def _percent(value: Any) -> str:
    return f"{float(value) * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    baseline = report["candidate_ranker_baseline"]["overall"]
    spatial = report["lofo_spatial_interaction"]["overall"]
    decision = report["acceptance"]
    lines = [
        "# Multi-game Snap Spatial Interaction",
        "",
        f"**Decision:** `{decision['decision']}`",
        "",
        "Anchor localization and ranker features were cross-fitted without each "
        "held-out game. Validation and final holdout data were not loaded.",
        "",
        "## Unseen-game comparison",
        "",
        "| Method | Within 500 ms | Median abs error | P90 abs error | Both paired angles |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| Candidate ranker | {baseline['within_500_ms']}/"
        f"{baseline['angle_judgments']} "
        f"({_percent(baseline['within_500_ms_share'])}) | "
        f"{baseline['median_absolute_error_ms']:.1f} ms | "
        f"{baseline['p90_absolute_error_ms']:.1f} ms | "
        f"{_percent(baseline['both_angles_within_500_ms_share'])} |",
        f"| Spatial interaction | {spatial['within_500_ms']}/"
        f"{spatial['angle_judgments']} "
        f"({_percent(spatial['within_500_ms_share'])}) | "
        f"{spatial['median_absolute_error_ms']:.1f} ms | "
        f"{spatial['p90_absolute_error_ms']:.1f} ms | "
        f"{_percent(spatial['both_angles_within_500_ms_share'])} |",
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
            "Freeze both locator and spatial-ranker artifacts, then run them once "
            "on the sealed Miami-Ohio State validation set."
        )
    else:
        lines.append(
            "Keep validation sealed. The next development change must improve the "
            "interaction representation or add more independent game supervision."
        )
    return "\n".join(lines) + "\n"


def run_spatial_interaction(
    protocol_path: Path,
    report_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    protocol_path = protocol_path.resolve()
    repository_root = protocol_path.parents[1]
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    transfer_path = _resolve(protocol["transfer_protocol"], repository_root)
    transfer = json.loads(transfer_path.read_text(encoding="utf-8"))
    candidate_protocol_path = _resolve(
        protocol["candidate_ranker_protocol"], repository_root
    )
    candidate_protocol = json.loads(
        candidate_protocol_path.read_text(encoding="utf-8")
    )
    candidate_report_path = _resolve(
        protocol["candidate_ranker_report"], repository_root
    )
    candidate_report = json.loads(
        candidate_report_path.read_text(encoding="utf-8")
    )
    locator_protocol_path = _resolve(
        protocol["anchor_locator_protocol"], repository_root
    )
    locator_protocol = json.loads(
        locator_protocol_path.read_text(encoding="utf-8")
    )
    locator_report_path = _resolve(
        protocol["anchor_locator_report"], repository_root
    )
    locator_report = json.loads(
        locator_report_path.read_text(encoding="utf-8")
    )
    if not locator_report["acceptance"]["passed"]:
        raise ValueError("Anchor locator gate did not pass")

    specs, counts = load_marked_angle_specs(transfer, repository_root)
    specs_by_angle = {
        (spec.cohort_id, spec.clip_id, spec.angle): spec for spec in specs
    }
    model_path = _resolve(protocol["detector_model"], repository_root)
    detected_cache = _resolve(
        protocol["detected_player_cache"], repository_root
    )
    traces, measurement = build_or_load_traces(
        specs, model_path, detected_cache
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
        protocol["candidate_generation"],
    )

    examples, statuses, _ = load_anchor_examples(
        locator_protocol, repository_root
    )
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
        _resolve(protocol["runtime_anchor_feature_cache"], repository_root),
        locator_protocol["feature_extractor"],
    )
    motion_grids, motion_measurement = build_or_load_motion_grids(
        specs,
        _resolve(protocol["motion_grid_cache_directory"], repository_root),
        protocol["motion_grid"],
    )

    spatial_scores, folds = leave_one_game_out_spatial(
        traces,
        base_groups,
        examples,
        exact_features,
        runtime_features,
        motion_grids,
        locator_protocol["locator_model"],
        protocol["interaction_features"],
        protocol["model"],
        specs_by_angle,
        old_policy,
    )
    transfer_decision = _transfer_decision(transfer, spatial_scores)
    baseline_scores = candidate_report["lofo_candidate_ranker"]
    acceptance = _acceptance(
        protocol,
        transfer_decision,
        bool(locator_report["acceptance"]["passed"]),
        spatial_scores,
        baseline_scores,
    )

    global_estimates, _ = global_anchor_estimates(
        base_groups,
        examples,
        exact_features,
        runtime_features,
        locator_protocol["locator_model"],
    )
    global_groups = augment_groups(
        base_groups,
        traces_by_key,
        motion_grids,
        global_estimates,
        protocol["interaction_features"],
    )
    global_models = train_ranker_ensemble(global_groups, protocol["model"])
    global_scores, _ = _ranker_scores(
        traces,
        global_groups,
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
    artifact_path = _resolve(
        protocol["spatial_ranker_artifact"], repository_root
    )
    combined_feature_names = base_feature_names + interaction_feature_names(
        protocol["interaction_features"]
    )
    save_model_bundle(
        artifact_path,
        global_models,
        combined_feature_names,
        protocol,
        grid_sha256,
        bool(acceptance["passed"]),
    )
    report = {
        "schema_version": "1.0",
        "iteration_id": protocol["iteration_id"],
        "dataset": counts,
        "anchor_labels": statuses,
        "candidate_generation": candidate_diagnostics,
        "measurement": measurement,
        "runtime_anchor_measurement": runtime_measurement,
        "motion_grid_measurement": motion_measurement,
        "anchor_locator_gate": locator_report["acceptance"],
        "candidate_ranker_baseline": baseline_scores,
        "lofo_spatial_interaction": spatial_scores,
        "folds": folds,
        "transfer_decision": transfer_decision,
        "acceptance": acceptance,
        "global_model": {
            "artifact": _fingerprint(artifact_path),
            "status": (
                "approved_for_sealed_validation"
                if acceptance["passed"] else "experimental_not_approved"
            ),
            "all_development_apparent_metrics": global_scores["overall"],
        },
        "inputs": {
            "protocol": _fingerprint(protocol_path),
            "transfer_protocol": _fingerprint(transfer_path),
            "candidate_ranker_protocol": _fingerprint(candidate_protocol_path),
            "candidate_ranker_report": _fingerprint(candidate_report_path),
            "anchor_locator_protocol": _fingerprint(locator_protocol_path),
            "anchor_locator_report": _fingerprint(locator_report_path),
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
    baseline = baseline_scores["overall"]
    spatial = spatial_scores["overall"]
    print(
        f"LOFO spatial interaction: {spatial['within_500_ms']}/"
        f"{spatial['angle_judgments']} "
        f"({spatial['within_500_ms_share'] * 100:.1f}%), versus "
        f"{baseline['within_500_ms']}/{baseline['angle_judgments']} "
        f"({baseline['within_500_ms_share'] * 100:.1f}%) for candidate ranker.",
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
