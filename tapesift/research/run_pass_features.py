"""Reproducible FFmpeg measurements for local run/pass research.

The extractor deliberately stops at observable video measurements. It does
not predict a label, alter a clip boundary, or inspect private notes. Later
iterations can evaluate transparent rules against these frozen features.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from tapesift.services import ffmpeg_service


FEATURE_SCHEMA_VERSION = "1.0"
EXTRACTOR_NAME = "ffmpeg-signalstats"
EXTRACTOR_VERSION = "1"
DEFAULT_SAMPLE_FPS = 4.0
DEFAULT_SAMPLE_WIDTH = 320


def reject_quarantined_input(path: Path) -> Path:
    """Refuse quarantined artifacts at training and dataset boundaries."""
    resolved = path.resolve()
    if any(part.casefold() == "quarantine" for part in resolved.parts):
        raise ValueError(
            f"Refusing quarantined training or dataset input: {resolved}")
    return resolved

_FRAME_RE = re.compile(
    r"^frame:(?P<frame>\d+)\s+pts:\S+\s+pts_time:(?P<time>-?[\d.]+)"
)
_VALUE_RE = re.compile(
    r"^lavfi\.signalstats\.(?P<key>[A-Z]+)=(?P<value>[-+\d.eE]+)"
)
_SCENE_RE = re.compile(
    r"^lavfi\.scene_score=(?P<value>[-+\d.eE]+)"
)
_WANTED_VALUES = frozenset({
    "YAVG", "SATAVG", "YDIF", "UDIF", "VDIF",
})


@dataclass(frozen=True)
class SignalFrame:
    """The small, explainable subset retained from one sampled frame."""

    time_s: float
    yavg: float
    satavg: float
    ydif: float
    udif: float
    vdif: float
    scene_score: float = 0.0


def build_signalstats_command(
    ffmpeg_path: str,
    source: Path,
    start_ms: int,
    end_ms: int,
    *,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    sample_width: int = DEFAULT_SAMPLE_WIDTH,
    include_scene_score: bool = False,
) -> list[str]:
    """Build the stable FFmpeg measurement command for one confirmed clip."""
    if end_ms <= start_ms:
        raise ValueError("Run/pass feature range must have positive duration")
    if sample_fps <= 0:
        raise ValueError("Sample FPS must be positive")
    if sample_width < 16:
        raise ValueError("Sample width must be at least 16 pixels")

    filters = [
        f"fps={sample_fps:g}",
        f"scale={sample_width}:-2:flags=area",
        "format=yuv420p",
    ]
    if include_scene_score:
        # gte(scene,0) retains every frame while asking FFmpeg to calculate
        # the same explainable scene score CSE already uses for hard cuts.
        filters.append("select='gte(scene,0)'")
    filters.extend(("signalstats", "metadata=print:file=-"))
    return [
        ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        ffmpeg_service.ms_to_ffmpeg_time(start_ms),
        "-t",
        ffmpeg_service.ms_to_ffmpeg_time(end_ms - start_ms),
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-vf",
        ",".join(filters),
        "-f",
        "null",
        "-",
    ]


def parse_signalstats(output: str) -> list[SignalFrame]:
    """Parse ``metadata=print`` output without depending on FFmpeg log prose."""
    parsed: list[SignalFrame] = []
    time_s: float | None = None
    values: dict[str, float] = {}
    scene_score = 0.0

    def finish_frame() -> None:
        if time_s is None or not _WANTED_VALUES.issubset(values):
            return
        parsed.append(SignalFrame(
            time_s=time_s,
            yavg=values["YAVG"],
            satavg=values["SATAVG"],
            ydif=values["YDIF"],
            udif=values["UDIF"],
            vdif=values["VDIF"],
            scene_score=scene_score,
        ))

    for raw_line in output.splitlines():
        line = raw_line.strip()
        frame_match = _FRAME_RE.match(line)
        if frame_match:
            finish_frame()
            time_s = float(frame_match.group("time"))
            values = {}
            scene_score = 0.0
            continue
        value_match = _VALUE_RE.match(line)
        if value_match and value_match.group("key") in _WANTED_VALUES:
            values[value_match.group("key")] = float(
                value_match.group("value")
            )
            continue
        scene_match = _SCENE_RE.match(line)
        if scene_match:
            scene_score = float(scene_match.group("value"))
    finish_frame()
    return parsed


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return statistics.fmean(items) if items else 0.0


def _stddev(values: Iterable[float]) -> float:
    items = list(values)
    return statistics.pstdev(items) if len(items) > 1 else 0.0


def _percentile(values: Iterable[float], percentile: float) -> float:
    items = sorted(values)
    if not items:
        return 0.0
    position = (len(items) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return items[lower]
    fraction = position - lower
    return items[lower] + (items[upper] - items[lower]) * fraction


def _rounded(value: float) -> float:
    return round(float(value), 6)


def summarize_signalstats(
    frames: Iterable[SignalFrame],
    *,
    duration_ms: int,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
) -> dict[str, int | float]:
    """Turn per-frame readings into one transparent motion fingerprint."""
    items = list(frames)
    if not items:
        raise ValueError("FFmpeg returned no signalstats frames")

    # FFmpeg reports zero differences for the first decoded frame because
    # there is no previous frame. Excluding it prevents an artificial still
    # frame at the beginning of every clip.
    motion_items = items[1:] or items
    y_motion = [frame.ydif for frame in motion_items]
    uv_motion = [
        (frame.udif + frame.vdif) / 2.0 for frame in motion_items
    ]
    duration_s = max(duration_ms / 1000.0, 0.001)

    quarters: list[list[float]] = [[], [], [], []]
    for frame in motion_items:
        relative = min(max(frame.time_s / duration_s, 0.0), 0.999999)
        quarters[int(relative * 4)].append(frame.ydif)

    peak_index = max(
        range(len(motion_items)),
        key=lambda index: motion_items[index].ydif,
    )
    peak_position = min(
        max(motion_items[peak_index].time_s / duration_s, 0.0),
        1.0,
    )
    early = _mean(quarters[0])
    late = _mean(quarters[-1])

    return {
        "sample_fps": _rounded(sample_fps),
        "sampled_frames": len(items),
        "duration_ms": duration_ms,
        "motion_y_mean": _rounded(_mean(y_motion)),
        "motion_y_std": _rounded(_stddev(y_motion)),
        "motion_y_p50": _rounded(_percentile(y_motion, 0.50)),
        "motion_y_p90": _rounded(_percentile(y_motion, 0.90)),
        "motion_y_p95": _rounded(_percentile(y_motion, 0.95)),
        "motion_y_max": _rounded(max(y_motion)),
        "motion_uv_mean": _rounded(_mean(uv_motion)),
        "still_frame_share": _rounded(
            sum(value <= 1.5 for value in y_motion) / len(y_motion)
        ),
        "high_motion_share": _rounded(
            sum(value >= 6.0 for value in y_motion) / len(y_motion)
        ),
        "luma_mean": _rounded(_mean(frame.yavg for frame in items)),
        "luma_std": _rounded(_stddev(frame.yavg for frame in items)),
        "saturation_mean": _rounded(
            _mean(frame.satavg for frame in items)
        ),
        "motion_q1_mean": _rounded(early),
        "motion_q2_mean": _rounded(_mean(quarters[1])),
        "motion_q3_mean": _rounded(_mean(quarters[2])),
        "motion_q4_mean": _rounded(late),
        "motion_late_early_ratio": _rounded(
            late / early if early > 0.000001 else 0.0
        ),
        "motion_peak_position": _rounded(peak_position),
    }


def read_signalstats_frames(
    ffmpeg_path: str,
    source: Path,
    start_ms: int,
    end_ms: int,
    *,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    sample_width: int = DEFAULT_SAMPLE_WIDTH,
    include_scene_score: bool = False,
) -> list[SignalFrame]:
    """Run FFmpeg once and retain the explainable sampled time series."""
    command = build_signalstats_command(
        ffmpeg_path,
        source,
        start_ms,
        end_ms,
        sample_fps=sample_fps,
        sample_width=sample_width,
        include_scene_score=include_scene_score,
    )
    timeout_s = max(60.0, ((end_ms - start_ms) / 1000.0) * 5.0)
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=ffmpeg_service.CREATE_NO_WINDOW
            | ffmpeg_service.IDLE_PRIORITY_CLASS,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"FFmpeg timed out measuring {source.name}"
        ) from exc
    if process.returncode:
        detail = (process.stderr or "").strip() or "unknown FFmpeg error"
        raise RuntimeError(
            f"FFmpeg could not measure {source.name}: {detail}"
        )
    frames = parse_signalstats(process.stdout or "")
    if not frames:
        raise ValueError("FFmpeg returned no signalstats frames")
    return frames


def measure_clip(
    ffmpeg_path: str,
    source: Path,
    start_ms: int,
    end_ms: int,
    *,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    sample_width: int = DEFAULT_SAMPLE_WIDTH,
) -> dict[str, int | float]:
    """Run FFmpeg once and return the clip's summarized measurements."""
    frames = read_signalstats_frames(
        ffmpeg_path,
        source,
        start_ms,
        end_ms,
        sample_fps=sample_fps,
        sample_width=sample_width,
    )
    return summarize_signalstats(
        frames,
        duration_ms=end_ms - start_ms,
        sample_fps=sample_fps,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on {path.name} line {line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise ValueError(
                f"Expected an object on {path.name} line {line_number}"
            )
        records.append(value)
    return records


def extract_run_pass_features(
    labels_path: Path,
    output_path: Path,
    ffmpeg_path: Path,
    *,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    sample_width: int = DEFAULT_SAMPLE_WIDTH,
    overwrite: bool = False,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
    measure: Callable[..., dict[str, int | float]] = measure_clip,
) -> dict[str, Any]:
    """Measure every trainable label and write a local feature JSONL file."""
    labels_path = reject_quarantined_input(labels_path)
    output_path = output_path.resolve()
    ffmpeg_path = ffmpeg_path.resolve()
    if not labels_path.is_file():
        raise FileNotFoundError(labels_path)
    if not ffmpeg_path.is_file():
        raise FileNotFoundError(ffmpeg_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Refusing to replace existing features: {output_path}"
        )

    labels = _read_jsonl(labels_path)
    trainable = [record for record in labels if record.get("trainable")]
    if not trainable:
        raise ValueError("Label manifest has no trainable records")
    cohort_ids = {
        str(record.get("research_cohort_id", "")).strip()
        for record in trainable
    }
    if "" in cohort_ids or len(cohort_ids) != 1:
        raise ValueError(
            "Trainable labels must belong to one approved research cohort"
        )
    research_cohort_id = next(iter(cohort_ids))

    results: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for index, record in enumerate(trainable, start=1):
        source = Path(str(record.get("source_video_path", "")))
        if not source.is_file():
            raise FileNotFoundError(
                f"Source video for clip {record.get('clip_number')}: {source}"
            )
        start_ms = int(record["start_ms"])
        end_ms = int(record["end_ms"])
        if on_progress is not None:
            on_progress(index, len(trainable), record)
        features = measure(
            str(ffmpeg_path),
            source,
            start_ms,
            end_ms,
            sample_fps=sample_fps,
            sample_width=sample_width,
        )
        label = str(record.get("label", ""))
        counts[label] += 1
        results.append({
            "schema_version": FEATURE_SCHEMA_VERSION,
            "dataset_kind": "tapesift_run_pass_features",
            "research_cohort_id": research_cohort_id,
            "feature_extractor": {
                "name": EXTRACTOR_NAME,
                "version": EXTRACTOR_VERSION,
                "ffmpeg_version": ffmpeg_service.get_version(
                    str(ffmpeg_path)
                ),
                "sample_fps": sample_fps,
                "sample_width": sample_width,
            },
            "clip_id": str(record.get("clip_id", "")),
            "clip_number": int(record.get("clip_number", 0)),
            "project_name": str(record.get("project_name", "")),
            "source_project": str(record.get("source_project", "")),
            "source_video_path": str(source),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "label": label,
            "label_display": str(record.get("label_display", "")),
            "play_type": str(record.get("play_type", "")),
            "play_action": str(record.get("play_action", "")),
            "features": features,
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n"
            for result in results
        ),
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return {
        "labels": str(labels_path),
        "output": str(output_path),
        "records": len(results),
        "label_counts": dict(sorted(counts.items())),
        "research_cohort_id": research_cohort_id,
        "extractor": f"{EXTRACTOR_NAME}-v{EXTRACTOR_VERSION}",
        "sample_fps": sample_fps,
        "sample_width": sample_width,
    }
