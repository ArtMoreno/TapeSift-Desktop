from __future__ import annotations

import json

import pytest

from tapesift.research.snap_center_anchor_window import AnchorLabelStore


def _package(tmp_path):
    path = tmp_path / "package.json"
    path.write_text(json.dumps({
        "package_id": "package-1",
        "items": [{
            "review_item_id": "clip-a:angle-1",
            "reference_sha256": "frame-a",
        }],
    }), encoding="utf-8")
    return path


def test_store_autosaves_and_resumes_visible_anchor(tmp_path):
    package = _package(tmp_path)
    labels = tmp_path / "labels.jsonl"
    store = AnchorLabelStore(package, labels)

    store.mark_visible("clip-a:angle-1", 0.25, 0.75)
    resumed = AnchorLabelStore(package, labels)

    assert resumed.labels["clip-a:angle-1"]["anchor_x"] == 0.25
    assert resumed.labels["clip-a:angle-1"]["anchor_y"] == 0.75


def test_store_rejects_stale_reference_frame(tmp_path):
    package = _package(tmp_path)
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({
        "review_item_id": "clip-a:angle-1",
        "anchor_status": "visible",
        "anchor_x": 0.25,
        "anchor_y": 0.75,
        "reference_sha256": "old-frame",
    }) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Stale reference frame"):
        AnchorLabelStore(package, labels)


def test_store_unavailable_state_has_no_coordinates(tmp_path):
    package = _package(tmp_path)
    labels = tmp_path / "labels.jsonl"
    store = AnchorLabelStore(package, labels)

    store.mark_unavailable("clip-a:angle-1", "occluded")

    assert store.labels["clip-a:angle-1"]["anchor_status"] == "occluded"
    assert store.labels["clip-a:angle-1"]["anchor_x"] is None
