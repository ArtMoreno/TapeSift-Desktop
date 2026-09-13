import json

from scripts.build_run_pass_frame_strips import (
    load_center_anchors,
    load_marked_snap_anchors,
    load_temporal_anchors,
)


def test_load_marked_snap_anchors_uses_first_angle_only(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"lofo_candidate_ranker": {"rows": [
        {"clip_id": "a", "angle": 1, "actual_snap_ms": 1200},
        {"clip_id": "a", "angle": 2, "actual_snap_ms": 4200},
        {"clip_id": "b", "angle": 1, "actual_snap_ms": 2500},
    ]}}), encoding="utf-8")

    assert load_marked_snap_anchors(report) == {"a": 1200, "b": 2500}


def test_load_center_anchors_uses_held_out_first_angle_predictions(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"leave_one_game_out": {"locator": {"rows": [
        {"key": ["game", "a", 1], "predicted_x": 0.4, "predicted_y": 0.6},
        {"key": ["game", "a", 2], "predicted_x": 0.7, "predicted_y": 0.2},
    ]}}}), encoding="utf-8")

    assert load_center_anchors(report) == {"a": (0.4, 0.6)}


def test_load_temporal_anchors_uses_first_usable_angle_onset(tmp_path):
    report = tmp_path / "features.jsonl"
    report.write_text(json.dumps({
        "clip_id": "a",
        "start_ms": 1000,
        "temporal_diagnostics": {"angles": [
            {"angle": 1, "usable": True, "start_seconds": 0.0,
             "onset_seconds": 2.25},
            {"angle": 2, "usable": True, "start_seconds": 12.0,
             "onset_seconds": 3.0},
        ]},
    }) + "\n", encoding="utf-8")

    assert load_temporal_anchors([report]) == {"a": 3250}
