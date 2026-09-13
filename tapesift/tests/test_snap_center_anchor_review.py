from __future__ import annotations

import json

import pytest

from tapesift.research.snap_center_anchor_review import (
    build_review_items,
    validate_exported_label,
)


def _judgment() -> dict:
    return {
        "item_id": "cohort:clip-a",
        "research_cohort_id": "cohort",
        "project_name": "Development Film",
        "clip_id": "clip-a",
        "clip_number": 4,
        "source_video_path": "film.mp4",
        "angles": [
            {"angle": 1, "snap_status": "marked", "actual_snap_ms": 1500,
             "range_start_ms": 1000, "range_end_ms": 2000},
            {"angle": 2, "snap_status": "marked", "actual_snap_ms": 2600,
             "range_start_ms": 2100, "range_end_ms": 3000},
        ],
    }


def test_build_review_items_prioritizes_frozen_failure(tmp_path):
    judgments = tmp_path / "judgments.jsonl"
    judgments.write_text(json.dumps(_judgment()) + "\n", encoding="utf-8")
    atlas = tmp_path / "atlas.json"
    atlas.write_text(json.dumps({
        "failures": [{"item_id": "cohort:clip-a", "angle": 2}]
    }), encoding="utf-8")

    items = build_review_items([judgments], atlas)

    assert len(items) == 2
    assert items[0].angle == 2
    assert items[0].priority_failure is True
    assert items[0].reference_ms == 2350
    assert items[1].priority_failure is False


def test_exported_visible_anchor_requires_matching_normalized_point(tmp_path):
    judgments = tmp_path / "judgments.jsonl"
    judgments.write_text(json.dumps(_judgment()) + "\n", encoding="utf-8")
    atlas = tmp_path / "atlas.json"
    atlas.write_text("{}", encoding="utf-8")
    item = build_review_items([judgments], atlas)[0]
    label = {
        "review_item_id": item.review_item_id,
        "anchor_status": "visible",
        "anchor_x": 0.4,
        "anchor_y": 0.6,
        "reference_sha256": "frame-hash",
    }

    assert validate_exported_label(label, item, "frame-hash") == label
    label["anchor_x"] = 1.2
    with pytest.raises(ValueError, match="between zero and one"):
        validate_exported_label(label, item, "frame-hash")


def test_exported_unavailable_anchor_rejects_coordinates(tmp_path):
    judgments = tmp_path / "judgments.jsonl"
    judgments.write_text(json.dumps(_judgment()) + "\n", encoding="utf-8")
    atlas = tmp_path / "atlas.json"
    atlas.write_text("{}", encoding="utf-8")
    item = build_review_items([judgments], atlas)[0]

    with pytest.raises(ValueError, match="must not contain coordinates"):
        validate_exported_label({
            "review_item_id": item.review_item_id,
            "anchor_status": "occluded",
            "anchor_x": 0.5,
            "anchor_y": None,
            "reference_sha256": "frame-hash",
        }, item, "frame-hash")
