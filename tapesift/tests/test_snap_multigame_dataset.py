from __future__ import annotations

import json
import sqlite3

from tapesift.research.snap_multigame_dataset import (
    evenly_spaced_indices,
    export_snap_cohort,
    finalize_temporal_features,
)


def test_evenly_spaced_indices_include_timeline_edges():
    assert evenly_spaced_indices(10, 4) == [0, 3, 6, 9]
    assert evenly_spaced_indices(3, 5) == [0, 1, 2]


def test_export_snap_cohort_keeps_enabled_clips_without_run_pass_labels(tmp_path):
    project = tmp_path / "game.tapesift"
    connection = sqlite3.connect(project)
    connection.executescript("""
        CREATE TABLE projects (
            name TEXT, source_video_path TEXT
        );
        CREATE TABLE clips (
            id TEXT, clip_number INTEGER, order_index INTEGER,
            start_ms INTEGER, end_ms INTEGER, enabled INTEGER,
            detection_lineage_json TEXT
        );
        INSERT INTO projects VALUES ('Game', 'game.mp4');
        INSERT INTO clips VALUES ('a', 1, 0, 100, 200, 1, '{}');
        INSERT INTO clips VALUES ('b', 2, 1, 200, 300, 0, '{}');
        INSERT INTO clips VALUES ('c', 3, 2, 300, 400, 1, '{}');
    """)
    connection.commit()
    connection.close()
    output = tmp_path / "cohort.jsonl"

    result = export_snap_cohort(
        project,
        output,
        cohort_id="game-dev-2",
        split_role="development",
        sample_count=2,
    )
    records = [json.loads(line) for line in output.read_text().splitlines()]

    assert result["selected_clip_numbers"] == [1, 3]
    assert [record["label"] for record in records] == ["snap", "snap"]
    assert all(record["trainable"] for record in records)


def test_export_snap_cohort_preserves_candidate_angle_start(tmp_path):
    project = tmp_path / "game.tapesift"
    connection = sqlite3.connect(project)
    connection.executescript("""
        CREATE TABLE projects (name TEXT, source_video_path TEXT);
        CREATE TABLE clips (
            id TEXT, clip_number INTEGER, order_index INTEGER,
            start_ms INTEGER, end_ms INTEGER, enabled INTEGER,
            detection_lineage_json TEXT
        );
        CREATE TABLE autodetect_candidates (id TEXT, angle_starts_json TEXT);
        INSERT INTO projects VALUES ('Game', 'game.mp4');
        INSERT INTO clips VALUES (
            'a', 1, 0, 100, 500, 1, '{"candidate_ids":["candidate-a"]}'
        );
        INSERT INTO autodetect_candidates VALUES ('candidate-a', '[100,300]');
    """)
    connection.commit()
    connection.close()
    output = tmp_path / "cohort.jsonl"

    export_snap_cohort(
        project,
        output,
        cohort_id="game-dev-1",
        split_role="development",
        sample_count=1,
    )
    record = json.loads(output.read_text())

    assert record["angle_starts_ms"] == [100, 300]
    assert record["angle_count"] == 2


def test_finalize_temporal_features_fingerprints_complete_cohort(tmp_path):
    feature_dir = tmp_path / "features"
    feature_dir.mkdir()
    feature = feature_dir / "game-dev-1-temporal-features.jsonl"
    feature.write_text(json.dumps({
        "temporal_diagnostics": {"angles": [{"angle": 1}, {"angle": 2}]}
    }) + "\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "cohorts": [{"cohort_id": "game-dev-1", "selected_clips": 1}],
        "holdout_status": "recorded_paths_not_opened",
    }), encoding="utf-8")

    result = finalize_temporal_features(manifest_path, feature_dir)

    assert result["status"] == "temporal_features_ready_for_calibration"
    assert result["temporal_feature_records"] == 1
    assert result["proposed_angle_records"] == 2
    assert result["cohorts"][0]["temporal_feature_sha256"]
