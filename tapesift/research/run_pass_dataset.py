"""Local JSONL manifests for developing a non-AI run/pass classifier."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from tapesift.services.run_pass_label_service import resolve_run_pass_label


SCHEMA_VERSION = "1.2"


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def export_project_run_pass_labels(
    project_path: Path,
    output_path: Path,
    *,
    research_cohort_id: str = "",
    approved_clip_numbers: set[int] | None = None,
    include_derived: bool = False,
    include_disabled: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Export privacy-restrained time ranges and canonical analyst labels.

    Notes, player names, and arbitrary tags are deliberately omitted. The
    manifest stays local and references the source video by path; it never
    copies or uploads footage.
    """
    project_path = project_path.resolve()
    output_path = output_path.resolve()
    research_cohort_id = research_cohort_id.strip()
    if bool(research_cohort_id) != (approved_clip_numbers is not None):
        raise ValueError(
            "Research truth requires both a cohort ID and an explicit "
            "approved clip selection"
        )
    if approved_clip_numbers is not None and not approved_clip_numbers:
        raise ValueError("Approved clip selection cannot be empty")
    if not project_path.is_file():
        raise FileNotFoundError(project_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Refusing to replace existing label manifest: {output_path}"
        )

    connection = _readonly_connection(project_path)
    try:
        project = connection.execute(
            "SELECT name, source_video_path FROM projects LIMIT 1"
        ).fetchone()
        if project is None:
            raise ValueError(f"Project has no project row: {project_path}")
        rows = connection.execute(
            """
            SELECT id, clip_number, start_ms, end_ms, enabled,
                   details_json, tags_json, detection_lineage_json
            FROM clips
            ORDER BY order_index, clip_number, id
            """
        ).fetchall()
        try:
            candidate_rows = connection.execute(
                """
                SELECT id, angle_starts_json, angle_count
                FROM autodetect_candidates
                """
            ).fetchall()
        except sqlite3.OperationalError:
            # Older projects predate immutable CSE candidate capture.
            candidate_rows = []
    finally:
        connection.close()
    candidates = {
        str(row["id"]): {
            "angle_starts_ms": json.loads(
                row["angle_starts_json"] or "[]"),
            "angle_count": int(row["angle_count"]),
        }
        for row in candidate_rows
    }

    records: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    for row in rows:
        clip_number = int(row["clip_number"])
        if approved_clip_numbers is not None \
                and clip_number not in approved_clip_numbers:
            skipped["outside_cohort"] += 1
            continue
        enabled = bool(row["enabled"])
        if not enabled and not include_disabled:
            skipped["disabled"] += 1
            continue
        details = json.loads(row["details_json"] or "{}")
        tags = json.loads(row["tags_json"] or "[]")
        lineage = json.loads(row["detection_lineage_json"] or "{}")
        candidate_ids = lineage.get("candidate_ids", [])
        if isinstance(candidate_ids, str):
            candidate_ids = [candidate_ids]
        owned_candidates = [
            candidates[str(candidate_id)]
            for candidate_id in candidate_ids
            if str(candidate_id) in candidates
        ]
        internal_angle_starts = sorted({
            int(value)
            for candidate in owned_candidates
            for value in candidate["angle_starts_ms"]
            if int(row["start_ms"]) < int(value) < int(row["end_ms"])
        })
        angle_starts_ms = [int(row["start_ms"]), *internal_angle_starts]
        resolution = resolve_run_pass_label(details, tags)
        if not resolution.label:
            skipped["unlabeled"] += 1
            continue
        if resolution.conflict:
            skipped["conflict"] += 1
            continue
        if resolution.source != "explicit" and not include_derived:
            skipped["derived"] += 1
            continue

        labels[resolution.label] += 1
        research_approved = bool(
            research_cohort_id and resolution.trainable
        )
        records.append({
            "schema_version": SCHEMA_VERSION,
            "dataset_kind": "tapesift_run_pass_labels",
            "research_cohort_id": research_cohort_id,
            "research_approved": research_approved,
            "project_name": str(project["name"]),
            "source_video_path": str(project["source_video_path"]),
            "source_project": str(project_path),
            "clip_id": str(row["id"]),
            "clip_number": clip_number,
            "start_ms": int(row["start_ms"]),
            "end_ms": int(row["end_ms"]),
            "enabled": enabled,
            "label": resolution.label,
            "label_display": resolution.display,
            "label_source": resolution.source,
            "label_valid": resolution.trainable,
            "trainable": research_approved,
            "play_type": str(details.get("play_type", "")),
            "play_action": str(details.get("play_action", "")),
            "angle_starts_ms": angle_starts_ms,
            "angle_count": len(angle_starts_ms),
            "angle_source": (
                "cse_candidate" if owned_candidates else "unavailable"
            ),
            "result": str(details.get("result", "")),
            "action": str(details.get("action", "")),
            "autodetect_reviewed": bool(lineage.get("reviewed_at")),
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return {
        "project": str(project_path),
        "output": str(output_path),
        "records": len(records),
        "label_counts": dict(sorted(labels.items())),
        "skipped": dict(sorted(skipped.items())),
        "research_cohort_id": research_cohort_id,
        "research_approved": bool(research_cohort_id),
        "approved_clip_count": (
            len(approved_clip_numbers)
            if approved_clip_numbers is not None else 0
        ),
        "include_derived": include_derived,
        "include_disabled": include_disabled,
    }
