"""The new preview and logging controls use the real Review save authority."""
import json

from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.services.detail_service import quarterback_at
from tapesift.tests.test_shell_v3_review import _window, _close_test_window


def test_quarterback_scopes_names_and_saved_assignments_in_real_review(tmp_path):
    from copy import deepcopy
    from PySide6.QtTest import QTest
    from tapesift.services.filename_service import render_template
    from tapesift.ui_v3.workspace_state import ReviewRailState

    window = _window(tmp_path)
    window._v3_review.set_rail_state(ReviewRailState(True, True))
    session = ProjectSession.create("QB logging", tmp_path / "projects", tmp_path / "exports")
    clips = [session.add_clip(Clip(i * 10_000, i * 10_000 + 6000)) for i in range(8)]
    clips[6].details = {"quarterback": "Saved QB", "run_pass": "Pass", "logging_saved": "1"}
    clips[6].clip_title = "Keep my custom title"
    session.save()
    window.session = session
    window._refresh_clip_list()
    editor = window.clip_editor

    def select(index):
        window.select_clip(clips[index].id, seek=False)
        QApplication.processEvents()
        return editor.detail_edits["quarterback"].text()

    assert select(0) == ""
    assert editor.quarterback_scope.currentData() == "forward"
    editor.detail_edits["quarterback"].setText("Darian Mensah")
    editor.detail_edits["run_pass"].setText("Pass")
    editor.detail_edits["player_name"].setText("Malachi Toney")
    assert "Malachi Toney" in editor.title_edit.text()
    assert "QB Darian Mensah" in editor.title_edit.text()
    editor.save_next_btn.click()
    assert window._selected_clip_id == clips[1].id
    assert editor.detail_edits["quarterback"].text() == "Darian Mensah"
    assert "QB-Darian-Mensah" in render_template(session.project.naming_template, clips[0])
    first_defaults = deepcopy(session.project.logging_defaults)

    # A one-play substitute saves to this clip without changing the sequence.
    editor.quarterback_scope.setCurrentIndex(0)
    editor.detail_edits["quarterback"].setText("One play QB")
    editor.apply_btn.click()
    assert clips[1].details["quarterback"] == "One play QB"
    assert session.project.logging_defaults == first_defaults
    assert select(2) == "Darian Mensah"

    editor.detail_edits["quarterback"].setText("Luke Nickel")
    editor.apply_btn.click()
    assert quarterback_at(session.project.logging_defaults, clips[3].start_ms) == "Luke Nickel"
    window._undo()
    assert session.get_clip(clips[2].id).details == {}
    assert session.project.logging_defaults == first_defaults
    window._redo()
    assert session.get_clip(clips[2].id).details["quarterback"] == "Luke Nickel"
    assert select(3) == "Luke Nickel"

    assert select(5) == "Luke Nickel"
    editor.detail_edits["quarterback"].setText("Third QB")
    editor.apply_btn.click()
    assert select(4) == "Luke Nickel"  # Visiting backward cannot inherit the later QB.
    assert select(7) == "Third QB"
    assert select(6) == "Saved QB"
    assert editor.quarterback_scope.currentData() == "clip"
    editor.detail_edits["quarterback"].setText("Local correction")
    editor.apply_btn.click()
    assert session.get_clip(clips[6].id).clip_title == "Keep my custom title"
    assert select(7) == "Third QB"

    # Revising an earlier substitution respects later explicit substitutions.
    select(2)
    editor.quarterback_scope.setCurrentIndex(1)
    editor.detail_edits["quarterback"].setText("Corrected backup")
    editor.apply_btn.click()
    assert select(4) == "Corrected backup"
    assert select(5) == "Third QB"
    assert select(1) == "One play QB"
    assert select(0) == "Darian Mensah"
    editor.form_area.verticalScrollBar().setValue(0)
    QTest.qWait(200)
    assert editor.quarterback_scope.isVisible()
    assert editor.quarterback_scope.width() >= editor.quarterback_scope.fontMetrics().horizontalAdvance(editor.quarterback_scope.currentText()) + 24
    assert editor.grab().save(str(tmp_path / "quarterback-scope.png"))

    reopened = ProjectSession.open(session.db_path)
    try:
        assert reopened.project.logging_defaults == session.project.logging_defaults
        assert reopened.get_clip(clips[1].id).details["quarterback"] == "One play QB"
        assert reopened.get_clip(clips[6].id).clip_title == "Keep my custom title"
        assert quarterback_at(reopened.project.logging_defaults, clips[7].start_ms) == "Third QB"
    finally:
        reopened.conn.close()


