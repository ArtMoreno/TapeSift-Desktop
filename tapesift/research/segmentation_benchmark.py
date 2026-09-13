"""Offline benchmark tools for TapeSift play segmentation.

This module deliberately calls the shipping detector without changing it.
Ground truth, predictions, and reports live outside project files so research
work cannot affect a user's clips.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import statistics
import subprocess
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tapesift.services.play_detect_service import DetectionResult, detect_plays
# Moved to services so the product does not have to import this sandbox for
# them; re-exported because research modules, scripts and tests already
# import them from here.
from tapesift.services.segment_scoring import (  # noqa: F401
    DEFAULT_IOU_THRESHOLD,
    DEFAULT_RELATIONSHIP_THRESHOLD,
    duration_ms,
    intersection_ms,
    match_segments,
    percentile,
    relationship_failures,
    score_segments,
    segment_iou,
)

SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported manifest schema: {payload.get('schema_version')!r}")
    if not isinstance(payload.get("films"), list):
        raise ValueError("Manifest must contain a films list")
    return payload


def select_films(manifest: dict[str, Any],
                 film_ids: Iterable[str]) -> list[dict[str, Any]]:
    requested = list(film_ids)
    films = manifest["films"]
    if not requested or requested == ["all"]:
        return films
    by_id = {film["film_id"]: film for film in films}
    missing = [film_id for film_id in requested if film_id not in by_id]
    if missing:
        raise ValueError(f"Unknown film id(s): {', '.join(missing)}")
    return [by_id[film_id] for film_id in requested]


def artifact_path(manifest_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else manifest_path.parent / path


def film_id_from_source(source: Path) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", source.stem.casefold()).strip("_")
    return value or "film"


def find_proxy_for_source(source: Path, proxy_root: Path) -> Path | None:
    """Find a completed proxy whose generated name belongs to this source."""
    if not proxy_root.is_dir():
        return None
    prefix = f"{source.stem}_"
    candidates = [
        path for path in proxy_root.rglob("*.preview.mp4")
        if path.name.startswith(prefix) and path.stat().st_size > 0
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) \
        if candidates else None


def discover_films(
    manifest_path: Path,
    source_root: Path,
    proxy_root: Path,
) -> dict[str, Any]:
    """Add local All-22 sources to an ignored machine-local manifest."""
    manifest = load_manifest(manifest_path)
    sources = sorted(source_root.rglob("*.mp4"))
    by_source = {
        str(Path(film["source_file"]).resolve()).casefold(): film
        for film in manifest["films"]
    }
    used_ids = {film["film_id"] for film in manifest["films"]}
    added = 0
    proxied = 0
    for source in sources:
        source_key = str(source.resolve()).casefold()
        proxy = find_proxy_for_source(source, proxy_root)
        if source_key in by_source:
            film = by_source[source_key]
            if proxy is not None:
                film["analysis_source"] = str(proxy)
            proxied += proxy is not None
            continue

        base_id = film_id_from_source(source)
        film_id = base_id
        suffix = 2
        while film_id in used_ids:
            film_id = f"{base_id}_{suffix}"
            suffix += 1
        used_ids.add(film_id)
        film = {
            "film_id": film_id,
            "role": "diagnostic",
            "source_group": source.parent.name,
            "source_file": str(source),
            "analysis_source": str(proxy or source),
            "ground_truth_file": f"ground_truth/{film_id}.jsonl",
            "prediction_file": f"predictions/{film_id}.json",
            "report_file": f"reports/{film_id}.json",
        }
        manifest["films"].append(film)
        by_source[source_key] = film
        added += 1
        proxied += proxy is not None

    manifest["films"].sort(key=lambda film: film["film_id"])
    manifest["updated_at"] = utc_now()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return {
        "source_root": str(source_root),
        "films_found": len(sources),
        "films_added": added,
        "films_with_proxy": proxied,
        "manifest_films": len(manifest["films"]),
    }


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def seed_ground_truth(manifest_path: Path, film: dict[str, Any]) -> dict[str, Any]:
    """Export existing project clips as explicitly unverified seed truth."""
    project_value = film.get("project_file")
    if not project_value:
        raise ValueError(f"{film['film_id']} has no project_file")
    project_path = Path(project_value)
    if not project_path.is_file():
        raise FileNotFoundError(project_path)

    output_path = artifact_path(manifest_path, film["ground_truth_file"])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    connection = _readonly_connection(project_path)
    try:
        rows = connection.execute(
            """
            SELECT id, clip_number, order_index, start_ms, end_ms, clip_title,
                   label, tags_json, notes, details_json, enabled
            FROM clips
            ORDER BY order_index, clip_number
            """
        ).fetchall()
    finally:
        connection.close()

    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        details = json.loads(row["details_json"] or "{}")
        records.append({
            "schema_version": SCHEMA_VERSION,
            "film_id": film["film_id"],
            "play_id": f"{film['film_id']}_{index:04d}",
            "start_ms": row["start_ms"],
            "end_ms": row["end_ms"],
            "angle_starts_ms": [],
            "primary_label": details.get("run_pass", ""),
            "quality_flags": [],
            "verification_status": "seed_unverified",
            "seed_source": "existing_tapesift_project",
            "source_project": str(project_path),
            "source_clip_id": row["id"],
            "source_clip_number": row["clip_number"],
            "source_title": row["clip_title"] or "",
            "enabled": bool(row["enabled"]),
            "notes": row["notes"] or "",
        })

    text = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    output_path.write_text(text, encoding="utf-8")
    return {
        "film_id": film["film_id"],
        "ground_truth_file": str(output_path),
        "records": len(records),
        "verification_status": "seed_unverified",
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return records


def probe_duration_ms(ffprobe_path: Path, video_path: Path) -> int:
    command = [
        str(ffprobe_path),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return int(round(float(completed.stdout.strip()) * 1000))


def serialize_detection(
    film: dict[str, Any],
    analysis_source: Path,
    duration_ms: int,
    runtime_seconds: float,
    result: DetectionResult,
    parameters: dict[str, float],
) -> dict[str, Any]:
    profile = asdict(result.profile) if result.profile is not None else None
    return {
        "schema_version": SCHEMA_VERSION,
        "film_id": film["film_id"],
        "generated_at": utc_now(),
        "analysis_source": str(analysis_source),
        "duration_ms": duration_ms,
        "runtime_seconds": round(runtime_seconds, 3),
        "parameters": parameters,
        "summary": {
            "signal": result.signal,
            "spans_found": result.spans_found,
            "separators_found": result.separators_found,
            "coverage_pct": result.coverage_pct,
            "unclassified_ms": result.unclassified_ms,
            "profile": profile,
        },
        "plays": [asdict(play) for play in result.plays],
        "unclassified": [asdict(segment) for segment in result.unclassified],
    }


def run_detection(
    manifest_path: Path,
    film: dict[str, Any],
    ffmpeg_path: Path,
    ffprobe_path: Path,
    *,
    separator_max_s: float,
    min_play_s: float,
    max_play_s: float,
    scene_threshold: float,
) -> dict[str, Any]:
    analysis_source = Path(film.get("analysis_source")
                           or film["source_file"])
    if not analysis_source.is_file():
        raise FileNotFoundError(analysis_source)
    duration_ms = probe_duration_ms(ffprobe_path, analysis_source)
    parameters = {
        "separator_max_s": separator_max_s,
        "min_play_s": min_play_s,
        "max_play_s": max_play_s,
        "scene_threshold": scene_threshold,
    }
    started = time.perf_counter()
    result = detect_plays(
        str(ffmpeg_path),
        analysis_source,
        duration_ms,
        separator_max_s=separator_max_s,
        min_play_s=min_play_s,
        max_play_s=max_play_s,
        scene_threshold=scene_threshold,
    )
    runtime_seconds = time.perf_counter() - started
    payload = serialize_detection(
        film, analysis_source, duration_ms, runtime_seconds, result, parameters)
    output_path = artifact_path(manifest_path, film["prediction_file"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload




def score_film(
    manifest_path: Path,
    film: dict[str, Any],
    *,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    relationship_threshold: float = DEFAULT_RELATIONSHIP_THRESHOLD,
) -> dict[str, Any]:
    truth_path = artifact_path(manifest_path, film["ground_truth_file"])
    prediction_path = artifact_path(manifest_path, film["prediction_file"])
    if not truth_path.is_file():
        raise FileNotFoundError(truth_path)
    if not prediction_path.is_file():
        raise FileNotFoundError(prediction_path)
    truth = [
        record for record in read_jsonl(truth_path)
        if record.get("enabled", True)
        and record.get("verification_status") != "excluded"
    ]
    prediction_payload = json.loads(
        prediction_path.read_text(encoding="utf-8"))
    predictions = prediction_payload["plays"]
    metrics = score_segments(
        truth,
        predictions,
        iou_threshold=iou_threshold,
        relationship_threshold=relationship_threshold,
    )
    verification = Counter(
        record.get("verification_status", "unknown") for record in truth)
    unclassified = prediction_payload.get("unclassified", [])
    film_duration = int(prediction_payload.get("duration_ms") or 0)
    unclassified_ms = sum(duration_ms(segment) for segment in unclassified)
    report = {
        "schema_version": SCHEMA_VERSION,
        "film_id": film["film_id"],
        "generated_at": utc_now(),
        "preliminary": any(
            status != "verified" for status in verification),
        "ground_truth_statuses": dict(verification),
        "detector_runtime_seconds": prediction_payload.get("runtime_seconds"),
        "detector_signal": prediction_payload.get(
            "summary", {}).get("signal"),
        "needs_review_prediction_count": sum(
            bool(play.get("needs_review")) for play in predictions),
        "unclassified_count": len(unclassified),
        "unclassified_ms": unclassified_ms,
        "unclassified_pct": (
            100 * unclassified_ms / film_duration if film_duration else 0.0),
        "metrics": metrics,
    }
    output_path = artifact_path(manifest_path, film["report_file"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def summarize_predictions(
    manifest_path: Path,
    films: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a structural diagnostic report across every completed film."""
    rows: list[dict[str, Any]] = []
    for film in films:
        prediction_path = artifact_path(
            manifest_path, film["prediction_file"])
        if not prediction_path.is_file():
            continue
        payload = json.loads(prediction_path.read_text(encoding="utf-8"))
        summary = payload["summary"]
        profile = summary.get("profile") or {}
        plays = payload["plays"]
        duration_seconds = payload["duration_ms"] / 1000
        runtime_seconds = float(payload["runtime_seconds"])
        review_count = sum(bool(play.get("needs_review")) for play in plays)
        unclassified_ms = int(summary.get("unclassified_ms") or 0)
        source_path = Path(film["source_file"])
        analysis_path = Path(film.get("analysis_source")
                             or film["source_file"])
        rows.append({
            "film_id": film["film_id"],
            "source_group": film.get("source_group", source_path.parent.name),
            "film_name": source_path.stem,
            "analysis_kind": (
                "proxy" if analysis_path.name.endswith(".preview.mp4")
                else "source"),
            "duration_minutes": round(duration_seconds / 60, 2),
            "runtime_seconds": round(runtime_seconds, 3),
            "processing_speed_x": round(
                duration_seconds / runtime_seconds, 2)
            if runtime_seconds else None,
            "signal": summary["signal"],
            "plays": len(plays),
            "review_count": review_count,
            "review_pct": round(
                100 * review_count / len(plays), 2) if plays else 0.0,
            "unclassified_count": len(payload.get("unclassified", [])),
            "unclassified_minutes": round(unclassified_ms / 60_000, 2),
            "unclassified_pct": round(
                100 * unclassified_ms / payload["duration_ms"], 2)
            if payload["duration_ms"] else 0.0,
            "coverage_pct": round(float(summary["coverage_pct"]), 2),
            "modal_angles": profile.get("modal_angles"),
            "modal_share": profile.get("modal_share"),
            "median_play_seconds": round(
                float(profile.get("median_play_ms") or 0) / 1000, 2),
            "angle_count_suspect": (
                bool(profile)
                and profile.get("modal_angles", 0) > 0
                and (float(profile.get("median_play_ms") or 0) / 1000)
                / profile["modal_angles"] > 26.0
            ),
        })

    signal_counts = Counter(row["signal"] for row in rows)
    kind_counts = Counter(row["analysis_kind"] for row in rows)
    aggregate = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "manifest_films": len(films),
        "completed_films": len(rows),
        "signal_counts": dict(signal_counts),
        "analysis_kind_counts": dict(kind_counts),
        "total_duration_hours": round(
            sum(row["duration_minutes"] for row in rows) / 60, 2),
        "total_runtime_minutes": round(
            sum(row["runtime_seconds"] for row in rows) / 60, 2),
        "median_review_pct": (
            statistics.median(row["review_pct"] for row in rows)
            if rows else None),
        "median_unclassified_pct": (
            statistics.median(row["unclassified_pct"] for row in rows)
            if rows else None),
        "highest_review": sorted(
            rows, key=lambda row: row["review_pct"], reverse=True)[:5],
        "highest_unclassified": sorted(
            rows, key=lambda row: row["unclassified_pct"], reverse=True)[:5],
        "films": sorted(rows, key=lambda row: row["film_name"]),
    }

    reports_dir = manifest_path.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "library_sweep.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    if rows:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(aggregate["films"])
        (reports_dir / "library_sweep.csv").write_text(
            stream.getvalue(), encoding="utf-8")

    table_lines = [
        "# TapeSift All-22 Structural Sweep",
        "",
        f"Completed: {len(rows)} of {len(films)} films",
        "",
        "| Film | Side | Input | Signal | Plays | Review | Unclassified | Angles |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in aggregate["films"]:
        side = "Defense" if "defense" in row["source_group"].casefold() \
            else "Offense"
        table_lines.append(
            f"| {row['film_name']} | {side} | {row['analysis_kind']} | "
            f"{row['signal']} | {row['plays']} | {row['review_pct']:.1f}% | "
            f"{row['unclassified_pct']:.1f}% | "
            f"{row['modal_angles'] or '?'} |")
    (reports_dir / "library_sweep.md").write_text(
        "\n".join(table_lines) + "\n", encoding="utf-8")
    return aggregate
