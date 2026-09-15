"""The V3 inspector preserves existing saves and real field identity."""
import pytest
from PySide6.QtWidgets import QApplication
from tapesift.models.clip import Clip
from tapesift.ui_core.clip_editor import ClipEditor
from tapesift.ui_v3.clip_details import ClipDetailsV3, GameTeamsDialog
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow

IDS=["cfbd:team:2390","cfbd:team:57"]


def test_play_timing_follows_film_and_saves_with_undo(editor, tmp_path):
    from tapesift.services import snap_prediction_service as snap_service
    from tapesift.services.project_service import ProjectSession
    from tapesift.ui_v3.play_timing import timing_marks

    session = ProjectSession.create("Timing", tmp_path, tmp_path / "out")
    try:
        clip = session.add_clip(Clip(1000, 10000, details={"formation": "Shotgun"}))
        clip.analysis[snap_service.PREDICTION_KEY] = {
            "predictor_id": snap_service.PREDICTOR_ID,
            "predictor_version": snap_service.PREDICTOR_VERSION,
            "source_ms": 2000, "clip_start_ms": 1000, "clip_end_ms": 10000,
        }
        editor.edit_started.connect(lambda _: session.checkpoint("edit timing"))
        editor.clip_edited.connect(lambda _: session.commit())
        editor.set_clip(clip)
        panel = editor.play_timing
        assert panel.body.isHidden()
        panel.enabled_check.click()
        assert not panel.body.isHidden()
        assert "Estimated" in panel.status_label.text()
        assert "timing_enabled" not in clip.details
        panel.set_position(1500)
        assert panel.elapsed_label.text() == "0.00 s"
        panel.set_position(4430)
        assert panel.elapsed_label.text() == "2.43 s"
        panel.set_position(3000)
        assert panel.elapsed_label.text() == "1.00 s"
        panel.mark("release", None)
        panel.mark("release", 1999)
        assert "timing_release_ms" not in panel.details
        panel.angle_starts = (1000, 6000)
        panel.mark("release", 7000)
        assert "timing_release_ms" not in panel.details
        panel.set_position(7000)
        assert panel.elapsed_label.text() == "—"
        panel.set_position(4430)
        panel.mark("release", 4430)
        assert panel.throw_label.text() == "Time to throw: 2.43 s · Estimated"
        clip.analysis[snap_service.PREDICTION_KEY]["source_ms"] = 2200
        panel.refresh()
        assert "2.43 s" in panel.throw_label.text()
        panel.confirm_button.click()
        assert panel.throw_label.text() == "Time to throw: 2.43 s"
        panel.overlay_check.click()
        panel.mark("snap", 2100)
        assert panel.throw_label.text() == "Time to throw: 2.33 s"
        editor.notes_edit.setPlainText("Keep this note")
        assert editor._apply()
        stored = ProjectSession.open(session.db_path)
        try:
            saved = stored.get_clip(clip.id)
            assert timing_marks(saved, saved.details) == (2100, 4430, True)
            assert saved.details["timing_overlay"] == "1"
            assert saved.details["formation"] == "Shotgun"
            assert saved.notes == "Keep this note"
        finally:
            stored.close()
        session.undo()
        editor.set_clip(session.get_clip(clip.id))
        assert editor.play_timing.body.isHidden()
        session.redo()
        editor.set_clip(session.get_clip(clip.id))
        assert editor.play_timing.throw_label.text() == "Time to throw: 2.33 s"
        panel.clear_release_button.click()
        assert panel.throw_label.text() == "Time to throw: —"
        assert editor._apply()
        assert "timing_release_ms" not in session.get_clip(clip.id).details
        editor.set_clip(Clip(12000, 16000))
        assert panel.body.isHidden() and panel.position is None
        assert timing_marks(clip, {"timing_snap_ms": "bad"}) == (None, None, False)
        assert timing_marks(clip, {"timing_snap_ms": "999999"}) == (None, None, False)
    finally:
        session.close()