def test_details_field_draft_save_next_and_defaults_roundtrip(tmp_path):
    window = _window(tmp_path)
    session = ProjectSession.create("Field logging", tmp_path / "projects", tmp_path / "exports")
    session.project.game_team_ids = ["cfbd:team:2390", "cfbd:team:57"]
    first = session.add_clip(Clip(1000, 7000))
    second = session.add_clip(Clip(8000, 14000))
    window.session = session
    window._refresh_clip_list()
    window.select_clip(first.id, seek=False)
    editor = window.clip_editor
    editor.set_game_teams(session.project.game_team_ids)
    values = {"quarter": "OT", "quarterback": "#10 D. Mensah", "run_pass": "Pass",
              "result": "Completion; First Down; Gain", "ball_on": "OWN 25",
              "down_distance": "1st & 10", "yards": "18", "yac": "11",
              "other_players": "#1 M. Toney, #9 B. Nicholson"}
    for key, value in values.items():
        editor.detail_edits[key].setText(value)
    assert window._v3_play_field_ribbon is None
    assert editor.context_panel.mini_field.details["yards"] == "18"
    assert first.details == {}  # Preview edits never save on their own.
    assert window.player.timeline_header.height() == 0
    assert not window.player.attribute_grid.isHidden()
    assert window.player.quick_tag_slot.isHidden()
    assert window.quick_tag_tray.manage_button.isHidden()
    assert window.player.attribute_grid.heatmap_button.isHidden()
    assert not window._v3_map_menu_button.isHidden()
    editor.save_next_btn.click()
    QApplication.processEvents()
    assert window._selected_clip_id == second.id
    assert session.project.logging_defaults == {
        "quarter": "OT", "quarterback": "#10 D. Mensah", "quarterback_initial": "",
        "quarterback_changes": [{"start_ms": first.start_ms, "quarterback": "#10 D. Mensah"}]}
    assert editor.detail_edits["quarter"].text() == "OT"
    assert second.details == {}  # Carry-forward remains a visible draft until Save.
    stored = json.loads(session.conn.execute("SELECT details_json FROM clips WHERE id=?", (first.id,)).fetchone()[0])
    assert {key: stored[key] for key in values} == values
    row = next(c for c in window.player.attribute_grid._clips if c.id == first.id)
    assert row.details["yac"] == "11"
    window.select_clip(first.id, seek=False)
    editor.detail_edits["quarter"].setText("Q3")
    assert editor._apply()
    assert session.project.logging_defaults["quarter"] == "OT"
    editor.open_field_editor()
    field_dialog = editor._field_dialog
    field_dialog._place(44)
    window.select_clip(second.id, seek=False)
    assert editor._field_dialog is None
    assert not field_dialog.isVisible()


def test_failed_commit_keeps_save_next_on_same_clip_and_defaults(tmp_path, monkeypatch):
    window = _window(tmp_path)
    session = ProjectSession.create("Failed save", tmp_path / "projects", tmp_path / "exports")
    first = session.add_clip(Clip(1000, 7000))
    session.add_clip(Clip(8000, 14000))
    window.session = session
    window._refresh_clip_list()
    window.select_clip(first.id, seek=False)
    editor = window.clip_editor
    editor.detail_edits["quarter"].setText("Q2")
    editor.detail_edits["quarterback"].setText("Substitute")
    def fail():
        raise OSError("Simulated disk failure")
    monkeypatch.setattr(session, "commit", fail)
    editor.save_next_btn.click()
    assert window._selected_clip_id == first.id
    assert session.project.logging_defaults == {}
    assert first.details == {}
    assert editor.save_state_label.property("state") == "dirty"
    assert "Simulated disk failure" in editor.error_label.text()


