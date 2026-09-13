"""Integration checks for the in-app, read-only Temporal Review mode."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.app_v2 import (  # noqa: E402
    _option_values,
    _temporal_review_session_from_args,
)
from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.core.exceptions import DatabaseError  # noqa: E402
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
        "research_cohort_id": "integration-cohort",
        "project_name": project.stem,
        "source_project": str(project),
        "source_video_path": "",
        "clip_id": clip.id,
        "clip_number": clip.clip_number,
        "start_ms": clip.start_ms,
        "end_ms": clip.end_ms,
        "classifier_eligible": False,
        "abstain_reasons": ["visual_check"],
        "label": "SECRET-PASS",
        "features": {"secret": 1.0},
        "temporal_diagnostics": {
            "abstain_reasons": ["visual_check"],
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
                    "eligibility_reasons": ["visual_check"],
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
        "Temporal Review Game", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        id="temporal-review-clip",
        start_ms=100_000,
        end_ms=126_560,
        clip_number=1,
        clip_title="Play 001",
    ))
    session.close()
    return tmp_path / "Temporal Review Game.tapesift", clip


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_repeatable_temporal_options_and_session_bootstrap(
        tmp_path: Path) -> None:
    project, clip = _project_with_clip(tmp_path)
    manifest = tmp_path / "temporal.jsonl"
    state = tmp_path / "review-state.json"
    _write_manifest(manifest, _record(project, clip))
    args = [
        "--temporal-review",
        f"--temporal-review-manifest={manifest}",
        "--temporal-review-manifest",
        str(manifest),
        f"--temporal-review-project={project}",
        f"--temporal-review-state={state}",
    ]

    assert _option_values(args, "--temporal-review-manifest") == [
        str(manifest), str(manifest)]
    with pytest.raises(ValueError, match="Duplicate Temporal review item"):
        _temporal_review_session_from_args(args)

    args.pop(2)
    args.pop(2)
    session, projects = _temporal_review_session_from_args(args)
    assert len(session.items) == 1
    assert projects == (project,)
    assert session.state_path == state


def test_project_session_read_only_close_preserves_database(
        tmp_path: Path) -> None:
    project, clip = _project_with_clip(tmp_path)
    before = _sha256(project)

    session = ProjectSession.open_read_only(project)
    assert session.read_only
    assert session.get_clip(clip.id) is not None
    with pytest.raises(DatabaseError, match="read-only research mode"):
        session.save()
    session.close()

    assert _sha256(project) == before


def test_window_seeks_and_previews_without_editing_project(
        qapp: QApplication,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    project, clip = _project_with_clip(tmp_path)
    manifest = tmp_path / "temporal.jsonl"
    state = tmp_path / "review-state.json"
    judgments = tmp_path / "reviewed.jsonl"
    _write_manifest(manifest, _record(project, clip))
    review = TemporalReviewSession.create(
        [manifest], state, judgments)
    settings = AppSettings(onboarding_seen=True, recent_projects=[])
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "exports")
    settings.save = lambda *args, **kwargs: None
    before = _sha256(project)

    window = MainWindowV2(
        settings,
        temporal_review_session=review,
        temporal_review_project_paths=(project,),
    )
    seek_calls: list[int] = []
    focus_calls: list[tuple[int, int]] = []
    preview_calls: list[tuple[int, int, bool]] = []
    monkeypatch.setattr(
        window.player, "seek_to", lambda value: seek_calls.append(value))
    monkeypatch.setattr(
        window.player,
        "focus_source_range",
        lambda start, end, **_kwargs:
        focus_calls.append((start, end)),
    )
    monkeypatch.setattr(
        window.player,
        "play_clip_range",
        lambda start, end, loop:
        preview_calls.append((start, end, loop)),
    )

    window.start_temporal_review()
    item = review.current
    assert item is not None
    assert window.session is not None and window.session.read_only
    assert window._selected_clip_id == clip.id
    assert seek_calls[-1] == 102_125
    assert window.temporal_review_panel is not None
    assert window.quick_tag_tray.isHidden()

    window._temporal_review_inspect_requested(item, 2, True)
    assert seek_calls[-1] == 113_000
    assert preview_calls[-1] == (111_000, 116_000, False)
    assert focus_calls[-1] == (111_000, 116_000)

    window.temporal_review_panel.angle_cards[1] \
        .judgment_buttons["correct"].click()
    assert review.current is not None
    assert review.current.angles[0].judgment == "correct"
    window._close_project()
    assert _sha256(project) == before
    window.hide()
