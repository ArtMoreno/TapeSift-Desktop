"""Freeze and prepare the Iteration 4B untouched-corpus review.

This driver deliberately separates four irreversible research stages:

1. freeze the film selection and detector identities;
2. run baseline and candidate predictions outside this script;
3. hash-lock those predictions;
4. build a blind, sampled owner-review queue.

The generated artifacts live under the ignored segmentation benchmark tree.
Existing artifacts are never replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tapesift.research.segmentation_benchmark import probe_duration_ms  # noqa: E402
from tapesift.research.segmentation_verification import (  # noqa: E402
    VerificationSession,
    build_pilot_items,
)
from tapesift.services.play_detect_service import (  # noqa: E402
    DEFAULT_MAX_PLAY_S,
    DEFAULT_MIN_PLAY_S,
    DEFAULT_SCENE_THRESHOLD,
    DEFAULT_SEPARATOR_MAX_S,
)

QUEUE_ID = "segmentation-iteration-4b-generalization-v1"
CANDIDATE_ID = "iteration-4a-bimodal-pair-v2"
CANDIDATE_COMMIT = "b19d572"
BASELINE_ID = "iteration-3a-v2"
BASELINE_COMMIT = "0189b70"
DETECTOR_SOURCE = "tapesift/services/play_detect_service.py"
DEFAULT_ARTIFACT_ROOT = (
    ROOT / "research" / "segmentation_benchmark"
    / "reports" / "iteration_4b_generalization_v1"
)
DEFAULT_FFPROBE = ROOT / "vendor" / "ffmpeg" / "ffprobe.exe"
REVIEW_PER_FILM = 5
REVIEW_STRATA = {
    "confident": 1,
    "weak_recovered": 1,
    "other_review": 2,
    "unclassified": 1,
    "scene_angle_pair": 0,
}
PARAMETERS = {
    "separator_max_s": DEFAULT_SEPARATOR_MAX_S,
    "min_play_s": DEFAULT_MIN_PLAY_S,
    "max_play_s": DEFAULT_MAX_PLAY_S,
    "scene_threshold": DEFAULT_SCENE_THRESHOLD,
}
FILMS = (
    {
        "film_id": "sample_game_004_holdout_4b_v1",
        "source_group": "All 22 For Testing",
        "source_file": "sample-inputs/game-2056.mp4",
    },
    {
        "film_id": "sample_game_006_holdout_4b_v1",
        "source_group": "More all 22 Test",
        "source_file": "sample-inputs/game-2306.mp4",
    },
    {
        "film_id": "sample_game_003_holdout_4b_v1",
        "source_group": "More all 22 Test",
        "source_file": "sample-inputs/game-2575.mp4",
    },
    {
        "film_id": "sample_game_005_holdout_4b_v1",
        "source_group": "More all 22 Test",
        "source_file": "sample-inputs/game-2845.mp4",
    },
    {
        "film_id": "sample_game_001_holdout_4b_v1",
        "source_group": "More all 22 Test",
        "source_file": "sample-inputs/game-3121.mp4",
    },
)


class Iteration4BError(RuntimeError):
    """A frozen Iteration 4B invariant failed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_new(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to replace frozen artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def git_output(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def git_blob_sha256(commit: str, source_file: str) -> str:
    return hashlib.sha256(
        git_output("show", f"{commit}:{source_file}")).hexdigest()


def detector_lock(commit: str, detector_id: str) -> dict[str, Any]:
    resolved = git_output("rev-parse", commit).decode("ascii").strip()
    return {
        "id": detector_id,
        "source_commit": resolved,
        "source_file": DETECTOR_SOURCE,
        "source_sha256": git_blob_sha256(resolved, DETECTOR_SOURCE),
        "configuration": {
            "id": "play-detect-defaults-v1",
            "parameters": PARAMETERS,
            "sha256": hashlib.sha256(
                json.dumps(
                    PARAMETERS,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        },
    }


def selection_path(artifact_root: Path) -> Path:
    return artifact_root / "selection.locked.json"


def manifest_path(artifact_root: Path) -> Path:
    return artifact_root / "manifest.local.json"


def prediction_lock_path(artifact_root: Path) -> Path:
    return artifact_root / "predictions.locked.json"


def freeze(artifact_root: Path, ffprobe: Path) -> dict[str, Any]:
    if not ffprobe.is_file():
        raise FileNotFoundError(ffprobe)
    film_rows = []
    for spec in FILMS:
        source = Path(spec["source_file"]).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        film_rows.append({
            **spec,
            "source_file": str(source),
            "analysis_source": str(source),
            "source_size_bytes": source.stat().st_size,
            "source_sha256": sha256_file(source),
            "duration_ms": probe_duration_ms(ffprobe, source),
        })
    selection = {
        "schema_version": "1.0",
        "queue_id": QUEUE_ID,
        "status": "locked_before_detection",
        "created_at": utc_now(),
        "selection_policy": {
            "description": (
                "Five untouched games spanning multiple teams and production "
                "families; none appeared in Iteration 3A or 4A development."
            ),
            "selected_before_predictions": True,
            "film_count": len(film_rows),
        },
        "detectors": {
            "baseline": detector_lock(BASELINE_COMMIT, BASELINE_ID),
            "candidate": detector_lock(CANDIDATE_COMMIT, CANDIDATE_ID),
        },
        "predeclared_review": {
            "items_per_film": REVIEW_PER_FILM,
            "total_items": REVIEW_PER_FILM * len(film_rows),
            "strata_per_film": REVIEW_STRATA,
            "sampling": (
                "deterministic even-time stratified candidate sample; "
                "unavailable strata are deterministically backfilled"
            ),
        },
        "films": film_rows,
    }
    manifest = {
        "schema_version": "1.0",
        "films": [
            {
                "film_id": row["film_id"],
                "role": "holdout",
                "source_group": row["source_group"],
                "source_file": row["source_file"],
                "analysis_source": row["analysis_source"],
                "ground_truth_file": str(
                    artifact_root / "review" / "verified.jsonl"),
                "prediction_file": str(
                    artifact_root / "predictions" / "candidate"
                    / f"{row['film_id']}.json"),
                "report_file": str(
                    artifact_root / "reports" / f"{row['film_id']}.json"),
            }
            for row in film_rows
        ],
        "updated_at": selection["created_at"],
    }
    write_new(selection_path(artifact_root), canonical_json(selection))
    write_new(manifest_path(artifact_root), canonical_json(manifest))
    return selection


def load_selection(artifact_root: Path) -> dict[str, Any]:
    path = selection_path(artifact_root)
    if not path.is_file():
        raise FileNotFoundError(path)
    selection = json.loads(path.read_text(encoding="utf-8"))
    if selection.get("queue_id") != QUEUE_ID \
            or selection.get("status") != "locked_before_detection":
        raise Iteration4BError("Selection identity or frozen status changed")
    if len(selection.get("films", [])) != len(FILMS):
        raise Iteration4BError("Frozen film count changed")
    return selection


def validate_prediction(
    path: Path,
    film: dict[str, Any],
    detector: dict[str, Any],
) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("film_id") != film["film_id"]:
        raise Iteration4BError(f"Prediction film mismatch: {path}")
    if int(payload.get("duration_ms") or 0) != int(film["duration_ms"]):
        raise Iteration4BError(f"Prediction duration mismatch: {path}")
    if payload.get("parameters") != detector["configuration"]["parameters"]:
        raise Iteration4BError(f"Prediction parameters changed: {path}")
    plays = payload.get("plays")
    unclassified = payload.get("unclassified")
    if not isinstance(plays, list) or not isinstance(unclassified, list):
        raise Iteration4BError(f"Prediction payload is incomplete: {path}")
    return {
        "film_id": film["film_id"],
        "file": str(path.resolve()),
        "sha256": sha256_file(path),
        "duration_ms": payload["duration_ms"],
        "play_count": len(plays),
        "unclassified_count": len(unclassified),
        "fallback_pair_count": sum(
            play.get("review_reason", "").startswith(
                "paired from an uncalibrated alternating black-gap pattern")
            for play in plays
        ),
    }


def lock_predictions(artifact_root: Path) -> dict[str, Any]:
    selection = load_selection(artifact_root)
    predictions: dict[str, list[dict[str, Any]]] = {}
    for arm in ("baseline", "candidate"):
        detector = selection["detectors"][arm]
        rows = []
        for film in selection["films"]:
            path = (
                artifact_root / "predictions" / arm
                / f"{film['film_id']}.json"
            )
            rows.append(validate_prediction(path, film, detector))
        predictions[arm] = rows
    lock = {
        "schema_version": "1.0",
        "queue_id": QUEUE_ID,
        "status": "predictions_frozen_before_review",
        "created_at": utc_now(),
        "selection": {
            "file": str(selection_path(artifact_root).resolve()),
            "sha256": sha256_file(selection_path(artifact_root)),
        },
        "detectors": selection["detectors"],
        "predictions": predictions,
    }
    write_new(prediction_lock_path(artifact_root), canonical_json(lock))
    return lock


def prepare_review(artifact_root: Path, ffprobe: Path) -> VerificationSession:
    selection = load_selection(artifact_root)
    lock_path = prediction_lock_path(artifact_root)
    if not lock_path.is_file():
        raise FileNotFoundError(lock_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("status") != "predictions_frozen_before_review":
        raise Iteration4BError("Prediction lock status changed")
    if lock.get("selection", {}).get("sha256") != sha256_file(
            selection_path(artifact_root)):
        raise Iteration4BError("Selection changed after predictions")

    selections = [
        (film["film_id"], REVIEW_PER_FILM) for film in selection["films"]]
    items = build_pilot_items(
        manifest_path(artifact_root),
        selections,
        ffprobe,
        strata=REVIEW_STRATA,
    )
    if len(items) != REVIEW_PER_FILM * len(selection["films"]):
        raise Iteration4BError("Blind review sample did not reach its target")
    review_dir = artifact_root / "review"
    state = review_dir / "iteration_4b_generalization_v1.state.json"
    truth = review_dir / "iteration_4b_generalization_v1.verified.jsonl"
    if state.exists() or truth.exists():
        raise FileExistsError("Refusing to replace an existing review queue")
    session = VerificationSession.create(state, truth, QUEUE_ID, items)
    queue_lock = {
        "schema_version": "1.0",
        "queue_id": QUEUE_ID,
        "status": "blind_review_queue_locked",
        "created_at": utc_now(),
        "selection_sha256": sha256_file(selection_path(artifact_root)),
        "prediction_lock_sha256": sha256_file(lock_path),
        "state_file": str(state.resolve()),
        "state_sha256": sha256_file(state),
        "truth_file": str(truth.resolve()),
        "item_count": len(items),
        "item_ids": [item.item_id for item in items],
    }
    write_new(review_dir / "queue.locked.json", canonical_json(queue_lock))
    return session


def status(artifact_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "queue_id": QUEUE_ID,
        "artifact_root": str(artifact_root.resolve()),
    }
    for name, path in (
        ("selection", selection_path(artifact_root)),
        ("manifest", manifest_path(artifact_root)),
        ("prediction_lock", prediction_lock_path(artifact_root)),
        ("review_state", artifact_root / "review"
         / "iteration_4b_generalization_v1.state.json"),
        ("queue_lock", artifact_root / "review" / "queue.locked.json"),
    ):
        result[name] = {
            "exists": path.is_file(),
            "file": str(path.resolve()),
            "sha256": sha256_file(path) if path.is_file() else "",
        }
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Freeze and prepare TapeSift Iteration 4B")
    value.add_argument(
        "--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    value.add_argument("--ffprobe", type=Path, default=DEFAULT_FFPROBE)
    value.add_argument(
        "command",
        choices=("freeze", "lock-predictions", "prepare-review", "status"),
    )
    return value


def main() -> int:
    args = parser().parse_args()
    artifact_root = args.artifact_root.resolve()
    if args.command == "freeze":
        result: Any = freeze(artifact_root, args.ffprobe.resolve())
    elif args.command == "lock-predictions":
        result = lock_predictions(artifact_root)
    elif args.command == "prepare-review":
        session = prepare_review(
            artifact_root, args.ffprobe.resolve())
        result = {
            "queue_id": session.queue_id,
            "items": len(session.items),
            "state": str(session.state_path),
            "truth": str(session.ground_truth_path),
        }
    else:
        result = status(artifact_root)
    print(canonical_json(result).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
