"""Exercise the actual V3 map keys, inspector save, and native toolbar geometry."""
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.tests.test_shell_v3_review import _window
from tapesift.ui_v3.workspace_state import ReviewRailState


def test_map_keyboard_edit_save_navigation_and_centered_transport(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    session = ProjectSession.create("Map keyboard QA", tmp_path / "projects", tmp_path / "exports")
    for i in range(3):
        session.add_clip(Clip(i * 10000, (i + 1) * 10000 - 1,
            details={"quarter": "Q1", "down_distance": "2nd & 8", "run_pass": "Pass"}))
    session.save()
    window.session = session
    window._refresh_clip_list()
    window._v3_review.set_rail_state(ReviewRailState(True, True))
    window.player.slider.setRange(0, 30000)
    window.player.slider.fit_range(0, 30000)
    grid = window.player.attribute_grid
    grid.set_clips(session.clips)
    window.settings.hidden_tag_map_rows = ["action"]
    window._apply_v3_tag_map_rows()
    window.select_clip(session.clips[0].id)
    window.activateWindow()
    app.processEvents()

    def key(widget, code):
        QTest.keyClick(widget, code)
        app.processEvents()

    def focus(row):
        window._focus_map_row(row)
        app.processEvents()
        assert grid.hasFocus()
        assert grid.cursor_cell()[0].key == row

    def popup():
        key(grid, Qt.Key.Key_Return)
        assert window._v3_map_editor.isVisible()
        return window._v3_map_editor

    for width in (1908, 1250):
        window.resize(width, 900)
        app.processEvents()
        for playing in (True, False):
            window.control_center.set_playing(playing)
            app.processEvents()
            play = window._v3_transport_surface.buttons[2]
            video = window.player.video_widget
            assert abs(play.mapToGlobal(play.rect().center()).x() - video.mapToGlobal(video.rect().center()).x()) <= 1
            controls = (window.player.timeline_fit_play, window.control_center.in_button,
                window.control_center.out_button, window._v3_timeline_zoom_cluster,
                *window._v3_transport_surface.buttons, window.player.predicted_snap_button,
                window.control_center.overflow_button, window._v3_play_type_key)
            rects = [QRect(c.mapToGlobal(QPoint()), c.size()) for c in controls if c.isVisible()]
            assert all(a.right() < b.left() for a, b in zip(rects, rects[1:]))
    window.resize(1908, 900)
    app.processEvents()
    # A real cell click establishes map focus and a row, without double-clicking.
    clip = session.clips[0]
    x1, x2 = grid.clip_span(clip)
    QTest.mouseClick(grid, Qt.MouseButton.LeftButton, pos=QPoint(int((x1+x2)/2), grid._row_top(0)+10))
    app.processEvents()
    assert grid.hasFocus() and grid.cursor_cell()[0].key == "quarter"
    key(grid, Qt.Key.Key_J)
    assert grid.cursor_cell()[0].key == "down"
    key(grid, Qt.Key.Key_K)
    assert grid.cursor_cell()[0].key == "quarter"
    edit = popup()
    key(edit.buttons["Q1"], Qt.Key.Key_Right)
    assert edit.buttons["Q2"].isChecked()
    key(edit.buttons["Q2"], Qt.Key.Key_Return)
    assert clip.details["quarter"] == window.clip_editor.detail_edits["quarter"].text() == "Q2"
    assert grid.hasFocus() and grid.cursor_cell()[0].key == "quarter"
    key(grid, Qt.Key.Key_W)
    assert window._selected_clip_id == session.clips[1].id
    assert grid.hasFocus() and grid.cursor_cell()[0].key == "quarter"
    key(grid, Qt.Key.Key_B)
    assert window._selected_clip_id == clip.id
    focus("down")
    edit = popup()
    edit.fields["down_distance"].setText("garbage")
    key(edit.fields["down_distance"], Qt.Key.Key_Return)
    assert edit.isVisible() and clip.details["down_distance"] == "2nd & 8"
    edit.fields["down_distance"].setText("3rd & 12")
    key(edit.fields["down_distance"], Qt.Key.Key_Return)
    assert clip.details["down_distance"] == "3rd & 12"
    focus("notes")
    edit = popup()
    edit.notes.setPlainText("")
    QTest.keyClicks(edit.notes, "jkwb notes")
    assert edit.notes.toPlainText() == "jkwb notes"
    key(edit.notes, Qt.Key.Key_Return)
    assert clip.notes == "jkwb notes" and grid.hasFocus()
    key(grid, Qt.Key.Key_J)
    assert grid.cursor_cell()[0].key == "notes"  # hidden Action is skipped
    edit = popup()
    edit.notes.setPlainText("cancel me")
    key(edit.notes, Qt.Key.Key_Escape)
    assert clip.notes == "jkwb notes" and grid.hasFocus()
    focus("primary_tag")
    edit = popup()
    edit.buttons["RPO"].click()
    edit.buttons["Run"].click()
    assert edit.buttons["RPO"].isChecked() and edit.buttons["Run"].isChecked()
    assert not edit.buttons["Pass"].isChecked()
    key(edit.buttons["Run"], Qt.Key.Key_Return)
    assert clip.details["run_pass"] == "Run" and clip.details["play_type"] == "RPO"
    focus("result")
    edit = popup()
    edit.buttons["Completion"].click()
    assert edit.buttons["Completion"].isChecked()
    edit.fields["result"].clear()
    assert not edit.buttons["Completion"].isChecked()
    edit.buttons["Completion"].click()
    key(edit.buttons["Completion"], Qt.Key.Key_Return)
    assert "Completion" in clip.details["result"]
    window.clip_editor.detail_edits["result"].clear()
    focus("result")
    edit = popup()
    assert edit.fields["result"].text() == ""
    key(edit.fields["result"], Qt.Key.Key_Escape)
    assert window.clip_editor.detail_edits["result"].text() == ""
    edit = popup()
    key(edit.fields["result"], Qt.Key.Key_Return)
    assert not clip.details.get("result")
    focus("people")
    edit = popup()
    edit.fields["player_name"].setText("Cooper Barkate")
    edit.fields["other_players"].setText("Darian Mensah; Malachi Toney")
    key(edit.fields["player_name"], Qt.Key.Key_Return)
    assert clip.details["player_name"] == "Cooper Barkate"
    assert "Darian Mensah" in clip.details["other_players"]
    window.settings.hidden_tag_map_rows = []
    window._apply_v3_tag_map_rows()
    focus("action")
    edit = popup()
    action = next(iter(edit.buttons))
    edit.buttons[action].click()
    assert edit.buttons[action].isChecked()
    key(edit.buttons[action], Qt.Key.Key_Return)
    assert clip.details["action"] == action
    # Cancelling a failed draft must also remove derived tags/names, so a
    # later unrelated save cannot bring the rejected metadata back.
    window.clip_editor.details_to_tags_check.setChecked(True)
    focus("quarter")
    edit = popup()
    prior_tags = window.clip_editor.tags_edit.text()
    prior_title = window.clip_editor.title_edit.text()
    prior_filename = window.clip_editor.filename_edit.text()
    edit.buttons["OT"].click()
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", lambda: (_ for _ in ()).throw(OSError("disk full")))
        key(edit.buttons["OT"], Qt.Key.Key_Return)
        assert edit.isVisible()
    key(edit.buttons["OT"], Qt.Key.Key_Escape)
    assert window.clip_editor.tags_edit.text() == prior_tags
    assert window.clip_editor.title_edit.text() == prior_title
    assert window.clip_editor.filename_edit.text() == prior_filename
    window.clip_editor.notes_edit.setPlainText("jkwb notes")
    assert window.clip_editor._apply()
    assert "OT" not in clip.tags and clip.details["quarter"] == "Q2"
    # Database failures must leave the clip selected and the popup draft intact.
    focus("quarter")
    edit = popup()
    edit.buttons["Q3"].click()
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", lambda: (_ for _ in ()).throw(OSError("disk full")))
        key(edit.buttons["Q3"], Qt.Key.Key_W)
        assert edit.isVisible() and window._selected_clip_id == clip.id
        assert clip.details["quarter"] == "Q2"
    key(edit.buttons["Q3"], Qt.Key.Key_W)
    assert clip.details["quarter"] == "Q3" and window._selected_clip_id == session.clips[1].id
    assert grid.hasFocus() and grid.cursor_cell()[0].key == "quarter"
    # Dirty inspector fields are saved before W/B, including validation failure.
    window.clip_editor.detail_edits["yards"].setText("bad")
    key(grid, Qt.Key.Key_W)
    assert window._selected_clip_id == session.clips[1].id and grid.hasFocus()
    window.clip_editor.detail_edits["yards"].setText("7")
    key(grid, Qt.Key.Key_W)
    assert session.clips[1].details["yards"] == "7" and window._selected_clip_id == session.clips[2].id
    key(grid, Qt.Key.Key_W)
    assert window._selected_clip_id == session.clips[2].id  # end stays selected
    key(grid, Qt.Key.Key_Escape)
    assert not grid.hasFocus()
    focus("quarter")
    with monkeypatch.context() as patch:
        patch.setattr(session, "read_only", True)
        key(grid, Qt.Key.Key_Return)
        assert not window._v3_map_editor.isVisible()
        key(grid, Qt.Key.Key_1)
        assert session.clips[2].details["quarter"] == "Q1"
    # A viewport pan must keep the same row and zoom at either edge.
    window.player.slider.fit_range(0, 15000)
    start, end = window.player.slider.visible_range()
    window._focus_map_row("quarter")
    key(grid, Qt.Key.Key_B)
    assert grid.cursor_cell()[1].id == session.clips[1].id
    new_start, new_end = window.player.slider.visible_range()
    assert new_end - new_start == end - start
    key(grid, Qt.Key.Key_Escape)
    assert "Map edit" not in window._v3_review.footer.shortcut_label.text()
    saved = ProjectSession.open(session.db_path)
    assert saved.get_clip(clip.id).details["quarter"] == "Q3"
    assert saved.get_clip(clip.id).notes == "jkwb notes"
    assert saved.get_clip(clip.id).details["run_pass"] == "Run"
    assert saved.get_clip(clip.id).details["play_type"] == "RPO"
    assert saved.get_clip(clip.id).details["player_name"] == "Cooper Barkate"
    assert saved.get_clip(clip.id).details["action"] == action
    assert not saved.get_clip(clip.id).details.get("result")
    saved.close()
    session.save()
    window.close()


def test_map_owns_native_window_keys_and_releases_shuttle_on_escape(tmp_path):
    from PySide6.QtTest import QSignalSpy
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    session = ProjectSession.create("Key ownership", tmp_path / "projects", tmp_path / "exports")
    clip = session.add_clip(Clip(0, 10000, details={"quarter": "Q1", "run_pass": "Pass"}))
    window.session = session
    window._refresh_clip_list()
    window.player.slider.setRange(0, 10000)
    window.player.slider.fit_range(0, 10000)
    window.settings.hidden_tag_map_rows = ["down", "action"]
    window._apply_v3_tag_map_rows()
    window.select_clip(clip.id)
    window.activateWindow()
    app.processEvents()
    grid = window.player.attribute_grid
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_E, Qt.KeyboardModifier.ShiftModifier)
    app.processEvents()
    assert grid.hasFocus() and grid.cursor_cell()[0].key == "quarter"
    shuttles = {key: QSignalSpy(window._transport_shortcuts[key].activated) for key in "JKL"}
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_J)
    app.processEvents()
    assert grid.cursor_cell()[0].key == "primary_tag"
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_K)
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_L)
    app.processEvents()
    assert grid.cursor_cell()[0].key == "quarter"
    assert all(spy.count() == 0 for spy in shuttles.values())
    assert all(not window._transport_shortcuts[k].isEnabled() for k in "JKL")
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_Escape)
    app.processEvents()
    assert not grid.hasFocus()
    assert all(window._transport_shortcuts[k].isEnabled() for k in "JKL")
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_J)
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_K)
    app.processEvents()
    assert shuttles["J"].count() == 1 and shuttles["K"].count() == 1
    window.player.shuttle_reverse()
    window._focus_map_row("quarter")
    assert not window.player._shuttle_active()
    window._return_focus_to_playback()
    app.processEvents()
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_E, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_J)
    app.processEvents()
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_Return)
    app.processEvents()
    assert window._v3_map_editor.isVisible()
    assert all(not window._transport_shortcuts[k].isEnabled() for k in "JKL")
    QTest.keyClick(window._v3_map_editor, Qt.Key.Key_Escape)
    app.processEvents()
    assert grid.hasFocus() and grid.cursor_cell()[0].key == "primary_tag"
    QTest.mouseClick(window.player.video_widget, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert all(window._transport_shortcuts[k].isEnabled() for k in "JKL")
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_E, Qt.KeyboardModifier.ShiftModifier)
    app.processEvents()
    assert grid.cursor_cell()[0].key == "primary_tag"
    # Page changes must not re-enable global shuttle over a focused map.
    window._sync_transport_shortcut_page(window.stack.currentIndex())
    assert all(not window._transport_shortcuts[k].isEnabled() for k in "JKL")
    window._v3_read_only_recovery = True
    window._return_focus_to_playback()
    app.processEvents()
    window._sync_transport_shortcut_page(window.stack.currentIndex())
    assert all(window._transport_shortcuts[k].isEnabled() for k in "JKL")
    assert not window._transport_shortcuts["Delete"].isEnabled()
    window._v3_read_only_recovery = False
    window.settings.hidden_tag_map_rows = [row.key for row in grid._all_review_rows]
    window._apply_v3_tag_map_rows()
    QTest.keyClick(window.windowHandle(), Qt.Key.Key_E, Qt.KeyboardModifier.ShiftModifier)
    app.processEvents()
    assert grid.isHidden() and all(window._transport_shortcuts[k].isEnabled() for k in "JKL")
    session.save()
    window.close()
