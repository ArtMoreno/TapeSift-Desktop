import json

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.tests.test_shell_v3_review import _window, _close_test_window
from tapesift.ui_v3.workspace_state import ReviewRailState


def test_distance_buttons_drive_apply_undo_break_and_failure(tmp_path, monkeypatch):
    window = _window(tmp_path)
    window._v3_review.set_rail_state(ReviewRailState(True, True))
    session = ProjectSession.create("Drive suggestions", tmp_path / "projects", tmp_path / "exports")
    session.project.game_team_ids = ["cfbd:team:2390", "cfbd:team:57"]
    first = session.add_clip(Clip(0, 6000, clip_title="Keep custom name", details={
        "quarter": "Q2", "ball_on": "OWN 30", "down_distance": "1st & 10", "run_pass": "Run"}))
    second = session.add_clip(Clip(10000, 16000))
    third = session.add_clip(Clip(20000, 26000))
    window.session = session
    window._refresh_clip_list()
    window.select_clip(second.id, seek=False)
    editor = window.clip_editor
    editor.set_game_teams(session.project.game_team_ids)
    editor.down_buttons["2"].click()
    for yards, button in editor.distance_buttons.items():
        button.click()
        assert editor.detail_edits["down_distance"].text() == f"2nd & {yards}"
        assert button.isChecked()
    editor.context_panel.distance_edit.setText("25")
    assert editor.detail_edits["down_distance"].text() == "2nd & 25"
    editor.context_panel.distance_edit.setText("Goal")
    assert editor.detail_edits["down_distance"].text() == "2nd & Goal"
    editor.detail_edits["down_distance"].setText("")
    editor.detail_edits["ball_on"].setText("OWN 38")
    assert editor.drive_apply.isEnabled()
    assert editor.drive_gain.text() == "+8 yd?"
    assert first.details.get("yards") is None
    editor.drive_apply.click()
    assert first.details["yards"] == "8"
    assert second.details["ball_on"] == "OWN 38"
    assert second.details["down_distance"] == "2nd & 2"
    assert "yards" not in second.details
    assert first.clip_title == "Keep custom name"
    assert next(c for c in window.player.attribute_grid._clips if c.id == first.id).details["yards"] == "8"
    stored = json.loads(session.conn.execute("SELECT details_json FROM clips WHERE id=?", (first.id,)).fetchone()[0])
    assert stored["yards"] == "8"
    window._undo()
    assert "yards" not in session.get_clip(first.id).details
    assert session.get_clip(second.id).details == {}
    window._redo()
    assert session.get_clip(first.id).details["yards"] == "8"
    window.select_clip(second.id, seek=False)
    editor.new_drive.click()
    assert session.get_clip(second.id).details["drive_start"] == "1"
    assert "yards" not in session.get_clip(first.id).details
    assert not editor.drive_apply.isEnabled()
    window._undo()
    window.select_clip(second.id, seek=False)
    assert session.get_clip(first.id).details["yards"] == "8"

    # Explicitly entered yardage survives a drive break.
    window.select_clip(first.id, seek=False)
    assert editor.gain_origin.isVisible()
    editor.detail_edits["yards"].setText("7")
    editor.apply_btn.click()
    assert "yards_inferred_from" not in session.get_clip(first.id).details
    window.select_clip(second.id, seek=False)
    editor.new_drive.click()
    assert session.get_clip(first.id).details["yards"] == "7"
    editor.detail_edits["yards"].setText("6")
    editor.apply_btn.click()
    window.select_clip(third.id, seek=False)
    assert editor.drive_apply.isEnabled()
    editor.drive_apply.click()
    assert session.get_clip(third.id).details["ball_on"] == "OWN 44"
    assert session.get_clip(third.id).details["down_distance"] == "1st & 10"
    assert session.get_clip(second.id).details["yards"] == "6"

    # Failure must not persist either half of a cross-clip edit.
    fourth = session.add_clip(Clip(30000, 36000))
    window._refresh_clip_list()
    window.select_clip(fourth.id, seek=False)
    editor.detail_edits["ball_on"].setText("OPP 48")
    assert editor.drive_apply.isEnabled()
    with monkeypatch.context() as patch:
        def fail():
            raise OSError("Test disk failure")
        patch.setattr(session, "commit", fail)
        editor.drive_apply.click()
        assert "Test disk failure" in editor.error_label.text()
        assert session.get_clip(fourth.id).details == {}
        assert "yards" not in session.get_clip(third.id).details
    editor.drive_apply.click()
    assert session.get_clip(third.id).details["yards"] == "8"
    reopened = ProjectSession.open(session.db_path)
    try:
        assert reopened.get_clip(third.id).details["yards"] == "8"
        assert reopened.get_clip(second.id).details["drive_start"] == "1"
    finally:
        reopened.conn.close()
    # Show a pending suggestion at the real inspector width.
    fifth = session.add_clip(Clip(40000, 46000))
    window._refresh_clip_list()
    window.select_clip(fifth.id, seek=False)
    editor.detail_edits["ball_on"].setText("OPP 45")
    editor.form_area.verticalScrollBar().setValue(0)
    QTest.qWait(200)
    assert editor.drive_suggestion.isVisible()
    assert editor.drive_apply.width() > 80 and editor.new_drive.width() > 65
    for button in editor.distance_buttons.values():
        assert button.parentWidget().rect().contains(button.geometry())
    assert editor.form_area.horizontalScrollBar().maximum() == 0
    assert editor.grab().save(str(tmp_path / "drive-distance-panel.png"))
