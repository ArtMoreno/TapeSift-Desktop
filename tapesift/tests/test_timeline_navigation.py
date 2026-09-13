"""Timeline blocks select the production clip list and seek precisely."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.services.project_service import (  # noqa: E402
    FragmentReclaimOption, ProjectSession,
)
from tapesift.ui_core.clip_list import COL_DUR, COL_START  # noqa: E402
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
from tapesift.ui_v2.main_window import MainWindowV2  # noqa: E402
from tapesift.ui_core.timeline import PLAY_COLORS  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_block_signal_selects_row_and_seeks(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create("Timeline", tmp_path, tmp_path / "out")
    first = session.add_clip(Clip(
        start_ms=10_000, end_ms=20_000, clip_title="Run",
        details={"run_pass": "Run"}))
    second = session.add_clip(Clip(
        start_ms=30_000, end_ms=42_000, clip_title="Pass",
        details={"run_pass": "Pass"}))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()

    seeks: list[int] = []
    monkeypatch.setattr(window.player, "seek_to", seeks.append)
    monkeypatch.setattr(window.player.player, "pause", lambda: None)
    window.clip_list.filter_edit.setText("does-not-match")
    assert window.clip_list.table.isRowHidden(1)

    window.player.clip_block_activated.emit(second.id, 36_000)

    assert window.clip_list.selected_clip_ids() == [second.id]
    assert window.clip_list.filter_edit.text() == ""
    assert not window.clip_list.table.isRowHidden(1)
    assert window._selected_clip_id == second.id
    assert seeks == [36_000]
    assert [block.selected for block in window.player.slider._blocks] == [
        False, True,
    ]
    assert window.player.slider._blocks[0].clip_id == first.id
    assert window.player.slider._blocks[0].kind == "run"
    assert window.player.slider._blocks[1].kind == "pass"

    window.session.conn.close()
    window.hide()


def test_list_click_selects_timeline_block_and_seeks_once(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "List selection", tmp_path, tmp_path / "out")
    first = session.add_clip(Clip(
        start_ms=10_000, end_ms=20_000, clip_title="Run",
        details={"run_pass": "Run"}))
    second = session.add_clip(Clip(
        start_ms=30_000, end_ms=42_000, clip_title="Pass",
        details={"run_pass": "Pass"}))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()

    seeks: list[int] = []
    monkeypatch.setattr(window.player, "seek_to", seeks.append)
    monkeypatch.setattr(window.player.player, "pause", lambda: None)
    monkeypatch.setattr(
        window.clip_list, "set_clips",
        lambda *_args, **_kwargs: pytest.fail(
            "selection must not rebuild the clip list"))

    window.clip_list.select_clip_id(second.id)

    assert window.clip_list.selected_clip_ids() == [second.id]
    assert window._selected_clip_id == second.id
    assert seeks == [second.start_ms]
    assert [
        (block.clip_id, block.selected)
        for block in window.player.slider._blocks
    ] == [
        (first.id, False),
        (second.id, True),
    ]

    window.session.conn.close()
    window.hide()


def test_color_by_result_rebuilds_blocks_and_popup_key(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, timeline_color_by="play_type")
    saves = []
    settings.save = lambda *args, **kwargs: saves.append(True)
    window = MainWindowV2(settings)
    session = ProjectSession.create("Timeline colors", tmp_path, tmp_path / "out")
    session.add_clip(Clip(
        start_ms=10_000, end_ms=20_000, clip_title="Completion",
        details={"run_pass": "Pass", "result": "Completion"}))
    session.add_clip(Clip(
        start_ms=30_000, end_ms=42_000, clip_title="Score",
        details={"run_pass": "Run", "result": "Touchdown"}))
    session.add_clip(Clip(
        start_ms=45_000, end_ms=50_000, clip_title="Needs logging"))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()

    window.player.timeline_color_combo.setCurrentIndex(
        window.player.timeline_color_combo.findData("result"))

    assert settings.timeline_color_by == "result"
    assert saves == [True]
    assert [
        (block.kind, block.category_label)
        for block in window.player.slider._blocks
    ] == [
        ("complete", "Complete"),
        ("touchdown", "Touchdown"),
        ("", "Unlabelled"),
    ]
    assert all(
        block.colour for block in window.player.slider._blocks)
    assert {
        key: label.text()
        for key, label in window.player.timeline_legend_items.items()
    } == {
        "complete": "Complete",
        "touchdown": "Touchdown",
        "": "Unlabelled",
    }

    window.session.conn.close()
    window.hide()


def test_rpo_and_penalty_semantics_survive_every_color_mode(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Semantic timeline colors", tmp_path, tmp_path / "out")
    session.add_clip(Clip(
        start_ms=10_000,
        end_ms=20_000,
        clip_title="RPO reception",
        details={
            "run_pass": "Pass",
            "play_type": "RPO",
            "result": "Reception; First Down",
        },
    ))
    session.add_clip(Clip(
        start_ms=30_000,
        end_ms=42_000,
        clip_title="RPO wiped out",
        details={
            "run_pass": "Run",
            "play_type": "RPO",
            "result": "First Down; Penalty",
        },
    ))
    session.project.source_duration_ms = 60_000
    window.session = session

    for mode in (
            "play_type", "result", "primary_tag", "personnel",
            "review_status"):
        settings.timeline_color_by = mode
        window._refresh_timeline_presentation()
        blocks = window.player.slider._blocks
        assert [(block.kind, block.colour) for block in blocks] == [
            ("rpo", PLAY_COLORS["rpo"].name()),
            ("penalty", PLAY_COLORS["penalty"].name()),
        ]

    session.conn.close()
    window.hide()


def test_primary_tag_styles_control_timeline_color_and_visibility(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, timeline_color_by="primary_tag")
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create("Tag colors", tmp_path, tmp_path / "out")
    session.project.tag_styles = {
        "pressure": {
            "color": "#f59e0b", "category": "Disruption",
            "primary": True, "show_on_timeline": True,
        },
        "catch": {
            "primary": False, "show_on_timeline": True,
        },
    }
    session.add_clip(Clip(
        start_ms=10_000, end_ms=20_000, clip_title="Pressure",
        tags=["Pressure", "Pass"]))
    session.add_clip(Clip(
        start_ms=30_000, end_ms=40_000, clip_title="Catch",
        tags=["Catch"]))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()

    blocks = window.player.slider._blocks
    assert [(block.kind, block.category_label, block.colour) for block in blocks] == [
        ("disruption", "Disruption", "#f59e0b"),
        ("", "Unlabelled", "#9aa3a6"),
    ]
    assert window.player.timeline_legend_items["disruption"].text() == "Disruption"

    window.session.conn.close()
    window.hide()


def test_timeline_context_menu_splits_and_undoes(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create("Timeline menu", tmp_path, tmp_path / "out")
    first = session.add_clip(Clip(
        start_ms=10_000, end_ms=30_000, clip_title="Long play",
        details={"quarter": "Q2", "run_pass": "Pass"}))
    session.add_clip(Clip(start_ms=40_000, end_ms=50_000,
                          clip_title="Following play"))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()
    monkeypatch.setattr(window.player.player, "pause", lambda: None)
    monkeypatch.setattr(window.player, "seek_to", lambda _ms: None)
    monkeypatch.setattr(window, "_start_thumbnails", lambda _clips=None: None)

    monkeypatch.setattr(QMenu, "popup", lambda _menu, _global_pos: None)
    window._timeline_context_menu_requested(first.id, 20_000, QPoint())
    actions = {action.text(): action
               for action in window._timeline_context_menu.actions()}
    actions["Cut clip here  (C)"].trigger()

    assert [clip.clip_number for clip in session.clips] == [1, 2, 3]
    assert (session.clips[0].start_ms, session.clips[0].end_ms) == (10_000, 20_000)
    assert session.clips[1].details == {"quarter": "Q2"}

    window._timeline_context_menu_requested("", 20_000, QPoint())
    actions = {action.text(): action
               for action in window._timeline_context_menu.actions()}
    actions["Undo last edit"].trigger()

    assert len(session.clips) == 2
    assert session.clips[0].end_ms == 30_000
    assert [clip.clip_number for clip in session.clips] == [1, 2]

    window.session.conn.close()
    window.hide()


def test_timeline_context_menu_reclaims_preserved_footage(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Timeline reclaim", tmp_path, tmp_path / "out")
    first = session.add_clip(Clip(
        start_ms=10_000, end_ms=20_000, clip_title="First play"))
    session.add_clip(Clip(
        start_ms=30_000, end_ms=40_000, clip_title="Following play"))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()
    option = FragmentReclaimOption(
        segment_index=1,
        clip_id=first.id,
        edge="end",
        fragment_start_ms=20_000,
        fragment_end_ms=30_000,
        original_boundary_ms=20_000,
        target_boundary_ms=30_000,
    )
    monkeypatch.setattr(
        session,
        "fragment_reclaim_options",
        lambda **_kwargs: [option],
    )

    def reclaim(segment_index, clip_id, edge):
        assert (segment_index, clip_id, edge) == (1, first.id, "end")
        first.end_ms = option.target_boundary_ms
        first.touch()
        session.commit()
        return option

    monkeypatch.setattr(session, "reclaim_fragment_into_clip", reclaim)
    monkeypatch.setattr(window, "_index_current_project", lambda: None)
    monkeypatch.setattr(QMenu, "popup", lambda _menu, _global_pos: None)

    window._timeline_context_menu_requested(first.id, 19_000, QPoint())
    actions = {
        action.text(): action
        for action in window._timeline_context_menu.actions()
    }
    actions["Reclaim 10.0s after this play"].trigger()

    assert session.get_clip(first.id).end_ms == 30_000
    assert "Reclaimed 10.0s" in window.statusBar().currentMessage()
    assert window.clip_list.selected_clip_ids() == [first.id]

    session.close()
    window.hide()


def test_c_cuts_the_only_clip_under_playhead(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Playhead cut", tmp_path, tmp_path / "out")
    first = session.add_clip(Clip(
        start_ms=10_000, end_ms=30_000, clip_title="Long play"))
    session.add_clip(Clip(
        start_ms=40_000, end_ms=50_000, clip_title="Following play"))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    window.activateWindow()
    qapp.processEvents()
    monkeypatch.setattr(window.player, "position_ms", lambda: 20_000)
    monkeypatch.setattr(window.player.player, "pause", lambda: None)
    monkeypatch.setattr(window.player, "seek_to", lambda _ms: None)
    monkeypatch.setattr(window, "_start_thumbnails", lambda _clips=None: None)

    assert window._cut_shortcut.key().toString() == "C"
    assert window._cut_shortcut.context() == \
        Qt.ShortcutContext.ApplicationShortcut
    window._cut_shortcut.activated.emit()

    assert session.get_clip(first.id).end_ms == 20_000
    assert len(session.clips) == 3

    window.session.conn.close()
    window.hide()


def test_c_prefers_selected_clip_when_ranges_overlap(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Selected overlap cut", tmp_path, tmp_path / "out")
    selected = session.add_clip(Clip(
        start_ms=10_000, end_ms=30_000, clip_title="Selected play"))
    other = session.add_clip(Clip(
        start_ms=15_000, end_ms=25_000, clip_title="Overlapping play"))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()
    window.clip_list.select_clip_id(selected.id)
    monkeypatch.setattr(window.player, "position_ms", lambda: 20_000)
    monkeypatch.setattr(window.player.player, "pause", lambda: None)
    monkeypatch.setattr(window.player, "seek_to", lambda _ms: None)
    monkeypatch.setattr(window, "_start_thumbnails", lambda _clips=None: None)

    window._cut_clip_at_playhead()

    assert session.get_clip(selected.id).end_ms == 20_000
    assert session.get_clip(other.id).end_ms == 25_000

    window.session.conn.close()
    window.hide()


def test_c_abstains_when_unselected_clips_overlap(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Ambiguous overlap cut", tmp_path, tmp_path / "out")
    session.add_clip(Clip(start_ms=10_000, end_ms=30_000))
    session.add_clip(Clip(start_ms=15_000, end_ms=25_000))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()
    monkeypatch.setattr(window.player, "position_ms", lambda: 20_000)

    window._cut_clip_at_playhead()

    assert len(session.clips) == 2
    assert "Multiple clips overlap" in window.statusBar().currentMessage()

    window.session.conn.close()
    window.hide()


def test_timeline_trim_commits_once_without_rebuilding_list(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create("Timeline trim", tmp_path, tmp_path / "out")
    clip = session.add_clip(Clip(
        start_ms=10_000, end_ms=30_000, clip_title="Trim me"))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()
    window.clip_list.select_clip_id(clip.id)
    monkeypatch.setattr(
        window.clip_list, "set_clips",
        lambda *_args, **_kwargs: pytest.fail(
            "trim release must not rebuild the clip list"))
    monkeypatch.setattr(window, "_index_current_project", lambda *_args: None)

    window._timeline_trim_preview(clip.id, "start", 11_500)

    # The V2 clip list groups plays under a header row, so find the clip's
    # row by identity instead of assuming a fixed position.
    clip_row = window.clip_list._clip_rows[clip.id]
    assert window.clip_list.table.item(clip_row, COL_START).text() == "00:11.500"
    assert window.clip_list.table.item(clip_row, COL_DUR).text() == "00:18.500"
    assert session.get_clip(clip.id).start_ms == 10_000

    window._timeline_trim_finished(clip.id, "start", 12_000)

    assert session.get_clip(clip.id).start_ms == 12_000
    assert window.player.slider._blocks[0].start_ms == 12_000
    assert session.undo() == "trim clip"
    assert session.get_clip(clip.id).start_ms == 10_000

    window.session.conn.close()
    window.hide()


def test_detected_and_manual_plays_are_distinguishable_on_the_timeline(
        qapp, tmp_path, monkeypatch):
    """Provenance is a shape cue, so it must reach the block, not the colour."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create("Provenance", tmp_path, tmp_path / "out")
    session.add_clip(Clip(
        start_ms=10_000, end_ms=20_000, clip_title="Detected",
        detection_lineage={"derivation": "detected"}))
    session.add_clip(Clip(
        start_ms=30_000, end_ms=40_000, clip_title="Hand cut"))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()

    blocks = window.player.slider._blocks
    assert [block.detected for block in blocks] == [True, False]
    # Same colour mode, same colour: provenance never repaints a block.
    assert blocks[0].colour == blocks[1].colour

    window.session.conn.close()
    window.hide()


def test_trimming_across_a_neighbour_warns_instead_of_clamping(
        qapp, tmp_path, monkeypatch):
    """Overlaps are legal here, so the user is told - never silently moved."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create("Overlap warn", tmp_path, tmp_path / "out")
    first = session.add_clip(Clip(
        start_ms=10_000, end_ms=20_000, clip_title="First play"))
    second = session.add_clip(Clip(
        start_ms=22_000, end_ms=32_000, clip_title="Second play"))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()
    monkeypatch.setattr(window.player, "seek_to", lambda _ms: None)

    assert window._trim_overlap_note(second.id, 22_000, 32_000) == ""

    window._timeline_trim_preview(second.id, "start", 15_000)
    message = window.statusBar().currentMessage()

    assert "overlaps First play" in message
    # The warning is advisory: nothing is written until the drag is released.
    assert session.get_clip(second.id).start_ms == 22_000
    assert session.get_clip(first.id).end_ms == 20_000

    window.session.conn.close()
    window.hide()