def test_action_templates_edit_persist_and_save_without_erasing_hidden_details(editor, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit
    from tapesift.core.config import AppSettings
    from tapesift.services.project_service import ProjectSession

    target = tmp_path / "preferences.json"
    monkeypatch.setattr("tapesift.core.paths.settings_file", lambda: target)
    editor.settings = AppSettings()
    clip = Clip(0, 1000, details={"game_clock": "12:34", "formation": "Shotgun"})
    editor.set_clip(clip)
    assert editor.advanced_details_section.isHidden()
    assert len(editor.action_buttons) == 8
    assert {"Explosive Play", "Big Gain"} <= set(editor._action_values())
    assert not {"Route", "One-Handed Catch"} & set(editor._action_values())
    editor._action_shortcuts["Offense"] = ["Catch", "Run", "One-Handed Catch", "Contested Catch",
                                            "Broken Tackle", "Run Block", "Pass Block", "Route"]
    assert {"Explosive Play", "Big Gain"} <= set(editor._action_values())
    editor._action_shortcuts["Offense"] = list(editor.ACTION_SHORTCUTS["Offense"])
    editor._action_shortcuts["Offense"][0] = "Custom catch"
    assert editor._action_values()[0] == "Custom catch"
    editor._action_shortcuts.clear()
    from tapesift.services.football_vocab import values_for
    assert {"Route", "One-Handed Catch", "Explosive Play", "Big Gain"} <= set(values_for("action"))
    for name in ("Explosive Play", "Big Gain"):
        editor.action_buttons[editor._action_values().index(name)].click()
        assert editor._apply()
        assert clip.details["action"] == name
        assert name in clip.tags
        editor.action_buttons[editor._action_values().index(name)].click()
    editor.action_buttons[0].click()
    assert editor.detail_edits["action"].text() == "Catch"
    editor.action_template.setCurrentText("Defense")
    assert editor.detail_edits["action"].text() == "Catch"
    assert not any(b.isChecked() for b in editor.action_buttons)
    editor.action_buttons[0].click()
    assert editor.detail_edits["action"].text() == "Catch; Tackle"
    editor.action_buttons[0].click()
    assert editor.detail_edits["action"].text() == "Catch"
    editor.detail_edits["action"].clear()

    def edit_dialog(dialog):
        dialog.findChildren(QLineEdit)[0].setText("Open Field Tackle")
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", edit_dialog)
    editor.edit_actions_button.click()
    saved = AppSettings.load(target)
    assert saved.action_shortcuts["Defense"][0] == "Open Field Tackle"
    editor.action_template.setCurrentText("Offense")
    assert editor._action_values()[0] == "Catch"
    editor.action_template.setCurrentText("Defense")
    editor.action_buttons[0].click()
    session = ProjectSession.create("Actions", tmp_path, tmp_path / "out")
    try:
        session.add_clip(clip)
        assert editor._apply()
        session.save()
        reloaded = ProjectSession.open(session.db_path)
        try:
            stored = reloaded.clips[0]
            assert stored.details["action"] == "Open Field Tackle"
            assert "Open Field Tackle" in stored.tags
            assert stored.details["game_clock"] == "12:34"
            assert stored.details["formation"] == "Shotgun"
        finally:
            reloaded.close()
        editor.set_clip(Clip(2000, 3000))
        assert editor.advanced_details_section.isHidden()
    finally:
        session.close()


def test_multiple_actions_toggle_save_reopen_and_map_separately(editor, tmp_path):
    from tapesift.core.config import AppSettings
    from tapesift.services.project_service import ProjectSession
    from tapesift.ui_core.tag_edit import configure_detail_edit
    from tapesift.ui_v2.attribute_grid import AttributeGrid

    clip = Clip(0, 1000, details={"action": "Coverage", "result": "Incompletion"})
    editor.set_clip(clip)
    editor.action_template.setCurrentText("Defense")
    action = editor.detail_edits["action"]
    configure_detail_edit(action, "action", AppSettings())
    assert editor.action_buttons[editor._action_values().index("Coverage")].isChecked()
    editor.action_buttons[editor._action_values().index("PBU")].click()
    action.showPopup()
    blitz = next(item for item in action._multi_menu.actions() if item.text() == "Blitz")
    blitz.trigger()
    action._multi_menu.hide()
    assert action.text() == "Coverage; PBU; Blitz"
    assert editor._apply()
    assert {"Coverage", "PBU", "Blitz"} <= set(clip.tags)
    assert "Coverage; PBU; Blitz" not in clip.tags
    assert clip.details["result"] == "Incompletion"

    session = ProjectSession.create("Multiple actions", tmp_path, tmp_path / "out")
    grid = AttributeGrid()
    try:
        session.add_clip(clip)
        session.save()
        reloaded = ProjectSession.open(session.db_path)
        try:
            stored = reloaded.clips[0]
            assert stored.details["action"] == "Coverage; PBU; Blitz"
            editor.set_clip(stored)
            grid.enable_review_style()
            row = next(row for row in grid.rows() if row.key == "action")
            assert [label for label, _ in grid._lane_color_entries(row, stored)] == ["Coverage", "PBU", "Blitz"]
            assert grid._review_value(row, stored) == "Coverage · PBU · Blitz"
            action.showPopup()
            assert next(item for item in action._multi_menu.actions() if item.text() == "Blitz").isChecked()
            action._multi_menu.hide()
            editor.action_buttons[editor._action_values().index("PBU")].click()
            assert action.text() == "Coverage; Blitz"
            assert editor._apply()
            assert "PBU" not in stored.tags
            assert {"Coverage", "Blitz"} <= set(stored.tags)
            # Custom text stays editable, ordered, and deduplicated on save.
            action.setText("Coverage; Blitz; Good coverage; blitz")
            assert editor._apply()
            assert stored.details["action"] == "Coverage; Blitz; Good coverage"
            assert stored.details["result"] == "Incompletion"
        finally:
            reloaded.close()
    finally:
        grid.close()
        grid.deleteLater()
        session.close()


@pytest.mark.parametrize("source,linked", [("Drop", "Incompletion"), ("Fumble Lost", "Fumble")])
def test_result_shortcuts_are_one_way_and_conflicts_allow_save(editor, tmp_path, source, linked):
    from tapesift.services import result_service
    from tapesift.services.project_service import ProjectSession

    assert result_service.add_results("", linked) == linked
    assert result_service.add_results(source, "YAC") == source + "; YAC"
    assert result_service.join_results([source]) == source
    editor.set_clip(Clip(0, 1000))
    editor.quick_result_buttons[source].click()
    assert editor.quick_result_buttons[linked].isChecked()
    editor.quick_result_buttons[source].click()
    assert editor.detail_edits["result"].text() == linked
    editor.quick_result_buttons[linked].click()
    assert not editor.detail_edits["result"].text()

    session = ProjectSession.create("Result warnings", tmp_path, tmp_path / "out")
    try:
        clip = session.add_clip(Clip(0, 1000))
        editor.set_clip(clip)
        for value in ("Completion", "Drop", "Gain", "Loss"):
            editor.quick_result_buttons[value].click()
        assert "Gain" not in result_service.split_results(editor.detail_edits["result"].text())
        # Existing contradictory records remain visible and can still be saved.
        editor.detail_edits["result"].setText("Gain; Loss; Drop; Incompletion")
        assert not editor.result_warning.isHidden()
        assert "Catch and incompletion" not in editor.result_warning.text()
        assert "Gain and loss" in editor.result_warning.text()
        assert editor._apply()
        session.save()
        reloaded = ProjectSession.open(session.db_path)
        try:
            saved = reloaded.clips[0]
            assert set(result_service.split_results(saved.details["result"])) == {
                "Drop", "Incompletion", "Gain", "Loss"}
            assert set(result_service.split_results(saved.details["result"])) <= set(saved.tags)
        finally:
            reloaded.conn.close()
        editor.quick_result_buttons["Completion"].click()
        editor.quick_result_buttons["Gain"].click()
        assert editor.result_warning.isHidden()
        editor.set_clip(Clip(2000, 3000, details={"result": "Reception; Incomplete"}))
        assert not editor.result_warning.isHidden()
        editor.set_clip(Clip(3000, 4000))
        assert editor.result_warning.isHidden()
    finally:
        session.conn.close()


@pytest.mark.parametrize("first,second", [("Completion", "Reception"), ("Reception", "Completion")])
def test_catch_buttons_link_both_directions_save_and_remove_without_double_counting(editor, tmp_path, first, second):
    from tapesift.services import result_service
    from tapesift.services.project_service import ProjectSession
    from tapesift.services.heatmap_service import build_heatmap
    session = ProjectSession.create("Linked catches", tmp_path, tmp_path / "out")
    try:
        clip = session.add_clip(Clip(0, 1000, tags=["Incomplete"], details={"run_pass": "Pass", "result": "First Down; Incomplete"}))
        editor.set_clip(clip)
        editor.quick_result_buttons[first].click()
        assert all(editor.quick_result_buttons[value].isChecked() for value in (first, second))
        assert not editor.quick_result_buttons["Incompletion"].isChecked()
        assert editor._apply()
        session.save()
        reloaded = ProjectSession.open(session.db_path)
        try:
            stored = reloaded.clips[0]
            assert set(result_service.split_results(stored.details["result"])) == {"Completion", "Reception", "First Down"}
            assert build_heatmap(reloaded.clips, layered=True).play_count == 1
            assert {"Completion", "Reception"}.issubset(set(stored.tags))
            assert "Incomplete" not in stored.tags
        finally:
            reloaded.conn.close()
        editor.quick_result_buttons[second].click()
        assert not any(editor.quick_result_buttons[value].isChecked() for value in (first, second))
        assert editor._apply()
        session.save()
        assert clip.details["result"] == "First Down"
        assert not {"Completion", "Reception"} & set(clip.tags)
        # Raw read/write and unrelated edits preserve historical single tags.
        assert result_service.join_results(result_service.split_results("Completion")) == "Completion"
        assert result_service.add_results("Completion", "YAC") == "Completion; YAC"
        editor.quick_result_buttons[first].click()
        editor.quick_result_buttons["Incompletion"].click()
        assert not any(editor.quick_result_buttons[value].isChecked() for value in (first, second))
        assert editor._apply()
        assert set(result_service.split_results(clip.details["result"])) == {"First Down", "Incompletion"}
    finally:
        session.conn.close()

def test_pinned_controls_wheel_protection_and_yardage_results(editor, app, tmp_path):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from tapesift.services.result_service import split_results
    from tapesift.ui_v3.theme import stylesheet

    previous_style = app.styleSheet()
    app.setStyleSheet(stylesheet())
    try:
        editor.set_game_teams(["cfbd:team:24", "cfbd:team:2390"])
        clip = Clip(0, 2000, clip_number=48, details={"quarter": "Q3", "down_distance": "1st & 10",
            "ball_on": "OWN 25", "run_pass": "Run", "player_name": "Omar Thornton", "action": "Tackle"})
        editor.set_clip(clip)
        editor.action_template.setCurrentText("Defense")
        previous = Clip(0, 500, clip_number=47)
        editor.previous_clip_provider = lambda _clip: previous
        editor._sync_drive_suggestion()
        editor.notes_edit.setPlainText("Nice run defense by Omar Thornton")
        editor.resize(380, 900)
        editor.show()
        QTest.qWait(100)
        run = editor.quick_play_buttons["run"]
        assert run.width() >= 140 and not run.icon().isNull()
        assert not editor.quick_play_buttons["pass"].icon().isNull()
        bar = editor.form_area.verticalScrollBar()
        run_y = run.mapTo(editor, QPoint()).y()
        save_y = editor.save_next_btn.mapTo(editor, QPoint()).y()
        assert editor.pinned_play.geometry().bottom() < editor.form_area.y()
        assert editor.form_area.geometry().bottom() < editor.pinned_save.y()
        assert editor.form_area.height() >= 400
        editor.grab().save(str(tmp_path / "pinned-top.png"))
        for control in (editor.quick_ball, editor.context_panel.distance_edit, editor.detail_edits["yards"], editor.quarterback_scope):
            editor.form_area.ensureWidgetVisible(control)
            control.setFocus()
            QTest.qWait(20)
            bar.setValue(bar.maximum() // 2)
            original = control.currentText()
            position = bar.value()
            wheel = QWheelEvent(QPointF(control.rect().center()), QPointF(control.mapToGlobal(control.rect().center())),
                QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False)
            app.sendEvent(control, wheel)
            assert control.currentText() == original
            assert bar.value() > position
        bar.setValue(bar.maximum())
        QTest.qWait(20)
        assert run.mapTo(editor, QPoint()).y() == run_y
        assert editor.save_next_btn.mapTo(editor, QPoint()).y() == save_y
        editor.grab().save(str(tmp_path / "pinned-bottom.png"))
        assert editor.form_area.horizontalScrollBar().maximum() == 0
        yac = editor.detail_edits["yac"]
        assert yac.height() <= 34
        assert yac.parentWidget().rect().contains(yac.geometry())
        editor.quick_result_buttons["Reception"].click()
        result = editor.detail_edits["result"]
        assert set(split_results(result.text())) == {"Reception", "Completion"}
        yards = editor.detail_edits["yards"]
        assert not yards.text()
        for value, expected in (("5", "Gain"), ("0", "No Gain"), ("-3", "Loss")):
            yards.setText(value)
            selected = set(split_results(result.text()))
            assert selected & {"Gain", "No Gain", "Loss"} == {expected}
            assert {"Reception", "Completion"} <= selected
        assert editor._apply()
        assert clip.details["yards"] == "-3" and "Loss" in clip.tags
        assert not {"Gain", "No Gain"} & set(clip.tags)
        editor.stage_field_details(dict(editor._collect_details(), yards="8"))
        assert set(split_results(result.text())) & {"Gain", "No Gain", "Loss"} == {"Gain"}
        legacy = Clip(3000, 4000, details={"yards": "5", "result": "Reception"})
        editor.set_clip(legacy)
        assert result.text() == "Reception"
        assert editor._apply() and legacy.details["result"] == "Reception"
        editor.set_clip(Clip(5000, 6000))
        yards.setText("-")
        assert not result.text()
        yards.clear()
        assert not result.text()
    finally:
        app.setStyleSheet(previous_style)


@pytest.fixture(scope="module")
def app():
    from tapesift.ui_v3.fonts import load_v3_fonts
    application = QApplication.instance() or QApplication([])
    # MainWindowV3 registers these before constructing the shipped inspector.
    load_v3_fonts()
    return application

@pytest.fixture
def editor(app):
    e=ClipDetailsV3(); e.set_analyst_mode(True)
    e.set_game_teams(IDS)
    yield e
    e.close(); e.deleteLater()


def test_automatic_names_follow_edits_and_persist_manual_override(editor, tmp_path):
    from PySide6.QtTest import QTest
    from tapesift.services.project_service import ProjectSession
    from tapesift.services import detail_service, filename_service

    session = ProjectSession.create("Names", tmp_path, tmp_path / "out")
    try:
        clip = Clip(0, 1000)
        session.add_clip(clip)
        editor.set_clip(clip)
        editor.detail_edits["run_pass"].setText("Pass")
        editor.detail_edits["down_distance"].setText("3rd & 7")
        editor.tags_edit.setText("Pressure, Pass")
        assert editor.title_edit.text() == "3rd & 7 Pass - Pressure"
        assert "3rd-&-7-Pass-Pressure" in editor.preview_label.text()
        assert clip.clip_title == ""  # The draft stays outside the undo checkpoint.
        assert editor._apply()
        session.save()
        db_path = session.db_path
        session.conn.close()
        session = ProjectSession.open(db_path)
        loaded = session.clips[0]
        assert loaded.uses_auto_name
        editor.set_clip(loaded)
        assert editor.apply_quick_details({"run_pass": "Run"})
        assert loaded.clip_title == "3rd & 7 Run - Pressure"

        editor.title_edit.selectAll()
        QTest.keyClicks(editor.title_edit, "My final name")
        editor.detail_edits["quarter"].setText("Q2")
        assert editor.title_edit.text() == "My final name"
        assert editor._apply()
        session.save()
        session.conn.close()
        session = ProjectSession.open(db_path)
        loaded = session.clips[0]
        assert not loaded.uses_auto_name
        editor.set_clip(loaded)
        assert editor.apply_quick_details({"run_pass": "Pass"})
        assert loaded.clip_title == "My final name"
        assert filename_service.effective_base(loaded) == "My-final-name"

        editor._auto_name()
        assert editor.title_edit.text() == "Q2 3rd & 7 Pass - Pressure"
        assert editor._apply()
        # Library detail edits use the same persistence path.
        loaded.details["run_pass"] = "Run"
        loaded.tags = detail_service.sync_detail_tags(
            loaded.tags, {"run_pass": "Pass"}, loaded.details)
        session.clip_repo.save(loaded)
        assert loaded.clip_title == "Q2 3rd & 7 Run - Pressure"
        loaded.clip_title = "Library custom name"
        session.clip_repo.save(loaded)
        assert loaded.generated_title is None
        assert loaded.clip_title == "Library custom name"

        legacy = Clip(0, 1000, clip_title="Play 001", details={"run_pass": "Run"})
        editor.set_clip(legacy)
        assert editor.apply_quick_details({"run_pass": "Pass"})
        assert legacy.clip_title == "Play 001"
        editor._auto_name()
        editor.filename_edit.setText("Exact filename")
        assert editor.apply_quick_details({"quarter": "Q3"})
        assert filename_service.effective_base(legacy) == "Exact-filename"

        automatic = Clip(2000, 4000, clip_title="Q1", generated_title="Q1",
                         details={"quarter": "Q1"})
        session.add_clip(automatic)
        tail = session.split_clip(automatic.id, 3000)
        assert tail.clip_title == "" and tail.generated_title is None
        session.undo()
        restored = session.get_clip(automatic.id)
        assert restored.end_ms == 4000 and restored.uses_auto_name
    finally:
        session.conn.close()


@pytest.mark.parametrize("save_button", ["save_next_btn", "top_save_next_btn"])
def test_selected_quick_rows_keep_order_and_use_live_save_controls(editor, app, tmp_path, save_button):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from tapesift.services.project_service import ProjectSession

    session = ProjectSession.create("Quick rows", tmp_path, tmp_path / "exports")
    try:
        clip = Clip(0, 2000, details={"attack_direction": "left", "custom": "keep"},
                    source_photo={"note": "keep evidence"})
        session.add_clip(clip)
        editor.edit_started.connect(lambda _id: session.checkpoint("details"))
        editor.clip_edited.connect(lambda _id: session.commit())
        advanced = []
        editor.save_and_advance.connect(lambda: advanced.append(True))
        editor.set_clip(clip)
        editor.resize(380, 760)
        editor.show()
        app.processEvents()
        controls = [editor.quarter_buttons["Q1"], editor.down_buttons["1"],
                    editor.detail_edits["player_name"],
                    editor.quick_results, editor.detail_edits["action"], editor.play_timing, editor.notes_edit,
                    editor.context_panel.mini_field, editor.qb_row, editor.qb_scope_row]
        tops = [control.mapTo(editor.quick_rows, QPoint()).y() for control in controls]
        assert tops == sorted(tops) and len(set(tops)) == len(tops)
        distance = editor.context_panel.distance_edit
        down = editor.down_buttons["1"]
        assert down.mapTo(editor, down.rect().center()).y() == distance.mapTo(editor, distance.rect().center()).y()
        quarter = editor.quarter_buttons["Q1"]
        assert quarter.mapTo(editor, quarter.rect().center()).y() == editor.quick_ball.mapTo(editor, editor.quick_ball.rect().center()).y()
        assert down.mapTo(editor, down.rect().topRight()).x() < distance.mapTo(editor, QPoint()).x()
        assert set(editor.distance_buttons) == set(range(1, 11))
        assert distance.width() >= 60 and editor.quick_ball.width() >= 60
        assert 28 <= editor.detail_edits["player_name"].height() <= 34
        assert editor.quick_play.width() >= 70
        assert all(button.isVisibleTo(editor) for button in editor.quick_play_buttons.values())
        run, pass_button = editor.quick_play_buttons["run"], editor.quick_play_buttons["pass"]
        assert run.y() == pass_button.y() and run.geometry().right() < pass_button.x()
        assert run.height() == pass_button.height() == 44
        assert editor.pinned_play.isAncestorOf(run)
        assert editor.pinned_save.isAncestorOf(editor.save_next_btn)
        distance.setFocus()
        QTest.keyClick(distance, Qt.Key.Key_Tab)
        assert app.focusWidget() == editor.distance_buttons[1]
        assert not editor.source_photo_panel.isVisibleTo(editor)
        assert not editor.context_panel.offense_combo.isVisibleTo(editor)
        assert not editor.context_panel.side_combo.isVisibleTo(editor)
        assert editor.notes_edit.tabChangesFocus()
        assert editor.title_edit.parentWidget() == editor.autoname_btn.parentWidget()
        assert editor.form_area.horizontalScrollBar().maximum() == 0

        editor.detail_edits["quarter"].setText("Q2")
        editor.context_panel.down_combo.setCurrentIndex(editor.context_panel.down_combo.findData("3"))
        editor.context_panel.distance_edit.setText("7")
        index = editor.quick_play.findData("rpo_pass")
        editor.quick_play.setCurrentIndex(index)
        editor.quick_play.activated.emit(index)
        editor.detail_edits["player_name"].setText("Test Player")
        editor._set_result_from_chip("Completion")
        editor._set_result_from_chip("First Down")
        assert editor.quick_result_buttons["Completion"].isChecked()
        assert editor.quick_result_buttons["First Down"].isChecked()
        editor.notes_edit.setPlainText("Quick note")
        editor.detail_edits["action"].setText("Pressure")
        assert "action" not in editor._advanced_detail_keys()
        index = editor.quick_ball.findData("OWN 35")
        editor.quick_ball.setCurrentIndex(index)
        editor.quick_ball.activated.emit(index)
        assert editor.context_panel.mini_field.context.los_yards == 35
        assert editor.context_panel.mini_field.context.to_gain_yards == 42
        editor.open_field_editor()
        dialog = editor._field_dialog
        assert not dialog.isModal()
        dialog.flip.click()
        dialog.hash.setCurrentIndex(dialog.hash.findData("left"))
        assert "field_flip" not in clip.details  # Field placement remains an unsaved draft.
        assert editor.context_panel.mini_field.details["field_flip"] == "1"
        dialog.close()
        button = getattr(editor, save_button)
        if save_button == "save_next_btn":
            editor.form_area.ensureWidgetVisible(button)
        app.processEvents()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert advanced == [True]
        saved = session.clip_repo.list_for_project(session.project.id)[0]
        assert saved.details["down_distance"] == "3rd & 7"
        assert saved.details["run_pass"] == "Pass" and saved.details["play_type"] == "RPO"
        assert saved.details["ball_on"] == "OWN 35"
        assert saved.details["action"] == "Pressure"
        assert saved.details["field_flip"] == "1" and saved.details["field_hash"] == "left"
        assert saved.details["attack_direction"] == "left" and saved.details["custom"] == "keep"
        assert saved.source_photo["note"] == "keep evidence"
        assert saved.notes == "Quick note" and "Test Player" in saved.clip_title
        assert "Completion" in saved.tags and "First Down" in saved.tags
        session.undo()
        assert session.get_clip(clip.id).details == {"attack_direction": "left", "custom": "keep"}
    finally:
        session.conn.close()


def test_selected_play_owns_field_scores_and_unknown_reset(editor):
    first=Clip(0,1000,details={"quarter":"Q1","down_distance":"1st & 10","ball_on":"OWN 4","offense_team_id":IDS[0],"attack_direction":"right","game_clock":"11:39","score:"+IDS[0]:"0","custom":"  exact  ","result":"Completion; First Down; Custom"})
    editor.set_clip(first)
    assert editor.context_panel.mini_field.context.los_yards==4
    assert editor.context_panel.mini_field.context.to_gain_yards==14
    editor.notes_edit.setPlainText('New note')
    assert editor._apply()
    assert first.details['custom']=='  exact  '
    assert first.details['result']=='Completion; First Down; Custom'
    editor.set_clip(Clip(1000,2000))
    assert editor.context_panel.mini_field.context.los_yards is None
    assert editor.detail_edits['game_clock'].text()==''
    assert editor.detail_edits['score:'+IDS[0]].text()==''
    editor.set_clip(None)
    assert editor.context_panel.mini_field.context.los_yards is None
    assert not editor._apply()


def test_invalid_input_blocks_checkpoint_and_midfield_control_agrees(editor):
    clip=Clip(0,1000,details={"custom":"kept"})
    editor.set_clip(clip)
    events=[]; editor.edit_started.connect(events.append)
    editor.detail_edits['game_clock'].setText('11:99')
    assert not editor._apply()
    assert events==[] and clip.details=={'custom':'kept'}
    editor.detail_edits['game_clock'].setText('11:39')
    panel=editor.context_panel
    panel.ball_edit.setText('4')
    panel.side_combo.setCurrentIndex(panel.side_combo.findData('MID'))
    assert panel.ball_edit.text()=='50'
    assert editor.detail_edits['ball_on'].text()=='50'
    assert not panel.ball_edit.isEnabled()
    assert editor._apply()
    assert len(events)==1 and clip.details['custom']=='kept'


def test_team_switch_keeps_scores_bound_to_ids_and_dialog_cancel_stages(app,editor):
    clip=Clip(0,1000,details={'score:'+IDS[0]:'7','score:'+IDS[1]:'3','offense_team_id':IDS[0],'attack_direction':'right','ball_on':'OWN 4','down_distance':'1st & 10'})
    editor.set_clip(clip)
    editor.set_game_teams([IDS[1],'cfbd:team:87'])
    assert editor.context_panel.mini_field.context.los_yards == 4
    assert editor.context_panel.mini_field.context.offense_id == IDS[1]
    assert editor.detail_edits['score:cfbd:team:87'].text()==''
    assert editor._apply()
    assert clip.details['score:'+IDS[0]]=='7'
    assert clip.details['score:'+IDS[1]]=='3'
    assert 'score:cfbd:team:87' not in clip.details
    ids=IDS.copy(); d=GameTeamsDialog(ids)
    d.team_b.setCurrentIndex(d.team_b.findData('cfbd:team:87'))
    d.reject()
    assert ids==IDS
    d.team_b.setCurrentIndex(d.team_b.findData(IDS[0]))
    with pytest.raises(ValueError): d.selected_ids()
    d.deleteLater()


def test_default_factory_keeps_shared_editor(app):
    e=MainWindowWorkflow._make_clip_editor(None,None)
    assert type(e) is ClipEditor
    e.deleteLater()


def test_naming_export_stays_open_through_review_entry_and_clip_changes(editor):
    editor.set_clip(Clip(0, 1000))
    editor.begin_review_entry()
    editor.details_section.toggle.click()
    editor.set_clip(Clip(1000, 2000))
    assert editor.details_section.is_expanded()
    assert not editor.details_section.body.isHidden()
    assert not editor.quick_export_btn.isHidden()
    assert not editor.package_btn.isHidden()


def test_partial_context_and_result_removal_save_without_stale_tags(editor):
    clip=Clip(0,1000,tags=['Completion','1st down','1st Dn','First Down; coaching note','custom'],details={'result':'Completion; First Down; Custom result','custom':'kept'})
    editor.set_clip(clip)
    panel=editor.context_panel
    panel.down_combo.setCurrentIndex(panel.down_combo.findData('2'))
    assert editor._apply()
    assert clip.details['down_distance']=='2nd'
    panel.down_combo.setCurrentIndex(0)
    panel.distance_edit.setText('7')
    editor._set_result_from_chip('First Down')
    assert editor._apply()
    assert clip.details['down_distance']=='To Go 7'
    assert clip.details['result']=='Completion; Custom result'
    assert '1st down' not in clip.tags and 'First Down' not in clip.tags
    assert '1st Dn' in clip.tags  # Unrecognized/custom spellings remain untouched.
    assert 'First Down; coaching note' in clip.tags
    assert 'Completion' in clip.tags and 'custom' in clip.tags


def test_distance_dropdown_preserves_partial_inches_long_values_and_rejects_invalid(editor):
    from PySide6.QtTest import QTest

    clip = Clip(0, 1000, details={"custom": "kept"})
    editor.set_clip(clip)
    panel = editor.context_panel
    assert panel.distance_edit.isEditable()
    assert panel.distance_edit.findText("Inches") >= 0
    panel.distance_edit.setText("inches")
    assert editor._apply()
    assert clip.details["down_distance"] == "To Go Inches"
    panel.down_combo.setCurrentIndex(panel.down_combo.findData("3"))
    assert editor._apply()
    assert clip.details["down_distance"] == "3rd & Inches"
    panel.distance_edit.lineEdit().selectAll()
    QTest.keyClicks(panel.distance_edit.lineEdit(), "105")
    assert editor._apply()
    assert clip.details["down_distance"] == "3rd & 105"
    editor.set_clip(clip)
    assert panel.distance_edit.text() == "105"
    before = dict(clip.details)
    panel.distance_edit.setText("-3")
    assert not editor._apply()
    assert clip.details == before
    panel.distance_edit.setText("")
    assert editor._apply()
    assert clip.details["down_distance"] == "3rd"


def test_top_save_next_tracks_disabled_state_and_keeps_validation(editor, app):
    clip = Clip(0, 1000)
    editor.set_clip(clip)
    advanced = []
    editor.save_and_advance.connect(lambda: advanced.append(True))
    editor.context_panel.distance_edit.setText("-3")
    editor.top_save_next_btn.click()
    assert advanced == [] and clip.details == {}
    editor.save_next_btn.setEnabled(False)
    assert not editor.top_save_next_btn.isEnabled()
    editor.save_next_btn.setEnabled(True)
    assert editor.top_save_next_btn.isEnabled()
    editor.set_clip(None)
    assert not editor.top_save_next_btn.isEnabled()
    editor.set_clip(clip)
    editor.resize(380, 760)
    editor.show()
    app.processEvents()
    editor.form_area.verticalScrollBar().setValue(editor.form_area.verticalScrollBar().maximum())
    assert editor.top_save_next_btn.isVisibleTo(editor)
    assert editor.top_save_next_btn.height() < editor.save_next_btn.height()


def test_quick_play_buttons_switch_scramble_and_keep_dropdown_choices(editor):
    clip = Clip(0, 1000, details={"result": "Gain", "yards": "8"})
    editor.set_clip(clip)
    for key, family, concept in (("run", "Run", ""), ("pass", "Pass", ""),
                                  ("scramble", "Run", "Scramble"), ("pass", "Pass", "")):
        editor.quick_play_buttons[key].click()
        assert editor.detail_edits["run_pass"].text() == family
        assert editor.detail_edits["play_type"].text() == concept
        assert [k for k, b in editor.quick_play_buttons.items() if b.isChecked()] == [key]
        assert editor.quick_play.currentText() == "More…"
    for key in ("screen", "rpo_run", "rpo_pass", "no_play"):
        assert editor.quick_play.findData(key) >= 0
    index = editor.quick_play.findData("rpo_run")
    editor.quick_play.setCurrentIndex(index)
    editor.quick_play.activated.emit(index)
    editor.quick_play_buttons["pass"].click()
    assert editor.detail_edits["play_type"].text() == "RPO"
    assert editor._apply()
    assert clip.details["run_pass"] == "Pass" and clip.details["play_type"] == "RPO"
    assert clip.details["result"] == "Gain" and clip.details["yards"] == "8"
    editor.set_clip(clip)
    assert editor.quick_play_buttons["pass"].isChecked()
    assert "RPO" in editor.quick_play.currentText()
    index = editor.quick_play.findData("")
    editor.quick_play.setCurrentIndex(index)
    editor.quick_play.activated.emit(index)
    assert not any(button.isChecked() for button in editor.quick_play_buttons.values())


def test_family_chips_keep_concepts_and_unknown_rpo_has_no_pass_selection(editor):
    from tapesift.ui_v2.attribute_grid import EDIT_CHOICES

    clip = Clip(0, 1000, details={"play_type": "RPO"})
    editor.set_clip(clip)
    assert not editor.classification_buttons["rpo_pass"].isChecked()
    assert not editor.classification_buttons["rpo_run"].isChecked()
    for concept in ("Custom Screen", "Inside Zone", "RPO"):
        editor.detail_edits["play_type"].setText(concept)
        editor._set_classification_from_chip("run")
        assert editor.detail_edits["play_type"].text() == concept
        editor._attribute_row_changed("run_pass", "Pass")
        assert editor.detail_edits["play_type"].text() == concept
        assert editor.apply_quick_details(dict(EDIT_CHOICES["primary_tag"][0][1]))
        assert clip.details["play_type"] == concept
    assert ClipEditor._play_kind({"run_pass": "Custom Run", "result": "Custom Sack"}) == "other"
    assert ClipEditor._play_kind({"play_type": "Custom RPO", "action": "Custom Interception"}) == "other"
    editor._attribute_row_changed("run_pass", "Screen")
    assert editor.detail_edits["run_pass"].text() == "Pass"
    assert editor.detail_edits["play_type"].text() == "Screen"


def test_result_faces_write_canonical_values_and_toggle_legacy_aliases(editor):
    from tapesift.services.football_vocab import short_for

    clip = Clip(0, 1000)
    editor.set_clip(clip)
    for value in ("Touchdown", "Interception"):
        button = editor._v3_result_buttons[value]
        assert button.text() == short_for("result", value)
        button.click()
    assert editor._apply()
    assert clip.details["result"] == "Touchdown; Interception"
    editor.detail_edits["result"].setText("TD; INT")
    editor._set_result_from_chip("Touchdown")
    assert editor.detail_edits["result"].text() == "INT"
    editor._set_result_from_chip("TD")
    assert editor.detail_edits["result"].text() == "INT; Touchdown"
    editor.detail_edits["result"].setText("INT; Interception")
    assert editor._apply()
    assert editor.apply_quick_details({"action": "Pressure"})
    assert clip.details["result"] == "INT; Interception"


def test_detail_tag_cleanup_keeps_other_field_references_and_free_text(editor):
    clip = Clip(0, 1000, tags=["Pressure", "First Down", "old formation", "free note"],
                details={"action": "Pressure", "result": "First Down", "off_formation": "old formation",
                         "other_players": "Pressure, First Down"})
    editor.set_clip(clip)
    editor.details_to_tags_check.setChecked(False)
    assert editor.apply_quick_details({"action": "Block", "result": "", "off_formation": "new formation"},
                                      remove_tag_values=("Pressure", "First Down"))
    assert clip.tags == ["Pressure", "First Down", "free note"]
    editor.detail_edits["other_players"].setText("")
    assert editor._apply()
    assert clip.tags == ["free note"]


def test_yards_accepts_negative_zero_blank_and_refuses_unfinished_integer(editor):
    clip = Clip(0, 1000, details={"down_distance": "2nd & 7"})
    editor.set_clip(clip)
    for raw in ("-3", "0", ""):
        editor.detail_edits["yards"].setText(raw)
        assert editor._apply()
        assert clip.details.get("yards", "") == raw
        assert clip.details["down_distance"] == "2nd & 7"
    before = dict(clip.details)
    editor.detail_edits["yards"].setText("-")
    assert not editor._apply()
    assert clip.details == before


def test_derived_summary_uses_threshold_settings_without_mutating_the_clip(editor):
    from tapesift.core.config import AppSettings

    editor.settings = AppSettings(explosive_rush_yards=8, explosive_pass_yards=20)
    clip = Clip(0, 1000, tags=["free"], details={"run_pass": "Run", "yards": "8", "down_distance": "2nd & 10"})
    before = dict(clip.details)
    editor.set_clip(clip)
    assert "Explosive: Yes" in editor.summary_card.toolTip()
    assert "Run 8+ / Pass 20+" in editor.summary_card.toolTip()
    assert "Successful: Yes" in editor.summary_card.toolTip()
    editor.detail_edits["run_pass"].setText("Pass")
    editor.detail_edits["down_distance"].setText("3rd & Inches")
    editor._refresh_analyst_summary()
    assert "Explosive: No" in editor.summary_card.toolTip()
    assert "Successful: Unknown" in editor.summary_card.toolTip()
    assert clip.details == before and clip.tags == ["free"]



def test_full_mini_field_keeps_both_endzones_and_fixed_orientation(app):
    from tapesift.ui_v3.clip_details import MiniFieldV3

    field = MiniFieldV3()
    field.resize(348, 148)
    details = {"ball_on": "OWN 41", "down_distance": "3rd & 8",
               "offense_team_id": IDS[0], "attack_direction": "right"}
    field.set_details(details, IDS)
    right = [field.x_for_yard(yard) for yard in (0, 41, 49, 100)]
    assert 7 < right[0] < right[1] < right[2] < right[3] < field.width()-7
    assert (right[3]-right[0]) == pytest.approx((field.width()-14)*100/120)
    field.set_details({**details, "ball_on": "OPP 4", "down_distance": "1st & Goal"}, IDS)
    assert field.x_for_yard(41) == right[1]  # Selection must not crop or zoom the field.
    assert field.context.to_gain_yards == 100
    field.set_details({**details, "attack_direction": "left"}, IDS)
    assert [field.x_for_yard(y) for y in (0, 41, 49, 100)] == pytest.approx(
        right)
    field.deleteLater()


@pytest.mark.parametrize("width", [276, 348])
def test_mini_field_paints_saved_lines_and_clears_unknown_gain(app, width):
    from PySide6.QtGui import QColor
    from tapesift.ui_v3.clip_details import MiniFieldV3

    field = MiniFieldV3()
    field.resize(width, 148)
    details = {"ball_on": "OWN 41", "down_distance": "3rd & 8",
               "offense_team_id": IDS[0], "attack_direction": "right"}

    def line_height(image, color, x):
        target = QColor(color)
        # Read the painted field, excluding the legend and antialiased line edges.
        return max(sum(max(abs(image.pixelColor(column, y).red()-target.red()),
                           abs(image.pixelColor(column, y).green()-target.green()),
                           abs(image.pixelColor(column, y).blue()-target.blue())) < 18
                       for y in range(9, 120))
                   for column in range(round(x)-2, round(x)+3))

    field.set_details(details, IDS)
    image = field.grab().toImage()
    assert line_height(image, "#41a5ef", field.x_for_yard(41)) > 100
    assert line_height(image, "#e7b344", field.x_for_yard(49)) > 100
    grass = image.pixelColor(round(field.x_for_yard(55)), 30)
    assert grass.green() > grass.red() and grass.green() > grass.blue()
    for changed in ({"down_distance": "3rd & Inches"}, {"down_distance": "3rd"},
                    {"ball_on": "OPP 4", "down_distance": "1st & 40"}):
        field.set_details({**details, **changed}, IDS)
        assert field.context.to_gain_yards is None
        image = field.grab().toImage()
        assert line_height(image, "#e7b344", field.x_for_yard(49)) < 10
    for changed in ({"offense_team_id": ""}, {"attack_direction": ""}):
        field.set_details({**details, **changed}, IDS)
        assert field.context.los_yards == 41 and field.context.to_gain_yards == 49
    for changed in ({"ball_on": "41"}, {"ball_on": ""}):
        field.set_details({**details, **changed}, IDS)
        assert field.context.los_yards is None and field.context.to_gain_yards is None
        image = field.grab().toImage()
        assert line_height(image, "#41a5ef", field.x_for_yard(41)) < 10
        assert line_height(image, "#e7b344", field.x_for_yard(49)) < 10
    field.set_details({}, [])
    assert field.context.los_yards is None
    assert "Set" in field.accessibleName() or "Choose" in field.accessibleName()
    field.deleteLater()


def test_teams_paint_before_logging_and_spot_saves_without_direction(editor, monkeypatch):
    clip = Clip(0, 1000, details={"attack_direction": "left"})
    editor.set_clip(clip)
    panel = editor.context_panel
    field = panel.mini_field
    assert field.game_team_ids == IDS
    assert all(not field._logos[team].isNull() for team in IDS)
    assert not field.grab().isNull()
    assert not hasattr(panel, "direction_combo")
    panel.ball_edit.setText("25")
    assert field.context.los_yards is None  # A bare 25 is still ambiguous.
    panel.side_combo.setCurrentIndex(panel.side_combo.findData("OWN"))
    panel.distance_edit.setText("10")
    assert (field.context.los_yards, field.context.to_gain_yards) == (25, 35)
    assert editor._apply()
    assert clip.details["ball_on"] == "OWN 25"
    assert "offense_team_id" not in clip.details
    assert clip.details["attack_direction"] == "left"  # Legacy data is retained.
    editor.set_clip(clip)
    assert field.context.direction == "right"  # Display convention only.
    assert field.context.to_gain_yards == 35


def test_quick_logging_defaults_players_and_yardage_save_together(editor):
    from tapesift.services.detail_service import details_to_tags, compose_clip_name
    from tapesift.services import result_service

    notifications = []
    editor.logging_defaults_changed.connect(notifications.append)
    editor.set_logging_defaults({"quarter": "Q2", "quarterback": "10 Cam Ward"})
    clip = Clip(0, 1000)
    editor.set_clip(clip)
    assert clip.details == {}  # Carry-forward remains a draft until Save.
    assert editor.quarter_buttons["Q2"].isChecked()
    assert "carried forward" in editor.carry_hint.text()
    editor.quarter_buttons["OT"].click()
    editor.down_buttons["3"].click()
    editor.context_panel.distance_edit.setText("7")
    assert editor.secondary_entry.isHidden()
    editor.add_secondary_button.click()
    assert not editor.secondary_entry.isHidden()
    editor.secondary_entry.setText("Xavier Restrepo, Mark Fletcher")
    editor.quick_result_buttons["Completion"].click()
    assert len(editor.quick_result_buttons) == 16
    assert "Kneel" not in editor.quick_result_buttons
    editor.quick_result_buttons["YAC"].click()
    assert editor.quick_result_buttons["YAC"].isChecked()
    assert editor._quick_result_flow.count() == 0
    editor._populate_result_menu()
    kneel = next(action for action in editor._v3_result_menu.actions() if action.text() == "Kneel")
    kneel.trigger()
    assert result_service.has_result(editor.detail_edits["result"].text(), "Kneel")
    kneel.trigger()
    editor.detail_edits["yards"].setText("18")
    editor.detail_edits["yac"].setText("11")
    assert editor._apply()
    assert clip.details["quarter"] == "OT"
    assert clip.details["quarterback"] == "10 Cam Ward"
    assert clip.details["down_distance"] == "3rd & 7"
    assert clip.details["yards"] == "18" and clip.details["yac"] == "11"
    assert result_service.has_result(clip.details["result"], "YAC")
    assert {"Xavier Restrepo", "Mark Fletcher", "10 Cam Ward"} <= set(details_to_tags(clip.details))
    assert "QOT" not in compose_clip_name(clip.details)
    assert notifications[-1] == {"quarter": "OT", "quarterback": "10 Cam Ward"}
    following = Clip(1000, 2000)
    editor.set_clip(following)
    assert editor.quarter_buttons["OT"].isChecked()
    assert editor.detail_edits["quarterback"].text() == "10 Cam Ward"
    assert editor.detail_edits["other_players"].text() == ""
    editor.set_clip(clip)
    editor.quarter_buttons["Q1"].click()
    assert editor._apply()
    assert editor.logging_defaults["quarter"] == "OT"  # Editing an old clip is local.
    assert len(notifications) == 1
    editor.set_clip(Clip(2000, 3000))
    editor.detail_edits["yac"].setText("not a number")
    assert not editor._apply()
    assert len(notifications) == 1


def test_gain_menu_shortcuts_and_inline_steps_save_exact_yards(editor, app, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from tapesift.services.project_service import ProjectSession
    from tapesift.services.play_field_service import describe_play
    from tapesift.ui_v3.theme import stylesheet

    previous_style = app.styleSheet()
    app.setStyleSheet(stylesheet())
    session = ProjectSession.create("Gain shortcuts", tmp_path, tmp_path / "exports")
    try:
        clip = session.add_clip(Clip(0, 1000, details={"yards": "7", "yac": "2", "result": "Completion; Reception",
            "ball_on": "OWN 30", "offense_team_id": IDS[0], "run_pass": "Pass", "down_distance": "1st & 10"}))
        editor.set_clip(clip)
        editor.resize(380, 760)
        editor.show()
        QTest.qWait(100)
        editor.form_area.ensureWidgetVisible(editor.gain_panel)
        QTest.qWait(100)
        buttons = list(editor.quick_gain_buttons.values())
        assert len(editor.quick_result_buttons) == 16
        menu = editor.gain_units_button.menu()
        menu.popup(editor.gain_units_button.mapToGlobal(editor.gain_units_button.rect().bottomLeft()))
        QTest.qWait(50)
        assert menu.isVisible() and all(button.isVisibleTo(menu) for button in buttons)
        assert len({b.y() for b in buttons}) == 1
        assert all(b.parentWidget().rect().contains(b.geometry()) for b in buttons)
        assert all(left.geometry().right() < right.x() for left, right in zip(buttons, buttons[1:]))
        controls = [*buttons, editor.gain_decrease_btn, editor.detail_edits["yards"], editor.gain_increase_btn]
        assert editor.gain_decrease_btn.geometry().center().y() == editor.detail_edits["yards"].geometry().center().y()
        assert editor.gain_decrease_btn.geometry().right() < editor.gain_increase_btn.x()
        assert all(control.parentWidget().rect().contains(control.geometry()) for control in controls)
        for yards in (5, 10, 20, 20):
            menu.popup(editor.gain_units_button.mapToGlobal(editor.gain_units_button.rect().bottomLeft()))
            QTest.qWait(20)
            assert editor.quick_gain_buttons[yards].isVisibleTo(menu)
            QTest.mouseClick(editor.quick_gain_buttons[yards], Qt.MouseButton.LeftButton)
            assert editor.detail_edits["yards"].text() == str(yards)
        menu.hide()
        edit = editor.detail_edits["yards"]
        edit.clear()
        editor.gain_decrease_btn.click()
        assert edit.text() == "-1"
        editor.gain_increase_btn.click()
        assert edit.text() == "0"
        for value, button in (("109", editor.gain_increase_btn), ("-99", editor.gain_decrease_btn),
                              ("-", editor.gain_decrease_btn)):
            edit.setText(value)
            button.click()
            assert edit.text() == value
        editor.quick_gain_buttons[10].click()
        editor.gain_increase_btn.click()
        assert edit.text() == "11"
        for _ in range(3):
            editor.gain_decrease_btn.click()
        assert edit.text() == "8"
        geometry = describe_play(editor.context_panel.mini_field.details, IDS)
        assert geometry.context.los_yards == 30 and geometry.end == 38
        assert clip.details["yards"] == "7"
        assert editor._apply()
        session.save()
        reopened = ProjectSession.open(session.db_path)
        try:
            assert reopened.clips[0].details["yards"] == "8"
            assert reopened.clips[0].details["ball_on"] == "OWN 30"
            assert reopened.clips[0].details["yac"] == "2"
            assert reopened.clips[0].details["result"] == "Completion; Reception; Gain"
        finally:
            reopened.close()
        editor.grab().save(str(tmp_path / "gain-shortcuts.png"))
    finally:
        session.conn.close()
        app.setStyleSheet(previous_style)


def test_failed_commit_keeps_draft_but_restores_live_clip(editor):
    clip = Clip(0, 1000)
    editor.set_logging_defaults({"quarter": "Q1"})
    editor.set_clip(clip)
    editor.quarter_buttons["Q2"].click()
    editor.secondary_entry.setText("Test Receiver")
    editor.clip_edited.connect(lambda _id: setattr(editor, "_save_commit_error", "Disk full"))
    advanced = []
    editor.save_and_advance.connect(lambda: advanced.append(True))
    editor.save_next_btn.click()
    assert advanced == []
    assert clip.details == {} and clip.clip_title == "" and clip.tags == []
    assert editor.detail_edits["quarter"].text() == "Q2"
    assert editor.secondary_entry.text() == "Test Receiver"
    assert editor.save_state_label.property("state") == "dirty"
    assert editor.logging_defaults == {"quarter": "Q1"}


def test_quarter_correction_on_current_new_play_updates_default_until_navigation(editor):
    clip = Clip(0, 1000)
    editor.set_clip(clip)
    editor.quarter_buttons["Q1"].click()
    assert editor._apply()
    editor.quarter_buttons["Q2"].click()
    assert editor._apply()
    assert editor.logging_defaults["quarter"] == "Q2"
    editor.set_clip(Clip(1000, 2000))
    assert editor.detail_edits["quarter"].text() == "Q2"
    editor.set_clip(clip)
    editor.quarter_buttons["Q3"].click()
    assert editor._apply()
    assert editor.logging_defaults["quarter"] == "Q2"


def test_modeless_field_tracks_panel_edits_and_cannot_restore_stale_metadata(editor):
    clip = Clip(0, 1000, details={"quarter": "Q1", "ball_on": "OWN 25", "run_pass": "Run", "custom": "keep"})
    editor.set_clip(clip)
    editor.open_field_editor()
    dialog = editor._field_dialog
    old_snapshot = dict(dialog.details)
    editor.quarter_buttons["Q2"].click()
    editor.detail_edits["player_name"].setText("Updated Player")
    editor.quick_result_buttons["Completion"].click()
    assert dialog.details["quarter"] == "Q2" and dialog.details["player_name"] == "Updated Player"
    editor.stage_field_details(old_snapshot | {"field_flip": "1"})
    assert editor.detail_edits["quarter"].text() == "Q2"
    assert editor.detail_edits["player_name"].text() == "Updated Player"
    assert "Completion" in editor.detail_edits["result"].text()
    assert dialog.details["field_flip"] == "1"
    assert editor._collect_details()["custom"] == "keep"
    dialog.close()


def test_searchable_results_and_colored_yards_preserve_saved_clip(editor, app, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from tapesift.services import result_service
    from tapesift.ui_v3.theme import stylesheet
    old_style = app.styleSheet()
    app.setStyleSheet(stylesheet())
    try:
        clip = Clip(0, 1000)
        editor.set_clip(clip)
        editor.resize(400, 1000)
        editor.show()
        QTest.qWait(80)
        editor.form_area.ensureWidgetVisible(editor.quick_results)
        QTest.qWait(40)
        assert editor.gain_status.text() == "Not entered"
        editor.quick_result_more.click()
        picker = editor._results_picker
        assert picker.isVisible() and picker.height() <= 570
        picker.search.setText("completion")
        assert not picker.checks["Completion"].isHidden()
        assert picker.checks["Gain"].isHidden()
        picker.checks["Completion"].click()
        assert picker.checks["Reception"].isChecked()
        assert not picker.checks["Incompletion"].isChecked()
        assert editor.gain_status.text() == "Not entered"
        picker.search.clear()
        picker.grab().save(str(tmp_path / "results-picker.png"))
        picker.close()
        editor.quick_gain_buttons[10].click()
        editor.gain_increase_btn.click()
        assert editor.detail_edits["yards"].text() == "11"
        assert editor.gain_status.text() == "+11 yd"
        editor.yac_increase_btn.click()
        assert editor.detail_edits["yac"].text() == "1"
        assert editor.detail_edits["yards"].text() == "11"
        editor.yac_decrease_btn.click()
        assert editor.detail_edits["yac"].text() == "0"
        assert clip.details == {}
        assert editor._apply()
        assert clip.details["yards"] == "11" and clip.details["yac"] == "0"
        assert result_service.has_result(clip.details["result"], "Gain")
        editor.form_area.ensureWidgetVisible(editor.gain_panel)
        QTest.qWait(40)
        assert editor.form_area.horizontalScrollBar().maximum() == 0
        editor.grab().save(str(tmp_path / "results-panel.png"))
    finally:
        app.setStyleSheet(old_style)


def test_result_shortcut_edit_add_remove_reorder_and_restart(editor, app, tmp_path, monkeypatch):
    from tapesift.core.config import AppSettings
    from tapesift.ui_v3.result_picker import ResultShortcutsDialog, ShortcutTile
    from PySide6.QtWidgets import QPushButton, QDialogButtonBox
    from PySide6.QtTest import QTest
    from tapesift.ui_v3.theme import stylesheet
    target = tmp_path / "preferences.json"
    monkeypatch.setattr("tapesift.core.paths.settings_file", lambda: target)
    editor.settings = AppSettings()
    clip = Clip(0, 1000, details={"result": "Completion; Reception"})
    editor.set_clip(clip)
    old_style = app.styleSheet()
    app.setStyleSheet(stylesheet())
    def interact(dialog):
        dialog.show()
        QTest.qWait(30)
        dialog.grab().save(str(tmp_path / "result-shortcuts.png"))
        tile = dialog.grid.itemAtPosition(0, 0).widget()
        tile.findChild(QPushButton).click()
        assert "Gain" not in dialog.favorites()
        dialog.custom_edit.setText("Contested reception")
        dialog.add_custom()
        assert len(dialog.favorites()) == 16
        dialog.move_shortcut(15, 0)
        assert dialog.favorites()[0] == "Contested reception"
        assert clip.details == {"result": "Completion; Reception"}
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        return dialog.result()
    monkeypatch.setattr(ResultShortcutsDialog, "exec", interact)
    try:
        editor.quick_result_edit.click()
        loaded = AppSettings.load(target)
        assert loaded.v3_result_favorites[0] == "Contested reception"
        assert "Contested reception" in loaded.fixed_details["result"]
        assert "Gain" not in editor.quick_result_buttons
        assert editor.quick_result_buttons["Completion"].isChecked()
        reopened = ClipDetailsV3(settings=loaded)
        try:
            reopened.set_analyst_mode(True)
            reopened.set_clip(clip)
            assert list(reopened.quick_result_buttons) == loaded.v3_result_favorites
            assert "Contested reception" in reopened._all_result_values()
            assert clip.details == {"result": "Completion; Reception"}
        finally:
            reopened.close()
            reopened.deleteLater()
        prior = list(editor.quick_result_buttons)
        def fail_save(*_args, **_kwargs):
            raise OSError("test save failure")
        monkeypatch.setattr(AppSettings, "save", fail_save)
        monkeypatch.setattr("tapesift.ui_v3.clip_details.QMessageBox.warning", lambda *a: None)
        assert not editor._save_result_preferences(["Gain"], ["New outcome"], editor)
        assert list(editor.quick_result_buttons) == prior
        assert AppSettings.load(target).v3_result_favorites == prior
    finally:
        app.setStyleSheet(old_style)


def test_result_picker_custom_library_and_empty_shortcuts_persist(editor, tmp_path, monkeypatch):
    from tapesift.core.config import AppSettings
    from tapesift.services import result_service
    target = tmp_path / "preferences.json"
    monkeypatch.setattr("tapesift.core.paths.settings_file", lambda: target)
    editor.settings = AppSettings()
    clip = Clip(0, 1000, details={"result": "Reception; Completion"})
    editor.set_clip(clip)
    editor.show()
    assert editor._save_result_preferences([], [], editor)
    assert list(editor.quick_result_buttons) == []
    assert AppSettings.load(target).v3_result_favorites == []
    assert clip.details == {"result": "Reception; Completion"}
    editor.quick_result_more.click()
    picker = editor._results_picker
    picker._show_custom()
    picker.custom_name.setText("Bad; two values")
    picker._add_custom()
    assert "Bad; two values" not in editor._all_result_values()
    picker.custom_name.setText("Contested reception")
    picker.pin_custom.setChecked(True)
    picker._add_custom()
    picker = editor._results_picker
    assert "Contested reception" in editor.quick_result_buttons
    assert picker.search.text() == "Contested reception"
    picker.checks["Contested reception"].click()
    assert result_service.has_result(editor.detail_edits["result"].text(), "Contested reception")
    picker.close()
    assert editor._apply()
    assert result_service.has_result(clip.details["result"], "Contested reception")
    loaded = AppSettings.load(target)
    assert loaded.v3_result_favorites == ["Contested reception"]
    assert "Contested reception" in loaded.fixed_details["result"]


def test_inline_yardage_and_expandable_notes_preserve_draft(editor, app, tmp_path):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from tapesift.ui_v3.theme import stylesheet
    old = app.styleSheet()
    app.setStyleSheet(stylesheet())
    try:
        clip = Clip(0, 1000, notes="Original notes")
        editor.set_clip(clip)
        editor.resize(400, 1050)
        editor.show()
        QTest.qWait(80)
        editor.quick_gain_buttons[10].click()
        editor.detail_edits["yac"].setText("4")
        assert editor.detail_edits["yards"].height() <= 34
        assert editor.gain_panel.isAncestorOf(editor.detail_edits["yards"])
        assert editor.gain_panel.isAncestorOf(editor.quick_result_buttons["Gain"])
        assert editor.gain_panel.mapTo(editor, QPoint()).y() < editor.yac_panel.mapTo(editor, QPoint()).y()
        editor.notes_edit.setPlainText("Longer draft notes\nSecond line")
        editor.expand_notes_button.click()
        assert editor.notes_edit.height() == 180
        editor.resize(380, 1000)
        QTest.qWait(60)
        assert editor.notes_edit.height() == 180
        editor.form_area.ensureWidgetVisible(editor.notes_edit)
        editor.grab().save(str(tmp_path / "notes-expanded.png"))
        editor.expand_notes_button.click()
        assert editor.notes_edit.height() == 76
        assert editor.notes_edit.toPlainText() == "Longer draft notes\nSecond line"
        assert clip.notes == "Original notes"
        assert editor._apply()
        assert clip.notes == "Longer draft notes\nSecond line"
        editor.form_area.ensureWidgetVisible(editor.yardage_panel)
        QTest.qWait(30)
        editor.grab().save(str(tmp_path / "paired-yardage.png"))
    finally:
        app.setStyleSheet(old)
