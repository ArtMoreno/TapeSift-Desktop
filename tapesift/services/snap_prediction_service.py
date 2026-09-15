"""Non-destructive predicted-snap bookmarks for ordinary TapeSift clips.

The estimator reuses the frozen Temporal v2 sustained-motion onset logic.  It
analyzes only the first camera angle, returns a source timestamp plus visible
confidence diagnostics, and never changes clip boundaries or human metadata.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any, Iterable

from tapesift.core.exceptions import TapeSiftError
from tapesift.models.clip import Clip
from tapesift.research.run_pass_features import (
    DEFAULT_SAMPLE_WIDTH,
    build_signalstats_command,
    parse_signalstats,
)
from tapesift.research.run_pass_temporal_features import (
    MIN_ANGLE_SECONDS,
    TEMPORAL_EXTRACTOR_VERSION,
    TEMPORAL_SAMPLE_FPS,
    summarize_temporal_angle,
)
from tapesift.services import ffmpeg_service


PREDICTION_KEY = "snap_prediction"
PREDICTION_SCHEMA_VERSION = "1.0"
PREDICTOR_ID = "tapesift-sustained-motion-onset"
PREDICTOR_VERSION = f"temporal-{TEMPORAL_EXTRACTOR_VERSION}"


class SnapPredictionError(TapeSiftError):
    """A selected play cannot produce a trustworthy snap bookmark."""


class SnapPredictionCancelled(Exception):
    """Internal cancellation while a project or the application is closing."""


def first_angle_end_ms(
        clip_start_ms: int,
        clip_end_ms: int,
        angle_starts_ms: Iterable[int] = (),
) -> int:
    """End of the first camera view, using immutable CSE topology when known."""
    internal = sorted({
        int(value)
        for value in angle_starts_ms
        if int(clip_start_ms) < int(value) < int(clip_end_ms)
    })
    return internal[0] if internal else int(clip_end_ms)


def _read_frames(
        ffmpeg_path: str,
        source: Path,
        start_ms: int,
        end_ms: int,
        cancel_event: Event | None,
):
    command = build_signalstats_command(
        ffmpeg_path,
        source,
        start_ms,
        end_ms,
        sample_fps=TEMPORAL_SAMPLE_FPS,
        sample_width=DEFAULT_SAMPLE_WIDTH,
        include_scene_score=False,
    )
    timeout_s = max(60.0, ((end_ms - start_ms) / 1000.0) * 5.0)
    deadline = time.monotonic() + timeout_s
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=(
            ffmpeg_service.CREATE_NO_WINDOW
            | ffmpeg_service.IDLE_PRIORITY_CLASS
        ),
        stdin=subprocess.DEVNULL,
    )
    while True:
        try:
            output, error = process.communicate(timeout=0.10)
            break
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                process.communicate()
                raise SnapPredictionCancelled
            if time.monotonic() >= deadline:
                process.kill()
                process.communicate()
                raise SnapPredictionError(
                    "Snap analysis took too long.",
                    "Try the optimized preview or a shorter play range.",
                )
    if process.returncode:
        detail = (error or "").strip() or "unknown FFmpeg error"
        raise SnapPredictionError(
            "TapeSift could not analyze this play for a snap.", detail)
    frames = parse_signalstats(output or "")
    if not frames:
        raise SnapPredictionError(
            "The snap analyzer received no usable video frames.")
    return frames


def predict_snap(
        ffmpeg_path: str,
        source: Path,
        clip_start_ms: int,
        clip_end_ms: int,
        *,
        angle_starts_ms: Iterable[int] = (),
        cancel_event: Event | None = None,
) -> dict[str, Any]:
    """Predict the first-view snap and return a cacheable source bookmark."""
    source = Path(source)
    if not source.is_file():
        raise SnapPredictionError(
            "The source video is not available.",
            "Relink the project source, then try Find Snap again.",
        )
    start_ms = int(clip_start_ms)
    clip_end_ms = int(clip_end_ms)
    angle_end_ms = first_angle_end_ms(
        start_ms, clip_end_ms, angle_starts_ms)
    if angle_end_ms - start_ms < round(MIN_ANGLE_SECONDS * 1000):
        raise SnapPredictionError(
            "The first camera angle is too short for snap prediction.",
            "Keep at least six seconds around the snap or use the play's "
            "original detected boundaries.",
        )

    frames = _read_frames(
        ffmpeg_path, source, start_ms, angle_end_ms, cancel_event)
    _features, diagnostics = summarize_temporal_angle(frames)
    source_ms = start_ms + round(
        float(diagnostics["onset_seconds"]) * 1000)
    source_ms = max(start_ms, min(angle_end_ms, source_ms))
    reasons = [
        str(value)
        for value in diagnostics.get("eligibility_reasons", [])
        if str(value)
    ]
    return {
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "predictor_id": PREDICTOR_ID,
        "predictor_version": PREDICTOR_VERSION,
        "source_ms": source_ms,
        "confidence": float(diagnostics.get("onset_confidence", 0.0)),
        "eligible": bool(diagnostics.get("classifier_usable", False)),
        "method": str(diagnostics.get("onset_method", "")),
        "reasons": reasons,
        "clip_start_ms": start_ms,
        "clip_end_ms": clip_end_ms,
        "angle_start_ms": start_ms,
        "angle_end_ms": angle_end_ms,
        "sample_fps": TEMPORAL_SAMPLE_FPS,
        "sample_width": DEFAULT_SAMPLE_WIDTH,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def snap_marker(clip: Clip) -> dict[str, Any] | None:
    """Human-confirmed film time takes precedence without rewriting analysis."""
    if clip.details.get("timing_snap_confirmed") == "1":
        try:
            source_ms = int(clip.details["timing_snap_ms"])
            if clip.start_ms <= source_ms < clip.end_ms:
                return {"source_ms": source_ms, "confirmed": True}
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    return cached_prediction(clip)


def cached_prediction(clip: Clip) -> dict[str, Any] | None:
    """Return only a current, in-range prediction for this clip geometry."""
    raw = clip.analysis.get(PREDICTION_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        source_ms = int(raw["source_ms"])
        start_ms = int(raw["clip_start_ms"])
        end_ms = int(raw["clip_end_ms"])
    except (KeyError, TypeError, ValueError):
        return None
    if raw.get("predictor_id") != PREDICTOR_ID \
            or raw.get("predictor_version") != PREDICTOR_VERSION:
        return None
    if start_ms != clip.start_ms or end_ms != clip.end_ms:
        return None
    if not clip.start_ms <= source_ms <= clip.end_ms:
        return None
    return raw
