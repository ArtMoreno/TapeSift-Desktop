import json
from pathlib import Path

import pytest

from scripts.export_run_pass_labels import parse_clip_range
from tapesift.models.clip import Clip
from tapesift.research.run_pass_dataset import (
    export_project_run_pass_labels,
)
from tapesift.services.project_service import ProjectSession
from tapesift.services.run_pass_label_service import (
    NO_PLAY,
    PASS,
    RUN,
    SPECIAL,
    resolve_run_pass_label,
)


def test_clip_selection_supports_intentional_gaps():
    assert parse_clip_range("17-22, 24-37") == (
        set(range(17, 23)) | set(range(24, 38))
    )


@pytest.mark.parametrize(("details", "expected"), [
    ({"run_pass": "Run"}, RUN),
    ({"run_pass": "Pass"}, PASS),
    ({"run_pass": "Special"}, SPECIAL),
    ({"run_pass": "No Play"}, NO_PLAY),
    ({"play_type": "Screen"}, PASS),
    ({"play_type": "QB Draw"}, RUN),
    ({"play_type": "RPO", "result": "Handoff"}, RUN),
    ({"play_type": "RPO", "result": "Throw"}, PASS),
    ({"result": "Sack"}, PASS),
    ({"play_type": "Scramble"}, RUN),
    ({"result": "Spike"}, PASS),
    ({"result": "Kneel"}, SPECIAL),
    ({"result": "Pre-Snap Penalty"}, NO_PLAY),
])
def test_taxonomy_resolves_agreed_football_cases(details, expected):
    assert resolve_run_pass_label(details).label == expected


def test_rpo_without_a_decision_remains_unlabeled():
    resolved = resolve_run_pass_label({"play_type": "RPO"})

    assert resolved.label == ""
    assert resolved.source == "unlabeled"
    assert not resolved.trainable


def test_play_action_alone_does_not_invent_a_primary_label():
    resolved = resolve_run_pass_label({"play_action": "Play Action"})

    assert resolved.label == ""
    assert resolved.source == "unlabeled"
    assert not resolved.trainable


def test_explicit_conflict_is_visible_and_not_trainable():
    resolved = resolve_run_pass_label({
        "run_pass": "Run",
        "play_type": "Screen",
    })

    assert resolved.label == RUN
    assert resolved.conflict is True
    assert resolved.trainable is False


def test_project_export_defaults_to_explicit_enabled_labels(tmp_path: Path):
    session = ProjectSession.create("Labels", tmp_path, tmp_path / "out")
    session.project.source_video_path = "D:/film/game.mp4"
    session.add_clips([
        Clip(
            start_ms=0, end_ms=10_000,
            details={
                "run_pass": "Run",
                "play_action": "Play Action",
            },
        ),
        Clip(
            start_ms=10_000, end_ms=20_000,
            details={"play_type": "QB Draw"},
        ),
        Clip(
            start_ms=20_000, end_ms=30_000,
            details={"run_pass": "Pass"},
            enabled=False,
        ),
        Clip(
            start_ms=30_000, end_ms=40_000,
            details={"play_type": "RPO"},
        ),
    ])
    project_path = session.db_path
    session.conn.close()
    output = tmp_path / "labels.jsonl"

    summary = export_project_run_pass_labels(project_path, output)
    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]

    assert summary["records"] == 1
    assert summary["label_counts"] == {"run": 1}
    assert summary["skipped"] == {
        "derived": 1,
        "disabled": 1,
        "unlabeled": 1,
    }
    assert records[0]["label"] == "run"
    assert records[0]["label_valid"] is True
    assert records[0]["trainable"] is False
    assert records[0]["research_approved"] is False
    assert records[0]["play_action"] == "Play Action"
    assert records[0]["angle_starts_ms"] == [0]
    assert records[0]["angle_count"] == 1
    assert records[0]["angle_source"] == "unavailable"
    assert "notes" not in records[0]
    assert "player_name" not in records[0]


def test_project_export_can_include_derived_and_disabled_diagnostics(
        tmp_path: Path):
    session = ProjectSession.create("Labels", tmp_path, tmp_path / "out")
    session.add_clips([
        Clip(
            start_ms=0, end_ms=10_000,
            details={"play_type": "QB Draw"},
        ),
        Clip(
            start_ms=10_000, end_ms=20_000,
            details={"run_pass": "Pass"},
            enabled=False,
        ),
    ])
    project_path = session.db_path
    session.conn.close()

    summary = export_project_run_pass_labels(
        project_path,
        tmp_path / "labels.jsonl",
        include_derived=True,
        include_disabled=True,
    )

    assert summary["records"] == 2
    assert summary["label_counts"] == {"pass": 1, "run": 1}


def test_only_an_explicit_clip_cohort_becomes_research_truth(tmp_path: Path):
    session = ProjectSession.create("Cohort", tmp_path, tmp_path / "out")
    session.add_clips([
        Clip(start_ms=0, end_ms=10_000, details={"run_pass": "Run"}),
        Clip(start_ms=10_000, end_ms=20_000,
             details={"run_pass": "Pass"}),
        Clip(start_ms=20_000, end_ms=30_000,
             details={"run_pass": "Run"}),
    ])
    project_path = session.db_path
    session.conn.close()
    output = tmp_path / "labels.jsonl"

    summary = export_project_run_pass_labels(
        project_path,
        output,
        research_cohort_id="test-cohort",
        approved_clip_numbers={1, 2},
    )
    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]

    assert summary["records"] == 2
    assert summary["research_cohort_id"] == "test-cohort"
    assert summary["skipped"] == {"outside_cohort": 1}
    assert all(record["trainable"] for record in records)
    assert all(
        record["research_cohort_id"] == "test-cohort"
        for record in records
    )


def test_cohort_id_and_clip_selection_are_an_indivisible_pair(
    tmp_path: Path,
):
    project = tmp_path / "project.tapesift"

    with pytest.raises(ValueError, match="both a cohort ID"):
        export_project_run_pass_labels(
            project,
            tmp_path / "labels.jsonl",
            research_cohort_id="incomplete",
        )
