import importlib.util
import json
import sqlite3
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/prepare_run_pass_snap_review.py"
SPEC = importlib.util.spec_from_file_location("prepare_run_pass_snap_review", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_review_queue_omits_run_pass_truth(tmp_path):
    labels = tmp_path / "labels"
    labels.mkdir()
    output = tmp_path / "output"
    video = tmp_path / "film.mp4"
    video.write_bytes(b"film")
    project = tmp_path / "game.tapesift"
    connection = sqlite3.connect(project)
    connection.execute("CREATE TABLE clips (id TEXT, start_ms INTEGER, end_ms INTEGER)")
    connection.execute("INSERT INTO clips VALUES ('new', 1000, 9000)")
    connection.commit()
    connection.close()
    source_rows = [
        {
            "clip_id": clip_id,
            "clip_number": number,
            "start_ms": 1000,
            "end_ms": 9000,
            "label": label,
            "label_display": label.title(),
            "play_type": "RPO",
            "play_action": "Play Action",
            "source_video_path": str(video),
            "source_project": str(project),
            "project_name": "Game",
            "angle_starts_ms": [1000],
            "angle_count": 1,
            "angle_source": "temporal_split",
        }
        for clip_id, number, label in (("old", 1, "run"), ("new", 2, "pass"))
    ]
    (labels / "miami-indiana.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in source_rows), encoding="utf-8"
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"records": [
        {"game_group": "miami-indiana", "clip_id": row["clip_id"]}
        for row in source_rows
    ]}), encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"lofo_candidate_ranker": {"rows": [
        {"clip_id": "old", "angle": 1, "actual_snap_ms": 4000}
    ]}}), encoding="utf-8")

    inventory = MODULE.prepare_review_inputs(
        split="DEV",
        labels_dir=labels,
        strips_manifest=manifest,
        snap_report=report,
        output_dir=output,
    )

    queued = json.loads(next(output.glob("*-snap-review-input.jsonl")).read_text())
    assert (inventory["existing_exact_marks"], inventory["missing_exact_marks"]) == (1, 1)
    assert inventory["project_review_ready_missing"] == 1
    assert queued["clip_id"] == "new"
    assert queued["label"] == "snap"
    assert queued["label_display"] == "Snap Localization"
    assert queued["play_type"] == queued["play_action"] == ""
