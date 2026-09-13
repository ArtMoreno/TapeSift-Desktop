"""Integration checks for the read-only exact-snap calibration mode."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.app_v2 import (  # noqa: E402
    _snap_calibration_session_from_args,
)
from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.research.run_pass_temporal_review import (  # noqa: E402
    TemporalReviewSession,
)
from tapesift.services.project_service import ProjectSession  # noqa: E402
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow  # noqa: E402
from tapesift.ui_v2.main_window import MainWindowV2  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _record(project: Path, clip: Clip) -> dict:
    return {
        "schema_version": "2.1",
        "dataset_kind": "tapesift_run_pass_temporal_features",
        "research_cohort_id": "snap-integration-cohort",
        "project_name": project.stem,
        "source_project": str(project),
        "source_video_path": "",
        "clip_id": clip.id,
        "clip_number": clip.clip_number,
        "start_ms": clip.start_ms,
        "end_ms": clip.end_ms,
        # Snap calibration intentionally includes eligible and abstained clips.
        "classifier_eligible": True,
        "abstain_reasons": [],
        # These fields must never leak into calibration state or sidecars.
        "label": "SECRET-PASS",
        "features": {"secret_feature": 1.0},
        "temporal_diagnostics": {
            "abstain_reasons": [],
            "angles": [
                {
                    "angle": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 9.0,
                    "onset_seconds": 2.125,
                    "onset_confidence": 0.8,
                    "eligibility_reasons": [],
                },
                {
                    "angle": 2,
                    "start_seconds": 9.625,
                    "end_seconds": 26.56,
                    "onset_seconds": 3.375,
                    "onset_confidence": 0.5,
                    "eligibility_reasons": [],
                },
            ],
            "transition": {
                "left_pulse_seconds": 9.0,
                "midpoint_seconds": 9.25,
                "right_pulse_seconds": 9.5,
            },
        },
    }


def _write_manifest(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def _project_with_clip(tmp_path: Path) -> tuple[Path, Clip]:
    session = ProjectSession.create(
        "Snap Calibration Game", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        id="snap-calibration-clip",
        start_ms=100_000,
        end_ms=126_560,
        clip_number=1,
        clip_title="Play 001",
    ))
    session.close()
    return tmp_path / "Snap Calibration Game.tapesift", clip


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_snap_calibration_cli_builds_all_clip_workflow(
        tmp_path: Path) -> None:
    project, clip = _project_with_clip(tmp_path)
    manifest = tmp_path / "temporal.jsonl"
    state = tmp_path / "snap-state.json"
    judgments = tmp_path / "snap-judgments.jsonl"
    _write_manifest(manifest, _record(project, clip))

    session, projects = _snap_calibration_session_from_args([
        "--snap-calibration",
        f"--snap-calibration-manifest={manifest}",
        f"--snap-calibration-project={project}",
        f"--snap-calibration-state={state}",
        f"--snap-calibration-judgments={judgments}",
    ])

    assert session.workflow == "snap_calibration"
    assert not session.only_flagged
    assert len(session.items) == 1
    assert projects == (project,)
    state_text = state.read_text(encoding="utf-8")
    assert "SECRET-PASS" not in state_text
    assert "secret_feature" not in state_text


def test_window_records_current_frames_without_mutating_project(
        qapp: QApplication,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    project, clip = _project_with_clip(tmp_path)
    manifest = tmp_path / "temporal.jsonl"
    state = tmp_path / "snap-state.json"
    judgments = tmp_path / "snap-judgments.jsonl"
    _write_manifest(manifest, _record(project, clip))
    calibration = TemporalReviewSession.create(
        [manifest],
        state,
        judgments,
        only_flagged=False,
        workflow="snap_calibration",
    )
    settings = AppSettings(onboarding_seen=True, recent_projects=[])
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "exports")
    settings.save = lambda *args, **kwargs: None
    before = _sha256(project)

    window = MainWindowV2(
        settings,
        snap_calibration_session=calibration,
        snap_calibration_project_paths=(project,),
    )
    seek_calls: list[int] = []
    focus_calls: list[tuple[int, int]] = []
    current_position = [102_500]
    monkeypatch.setattr(
        window.player, "seek_to", lambda value: seek_calls.append(value))
    monkeypatch.setattr(
        window.player,
        "focus_source_range",
        lambda start, end, **_kwargs:
        focus_calls.append((start, end)),
    )
    monkeypatch.setattr(
        window.player, "position_ms", lambda: current_position[0])

    window.start_snap_calibration()
    item = calibration.current
    assert item is not None
    assert window.session is not None and window.session.read_only
    assert window._selected_clip_id == clip.id
    assert seek_calls[-1] == 102_125
    assert window.snap_calibration_panel is not None
    assert window.temporal_review_panel is None
    assert window.quick_tag_tray.isHidden()
    assert not window.clip_editor.isEnabled()

    window._snap_calibration_exact_requested(item, 1)
    assert item.angle(1).actual_snap_ms == 102_500
    assert item.angle(1).snap_delta_ms == 375
    assert item.angle(1).snap_status == "marked"
    assert calibration.completed_count == 0
    assert judgments.read_text(encoding="utf-8") == ""

    window._snap_calibration_inspect_requested(item, 2, False)
    assert seek_calls[-1] == 113_000
    current_position[0] = 113_250
    window._snap_calibration_exact_requested(item, 2)

    assert calibration.completed_count == 1
    records = [
        json.loads(line)
        for line in judgments.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    assert records[0]["dataset_kind"] == \
        "tapesift_temporal_snap_calibration"
    assert records[0]["angles"][0]["delta_ms"] == 375
    assert records[0]["angles"][1]["delta_ms"] == 250
    serialized = json.dumps(records)
    assert "SECRET-PASS" not in serialized
    assert "secret_feature" not in serialized

    window._close_project()
    assert _sha256(project) == before
    window.hide()


def test_mark_rejects_a_frame_outside_the_selected_angle(
        qapp: QApplication,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    project, clip = _project_with_clip(tmp_path)
    manifest = tmp_path / "temporal.jsonl"
    state = tmp_path / "snap-state.json"
    judgments = tmp_path / "snap-judgments.jsonl"
    _write_manifest(manifest, _record(project, clip))
    calibration = TemporalReviewSession.create(
        [manifest],
        state,
        judgments,
        only_flagged=False,
        workflow="snap_calibration",
    )
    settings = AppSettings(onboarding_seen=True, recent_projects=[])
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "exports")
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(
        settings,
        snap_calibration_session=calibration,
        snap_calibration_project_paths=(project,),
    )
    monkeypatch.setattr(window.player, "seek_to", lambda _value: None)
    monkeypatch.setattr(window.player, "focus_source_range",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(window.player, "position_ms", lambda: 120_000)
    errors: list[str] = []
    monkeypatch.setattr(
        window, "_snap_calibration_error", errors.append)

    window.start_snap_calibration()
    item = calibration.current
    assert item is not None
    window._snap_calibration_exact_requested(item, 1)

    assert item.angle(1).snap_status == ""
    assert errors and "outside angle 1" in errors[-1]
    window._close_project()
    window.hide()
