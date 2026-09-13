import json
from pathlib import Path

import pytest

from tapesift.research.run_pass_features import (
    SignalFrame,
    build_signalstats_command,
    extract_run_pass_features,
    parse_signalstats,
    summarize_signalstats,
)


SIGNALSTATS = """\
frame:0    pts:0       pts_time:0
lavfi.signalstats.YAVG=100
lavfi.signalstats.SATAVG=20
lavfi.signalstats.YDIF=0
lavfi.signalstats.UDIF=0
lavfi.signalstats.VDIF=0
frame:1    pts:1       pts_time:0.25
lavfi.signalstats.YAVG=102
lavfi.signalstats.SATAVG=22
lavfi.signalstats.YDIF=2
lavfi.signalstats.UDIF=1
lavfi.signalstats.VDIF=3
"""

SCENE_SIGNALSTATS = """\
frame:0    pts:0       pts_time:0
lavfi.scene_score=0.625
lavfi.signalstats.YAVG=100
lavfi.signalstats.SATAVG=20
lavfi.signalstats.YDIF=0
lavfi.signalstats.UDIF=0
lavfi.signalstats.VDIF=0
"""


def test_parse_signalstats_keeps_only_explainable_measurements():
    frames = parse_signalstats(SIGNALSTATS)

    assert frames == [
        SignalFrame(0.0, 100.0, 20.0, 0.0, 0.0, 0.0),
        SignalFrame(0.25, 102.0, 22.0, 2.0, 1.0, 3.0),
    ]


def test_parse_signalstats_carries_optional_scene_score():
    frames = parse_signalstats(SCENE_SIGNALSTATS)

    assert len(frames) == 1
    assert frames[0].scene_score == 0.625


def test_summarize_signalstats_excludes_artificial_first_motion_frame():
    features = summarize_signalstats(
        parse_signalstats(SIGNALSTATS),
        duration_ms=1000,
        sample_fps=4,
    )

    assert features["sampled_frames"] == 2
    assert features["motion_y_mean"] == 2.0
    assert features["motion_uv_mean"] == 2.0
    assert features["still_frame_share"] == 0.0
    assert features["motion_peak_position"] == 0.25


def test_signalstats_command_is_range_bounded_and_low_resolution():
    command = build_signalstats_command(
        "ffmpeg",
        Path("game.mp4"),
        2100,
        25283,
    )

    assert command[command.index("-ss") + 1] == "2.100"
    assert command[command.index("-t") + 1] == "23.183"
    filters = command[command.index("-vf") + 1]
    assert "fps=4" in filters
    assert "scale=320:-2:flags=area" in filters
    assert "signalstats" in filters


def test_temporal_signalstats_command_retains_frames_and_adds_scene_score():
    command = build_signalstats_command(
        "ffmpeg",
        Path("game.mp4"),
        0,
        10_000,
        include_scene_score=True,
    )

    filters = command[command.index("-vf") + 1]
    assert "select='gte(scene,0)'" in filters
    assert "signalstats" in filters


def test_feature_export_preserves_label_and_omits_private_fields(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "film.mp4"
    source.write_bytes(b"film")
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({
        "clip_id": "clip-1",
        "clip_number": 7,
        "project_name": "Test",
        "source_project": "test.tapesift",
        "source_video_path": str(source),
        "start_ms": 1000,
        "end_ms": 5000,
        "label": "run",
        "label_display": "Run",
        "play_type": "RPO",
        "play_action": "Play Action",
        "trainable": True,
        "research_cohort_id": "test-cohort",
        "notes": "private",
        "player_name": "private",
    }) + "\n", encoding="utf-8")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"exe")

    def fake_measure(*_args, **_kwargs):
        return {"motion_y_mean": 2.5}

    monkeypatch.setattr(
        "tapesift.services.ffmpeg_service.get_version",
        lambda _path: "test-ffmpeg",
    )
    output = tmp_path / "features.jsonl"
    summary = extract_run_pass_features(
        labels,
        output,
        ffmpeg,
        measure=fake_measure,
    )
    record = json.loads(output.read_text(encoding="utf-8"))

    assert summary["label_counts"] == {"run": 1}
    assert summary["research_cohort_id"] == "test-cohort"
    assert record["label"] == "run"
    assert record["research_cohort_id"] == "test-cohort"
    assert record["play_type"] == "RPO"
    assert record["play_action"] == "Play Action"
    assert record["features"] == {"motion_y_mean": 2.5}
    assert "notes" not in record
    assert "player_name" not in record


def test_feature_export_requires_trainable_truth(tmp_path: Path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps({"trainable": False}) + "\n",
        encoding="utf-8",
    )
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"exe")

    with pytest.raises(ValueError, match="no trainable records"):
        extract_run_pass_features(
            labels,
            tmp_path / "features.jsonl",
            ffmpeg,
        )


def test_feature_export_rejects_unapproved_editing_labels(tmp_path: Path):
    source = tmp_path / "film.mp4"
    source.write_bytes(b"film")
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({
        "clip_id": "clip-1",
        "source_video_path": str(source),
        "start_ms": 0,
        "end_ms": 1000,
        "label": "run",
        "trainable": True,
    }) + "\n", encoding="utf-8")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"exe")

    with pytest.raises(ValueError, match="approved research cohort"):
        extract_run_pass_features(
            labels,
            tmp_path / "features.jsonl",
            ffmpeg,
            measure=lambda *_args, **_kwargs: {},
        )