def test_quarterback_carries_through_tagged_plays_and_stops_after_saved_play(tmp_path, monkeypatch):
    from copy import deepcopy
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from tapesift.ui_v3.workspace_state import ReviewRailState

    window = _window(tmp_path)
    window._v3_review.set_rail_state(ReviewRailState(True, True))
    session = ProjectSession.create("QB carry", tmp_path / "projects", tmp_path / "exports")
    clips = [session.add_clip(Clip(i * 10_000, i * 10_000 + 6000,
        details={"quarter": "Q2", "run_pass": "Pass", "logging_saved": "1"})) for i in range(6)]
    clips[4].details["quarterback"] = "Saved QB"
    session.save()
    window.session = session
    window._refresh_clip_list()
    editor = window.clip_editor

    def select(index):
        assert window.select_clip(clips[index].id, seek=False)
        QApplication.processEvents()
        return editor.detail_edits["quarterback"]

    def scope(value):
        control = editor.quarterback_scope
        index = control.findData(value)
        assert index >= 0, value
        editor.form_area.ensureWidgetVisible(control)
        control.showPopup()
        QTest.keyClick(control.view(), Qt.Key.Key_Home)
        for _ in range(index):
            QTest.keyClick(control.view(), Qt.Key.Key_Down)
        QTest.keyClick(control.view(), Qt.Key.Key_Return)
        assert control.currentData() == value

    qb = select(0)
    assert editor.quarterback_scope.currentData() == "forward"
    qb.lineEdit().setFocus()
    QTest.keyClicks(qb.lineEdit(), "Starter QB")
    editor.save_next_btn.click()
    assert window._selected_clip_id == clips[1].id
    assert editor.detail_edits["quarterback"].text() == "Starter QB"
    assert "quarterback" not in clips[1].details  # Selection still only stages a draft.
    editor.apply_btn.click()
    assert clips[1].details["quarterback"] == "Starter QB"
    assert clips[1].details["quarter"] == "Q2"
    assert select(4).text() == "Saved QB"

    select(1)
    scope("stop")
    before_stop = deepcopy(session.project.logging_defaults)
    with monkeypatch.context() as patch:
        def fail():
            raise OSError("QB save failed")
        patch.setattr(session, "commit", fail)
        editor.save_next_btn.click()
        assert window._selected_clip_id == clips[1].id
        assert session.project.logging_defaults == before_stop
    editor.save_next_btn.click()
    assert window._selected_clip_id == clips[2].id
    assert editor.detail_edits["quarterback"].text() == ""
    assert session.get_clip(clips[1].id).details["quarterback"] == "Starter QB"
    window._undo()
    assert select(2).text() == "Starter QB"
    window._redo()
    assert select(2).text() == ""
    select(1)
    assert editor.quarterback_scope.currentData() == "stop"

    qb = select(3)
    QTest.keyClicks(qb.lineEdit(), "Backup QB")
    scope("forward")
    editor.apply_btn.click()
    assert select(5).text() == "Backup QB"
    assert select(4).text() == "Saved QB"
    assert select(2).text() == ""
    select(1)
    scope("stop")
    editor.apply_btn.click()
    assert select(5).text() == "Backup QB"  # Stopping earlier preserves later substitutions.

    # An explicit one-play blank must survive revisiting while carry remains on.
    qb = select(5)
    scope("clip")
    qb.lineEdit().selectAll()
    QTest.keyClick(qb.lineEdit(), Qt.Key.Key_Backspace)
    editor.apply_btn.click()
    select(4)
    assert select(5).text() == ""
    # Clearing an assigned QB in Library is also an authoritative blank.
    library_clip = session.get_clip(clips[4].id)
    assert window._apply_library_edit(str(session.db_path), library_clip.id, {
        "clip_title": library_clip.clip_title, "tags": library_clip.tags,
        "notes": library_clip.notes, "details": {**library_clip.details, "quarterback": ""},
    }) == (True, "")
    select(3)
    assert select(4).text() == ""
    reopened = ProjectSession.open_read_only(session.db_path)
    try:
        assert quarterback_at(reopened.project.logging_defaults, clips[2].start_ms) == ""
        assert quarterback_at(reopened.project.logging_defaults, clips[5].start_ms) == "Backup QB"
        assert reopened.get_clip(clips[1].id).details["quarterback"] == "Starter QB"
        assert reopened.get_clip(clips[4].id).details.get("quarterback", "") == ""
        assert reopened.get_clip(clips[4].id).details["quarterback_cleared"] == "1"
        assert reopened.get_clip(clips[5].id).details.get("quarterback", "") == ""
    finally:
        reopened.close()
