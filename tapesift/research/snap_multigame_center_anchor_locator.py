"""Automatic center/QB exchange-point locator with game-level holdouts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

import cv2
import numpy as np

from tapesift.research.snap_onset_detected_players import configure_inference
from tapesift.research.snap_onset_refiner import sha256_file


FEATURE_VERSION = "center-anchor-layout-v1"


@dataclass(frozen=True)
class AnchorExample:
    key: tuple[str, str, int]
    review_item_id: str
    cohort_id: str
    game_id: str
    game_name: str
    clip_id: str
    clip_number: int
    angle: int
    image_path: Path
    image_sha256: str
    status: str
    anchor_x: float | None
    anchor_y: float | None


@dataclass(frozen=True)
class LocatorModel:
    seed: int
    prior_angle_one: np.ndarray
    prior_angle_two: np.ndarray
    mean: np.ndarray
    scale: np.ndarray
    projection: np.ndarray
    hidden_bias: np.ndarray
    coefficients: np.ndarray
    shrinkage: float
    training_rmse: float


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


def _fingerprint(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def load_anchor_examples(
    protocol: dict[str, Any],
    repository_root: Path,
) -> tuple[list[AnchorExample], dict[str, int], list[dict[str, Any]]]:
    transfer_path = _resolve(protocol["transfer_protocol"], repository_root)
    transfer = json.loads(transfer_path.read_text(encoding="utf-8"))
    cohorts = {
        str(item["cohort_id"]): (
            str(item["game_id"]), str(item["game_name"])
        )
        for item in transfer["development_cohorts"]
    }
    examples = []
    seen = set()
    statuses: dict[str, int] = {}
    input_fingerprints = []
    for anchor_set in protocol["anchor_sets"]:
        package_path = _resolve(anchor_set["package"], repository_root)
        labels_path = _resolve(anchor_set["labels"], repository_root)
        package = json.loads(package_path.read_text(encoding="utf-8"))
        labels = {
            str(row["review_item_id"]): row for row in _read_jsonl(labels_path)
        }
        if len(labels) != len(package["items"]):
            raise ValueError(
                f"Expected {len(package['items'])} labels beside {package_path}, "
                f"found {len(labels)}"
            )
        for item in package["items"]:
            review_item_id = str(item["review_item_id"])
            label = labels.get(review_item_id)
            if label is None:
                raise ValueError(f"Missing anchor label: {review_item_id}")
            if str(label["reference_sha256"]) != str(item["reference_sha256"]):
                raise ValueError(f"Stale anchor label: {review_item_id}")
            cohort_id = str(item["research_cohort_id"])
            if cohort_id not in cohorts:
                raise ValueError(f"Undeclared development cohort: {cohort_id}")
            key = (cohort_id, str(item["clip_id"]), int(item["angle"]))
            if key in seen:
                raise ValueError(f"Duplicate anchor example: {key}")
            seen.add(key)
            status = str(label["anchor_status"])
            statuses[status] = statuses.get(status, 0) + 1
            target_x = label.get("anchor_x")
            target_y = label.get("anchor_y")
            if status == "visible":
                target_x = float(target_x)
                target_y = float(target_y)
            else:
                target_x = target_y = None
            asset = item["assets"]["-250"]
            game_id, game_name = cohorts[cohort_id]
            examples.append(AnchorExample(
                key=key,
                review_item_id=review_item_id,
                cohort_id=cohort_id,
                game_id=game_id,
                game_name=game_name,
                clip_id=str(item["clip_id"]),
                clip_number=int(item["clip_number"]),
                angle=int(item["angle"]),
                image_path=package_path.parent / str(asset["path"]),
                image_sha256=str(asset["sha256"]),
                status=status,
                anchor_x=target_x,
                anchor_y=target_y,
            ))
        input_fingerprints.extend([
            _fingerprint(package_path),
            _fingerprint(labels_path),
        ])
    examples.sort(key=lambda item: (
        item.game_id, item.cohort_id, item.clip_number, item.angle
    ))
    if len(examples) != int(protocol["expected_anchor_items"]):
        raise ValueError(
            f"Expected {protocol['expected_anchor_items']} anchors, "
            f"found {len(examples)}"
        )
    return examples, statuses, input_fingerprints


def _splat(grid: np.ndarray, x: float, y: float, weight: float) -> None:
    height, width = grid.shape
    gx = np.clip(x, 0, 1) * (width - 1)
    gy = np.clip(y, 0, 1) * (height - 1)
    x0, y0 = int(np.floor(gx)), int(np.floor(gy))
    x1, y1 = min(x0 + 1, width - 1), min(y0 + 1, height - 1)
    dx, dy = gx - x0, gy - y0
    grid[y0, x0] += weight * (1 - dx) * (1 - dy)
    grid[y0, x1] += weight * dx * (1 - dy)
    grid[y1, x0] += weight * (1 - dx) * dy
    grid[y1, x1] += weight * dx * dy


def _feature_names(config: dict[str, Any]) -> list[str]:
    player_cells = int(config["player_grid_width"]) * int(
        config["player_grid_height"]
    )
    image_cells = int(config["image_grid_width"]) * int(
        config["image_grid_height"]
    )
    names = [f"player_center_{index}" for index in range(player_cells)]
    names += [f"player_foot_{index}" for index in range(player_cells)]
    names += [f"player_size_{index}" for index in range(player_cells)]
    names += [f"gray_{index}" for index in range(image_cells)]
    names += [f"edge_{index}" for index in range(image_cells)]
    names += [
        "detection_count",
        "confidence_mean",
        "center_x_mean",
        "center_y_mean",
        "center_x_std",
        "center_y_std",
        "foot_x_q10",
        "foot_x_q25",
        "foot_x_q50",
        "foot_x_q75",
        "foot_x_q90",
        "foot_y_q10",
        "foot_y_q25",
        "foot_y_q50",
        "foot_y_q75",
        "foot_y_q90",
        "box_width_mean",
        "box_height_mean",
        "angle_one",
        "angle_two",
    ]
    return names


def extract_anchor_features(
    model: Any,
    image: np.ndarray,
    angle: int,
    config: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    height, width = image.shape[:2]
    result = model.predict(
        source=image,
        imgsz=int(config["model_image_size"]),
        conf=float(config["person_confidence"]),
        classes=[0],
        verbose=False,
    )[0]
    boxes = np.empty((0, 4), dtype=np.float64)
    confidences = np.empty((0,), dtype=np.float64)
    if result.boxes is not None and len(result.boxes):
        boxes = result.boxes.xyxy.detach().cpu().numpy().astype(np.float64)
        confidences = result.boxes.conf.detach().cpu().numpy().astype(np.float64)
    normalized = []
    for box, confidence in zip(boxes, confidences, strict=True):
        x1, y1, x2, y2 = box
        box_width = max((x2 - x1) / width, 0.0)
        box_height = max((y2 - y1) / height, 0.0)
        area = box_width * box_height
        if (
            box_width < 0.003
            or box_height < 0.01
            or box_width > 0.3
            or box_height > 0.55
            or area > 0.09
        ):
            continue
        normalized.append((
            (x1 + x2) / (2 * width),
            (y1 + y2) / (2 * height),
            (x1 + x2) / (2 * width),
            y2 / height,
            box_width,
            box_height,
            float(confidence),
        ))

    grid_width = int(config["player_grid_width"])
    grid_height = int(config["player_grid_height"])
    center_grid = np.zeros((grid_height, grid_width), dtype=np.float64)
    foot_grid = np.zeros_like(center_grid)
    size_grid = np.zeros_like(center_grid)
    for center_x, center_y, foot_x, foot_y, box_width, box_height, confidence in normalized:
        _splat(center_grid, center_x, center_y, confidence)
        _splat(foot_grid, foot_x, foot_y, confidence)
        _splat(size_grid, center_x, center_y, confidence * box_height)
    normalizer = max(len(normalized), 1)
    center_grid /= normalizer
    foot_grid /= normalizer
    size_grid /= normalizer

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    image_size = (
        int(config["image_grid_width"]), int(config["image_grid_height"])
    )
    gray_grid = cv2.resize(gray, image_size, interpolation=cv2.INTER_AREA) / 255
    sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edge = cv2.magnitude(sobel_x, sobel_y)
    edge_grid = cv2.resize(edge, image_size, interpolation=cv2.INTER_AREA)
    edge_grid = np.clip(edge_grid / 255, 0, 4)

    if normalized:
        values = np.asarray(normalized, dtype=np.float64)
        centers_x, centers_y = values[:, 0], values[:, 1]
        feet_x, feet_y = values[:, 2], values[:, 3]
        box_widths, box_heights = values[:, 4], values[:, 5]
        detection_confidences = values[:, 6]
        quantiles_x = np.quantile(feet_x, [0.1, 0.25, 0.5, 0.75, 0.9])
        quantiles_y = np.quantile(feet_y, [0.1, 0.25, 0.5, 0.75, 0.9])
        stats = [
            min(len(normalized) / 30, 2),
            float(detection_confidences.mean()),
            float(centers_x.mean()),
            float(centers_y.mean()),
            float(centers_x.std()),
            float(centers_y.std()),
            *quantiles_x.tolist(),
            *quantiles_y.tolist(),
            float(box_widths.mean()),
            float(box_heights.mean()),
            float(angle == 1),
            float(angle == 2),
        ]
    else:
        stats = [0.0] * 18 + [float(angle == 1), float(angle == 2)]
    features = np.concatenate([
        center_grid.ravel(),
        foot_grid.ravel(),
        size_grid.ravel(),
        gray_grid.ravel(),
        edge_grid.ravel(),
        np.asarray(stats, dtype=np.float64),
    ])
    diagnostics = {
        "raw_detections": len(boxes),
        "retained_detections": len(normalized),
        "image_width": width,
        "image_height": height,
    }
    return features, diagnostics


def _measurement_signature(
    example: AnchorExample,
    model_sha256: str,
    config: dict[str, Any],
) -> str:
    payload = {
        "feature_version": FEATURE_VERSION,
        "image_sha256": example.image_sha256,
        "angle": example.angle,
        "model_sha256": model_sha256,
        "config": config,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def build_or_load_features(
    examples: list[AnchorExample],
    model_path: Path,
    cache_path: Path,
    config: dict[str, Any],
) -> tuple[dict[tuple[str, str, int], np.ndarray], dict[str, Any]]:
    model_sha256 = sha256_file(model_path)
    signatures = {
        example.key: _measurement_signature(example, model_sha256, config)
        for example in examples
    }
    cache = {}
    if cache_path.exists():
        cache = {
            str(row["measurement_signature"]): row
            for row in _read_jsonl(cache_path)
        }
    hits = sum(signature in cache for signature in signatures.values())
    print(
        f"Anchor features: {hits} cached, {len(examples) - hits} to measure.",
        flush=True,
    )
    model = None
    append_handle = None
    if hits != len(examples):
        from ultralytics import YOLO

        configure_inference()
        model = YOLO(str(model_path))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        append_handle = cache_path.open("a", encoding="utf-8")
    features = {}
    retained_counts = []
    try:
        for index, example in enumerate(examples, start=1):
            signature = signatures[example.key]
            record = cache.get(signature)
            if record is None:
                image = cv2.imread(str(example.image_path))
                if image is None:
                    raise FileNotFoundError(example.image_path)
                vector, diagnostics = extract_anchor_features(
                    model, image, example.angle, config
                )
                record = {
                    "schema_version": "1.0",
                    "measurement_signature": signature,
                    "feature_version": FEATURE_VERSION,
                    "key": list(example.key),
                    "features": vector.tolist(),
                    "diagnostics": diagnostics,
                }
                assert append_handle is not None
                append_handle.write(json.dumps(
                    record, sort_keys=True, separators=(",", ":")
                ) + "\n")
                append_handle.flush()
                cache[signature] = record
                if index == 1 or index % 20 == 0 or index == len(examples):
                    print(
                        f"[{index}/{len(examples)}] measured anchor layout features",
                        flush=True,
                    )
            features[example.key] = np.asarray(
                record["features"], dtype=np.float64
            )
            retained_counts.append(int(
                record["diagnostics"]["retained_detections"]
            ))
    finally:
        if append_handle is not None:
            append_handle.close()
    return features, {
        "feature_version": FEATURE_VERSION,
        "model_sha256": model_sha256,
        "feature_width": len(next(iter(features.values()))),
        "median_retained_players": statistics.median(retained_counts),
        "minimum_retained_players": min(retained_counts),
        "maximum_retained_players": max(retained_counts),
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


def fit_locator_ensemble(
    examples: list[AnchorExample],
    features: dict[tuple[str, str, int], np.ndarray],
    config: dict[str, Any],
) -> list[LocatorModel]:
    visible = [example for example in examples if example.status == "visible"]
    if len(visible) < 20:
        raise ValueError(f"Only {len(visible)} visible anchors available")
    matrix = np.stack([features[example.key] for example in visible])
    targets = np.asarray([
        [example.anchor_x, example.anchor_y] for example in visible
    ], dtype=np.float64)
    global_prior = np.median(targets, axis=0)
    priors = {}
    for angle in (1, 2):
        angle_targets = targets[
            np.asarray([example.angle == angle for example in visible])
        ]
        priors[angle] = (
            np.median(angle_targets, axis=0)
            if len(angle_targets)
            else global_prior
        )
    prior_rows = np.stack([priors[example.angle] for example in visible])
    residuals = targets - prior_rows
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale[scale < 1e-8] = 1.0
    standardized = np.clip((matrix - mean) / scale, -6, 6)
    hidden_features = int(config["random_hidden_features"])
    ridge_penalty = float(config["ridge_penalty"])
    shrinkage = float(config["residual_shrinkage"])
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
        system = design.T @ design
        penalty = np.eye(system.shape[0], dtype=np.float64) * ridge_penalty
        penalty[0, 0] = 0.0
        response = design.T @ residuals
        coefficients = np.linalg.solve(system + penalty, response)
        predictions = np.clip(
            prior_rows + shrinkage * (design @ coefficients), 0, 1
        )
        rmse = float(np.sqrt(np.mean(np.sum(
            (predictions - targets) ** 2, axis=1
        ))))
        models.append(LocatorModel(
            seed=int(seed),
            prior_angle_one=priors[1].copy(),
            prior_angle_two=priors[2].copy(),
            mean=mean.copy(),
            scale=scale.copy(),
            projection=projection,
            hidden_bias=hidden_bias,
            coefficients=coefficients,
            shrinkage=shrinkage,
            training_rmse=round(rmse, 6),
        ))
    return models


def _predict_one(
    models: list[LocatorModel],
    feature: np.ndarray,
    angle: int,
) -> tuple[np.ndarray, float]:
    predictions = []
    for model in models:
        prior = (
            model.prior_angle_one if angle == 1 else model.prior_angle_two
        )
        standardized = np.clip((feature - model.mean) / model.scale, -6, 6)
        design = _design(
            standardized[None, :], model.projection, model.hidden_bias
        )
        predictions.append(np.clip(
            prior + model.shrinkage * (design @ model.coefficients)[0], 0, 1
        ))
    matrix = np.stack(predictions)
    return matrix.mean(axis=0), float(np.sqrt(np.sum(
        matrix.var(axis=0)
    )))


def _prior_prediction(models: list[LocatorModel], angle: int) -> np.ndarray:
    return np.mean([
        model.prior_angle_one if angle == 1 else model.prior_angle_two
        for model in models
    ], axis=0)


def _metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = np.asarray([row["error"] for row in rows], dtype=np.float64)
    return {
        "visible_anchors": len(rows),
        "median_normalized_error": round(float(np.median(errors)), 6),
        "p90_normalized_error": round(float(np.percentile(errors, 90)), 6),
        "within_0_10": int(np.sum(errors <= 0.10)),
        "within_0_10_share": round(float(np.mean(errors <= 0.10)), 6),
        "within_0_15": int(np.sum(errors <= 0.15)),
        "within_0_15_share": round(float(np.mean(errors <= 0.15)), 6),
        "within_0_20": int(np.sum(errors <= 0.20)),
        "within_0_20_share": round(float(np.mean(errors <= 0.20)), 6),
    }


def leave_one_game_out(
    examples: list[AnchorExample],
    features: dict[tuple[str, str, int], np.ndarray],
    model_config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    games = sorted({example.game_id for example in examples})
    model_rows = []
    prior_rows = []
    folds = []
    for index, holdout_game in enumerate(games, start=1):
        training = [
            example for example in examples
            if example.game_id != holdout_game and example.status == "visible"
        ]
        holdout = [
            example for example in examples
            if example.game_id == holdout_game and example.status == "visible"
        ]
        print(
            f"[{index}/{len(games)}] fitting anchor locator without {holdout_game} "
            f"({len(training)} visible training anchors)",
            flush=True,
        )
        models = fit_locator_ensemble(training, features, model_config)
        fold_model_rows = []
        fold_prior_rows = []
        for example in holdout:
            target = np.asarray([example.anchor_x, example.anchor_y])
            prediction, uncertainty = _predict_one(
                models, features[example.key], example.angle
            )
            prior = _prior_prediction(models, example.angle)
            model_row = {
                "key": list(example.key),
                "game_id": holdout_game,
                "angle": example.angle,
                "target_x": float(target[0]),
                "target_y": float(target[1]),
                "predicted_x": round(float(prediction[0]), 6),
                "predicted_y": round(float(prediction[1]), 6),
                "uncertainty": round(uncertainty, 6),
                "error": float(np.linalg.norm(prediction - target)),
            }
            prior_row = {
                **model_row,
                "predicted_x": round(float(prior[0]), 6),
                "predicted_y": round(float(prior[1]), 6),
                "uncertainty": 0.0,
                "error": float(np.linalg.norm(prior - target)),
            }
            model_rows.append(model_row)
            prior_rows.append(prior_row)
            fold_model_rows.append(model_row)
            fold_prior_rows.append(prior_row)
        fold_metrics = _metric_summary(fold_model_rows)
        prior_metrics = _metric_summary(fold_prior_rows)
        folds.append({
            "holdout_game": holdout_game,
            "training_visible_anchors": len(training),
            "holdout_visible_anchors": len(holdout),
            "locator": fold_metrics,
            "angle_prior": prior_metrics,
        })
        print(
            f"    median error {fold_metrics['median_normalized_error']:.3f}; "
            f"{fold_metrics['within_0_15']}/{fold_metrics['visible_anchors']} "
            f"within 0.15",
            flush=True,
        )
    return (
        {
            "overall": _metric_summary(model_rows),
            "by_game": {
                game: _metric_summary([
                    row for row in model_rows if row["game_id"] == game
                ])
                for game in games
            },
            "rows": model_rows,
        },
        {
            "overall": _metric_summary(prior_rows),
            "by_game": {
                game: _metric_summary([
                    row for row in prior_rows if row["game_id"] == game
                ])
                for game in games
            },
        },
        folds,
    )


def _acceptance(
    locator: dict[str, Any],
    prior: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    overall = locator["overall"]
    radius_key = f"within_{float(config['game_within_radius']):.2f}".replace(
        ".", "_"
    ) + "_share"
    game_checks = {
        game_id: metrics[radius_key]
        >= float(config["minimum_game_within_radius_share"])
        for game_id, metrics in locator["by_game"].items()
    }
    games_passing = sum(game_checks.values())
    reduction = (
        float(prior["overall"]["median_normalized_error"])
        - float(overall["median_normalized_error"])
    )
    checks = {
        "median_error": (
            float(overall["median_normalized_error"])
            <= float(config["maximum_median_normalized_error"])
        ),
        "p90_error": (
            float(overall["p90_normalized_error"])
            <= float(config["maximum_p90_normalized_error"])
        ),
        "overall_within_0_15": (
            float(overall["within_0_15_share"])
            >= float(config["minimum_overall_within_0_15_share"])
        ),
        "game_consistency": (
            games_passing >= int(config["minimum_games_passing"])
        ),
        "improves_angle_prior": (
            reduction
            >= float(config[
                "minimum_median_error_reduction_vs_angle_prior"
            ])
        ),
    }
    passed = all(checks.values())
    return {
        "decision": (
            "anchor_locator_supported_for_interaction_features"
            if passed
            else "anchor_locator_not_transferable"
        ),
        "passed": passed,
        "checks": checks,
        "games_passing": games_passing,
        "games_total": len(game_checks),
        "game_checks": game_checks,
        "median_error_reduction_vs_angle_prior": round(reduction, 6),
    }


def save_model_bundle(
    path: Path,
    models: list[LocatorModel],
    feature_names: list[str],
    protocol: dict[str, Any],
    approved: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": "1.0",
        "iteration_id": protocol["iteration_id"],
        "feature_version": FEATURE_VERSION,
        "feature_names": feature_names,
        "feature_extractor": protocol["feature_extractor"],
        "locator_model": protocol["locator_model"],
        "approved_for_interaction_features": approved,
    }
    np.savez_compressed(
        path,
        metadata_json=np.asarray(json.dumps(metadata, sort_keys=True)),
        seeds=np.asarray([model.seed for model in models], dtype=np.int64),
        prior_angle_one=np.stack([model.prior_angle_one for model in models]),
        prior_angle_two=np.stack([model.prior_angle_two for model in models]),
        means=np.stack([model.mean for model in models]),
        scales=np.stack([model.scale for model in models]),
        projections=np.stack([model.projection for model in models]),
        hidden_biases=np.stack([model.hidden_bias for model in models]),
        coefficients=np.stack([model.coefficients for model in models]),
        shrinkages=np.asarray([model.shrinkage for model in models]),
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
    locator = report["leave_one_game_out"]["locator"]["overall"]
    prior = report["leave_one_game_out"]["angle_prior"]["overall"]
    decision = report["acceptance"]
    lines = [
        "# Multi-game Center/QB Anchor Locator",
        "",
        f"**Decision:** `{decision['decision']}`",
        "",
        "Every localization was made by a model trained without that game. "
        "Validation and final holdout data were not loaded.",
        "",
        "## Overall",
        "",
        "| Method | Visible anchors | Median error | P90 error | Within 0.15 |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| Angle prior | {prior['visible_anchors']} | "
        f"{prior['median_normalized_error']:.3f} | "
        f"{prior['p90_normalized_error']:.3f} | "
        f"{prior['within_0_15']}/{prior['visible_anchors']} "
        f"({_percent(prior['within_0_15_share'])}) |",
        f"| Player-layout locator | {locator['visible_anchors']} | "
        f"{locator['median_normalized_error']:.3f} | "
        f"{locator['p90_normalized_error']:.3f} | "
        f"{locator['within_0_15']}/{locator['visible_anchors']} "
        f"({_percent(locator['within_0_15_share'])}) |",
        "",
        "## Held-out games",
        "",
        "| Game | Visible anchors | Median error | Within 0.15 | Within 0.20 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    by_game = report["leave_one_game_out"]["locator"]["by_game"]
    for game_id, metrics in by_game.items():
        lines.append(
            f"| {game_id} | {metrics['visible_anchors']} | "
            f"{metrics['median_normalized_error']:.3f} | "
            f"{_percent(metrics['within_0_15_share'])} | "
            f"{_percent(metrics['within_0_20_share'])} |"
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
        "## Next iteration",
        "",
    ])
    if decision["passed"]:
        lines.append(
            "Use excluded-game anchor estimates to measure local center/QB "
            "motion, convergence, and separation around every snap candidate."
        )
    else:
        lines.append(
            "Do not build local interaction features around this locator. Improve "
            "formation geometry or train a direct exchange-area visual model first."
        )
    return "\n".join(lines) + "\n"


def run_anchor_locator(
    protocol_path: Path,
    report_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    protocol_path = protocol_path.resolve()
    repository_root = protocol_path.parents[1]
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    examples, statuses, anchor_inputs = load_anchor_examples(
        protocol, repository_root
    )
    print(
        "Anchor labels: " + ", ".join(
            f"{name}={count}" for name, count in sorted(statuses.items())
        ),
        flush=True,
    )
    model_path = _resolve(protocol["detector_model"], repository_root)
    cache_path = _resolve(protocol["feature_cache"], repository_root)
    features, extraction = build_or_load_features(
        examples,
        model_path,
        cache_path,
        protocol["feature_extractor"],
    )
    feature_names = _feature_names(protocol["feature_extractor"])
    if extraction["feature_width"] != len(feature_names):
        raise ValueError("Anchor feature-name width mismatch")
    locator, prior, folds = leave_one_game_out(
        examples, features, protocol["locator_model"]
    )
    acceptance = _acceptance(locator, prior, protocol["acceptance"])
    global_models = fit_locator_ensemble(
        [example for example in examples if example.status == "visible"],
        features,
        protocol["locator_model"],
    )
    artifact_path = _resolve(protocol["model_artifact"], repository_root)
    save_model_bundle(
        artifact_path,
        global_models,
        feature_names,
        protocol,
        bool(acceptance["passed"]),
    )
    report = {
        "schema_version": "1.0",
        "iteration_id": protocol["iteration_id"],
        "anchor_labels": {
            "total": len(examples),
            "statuses": statuses,
            "games": len({example.game_id for example in examples}),
        },
        "feature_extraction": extraction,
        "leave_one_game_out": {
            "locator": locator,
            "angle_prior": prior,
            "folds": folds,
        },
        "acceptance": acceptance,
        "global_model": {
            "artifact": _fingerprint(artifact_path),
            "status": (
                "approved_for_interaction_features"
                if acceptance["passed"]
                else "experimental_not_approved"
            ),
            "training_rmse": [model.training_rmse for model in global_models],
        },
        "inputs": {
            "protocol": _fingerprint(protocol_path),
            "detector_model": _fingerprint(model_path),
            "anchor_sets": anchor_inputs,
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
    overall = locator["overall"]
    print(
        f"LOFO locator median={overall['median_normalized_error']:.3f}, "
        f"p90={overall['p90_normalized_error']:.3f}, "
        f"within 0.15={overall['within_0_15']}/"
        f"{overall['visible_anchors']} "
        f"({overall['within_0_15_share'] * 100:.1f}%).",
        flush=True,
    )
    print(
        f"Decision: {acceptance['decision']}; games passing "
        f"{acceptance['games_passing']}/{acceptance['games_total']}",
        flush=True,
    )
    return report
