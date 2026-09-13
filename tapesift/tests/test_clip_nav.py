"""Clip-list keyboard navigation (Phase 3.1 / 3.2).

Ctrl+Up/Down + PgUp/PgDown move between clips; Ctrl+Home/End jump to the
first/last clip. In the normal clipping workflow each move seeks the player
to the clip's start (3.2). In review mode the seek/play is driven by
_enter_current_clip instead, so _land_on_row must NOT double-seek there.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.core.exceptions import DatabaseError  # noqa: E402
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.services import (  # noqa: E402
    autodetect_export_service, recovery_service,
)
from tapesift.services.project_service import ProjectSession  # noqa: E402
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
from tapesift.ui_v2.main_window import MainWindowV2  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    for name in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k:
                                         QMessageBox.StandardButton.Yes))


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    settings = AppSettings()
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "out")
    settings.review_mode = False
    monkeypatch.setattr(settings, "save", lambda *a, **k: None)
    recovery_service.mark_closed()
    win = MainWindowV2(settings)

    session = ProjectSession.create("Vs Duke", tmp_path, tmp_path / "out")
    session.add_clip(Clip(start_ms=1000, end_ms=6000, clip_title="Play 001"))
    session.add_clip(Clip(start_ms=9000, end_ms=15000, clip_title="Play 002"))
    session.add_clip(Clip(start_ms=20000, end_ms=26000, clip_title="Play 003"))
    session.save()
    win._activate_session(session)
    yield win, settings
    win._close_project()


def _spy_seeks(win, monkeypatch):
    seeks = []
    monkeypatch.setattr(win.player, "seek_to", lambda ms: seeks.append(ms))
    return seeks


def test_goto_clip_selects_and_seeks(window, monkeypatch):
    win, _ = window
    seeks = _spy_seeks(win, monkeypatch)

    win._select_row(0)
    seeks.clear()

    win._goto_clip(1)            # -> row 1 (Play 002 @ 9000)
    assert win._current_row() == 1
    assert seeks[-1] == 9000

    win._goto_clip(1)            # -> row 2 (Play 003 @ 20000)
    assert win._current_row() == 2
    assert seeks[-1] == 20000


def test_goto_clip_stops_at_ends(window, monkeypatch):
    win, _ = window
    seeks = _spy_seeks(win, monkeypatch)
    win._select_row(2)           # last clip
    seeks.clear()

    win._goto_clip(1)            # past the end -> no move, no seek
    assert win._current_row() == 2
    assert seeks == []


def test_goto_edge_first_and_last(window, monkeypatch):
    win, _ = window
    seeks = _spy_seeks(win, monkeypatch)
    win._select_row(1)
    seeks.clear()

    win._goto_edge(False)        # End -> last clip
    assert win._current_row() == 2
    assert seeks[-1] == 20000

    win._goto_edge(True)         # Home -> first clip
    assert win._current_row() == 0
    assert seeks[-1] == 1000


def test_review_mode_does_not_double_seek(window, monkeypatch):
    win, settings = window
    settings.review_mode = True
    seeks = _spy_seeks(win, monkeypatch)
    win._select_row(0)
    seeks.clear()

    win._goto_clip(1)
    # In review mode the playhead is driven by _enter_current_clip (autoplay),
    # not by _land_on_row, so no direct seek_to is issued here.
    assert win._current_row() == 1
    assert seeks == []


def test_active_batch_does_not_silently_scope_normal_navigation(
    window,
    monkeypatch,
):
    win, _ = window
    batch_clips = [win.session.clips[0], win.session.clips[2]]
    monkeypatch.setattr(
        win,
        "_active_autodetect_batch_clips",
        lambda *, pending_only=False: batch_clips,
    )

    win._select_row(0)
    win._goto_clip(1)
    assert win._current_row() == 1
    win._goto_clip(-1)
    assert win._current_row() == 0
    win._goto_edge(False)
    assert win._current_row() == 2
    win._goto_edge(True)
    assert win._current_row() == 0


def test_escape_leaves_global_pending_filter_without_claiming_active_batch(
    window,
):
    win, _ = window
    win.clip_list.show_review_filter(
        "autodetect_pending",
        clip_ids={win.session.clips[0].id},
    )

    win._shortcut_escape()

    assert win.clip_list.review_filter_mode == "all"
    assert "batch remains active" not in win.statusBar().currentMessage().lower()


def test_escape_leaves_pending_scope_without_ending_active_batch(
    window,
    monkeypatch,
):
    win, _ = window
    monkeypatch.setattr(
        win.session,
        "active_autodetect_review_batch",
        lambda: {"id": "batch-active"},
    )
    win.clip_list.show_review_filter(
        "autodetect_pending",
        clip_ids={win.session.clips[0].id},
    )

    win._shortcut_escape()

    assert win.clip_list.review_filter_mode == "all"
    assert "batch remains active" in win.statusBar().currentMessage().lower()


def test_pending_batch_navigation_preserves_pending_filter(
    window,
    monkeypatch,
):
    win, _ = window
    batch_clips = [win.session.clips[0], win.session.clips[2]]
    for index, clip in enumerate(batch_clips):
        clip.detection_lineage = {
            "session_id": "detect-test",
            "candidate_ids": [f"candidate-{index}"],
            "reviewed_at": "",
        }
    win._refresh_clip_list()
    pending_ids = {clip.id for clip in batch_clips}
    win.clip_list.show_review_filter(
        "autodetect_pending", clip_ids=pending_ids)
    monkeypatch.setattr(
        win,
        "_active_autodetect_batch_clips",
        lambda *, pending_only=False: (
            batch_clips if pending_only else batch_clips),
    )

    win.clip_list.select_clip_id(batch_clips[0].id, reveal=False)
    win._goto_clip(1)
    assert win.clip_list.selected_clip_ids() == [batch_clips[1].id]
    assert win.clip_list.review_filter_mode == "autodetect_pending"


def _configure_adjacent_pending_batch(win, monkeypatch, roots=None):
    clips = win.session.clips
    ranges = [(1_000, 6_000), (6_500, 12_000), (12_500, 18_000)]
    roots = roots or ["candidate-1", "candidate-2", "candidate-3"]
    for clip, (start_ms, end_ms), root in zip(clips, ranges, roots):
        clip.start_ms = start_ms
        clip.end_ms = end_ms
        clip.detection_lineage = {
            "session_id": "detect-adjacent",
            "candidate_ids": [root],
            "reviewed_at": "",
            "review_batch_id": "",
        }
    batch = {
        "id": "batch-adjacent",
        "session_id": "detect-adjacent",
        "start_ms": 0,
        "end_ms": 20_000,
    }
    monkeypatch.setattr(
        win.session,
        "active_autodetect_review_batch",
        lambda: batch,
    )
    monkeypatch.setattr(
        win,
        "_active_autodetect_candidate_ids",
        lambda _batch: set(roots),
    )
    win._refresh_clip_list()
    win.clip_list.show_review_filter(
        "autodetect_pending",
        clip_ids={clip.id for clip in clips},
    )
    return batch, clips


def test_adjacent_pending_accept_plays_and_advances_by_row(
    window,
    monkeypatch,
):
    win, _ = window
    batch, clips = _configure_adjacent_pending_batch(win, monkeypatch)
    played = []
    cleared = []
    monkeypatch.setattr(
        win.player,
        "play_clip_range",
        lambda *args: played.append(args),
    )
    monkeypatch.setattr(
        win.player,
        "clear_clip_range",
        lambda: cleared.append(True),
    )

    def mark_reviewed(clip_ids):
        for clip_id in clip_ids:
            clip = win.session.get_clip(clip_id)
            clip.detection_lineage["reviewed_at"] = "now"
            clip.detection_lineage["review_batch_id"] = batch["id"]
        return len(clip_ids)

    monkeypatch.setattr(
        win.session, "mark_detection_reviewed", mark_reviewed)

    win.clip_list.select_clip_id(clips[0].id, reveal=False)
    assert played[-1] == (1_000, 6_000, False)

    win._mark_detection_reviewed([clips[0].id])
    assert win.clip_list.selected_clip_ids() == [clips[1].id]
    assert played[-1] == (6_500, 12_000, False)

    win._mark_detection_reviewed([clips[1].id])
    assert win.clip_list.selected_clip_ids() == [clips[2].id]
    assert played[-1] == (12_500, 18_000, False)

    win._mark_detection_reviewed([clips[2].id])
    assert win.clip_list.review_filter_mode == "all"
    assert cleared
    assert "all batch candidates are resolved" in \
        win.statusBar().currentMessage().lower()


def test_disabled_pending_accept_restores_the_real_play(
    window,
    monkeypatch,
):
    win, _ = window
    batch, clips = _configure_adjacent_pending_batch(win, monkeypatch)
    clips[0].enabled = False
    restored = []
    marked = []
    monkeypatch.setattr(
        win.player, "play_clip_range", lambda *_args: None)

    def set_enabled(clip_id, enabled):
        clip = win.session.get_clip(clip_id)
        clip.enabled = enabled
        restored.append((clip_id, enabled))
        return clip

    def mark_reviewed(clip_ids):
        marked.append(list(clip_ids))
        for clip_id in clip_ids:
            clip = win.session.get_clip(clip_id)
            assert clip.enabled
            clip.detection_lineage["reviewed_at"] = "now"
            clip.detection_lineage["review_batch_id"] = batch["id"]
        return len(clip_ids)

    monkeypatch.setattr(win.session, "set_clip_enabled", set_enabled)
    monkeypatch.setattr(
        win.session, "mark_detection_reviewed", mark_reviewed)
    win.clip_list.select_clip_id(clips[0].id, reveal=False)

    win._mark_detection_reviewed([clips[0].id])

    assert restored == [(clips[0].id, True)]
    assert marked == [[clips[0].id]]
    assert win.clip_list.selected_clip_ids() == [clips[1].id]


def test_split_candidate_accepts_linked_sections_and_advances(
    window,
    monkeypatch,
):
    win, _ = window
    batch, clips = _configure_adjacent_pending_batch(
        win,
        monkeypatch,
        roots=["candidate-split", "candidate-split", "candidate-next"],
    )
    marked = []

    def mark_reviewed(clip_ids):
        marked.append(list(clip_ids))
        for clip_id in clip_ids:
            clip = win.session.get_clip(clip_id)
            clip.detection_lineage["reviewed_at"] = "now"
            clip.detection_lineage["review_batch_id"] = batch["id"]
        return len(clip_ids)

    monkeypatch.setattr(
        win.session, "mark_detection_reviewed", mark_reviewed)
    monkeypatch.setattr(
        win.player, "play_clip_range", lambda *_args: None)

    win.clip_list.select_clip_id(clips[0].id, reveal=False)
    win._mark_detection_reviewed([clips[0].id])

    assert marked == [[clips[0].id, clips[1].id]]
    assert win.clip_list.selected_clip_ids() == [clips[2].id]
    assert win._active_autodetect_batch_clips(
        pending_only=True) == [clips[2]]


def test_timeline_activation_keeps_exact_seek_in_pending_mode(
    window,
    monkeypatch,
):
    win, _ = window
    _batch, clips = _configure_adjacent_pending_batch(win, monkeypatch)
    played = []
    seeks = []
    monkeypatch.setattr(
        win.player,
        "play_clip_range",
        lambda *args: played.append(args),
    )
    monkeypatch.setattr(
        win.player, "seek_to", seeks.append)
    monkeypatch.setattr(win.player, "shuttle_stop", lambda: None)
    monkeypatch.setattr(win.player, "clear_clip_range", lambda: None)

    win._timeline_clip_activated(clips[1].id, 9_250)

    assert seeks == [9_250]
    assert played == []


def test_pending_trim_updates_player_stop_without_restarting(
    window,
    monkeypatch,
):
    win, _ = window
    _batch, clips = _configure_adjacent_pending_batch(win, monkeypatch)
    ranges = []
    played = []
    monkeypatch.setattr(
        win.player,
        "set_clip_range",
        lambda *args: ranges.append(args),
    )
    monkeypatch.setattr(
        win.player,
        "play_clip_range",
        lambda *args: played.append(args),
    )
    monkeypatch.setattr(win, "_index_current_project", lambda: None)

    def trim_boundary(clip_id, edge, position_ms, **_kwargs):
        clip = win.session.get_clip(clip_id)
        if edge == "start":
            clip.start_ms = position_ms
        else:
            clip.end_ms = position_ms
        return clip

    monkeypatch.setattr(
        win.session, "trim_clip_boundary", trim_boundary)

    win.clip_list.select_clip_id(clips[0].id, reveal=False)
    played.clear()
    win._timeline_trim_finished(clips[0].id, "end", 5_500)

    assert ranges[-1] == (1_000, 5_500, False)
    assert played == []


def test_pending_range_sync_ignores_an_unselected_batch_clip(
    window,
    monkeypatch,
):
    win, _ = window
    _batch, clips = _configure_adjacent_pending_batch(win, monkeypatch)
    ranges = []
    monkeypatch.setattr(
        win.player,
        "play_clip_range",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        win.player,
        "set_clip_range",
        lambda *args: ranges.append(args),
    )
    win.clip_list.select_clip_id(clips[0].id, reveal=False)

    win._sync_pending_autodetect_clip_range(clips[1])

    assert ranges == []


def test_opening_project_resumes_active_pending_scope(
    window,
    monkeypatch,
    tmp_path,
):
    win, _ = window
    session = ProjectSession.create(
        "Resume Detect", tmp_path, tmp_path / "resume-out")
    monkeypatch.setattr(
        session,
        "active_autodetect_review_batch",
        lambda: {"id": "active-batch"},
    )
    focused = []
    monkeypatch.setattr(
        win,
        "_focus_first_pending_autodetect_clip",
        lambda: focused.append(True) or True,
    )

    win._activate_session(session)

    # Working out which clip needs review walks every clip in the project
    # plus the candidate table, so it now runs on the tick after the
    # workspace has painted instead of inside _activate_session. The
    # contract is unchanged - opening a project with an active batch still
    # lands on the first clip needing review - it just no longer holds the
    # window blank while it works that out.
    assert focused == []
    QApplication.processEvents()
    assert focused == [True]


def test_prebatch_review_remains_visible_and_focusable_until_reattested(
    window,
    monkeypatch,
):
    win, _ = window
    clip = win.session.clips[0]
    clip.detection_lineage = {
        "session_id": "detect-prebatch",
        "candidate_ids": ["candidate-prebatch"],
        "reviewed_at": "before-batch",
        "review_batch_id": "",
    }
    batch = {
        "id": "batch-current",
        "session_id": "detect-prebatch",
        "start_ms": 0,
        "end_ms": 10_000,
    }
    monkeypatch.setattr(
        win.session,
        "active_autodetect_review_batch",
        lambda: batch,
    )
    monkeypatch.setattr(
        win,
        "_active_autodetect_candidate_ids",
        lambda _batch: {"candidate-prebatch"},
    )
    win._refresh_clip_list()

    assert win._active_autodetect_batch_clips(pending_only=True) == [clip]
    assert win._focus_first_pending_autodetect_clip() is True
    assert win.clip_list.review_filter_mode == "autodetect_pending"
    assert win.clip_list.selected_clip_ids() == [clip.id]
    assert not win.clip_list.table.isRowHidden(
        win.clip_list._clip_rows[clip.id])

    clip.detection_lineage["review_batch_id"] = batch["id"]
    win._refresh_clip_list()
    assert win._active_autodetect_batch_clips(pending_only=True) == []


def test_active_batch_delete_redirects_to_explicit_decisions(
    window,
    monkeypatch,
):
    win, _ = window
    candidate, recovery, ordinary = win.session.clips
    batch = {
        "id": "batch-test",
        "session_id": "detect-test",
        "start_ms": 0,
        "end_ms": 30_000,
    }
    candidate.detection_lineage = {
        "session_id": "detect-test",
        "candidate_ids": ["candidate-test"],
        "reviewed_at": "",
    }
    recovery.detection_lineage = {
        "session_id": "detect-test",
        "candidate_ids": [],
        "recovery_id": "recovery-test",
        "batch_id": "batch-test",
        "reviewed_at": "now",
    }
    monkeypatch.setattr(
        win.session, "active_autodetect_review_batch", lambda: batch)
    monkeypatch.setattr(
        win, "_active_autodetect_candidate_ids",
        lambda _batch: {"candidate-test"})
    removed = []
    confirmed = []
    withdrawn = []
    monkeypatch.setattr(
        win.session, "remove_clips",
        lambda ids: removed.extend(ids))
    monkeypatch.setattr(
        win, "_confirm_detection_false_positive",
        lambda ids: confirmed.extend(ids))
    monkeypatch.setattr(
        win.session, "withdraw_missed_detection",
        lambda ids: withdrawn.extend(ids) or 1)
    monkeypatch.setattr(win, "_refresh_clip_list", lambda: None)

    win._delete_clips([candidate.id])
    assert confirmed == [candidate.id]
    assert removed == []

    win._delete_clips([recovery.id])
    assert withdrawn == [recovery.id]
    assert removed == []

    win._delete_clips([candidate.id, ordinary.id])
    assert confirmed == [candidate.id]
    assert removed == []


def test_active_batch_replay_validates_captured_source(window, monkeypatch):
    win, _ = window
    plays = []
    monkeypatch.setattr(
        win.player, "play_clip_range",
        lambda *args: plays.append(args))
    monkeypatch.setattr(
        win.session,
        "validate_active_autodetect_batch_source",
        lambda: (_ for _ in ()).throw(
            DatabaseError("Different captured source.")),
    )
    win._play_active_autodetect_test_range()
    assert plays == []

    monkeypatch.setattr(
        win.session,
        "validate_active_autodetect_batch_source",
        lambda: {"start_ms": 1_000, "end_ms": 9_000},
    )
    win._play_active_autodetect_test_range()
    assert plays == [(1_000, 9_000, False)]


def test_finish_cleans_batch_ui_before_score_assembly_failure(
    window,
    monkeypatch,
):
    win, _ = window
    batch = {
        "id": "batch-finish",
        "session_id": "detect-finish",
        "start_ms": 1_000,
        "end_ms": 9_000,
    }
    monkeypatch.setattr(
        win.session, "active_autodetect_review_batch", lambda: batch)
    monkeypatch.setattr(
        win.session,
        "complete_autodetect_review_batch",
        lambda **_kwargs: batch,
    )
    monkeypatch.setattr(
        autodetect_export_service,
        "build_correction_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            autodetect_export_service.AutodetectExportError(
                "simulated score failure")),
    )
    cleared = []
    filters = []
    scopes = []
    monkeypatch.setattr(
        win.player, "clear_clip_range", lambda: cleared.append(True))
    monkeypatch.setattr(
        win.clip_list, "show_review_filter",
        lambda mode: filters.append(mode))
    monkeypatch.setattr(
        win.clip_list, "set_navigation_scope",
        lambda scope: scopes.append(scope))

    win._finish_autodetect_test_batch()
    assert cleared == [True]
    assert filters == ["all"]
    assert scopes == [None]


def test_finish_handles_export_error_from_completion_without_escape(
    window,
    monkeypatch,
):
    win, _ = window
    batch = {
        "id": "batch-finish-error",
        "session_id": "detect-finish",
        "start_ms": 1_000,
        "end_ms": 9_000,
    }
    monkeypatch.setattr(
        win.session, "active_autodetect_review_batch", lambda: batch)
    monkeypatch.setattr(
        win.session,
        "complete_autodetect_review_batch",
        lambda **_kwargs: (_ for _ in ()).throw(
            autodetect_export_service.AutodetectExportError(
                "simulated validation failure")),
    )
    focused = []
    monkeypatch.setattr(
        win, "_focus_first_pending_autodetect_clip",
        lambda: focused.append(True) or False)

    win._finish_autodetect_test_batch()
    assert focused == [True]


def test_finish_contains_unexpected_completion_exception(
    window,
    monkeypatch,
):
    win, _ = window
    batch = {
        "id": "batch-finish-runtime-error",
        "session_id": "detect-finish",
        "start_ms": 1_000,
        "end_ms": 9_000,
    }
    monkeypatch.setattr(
        win.session, "active_autodetect_review_batch", lambda: batch)
    monkeypatch.setattr(
        win.session,
        "complete_autodetect_review_batch",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated unexpected failure")),
    )
    critical = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        staticmethod(lambda *args, **_kwargs: critical.append(args)),
    )

    win._finish_autodetect_test_batch()

    assert len(critical) == 1
    assert critical[0][1] == "Could not finish test batch"


def test_small_completed_batch_hides_headline_rates_but_keeps_diagnostics():
    batch = {
        "start_ms": 0,
        "end_ms": 60_000,
        "pending_candidate_count": 0,
        "pending_recovery_count": 0,
        "eligible_for_scoped_development_metrics": True,
        "development_score": {
            "ready_for_tuning": False,
            "reviewed_truth_count": 2,
            "recommended_truth_per_batch": 20,
            "primary_metrics": {
                "precision": 1.0,
                "recall": 0.5,
                "f1": 2 / 3,
                "truth_count": 2,
                "prediction_count": 1,
                "matched_count": 1,
                "false_positive_count": 0,
                "false_negative_count": 1,
                "iou_threshold": 0.75,
            },
            "all_one_to_one_boundary_metrics": {
                "median_max_boundary_error_ms": 1_000,
            },
        },
    }

    text = MainWindowWorkflow._autodetect_score_text(batch)

    assert "Completed small-sample diagnostic" in text
    assert "Precision:" not in text
    assert "Recall:" not in text
    assert "F1:" not in text
    assert "Matches at IoU 0.75: 1" in text
    assert "Batch size: 2 / 20 recommended" in text


def test_view_score_contains_unexpected_bundle_exception(
    window,
    monkeypatch,
):
    win, _ = window
    monkeypatch.setattr(win.session, "save", lambda: None)
    monkeypatch.setattr(
        autodetect_export_service,
        "list_captured_sessions",
        lambda _conn: [{"id": "detect-corrupt"}],
    )
    monkeypatch.setattr(
        autodetect_export_service,
        "build_correction_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("simulated malformed snapshot")),
    )
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **_kwargs: warnings.append(args)),
    )

    win._show_autodetect_batch_score()

    assert len(warnings) == 1
    assert warnings[0][1] == "Autodetect score is unavailable"
