"""Prepare the remaining multi-game center/QB exchange-anchor queue."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tapesift.research.snap_center_anchor_review import (
    build_review_items,
    render_review_package,
)
from tapesift.research.snap_onset_refiner import sha256_file


def _resolve(value: str | Path, repository_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repository_root / path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_number}") from exc
    return rows


def _fingerprint(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def prepare_multigame_anchor_review(protocol_path: Path) -> dict[str, Any]:
    protocol_path = protocol_path.resolve()
    repository_root = protocol_path.parents[1]
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    report_path = _resolve(protocol["candidate_ranker_report"], repository_root)
    judgment_path = _resolve(protocol["judgment_path"], repository_root)
    existing_package_path = _resolve(
        protocol["existing_anchor_package"], repository_root
    )
    existing_labels_path = _resolve(
        protocol["existing_anchor_labels"], repository_root
    )
    output_directory = _resolve(protocol["output_directory"], repository_root)
    manifest_path = _resolve(protocol["manifest_path"], repository_root)
    expected = protocol["expected_counts"]

    existing_labels = _read_jsonl(existing_labels_path)
    if len(existing_labels) != int(expected["existing_anchor_labels"]):
        raise ValueError(
            f"Expected {expected['existing_anchor_labels']} existing anchors, "
            f"found {len(existing_labels)}"
        )
    ranker_report = json.loads(report_path.read_text(encoding="utf-8"))
    development_cohorts = set(protocol["new_development_cohorts"])
    threshold_ms = int(
        protocol["priority_rule"]["absolute_error_greater_than_ms"]
    )
    failures = [
        {
            "item_id": str(row["item_id"]),
            "clip_id": str(row["clip_id"]),
            "angle": int(row["angle"]),
            "error_ms": int(row["error_ms"]),
        }
        for row in ranker_report["lofo_candidate_ranker"]["rows"]
        if str(row["cohort_id"]) in development_cohorts
        and abs(int(row["error_ms"])) > threshold_ms
    ]
    if len(failures) != int(expected["priority_failures"]):
        raise ValueError(
            f"Expected {expected['priority_failures']} priority failures, "
            f"found {len(failures)}"
        )

    output_directory.mkdir(parents=True, exist_ok=True)
    failure_atlas_path = output_directory / "priority-failures.json"
    failure_atlas_path.write_text(
        json.dumps({"failures": failures}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    items = build_review_items([judgment_path], failure_atlas_path)
    if len(items) != int(expected["new_anchor_items"]):
        raise ValueError(
            f"Expected {expected['new_anchor_items']} review items, found {len(items)}"
        )
    priority_count = sum(item.priority_failure for item in items)
    cohort_count = len({item.research_cohort_id for item in items})
    video_count = len({item.source_video_path for item in items})
    checks = {
        "priority_failures": priority_count == int(expected["priority_failures"]),
        "new_cohorts": cohort_count == int(expected["new_cohorts"]),
        "new_videos": video_count == int(expected["new_videos"]),
        "combined_anchor_items": (
            len(existing_labels) + len(items)
            == int(expected["combined_anchor_items"])
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"Anchor review count checks failed: {checks}")

    package = render_review_package(items, output_directory)
    manifest = {
        "schema_version": "1.0",
        "iteration_id": protocol["iteration_id"],
        "status": "multigame_anchor_labeling_ready",
        "purpose": (
            "Train an automatic center/QB exchange-area locator and local "
            "interaction features; manual anchors are development supervision only."
        ),
        "methodology": {
            "reference_offset_ms": -250,
            "normalized_single_point": True,
            "priority_rule": protocol["priority_rule"],
            "runtime_manual_anchor_required": False,
        },
        "counts": {
            "existing_anchor_labels": len(existing_labels),
            "new_anchor_items": len(items),
            "priority_failures": priority_count,
            "new_cohorts": cohort_count,
            "new_videos": video_count,
            "combined_anchor_items": len(existing_labels) + len(items),
        },
        "checks": checks,
        "package": {
            **package,
            "output_path": str(output_directory.resolve()),
            "labels_path": str((output_directory / "labels.jsonl").resolve()),
        },
        "inputs": {
            "protocol": _fingerprint(protocol_path),
            "candidate_ranker_report": _fingerprint(report_path),
            "judgments": _fingerprint(judgment_path),
            "existing_anchor_package": _fingerprint(existing_package_path),
            "existing_anchor_labels": _fingerprint(existing_labels_path),
            "priority_failure_atlas": _fingerprint(failure_atlas_path),
        },
        "sealed_data": protocol["sealed_data_policy"],
        "next_gate": (
            "complete all 167 anchors, train anchor localization and local "
            "center/QB interaction features with leave-one-game-out evaluation"
        ),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Prepared {len(items)} new anchor items with {priority_count} "
        f"ranker failures first. Combined development anchors: "
        f"{len(existing_labels) + len(items)}.",
        flush=True,
    )
    print(f"Package: {output_directory / 'package.json'}", flush=True)
    print(f"Labels: {output_directory / 'labels.jsonl'}", flush=True)
    return manifest
