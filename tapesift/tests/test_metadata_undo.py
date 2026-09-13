"""Metadata edits must capture their undo snapshot before mutating the clip."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QBoxLayout

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.ui_core.clip_editor import ClipEditor


def test_metadata_undo_restores_original_values(tmp_path):
    _app = QApplication.instance() or QApplication([])
    session = ProjectSession.create("Game", tmp_path, tmp_path / "out")
    clip = Clip(
        start_ms=1_000, end_ms=6_000, clip_number=1,
        clip_title="Before", details={"quarter": "Q1"},
    )
    session.add_clip(clip, record_undo=False)

    editor = ClipEditor(AppSettings())
    editor.set_context("{clip_title}", "Game", "-", 60_000)
    editor.set_clip(clip)
    editor.edit_started.connect(
        lambda _clip_id: session.checkpoint("edit clip"))
    editor.clip_edited.connect(lambda _clip_id: session.commit())

    editor.title_edit.setText("After")
    editor.detail_edits["quarter"].setText("Q2")
    editor._apply()

    assert session.get_clip(clip.id).clip_title == "After"
    assert session.get_clip(clip.id).details["quarter"] == "Q2"
    assert session.undo() == "edit clip"
    assert session.get_clip(clip.id).clip_title == "Before"
    assert session.get_clip(clip.id).details["quarter"] == "Q1"


def test_play_details_density_changes_only_the_editor_layout():
    _app = QApplication.instance() or QApplication([])
    editor = ClipEditor(AppSettings())

    assert editor.property("density") == "compact"
    compact_height = editor.notes_edit.maximumHeight()
    editor.apply_density("spacious")

    assert editor.property("density") == "spacious"
    assert editor.notes_edit.maximumHeight() > compact_height
    assert set(editor.detail_edits) == {
        "quarter", "down_distance", "ball_on", "run_pass", "play_type",
        "play_action",
        "off_formation", "off_personnel", "def_formation", "def_personnel",
        "action", "result", "player_name", "other_players", "yards", "yac", "quarterback"}


def test_hidden_play_detail_keeps_existing_clip_data():
    _app = QApplication.instance() or QApplication([])
    settings = AppSettings(
        detail_field_order=["player_name", "quarter"],
        hidden_detail_fields=["ball_on"],
        detail_field_labels={"player_name": "Ball Carrier"},
    )
    editor = ClipEditor(settings)
    clip = Clip(
        start_ms=1_000, end_ms=6_000, clip_number=1,
        clip_title="Run", details={"quarter": "Q1", "ball_on": "35"},
    )
    editor.set_context("{clip_title}", "Game", "-", 60_000)
    editor.set_clip(clip)

    assert editor.visible_detail_keys()[:2] == ["player_name", "quarter"]
    assert "ball_on" not in editor.visible_detail_keys()
    assert editor.detail_labels["player_name"].text() == "Ball Carrier"

    editor.detail_edits["quarter"].setText("Q2")
    editor._apply()
    assert clip.details["quarter"] == "Q2"
    assert clip.details["ball_on"] == "35"


def test_analyst_programmatic_edits_mark_dirty_but_loading_stays_saved():
    app = QApplication.instance() or QApplication([])
    settings = AppSettings(
        use_fixed_dropdowns=True,
        fixed_details={"quarter": ["Q1", "Q2"]},
    )
    editor = ClipEditor(settings)
    editor.set_analyst_mode(True)
    clip = Clip(
        start_ms=1_000, end_ms=6_000, clip_number=1,
        clip_title="Before", details={"quarter": "Q1"},
    )
    editor.set_context("{clip_title}", "Game", "-", 60_000)
    editor.set_clip(clip)
    assert editor.save_state_label.text() == "SAVED"

    editor.title_edit.setText("Auto-named title")
    assert editor.save_state_label.text() == "UNSAVED"

    editor.set_clip(clip)
    assert editor.save_state_label.text() == "SAVED"
    editor.detail_edits["quarter"].setCurrentText("Q2")
    app.processEvents()
    assert editor.save_state_label.text() == "UNSAVED"


def test_analyst_review_focus_falls_back_to_visible_notes_when_core_hidden():
    app = QApplication.instance() or QApplication([])
    core = [
        "quarter", "down_distance", "ball_on", "yards", "run_pass",
        "play_type", "play_action", "action", "result", "player_name",
        "other_players", "quarterback", "yac",
    ]
    editor = ClipEditor(AppSettings(hidden_detail_fields=core))
    editor.set_analyst_mode(True)
    editor.set_clip(Clip(
        start_ms=1_000, end_ms=6_000, clip_number=1,
        clip_title="Play"))
    editor.show()
    app.processEvents()

    editor.begin_review_entry()
    app.processEvents()

    assert not editor.details_section.is_expanded()
    assert editor.notes_edit.hasFocus()
    editor.hide()


def test_analyst_layout_round_trip_keeps_shared_widgets_and_notes():
    _app = QApplication.instance() or QApplication([])
    editor = ClipEditor(AppSettings())
    clip = Clip(
        start_ms=1_000, end_ms=6_000, clip_number=1,
        clip_title="Play", notes="Keep this note")
    title_widget = editor.title_edit
    notes_widget = editor.notes_edit
    detail_widgets = dict(editor.detail_edits)
    detail_cells = dict(editor.detail_cells)

    editor.set_clip(clip)
    editor.set_analyst_mode(True)
    # Analyst cells stack label above field (see clip_editor comment).
    assert editor.detail_cell_layouts["player_name"].direction() \
        == QBoxLayout.Direction.TopToBottom
    editor.set_analyst_mode(False)

    assert editor.title_edit is title_widget
    assert editor.notes_edit is notes_widget
    assert editor.detail_edits == detail_widgets
    assert editor.detail_cells == detail_cells
    assert editor.notes_edit.toPlainText() == "Keep this note"
    assert editor._metadata_form.labelForField(editor.notes_edit).text() == "Notes:"
    assert editor.detail_cell_layouts["player_name"].direction() \
        == QBoxLayout.Direction.TopToBottom

    editor.set_analyst_mode(True)
    assert editor.notes_edit is notes_widget
    assert editor.notes_edit.toPlainText() == "Keep this note"
    assert editor.detail_edits == detail_widgets
    assert editor.detail_cells == detail_cells
