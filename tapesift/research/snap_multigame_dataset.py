"""Deterministic, label-agnostic clip cohorts for snap localization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "1.0"
DATASET_KIND = "tapesift_snap_localization_clips"
PLAN_KIND = "tapesift_multigame_snap_dataset_plan"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evenly_spaced_indices(total: int, selected: int) -> list[int]:
    if total <= 0 or selected <= 0:
        raise ValueError("Cohort sizes must be positive")
    if selected >= total:
        return list(range(total))
    if selected == 1:
        return [total // 2]
    indices = {
        round(index * (total - 1) / (selected - 1))
        for index in range(selected)
    }
    if len(indices) != selected:
        raise RuntimeError("Even cohort selection produced duplicate indices")
    return sorted(indices)


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def export_snap_cohort(
    project_path: Path,
    output_path: Path,
    *,
    cohort_id: str,
    split_role: str,
    sample_count: int,
    overwrite: bool = False,
) -> dict[str, Any]:
    project_path = project_path.resolve()
    output_path = output_path.resolve()
    if split_role not in {"development", "validation"}:
        raise ValueError("Snap cohorts may only be development or validation")
    if not cohort_id.strip():
        raise ValueError("Snap cohort requires an ID")
    if not project_path.is_file():
        raise FileNotFoundError(project_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(output_path)

    connection = _readonly(project_path)
    try:
        project = connection.execute(
            "SELECT name, source_video_path FROM projects LIMIT 1"
        ).fetchone()
        if project is None:
            raise ValueError(f"Project has no project row: {project_path}")
        clips = connection.execute(
            """
            SELECT id, clip_number, order_index, start_ms, end_ms,
                   detection_lineage_json
            FROM clips
            WHERE enabled = 1
            ORDER BY order_index, clip_number, id
            """
        ).fetchall()
        try:
            candidate_rows = connection.execute(
                "SELECT id, angle_starts_json FROM autodetect_candidates"
            ).fetchall()
        except sqlite3.OperationalError:
            candidate_rows = []
    finally:
        connection.close()
    if not clips:
        raise ValueError(f"Project has no enabled clips: {project_path}")
    selected_rows = [
        clips[index] for index in evenly_spaced_indices(len(clips), sample_count)
    ]
    candidate_starts = {
        str(row["id"]): [int(value) for value in json.loads(
            row["angle_starts_json"] or "[]"
        )]
        for row in candidate_rows
    }
    records: list[dict[str, Any]] = []
    for row in selected_rows:
        lineage = json.loads(row["detection_lineage_json"] or "{}")
        candidate_ids = lineage.get("candidate_ids", [])
        if isinstance(candidate_ids, str):
            candidate_ids = [candidate_ids]
        internal_starts = sorted({
            value
            for candidate_id in candidate_ids
            for value in candidate_starts.get(str(candidate_id), [])
            if int(row["start_ms"]) < value < int(row["end_ms"])
        })
        angle_starts = [int(row["start_ms"]), *internal_starts]
        records.append({
            "schema_version": SCHEMA_VERSION,
            "dataset_kind": DATASET_KIND,
            "research_cohort_id": cohort_id,
            "research_approved": True,
            "split_role": split_role,
            "project_name": str(project["name"]),
            "source_video_path": str(project["source_video_path"]),
            "source_project": str(project_path),
            "clip_id": str(row["id"]),
            "clip_number": int(row["clip_number"]),
            "start_ms": int(row["start_ms"]),
            "end_ms": int(row["end_ms"]),
            "enabled": True,
            "label": "snap",
            "label_display": "Snap Localization",
            "label_source": "not_required",
            "label_valid": True,
            "trainable": True,
            "play_type": "",
            "play_action": "",
            "angle_starts_ms": angle_starts,
            "angle_count": len(angle_starts),
            "angle_source": "cse_candidate" if internal_starts else "temporal_split",
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f"{output_path.name}.tmp")
    temporary.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return {
        "cohort_id": cohort_id,
        "split_role": split_role,
        "project_path": str(project_path),
        "project_name": str(project["name"]),
        "source_video_path": str(project["source_video_path"]),
        "available_enabled_clips": len(clips),
        "selected_clips": len(records),
        "selected_clip_numbers": [record["clip_number"] for record in records],
        "manifest_path": str(output_path),
        "manifest_sha256": _sha256(output_path),
    }


def prepare_multigame_dataset(
    plan_path: Path,
    output_dir: Path,
    manifest_path: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("dataset_kind") != PLAN_KIND:
        raise ValueError("Unsupported multigame snap dataset plan")
    cohorts = plan.get("cohorts")
    if not isinstance(cohorts, list) or not cohorts:
        raise ValueError("Multigame plan has no cohorts")
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for cohort in cohorts:
        if not isinstance(cohort, dict):
            raise ValueError("Multigame plan contains an invalid cohort")
        cohort_id = str(cohort["cohort_id"])
        result = export_snap_cohort(
            Path(str(cohort["project_path"])),
            output_dir / f"{cohort_id}-clips.jsonl",
            cohort_id=cohort_id,
            split_role=str(cohort["split_role"]),
            sample_count=int(cohort["sample_count"]),
            overwrite=overwrite,
        )
        results.append(result)
    development = sum(
        int(result["selected_clips"])
        for result in results if result["split_role"] == "development"
    )
    validation = sum(
        int(result["selected_clips"])
        for result in results if result["split_role"] == "validation"
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_kind": PLAN_KIND,
        "dataset_id": str(plan["dataset_id"]),
        "status": "clip_cohorts_frozen_features_pending",
        "selection": "evenly_spaced_enabled_clips_by_project_order",
        "existing_calibrated_development_plays": int(
            plan.get("existing_calibrated_development_plays", 0)
        ),
        "new_development_plays": development,
        "validation_plays": validation,
        "planned_development_plays": development + int(
            plan.get("existing_calibrated_development_plays", 0)
        ),
        "cohorts": results,
        "final_holdout": plan.get("final_holdout", {}),
        "holdout_status": "recorded_paths_not_opened",
        "plan_sha256": _sha256(plan_path),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def finalize_temporal_features(
    manifest_path: Path,
    feature_dir: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cohorts = manifest.get("cohorts")
    if not isinstance(cohorts, list) or not cohorts:
        raise ValueError("Multigame manifest has no cohorts")
    total_records = 0
    total_angles = 0
    for cohort in cohorts:
        feature_path = feature_dir / (
            f"{cohort['cohort_id']}-temporal-features.jsonl"
        )
        if not feature_path.is_file():
            raise FileNotFoundError(feature_path)
        records = _read_feature_records(feature_path)
        expected = int(cohort["selected_clips"])
        if len(records) != expected:
            raise ValueError(
                f"{cohort['cohort_id']} has {len(records)} features, expected {expected}"
            )
        angle_records = sum(
            len(record.get("temporal_diagnostics", {}).get("angles", []))
            for record in records
        )
        cohort["temporal_feature_path"] = str(feature_path.resolve())
        cohort["temporal_feature_sha256"] = _sha256(feature_path)
        cohort["temporal_feature_records"] = len(records)
        cohort["proposed_angle_records"] = angle_records
        total_records += len(records)
        total_angles += angle_records
    manifest["status"] = "temporal_features_ready_for_calibration"
    manifest["temporal_feature_records"] = total_records
    manifest["proposed_angle_records"] = total_angles
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def _read_feature_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number} is not an object")
            records.append(record)
    return records
