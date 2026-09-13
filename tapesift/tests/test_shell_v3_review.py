from __future__ import annotations

import gc
import json

import shiboken6
import pytest

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDockWidget, QMessageBox, QWidget

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.models.export_job import ExportJob, JobStatus, JobType
from tapesift.models.export_package import ExportStyle
from tapesift.services.project_service import ProjectSession
from tapesift.ui_core.timeline import TimelineBlock
from tapesift.ui_v3.main_window import MainWindowV3
from tapesift.ui_v3.theme import stylesheet
from tapesift.ui_v3.workspace_state import ReviewRailState


_WINDOWS: list[MainWindowV3] = []


@pytest.fixture(autouse=True)
def _close_test_window():
    yield
    app = QApplication.instance()
    windows = tuple(_WINDOWS)
    _WINDOWS.clear()
    for window in windows:
        if shiboken6.isValid(window):
            # Tests use temporary projects and sometimes leave their session
            # dirty. The real close path correctly asks a human whether to
            # save; a headless test has no human and used to block forever in
            # QMessageBox.exec(). Persist the temp session first, then take
            # the normal non-interactive close path.
            session = getattr(window, "session", None)
            if session is not None \
                    and getattr(session, "dirty", False) \
                    and not getattr(session, "read_only", False):
                session.save()
            window.close()
    if app is not None:
        app.processEvents()
        for window in windows:
            if shiboken6.isValid(window):
                shiboken6.delete(window)
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    del windows
    gc.collect()


def _window(tmp_path) -> MainWindowV3:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(stylesheet())
    window = MainWindowV3(
        AppSettings(), workspace_state_path=tmp_path / "shell-v3.json")
    window._screen_fit_done = True
    window.resize(1708, 921)
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    app.processEvents()
    _WINDOWS.append(window)
    return window


def _has_dock_ancestor(widget) -> bool:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QDockWidget):
            return True
        parent = parent.parentWidget()
    return False


def test_export_menu_is_visible_in_review_and_routes_each_choice(tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    calls = []
    monkeypatch.setattr(MainWindowV3, "_open_cutup_builder", lambda self: calls.append("cutups"))
    monkeypatch.setattr(MainWindowV3, "_open_play_export", lambda self, cid: calls.append(cid))
    monkeypatch.setattr(MainWindowV3, "_configure_selected_package_export", lambda self: calls.append("selected"))
    window = _window(tmp_path)
    session = ProjectSession.create("Export menu QA", tmp_path / "projects", tmp_path / "exports")
    clip = session.add_clip(Clip(0, 1000, clip_title="Play 001"))
    window.session = session
    window._refresh_clip_list()
    window.clip_editor.set_clip(clip)
    monkeypatch.setattr(window.clip_list, "selected_clip_ids", lambda: [clip.id])
    monkeypatch.setattr(window, "_v3_export_can_start", lambda: True)
    button = window._v3_review_export_button
    for rails in (ReviewRailState(False, False), ReviewRailState(True, True)):
        window._v3_review.set_rail_state(rails)
        QApplication.processEvents()
        assert button.isVisible() and button.width() >= 60
        assert button.mapTo(window, QPoint()).x() < window._v3_review_library_button.mapTo(window, QPoint()).x()
    menu = button.menu()
    observed = []
    def inspect_menu():
        observed.append(menu.isVisible())
        window.grab().save(str(tmp_path / "review-export-button.png"))
        menu.grab().save(str(tmp_path / "export-menu.png"))
        menu.close()
    QTimer.singleShot(100, inspect_menu)
    button.showMenu()
    QTest.qWait(200)
    assert observed == [True]
    for action in menu.actions():
        assert action.isEnabled()
        action.trigger()
    assert calls == ["cutups", clip.id, "selected"]
    window.session = None
    menu.aboutToShow.emit()
    assert all(not action.isEnabled() for action in menu.actions())
    session.close()


def test_timeline_and_tag_map_align_and_select_the_same_clip(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    session = ProjectSession.create("Aligned clips", tmp_path / "projects", tmp_path / "exports")
    clips = [session.add_clip(Clip(start_ms=start, end_ms=start+20_000,
                                  details={"quarter": "Q1", "run_pass": "Pass", "player_name": "10 QB"}))
             for start in (10_000, 50_000, 90_000)]
    window.session = session
    window._refresh_clip_list()
    window.player._duration_changed(140_000)
    grid, slider = window.player.attribute_grid, window.player.slider
    for width, labels, ledger, details in (
            (1708, True, False, False), (1708, False, True, False),
            (1708, True, False, True), (1708, False, True, True),
            (1280, False, True, True), (1280, True, True, True)):
        window.resize(width, 921)
        window.settings.show_tag_map_labels = labels
        window._apply_tag_map_labels()
        assert grid.plot_left() == 0  # Legacy profiles must not restore the label gutter.
        assert window.player.timeline_scrub_layout.contentsMargins().left() == 0
        window._v3_review.set_rail_state(ReviewRailState(ledger, details))
        app.processEvents()
        for start, end in ((0, 140_000), (40_000, 85_000), (0, 110_000)):
            slider.fit_range(start, end)
            app.processEvents()
            for ms in (start, start + (end-start)//3, (start+end)//2, end):
                slider.setValue(ms)
                top = slider.mapTo(window, QPoint(slider._x_for(slider.value()), 0)).x()
                bottom = grid.mapTo(window, QPoint(grid._x_for(ms), 0)).x()
                assert abs(top-bottom) <= 1, (width, labels, ms, top, bottom)
            for clip in grid.visible_clips():
                for ms in (clip.start_ms, clip.end_ms):
                    top = slider.mapTo(window, QPoint(slider._x_for(ms), 0)).x()
                    bottom = grid.mapTo(window, QPoint(grid._x_for(ms), 0)).x()
                    assert abs(top-bottom) <= 1, (ledger, details, ms, top, bottom)
                left, right = grid.clip_span(clip)
                assert grid.clip_at((left + right) / 2, grid._row_top(0) + 12).id == clip.id
    window.settings.show_tag_map_labels = False
    window._apply_tag_map_labels()
    menu = window._v3_map_menu_button.menu()
    window._populate_map_menu(menu)
    lanes = next(action.menu() for action in menu.actions() if action.text() == "Tag Map rows")
    people = next(action for action in lanes.actions() if action.text() == "People")
    people.trigger()
    assert "people" not in [row.key for row in grid.rows()]
    people.trigger()
    assert "people" in [row.key for row in grid.rows()]
    slider.reset_zoom()
    app.processEvents()
    second = clips[1]
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton,
                     pos=QPoint(slider._x_for(60_000), slider._band_top()+5))
    assert window._selected_clip_id == second.id
    assert grid._selected_clip_id == second.id
    assert window.clip_editor._clip.id == second.id
    slider.reset_zoom()
    app.processEvents()
    first = clips[0]
    QTest.mouseClick(grid, Qt.MouseButton.LeftButton,
                     pos=QPoint(grid._x_for(20_000), grid._row_top(0)+8))
    assert window._selected_clip_id == first.id
    assert {b.clip_id for b in slider._blocks if b.selected} == {first.id}
    assert window.clip_editor._clip.id == first.id
    QTest.qWait(200)
    editor = window.clip_editor
    editor.form_area.ensureWidgetVisible(editor.action_buttons[0])
    QTest.qWait(100)
    for buttons in (editor.action_buttons, list(editor.quick_result_buttons.values())):
        assert buttons[4].geometry().top() > buttons[0].geometry().bottom()
        assert all(button.parentWidget().rect().contains(button.geometry()) for button in buttons)
    window.grab().save(str(tmp_path / "aligned-review.png"))


def test_library_game_year_updates_live_and_closed_projects_without_touching_clips(tmp_path, monkeypatch):
    from tapesift.services import library_service
    from tapesift.ui.project_settings_dialog import ProjectSettingsDialog
    monkeypatch.setattr(library_service, "catalog_path", lambda: tmp_path / "catalog.db")
    window = _window(tmp_path)
    sessions = [ProjectSession.create("Same matchup", tmp_path / str(i), tmp_path / "exports") for i in range(2)]
    try:
        for session in sessions:
            session.add_clip(Clip(0, 10_000, clip_title="Preserved title", details={"result": "Completion"}))
            session.save()
            library_service.reindex_project_file(str(session.db_path))
        window.session = sessions[0]
        window._refresh_clip_list()
        for session, year in zip(sessions, ("2024", "2025")):
            before = [tuple(row) for row in session.conn.execute("SELECT * FROM clips")]
            assert window._apply_library_game_year(str(session.db_path), year) == (True, "")
            assert [tuple(row) for row in session.conn.execute("SELECT * FROM clips")] == before
            assert session.conn.execute("SELECT game_year FROM projects").fetchone()[0] == year
        assert sessions[0].project.game_year == "2024"
        sessions[0].read_only = True
        assert not window._apply_library_game_year(str(sessions[0].db_path), "2030")[0]
        assert sessions[0].project.game_year == "2024"
        sessions[0].read_only = False
        missing = tmp_path / "missing.tapesift"
        assert not window._apply_library_game_year(str(missing), "2030")[0]
        assert not missing.exists()
        sessions[0].save()
        assert sessions[0].conn.execute("SELECT game_year FROM projects").fetchone()[0] == "2024"
        screen = window.library_screen
        window.stack.setCurrentWidget(screen)
        screen.refresh()
        screen.year_combo.setCurrentIndex(screen.year_combo.findData("2024"))
        assert len(screen._results) == 1
        screen.results_list.setCurrentRow(0)
        screen.preview_notes.setPlainText("Unsaved note stays here")
        screen.game_year_edit.setValue(2026)
        screen.game_year_save.click()
        assert sessions[0].project.game_year == "2026"
        assert screen.preview_notes.toPlainText() == "Unsaved note stays here"
        assert "2026" in screen.preview_meta.text()
        assert screen.year_combo.currentData() == "2026"
        assert len(screen._results) == 1 and screen._results[0].game_year == "2026"
        screen.search_box.setText("2026")
        screen._run_search()
        screen.results_list.setCurrentRow(0)
        screen.preview_notes.setPlainText("Draft survives old-year search")
        screen.game_year_edit.setValue(2027)
        screen.game_year_save.click()
        QTest.qWait(200)
        assert screen._editing_row.game_year == "2027"
        assert screen.preview_notes.toPlainText() == "Draft survives old-year search"
        assert sessions[0].clips[0].notes == ""
        screen.year_combo.setCurrentIndex(screen.year_combo.findData("2025"))
        assert len(screen._results) == 1 and screen._results[0].project_path == str(sessions[1].db_path)
        screen.results_list.setCurrentRow(0)
        QApplication.processEvents()
        QTest.qWait(150)
        window.grab().save(str(tmp_path / "library-game-year.png"))
    finally:
        sessions[1].conn.close()


@pytest.mark.parametrize("advance", [False, True])
def test_details_save_next_persists_first_click_and_updates_tag_map(tmp_path, advance):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    session = ProjectSession.create("Save Next", tmp_path / "projects", tmp_path / "exports")
    first = session.add_clip(Clip(start_ms=1000, end_ms=7000, clip_title="First"))
    second = session.add_clip(Clip(start_ms=8000, end_ms=14000, clip_title="Second"))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(first.id, seek=False)
    editor = window.clip_editor
    for key, value in {"quarter": "Q1", "run_pass": "Pass", "result": "Completion"}.items():
        editor.detail_edits[key].setText(value)
    editor.notes_edit.setPlainText("Save without a separate Save click")
    # Clicking the selected timeline/Tag Map clip must not discard the draft.
    assert window.select_clip(first.id, seek=False)
    (editor.save_next_btn if advance else editor.apply_btn).click()
    app.processEvents()
    assert window._selected_clip_id == (second.id if advance else first.id)
    details, notes = session.conn.execute(
        "SELECT details_json, notes FROM clips WHERE id=?", (first.id,)).fetchone()
    assert json.loads(details)["run_pass"] == "Pass"
    assert json.loads(details)["quarter"] == "Q1"
    assert notes == "Save without a separate Save click"
    grid = window.player.attribute_grid
    mapped = next(clip for clip in grid._clips if clip.id == first.id)
    row = next(row for row in grid.rows() if row.key == "primary_tag")
    assert "Pass" in grid._review_value(row, mapped)
    assert "1 unlogged" in window.review_label.text()
    # An explicit merge replaces the survivor's range and clears metadata;
    # it must still reload the editor even though the Clip identity survives.
    window.select_clip(first.id, seek=False)
    editor.notes_edit.setPlainText("Draft before merge")
    window._merge_clips([first.id, second.id])
    editor.apply_btn.click()
    assert session.get_clip(first.id).end_ms == 14000
    assert editor.notes_edit.toPlainText() != "Draft before merge"


def test_v3_defaults_to_the_locked_both_collapsed_state(tmp_path):
    window = _window(tmp_path)
    review = window._v3_review
    assert review.rail_state() == ReviewRailState()
    assert review.ledger_rail.width() == 72
    assert review.details_rail.width() == 72
    assert review.center_stack.width() == 1564


def test_export_clip_opens_clean_setup_without_starting_a_preview_or_job(
        tmp_path):
    from PySide6.QtCore import QTimer
    from tapesift.ui_v3.play_export_dialog import PlayExportDialog
    window = _window(tmp_path)
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.mp4"
    source.write_bytes(b"setup-only source")
    session = ProjectSession.create(
        "Export Setup", tmp_path / "projects", tmp_path / "exports")
    session.project.source_video_path = str(source)
    clip = Clip(start_ms=1_000, end_ms=6_000, clip_title="Play 001")
    session.add_clip(clip)
    session.save()
    assert window._activate_session(session)
    app.processEvents()

    # The standard per-play chooser previews the summary first; Video only
    # continues through the established Clean export setup.
    def choose_video_only():
        dialog = window.findChild(PlayExportDialog)
        assert dialog is not None
        dialog._video_only()
    QTimer.singleShot(0, choose_video_only)
    window._v3_review.action_mirror.buttons["export"].click()
    app.processEvents()

    assert window._workspace_stage == "export"
    assert window._v3_review.center_stack.currentWidget() \
        is window._v3_review.workbench
    assert window._v3_review.export_page.isVisible()
    assert window._v3_review.export_page.route == "clip"
    assert window.export_panel.export_style is ExportStyle.CLEAN
    assert window.export_panel.mode_combo.currentData() == "individual"
    assert window._review_export_scope_ids == (clip.id,)
    assert window.export_worker is None
    assert window._export_preview_worker is None
    assert window._v3_review.back_to_review_button.isVisible()


def test_v3_uses_one_stable_width_for_each_open_panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    review = window._v3_review
    review.set_rail_state(ReviewRailState(True, False))
    app.processEvents()
    assert (review.ledger_rail.width(), review.details_rail.width()) \
        == (326, 72)
    review.set_rail_state(ReviewRailState(False, True))
    app.processEvents()
    assert (review.ledger_rail.width(), review.details_rail.width()) \
        == (72, 326)
    review.set_rail_state(ReviewRailState(True, True))
    app.processEvents()
    assert (review.ledger_rail.width(), review.center_stack.width(),
            review.details_rail.width()) == (326, 1056, 326)


def test_open_ledger_header_and_filters_clear_the_collapse_affordance(
        tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    review = window._v3_review
    review.set_rail_state(ReviewRailState(True, False))
    window._sync_v3_ledger_summary()
    app.processEvents()

    rail = review.ledger_rail
    header = window.clip_list.findChild(QWidget, "ClipLedgerHeader")
    title = window.clip_list.heading_label
    summary = window.clip_list.progress_label
    strip = rail._collapse_strip
    title_left = title.mapTo(rail, QPoint(0, 0)).x()
    summary_right = summary.mapTo(
        rail, QPoint(summary.width(), 0)).x()
    strip_left = strip.mapTo(rail, QPoint(0, 0)).x()

    # The pagebook has one 40px header and a one-pixel boundary.
    assert header.height() == 58
    assert title_left >= 14
    assert summary_right <= strip_left - 8

    filters = window._v3_ledger_filters
    buttons = (
        window.clip_list.all_filter_btn,
        window.clip_list.unlogged_filter_btn,
        window.clip_list.needs_fix_filter_btn,
        window.clip_list.detect_pending_filter_btn,
    )
    assert all(button.height() >= 32 for button in buttons)
    for button in buttons:
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 6, (
            button.text(), button.width(), button.fontMetrics().horizontalAdvance(button.text()))
    last_right = buttons[-1].mapTo(
        filters, QPoint(buttons[-1].width(), 0)).x()
    assert abs(last_right - filters.width()) <= 1


def test_open_details_header_reserves_the_collapse_gutter(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    review = window._v3_review
    review.set_rail_state(ReviewRailState(False, True))
    app.processEvents()

    rail = review.details_rail
    strip = rail._collapse_strip
    strip_right = strip.mapTo(rail, QPoint(strip.width(), 0)).x()
    header = window.clip_editor.findChild(QWidget, "InspectorHeader")
    reserved = header.layout().contentsMargins().left()
    assert strip_right <= reserved
    assert strip.height() <= header.height()


def test_rail_transitions_preserve_every_authoritative_object(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    def authorities():
        return (window.player, window.control_center,
                window.control_center.jog_window, window.clip_list,
                window.clip_editor, window.player.slider,
                window.player.attribute_grid, window.export_panel)

    identities = tuple(id(item) for item in authorities())
    for state in (
            ReviewRailState(), ReviewRailState(True, False),
            ReviewRailState(False, True), ReviewRailState(True, True),
            ReviewRailState()):
        window._v3_review.set_rail_state(state)
        app.processEvents()
        assert tuple(id(item) for item in authorities()) == identities
        assert all(shiboken6.isValid(item) for item in authorities())
        assert all(not _has_dock_ancestor(item) for item in authorities())


def test_collapsed_ledger_has_no_invented_marker_stack(tmp_path):
    window = _window(tmp_path)
    rail = window._v3_review.ledger_rail
    assert rail.selected_label.text() == "--"
    assert rail.count_label.text() == "0 PLAYS"
    assert rail.findChildren(type(rail.selected_label), "V3RailStatusMarker") \
        == []


def test_collapsed_rails_use_the_locked_vertical_open_labels(tmp_path):
    window = _window(tmp_path)
    ledger = window._v3_review.ledger_rail.collapsed_title
    details = window._v3_review.details_rail.collapsed_title
    assert ledger.label.text() == "CLIP LEDGER  ·  OPEN"
    assert details.label.text() == "PLAY DETAILS  ·  OPEN"
    assert ledger.proxy.rotation() == -90
    assert details.proxy.rotation() == -90


def test_clip_export_output_fields_use_the_locked_two_by_two_grid(tmp_path):
    window = _window(tmp_path)
    setup = window._v3_review.export_page.clip_setup
    layout = setup.format_combo.parentWidget().layout()

    def cell(widget):
        row, column, _row_span, _column_span = layout.getItemPosition(
            layout.indexOf(widget))
        return row, column

    assert cell(setup.format_combo) == (1, 1)
    assert cell(setup.style_combo) == (1, 3)
    assert cell(setup.preset_combo) == (2, 1)
    assert cell(setup.accurate_check) == (2, 3)


def test_v3_timeline_zoom_controls_stay_left_and_work_at_compact_width(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    player, deck = window.player, window.control_center
    cluster = window._v3_timeline_zoom_cluster
    assert all(control.parentWidget() is cluster for control in (
        player.timeline_zoom_out, player.timeline_zoom_in, player.timeline_zoom_label))
    window._v3_review.set_rail_state(ReviewRailState(True, True))
    window.resize(1248, 800)
    app.processEvents()
    assert cluster.isVisible()
    fit_x = player.timeline_fit_play.mapTo(deck, QPoint()).x()
    zoom_x = cluster.mapTo(deck, QPoint()).x()
    play_x = deck.play_btn.mapTo(deck, QPoint()).x()
    assert fit_x < zoom_x < play_x
    assert player.predicted_snap_button.isVisibleTo(deck)

    player.slider.viewport.set_source_range(0, 1_200_000)
    player._sync_timeline_viewport_controls()
    before = player.timeline_zoom_slider.value()
    QTest.mouseClick(player.timeline_zoom_in, Qt.MouseButton.LeftButton)
    app.processEvents()

    assert player.timeline_zoom_slider.value() == before + 1
    assert player.timeline_zoom_label.text() != "1×"


def test_v3_export_complete_shows_output_path_and_open_actions(tmp_path, monkeypatch):
    # Verify the real launch route without handing this fake MP4 to a host app.
    launched = []
    monkeypatch.setattr("tapesift.ui.export_panel.os.startfile", launched.append, raising=False)
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    output = tmp_path / "Individual Clips" / "002_Play-002.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"exported clip")
    job = ExportJob(
        job_type=JobType.CLIP,
        display_name="Play 002",
        output_path=str(output),
        status=JobStatus.COMPLETED,
    )
    page = window._v3_review.export_page
    page.mark_started()
    window._set_workspace_stage("export")
    window.export_panel.upsert_job(job)
    window._sync_v3_export_state()
    app.processEvents()

    queue = page.clip_setup.queue
    assert page.stage_strip.cells[2].property("active") is True
    assert queue.complete_title.text() == output.name
    assert queue.complete_detail.text() == "Export complete"
    assert queue.complete_path.text() == str(output.parent)
    assert page.clip_setup.status.text() == "Export complete"
    assert queue.open_button.isVisibleTo(page)
    assert queue.folder_button.isVisibleTo(page)

    opened = QSignalSpy(page.open_path_requested)
    QTest.mouseClick(queue.open_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(queue.folder_button, Qt.MouseButton.LeftButton)
    app.processEvents()

    assert opened.count() == 2
    assert opened.at(0)[0] == str(output)
    assert opened.at(1)[0] == str(output.parent)
    import os
    if os.name == "nt":
        assert launched == [str(output), str(output.parent)]


def test_v3_export_complete_hides_dead_output_actions(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    missing = tmp_path / "missing" / "deleted-output.mp4"
    job = ExportJob(
        job_type=JobType.CLIP,
        display_name="Deleted output",
        output_path=str(missing),
        status=JobStatus.COMPLETED,
    )
    page = window._v3_review.export_page
    page.mark_started()
    window._set_workspace_stage("export")
    window.export_panel.upsert_job(job)
    window._sync_v3_export_state()
    app.processEvents()

    queue = page.clip_setup.queue
    assert queue.complete_state.text() == "COMPLETED"
    assert queue.complete_path.text() == str(missing.parent)
    assert not queue.open_button.isVisibleTo(page)
    assert not queue.folder_button.isVisibleTo(page)


def test_collapsed_rail_commits_open_on_release_not_press(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    rail = window._v3_review.ledger_rail
    target = rail._collapsed_page
    point = QPoint(target.width() // 2, target.height() // 2)

    QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert not rail.is_open()
    QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert rail.is_open()


def test_collapsed_details_has_no_floating_action_tray(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    review = window._v3_review
    review.action_mirror.sync(review._action_sources)
    assert not review.action_mirror.isVisible()
    review.set_stage("review")
    review.set_rail_state(ReviewRailState(False, True))
    app.processEvents()
    assert not review.action_mirror.isVisible()


def test_export_back_returns_to_review_without_touching_active_queue(tmp_path):
    window = _window(tmp_path)
    app = QApplication.instance() or QApplication([])
    review = window._v3_review
    rail_state = ReviewRailState(True, False)
    review.set_rail_state(rail_state)
    panel = window.export_panel
    panel_identity = id(panel)
    jobs_identity = id(panel.jobs)
    cancellations = []
    panel.cancel_current_requested.connect(
        lambda: cancellations.append("current"))
    panel.cancel_all_requested.connect(lambda: cancellations.append("all"))
    panel.set_running(True)

    window._set_workspace_stage("export")
    app.processEvents()
    button = review.back_to_review_button
    assert window._workspace_stage == "export"
    assert review.center_stack.currentWidget() is review.workbench
    assert review.export_page.isVisible()
    assert button.isVisible() and button.isEnabled()
    assert button.text() == "Back to Review"
    assert button.accessibleName() == "Return to Review"

    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window._workspace_stage == "review"
    assert review.center_stack.currentWidget() is review.workbench
    assert not review.export_page.isVisible()
    assert review.rail_state() == rail_state
    assert id(window.export_panel) == panel_identity
    assert id(panel.jobs) == jobs_identity
    assert panel._running is True
    assert panel.setup_stack.currentIndex() == 1
    assert cancellations == []

    window._set_workspace_stage("export")
    app.processEvents()
    assert review.center_stack.currentWidget() is review.workbench
    assert review.export_page.isVisible()
    assert panel._running is True
    assert cancellations == []


def test_escape_handler_returns_from_export_to_review(tmp_path):
    window = _window(tmp_path)
    app = QApplication.instance() or QApplication([])
    review = window._v3_review
    window._set_workspace_stage("export")
    app.processEvents()

    window._shortcut_escape()
    app.processEvents()
    assert window._workspace_stage == "review"
    assert review.center_stack.currentWidget() is review.workbench


def test_package_route_is_distinct_ordered_and_keeps_player_mounted(
        tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    session = ProjectSession.create(
        "Package Route", tmp_path / "projects", tmp_path / "exports")
    clips = [
        session.add_clip(Clip(
            start_ms=index * 10_000,
            end_ms=index * 10_000 + 5_000,
            clip_title=f"Play {index + 1:03d}",
        ))
        for index in range(3)
    ]
    window.session = session
    window._refresh_clip_list()
    window.select_clip(clips[0].id, seek=False)
    player_identity = id(window.player)
    export_identity = id(window.export_panel)

    window._configure_package_export()
    app.processEvents()

    page = window._v3_review.export_page
    assert page.route == "package"
    assert page.package_setup.mode_combo.currentData() == "both"
    assert window._v3_package_scope_ids == tuple(clip.id for clip in clips)
    assert id(window.player) == player_identity
    assert id(window.export_panel) == export_identity
    assert window.player.video_widget.isVisible()
    assert window.player.slider.isVisible()
    assert window.control_center.isVisible()
    assert not window.player.timeline_header.isVisible()
    assert not window.player.attribute_grid.isVisible()
    assert "Back to Review" in \
        window._v3_review.footer.shortcut_label.text()

    page.package_setup._move_clip(clips[0].id, 1)
    ordered_clips = [clips[1], clips[0], clips[2]]
    assert window._v3_package_scope_ids == tuple(
        clip.id for clip in ordered_clips)
    assert "custom order" in page.subtitle.text()

    starts = []
    monkeypatch.setattr(
        window, "_start_export",
        lambda mode, preset, accurate, clips=None, quick=False:
        starts.append((mode, preset, accurate, clips, quick)),
    )
    window._export_restore_complete = True
    window._sync_v3_export_state()
    page.package_setup.start_button.click()
    app.processEvents()
    assert starts == [(
        "both", "social_1080p", True, ordered_clips, False)]

    window._return_from_v3_export()
    app.processEvents()
    assert window.player.timeline_header.height() == 0
    assert window.player.attribute_grid.isVisible()


def test_visible_export_start_never_raises_old_running_popup(
        tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    session = ProjectSession.create(
        "Busy Route", tmp_path / "projects", tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=1_000, end_ms=6_000, clip_title="Play 001"))
    window.session = session
    window._refresh_clip_list()
    window.select_clip(clip.id, seek=False)
    window._export_restore_complete = True
    window._configure_single_clip_export(clip.id)

    class BusyWorker:
        def isRunning(self):
            return True

    popup_calls = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *args, **kwargs: popup_calls.append((args, kwargs)),
    )
    window.export_worker = BusyWorker()
    window._sync_v3_export_state()
    button = window._v3_review.export_page.clip_setup.start_button
    assert not button.isEnabled()
    button.click()
    app.processEvents()
    assert popup_calls == []
    window.export_worker = None


def test_visible_export_clip_action_opens_setup_while_worker_is_busy(
        tmp_path, monkeypatch):
    from tapesift.ui_v3.play_export_dialog import PlayExportDialog
    # The visible action first offers artwork or video-only export.
    monkeypatch.setattr(PlayExportDialog, "exec", lambda dialog: dialog.video_only_requested.emit())
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    session = ProjectSession.create(
        "Busy Visible Route", tmp_path / "projects", tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=1_000, end_ms=6_000, clip_title="Play 001"))
    window.session = session
    window._refresh_clip_list()
    window.select_clip(clip.id, seek=False)

    class BusyWorker:
        jobs = []

        def isRunning(self):
            return True

    popup_calls = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *args, **kwargs: popup_calls.append((args, kwargs)),
    )
    window.export_worker = BusyWorker()
    window.clip_editor.quick_export_btn.click()
    app.processEvents()

    page = window._v3_review.export_page
    assert page.route == "clip"
    assert page.isVisible()
    assert not page.clip_setup.start_button.isEnabled()
    assert popup_calls == []
    window.export_worker = None


def test_v3_export_progress_is_percent_and_cancel_always_stops_batch(
        tmp_path):
    window = _window(tmp_path)
    job = ExportJob(
        job_type=JobType.CLIP,
        display_name="Play 001",
        output_path=str(tmp_path / "Play-001.mp4"),
        status=JobStatus.EXPORTING,
    )
    window.export_panel.upsert_job(job)

    class BusyWorker:
        jobs = []
        _cancel_all = False

        def isRunning(self):
            return True

        def cancel_current(self):
            return None

    worker = BusyWorker()
    window.export_worker = worker
    window.export_panel.on_job_progress(job.id, 2, 1)
    window._v3_export_progress(job.id, 2, 1)
    progress = window._v3_review.export_page.clip_setup.queue.progress
    assert (progress.minimum(), progress.maximum(), progress.value()) \
        == (0, 100, 2)
    progress.setRange(0, 0)
    window.export_panel.on_job_progress(job.id, 50, 3)
    window._v3_export_progress(job.id, 50, 3)
    assert (progress.minimum(), progress.maximum(), progress.value()) \
        == (0, 100, 50)

    window._v3_review.export_page.cancel_requested.emit()
    assert worker._cancel_all is True
    window.export_worker = None


def test_v3_export_restore_completion_resyncs_visible_start(tmp_path):
    window = _window(tmp_path)
    session = ProjectSession.create(
        "Restore Route", tmp_path / "projects", tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=1_000, end_ms=6_000, clip_title="Play 001"))
    window.session = session
    window._export_restore_complete = False
    window.export_panel.set_bridge_readiness_error(
        "Restoring the durable export queue before starting new work.")
    window._configure_single_clip_export(clip.id)
    button = window._v3_review.export_page.clip_setup.start_button
    assert not button.isEnabled()

    window._export_queue_summaries_ready((
        window._export_bridge_generation, []))
    assert button.isEnabled()

    window._export_queue_restore_failed(
        window._export_bridge_generation, "database locked")
    assert not button.isEnabled()
    assert "database locked" in \
        window._v3_review.export_page.clip_setup.status.text()


def test_tag_map_follows_timeline_fit_zoom_pan_and_full_game(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    clips = [
        Clip(
            start_ms=10_000 + index * 40_000,
            end_ms=30_000 + index * 40_000,
            clip_number=index + 1,
            clip_title=f"Play {index + 1:03d}",
        )
        for index in range(12)
    ]
    window.player._duration_changed(600_000)
    window.player.set_attribute_clips(clips)
    window.player.set_clip_blocks([
        TimelineBlock(
            clip.start_ms, clip.end_ms,
            clip_id=clip.id, title=clip.clip_title)
        for clip in clips
    ])
    grid = window.player.attribute_grid
    selected = clips[4]
    full_width = grid.clip_span(selected)[1] - grid.clip_span(selected)[0]
    window.player.set_selected_clip_id(selected.id)
    window.player.fit_timeline_play()
    app.processEvents()

    assert grid._visible_range == window.player.slider.visible_range()
    assert grid._visible_range != (0, 600_000)
    assert grid.clip_span(selected)[1] - grid.clip_span(selected)[0] > full_width
    assert selected in grid.visible_clips()
    window.player.slider.fit_range(300_000, 380_000)
    assert grid._visible_range == (300_000, 380_000)
    assert selected not in grid.visible_clips()
    window.player.slider.reset_zoom()
    assert grid._visible_range == (0, 600_000)
    assert len(grid.visible_clips()) == len(clips)
    assert grid.clip_span(selected)[1] - grid.clip_span(selected)[0] == full_width


def test_review_transport_releases_space_and_restores_controls_across_rail_widths(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    review, deck = window._v3_review, window.control_center
    controls = (deck.transport_island, deck.in_button, deck.out_button,
                deck.current_label, deck.position_detail,
                window.player.predicted_snap_button, deck.jog_toggle,
                deck.overflow_button, window._v3_timeline_zoom_cluster)
    owners = tuple(id(widget) for widget in controls)
    review.set_rail_state(ReviewRailState(True, True))
    for width, height, center_width in ((1672, 941, 1020), (1248, 608, 648),
                                         (1672, 941, 1020)):
        window.resize(width, height)
        app.processEvents()
        deck._apply_premium_geometry()
        deck.set_playing(True)
        deck.set_playing(False)
        app.processEvents()
        assert review.center_stack.width() == center_width
        assert tuple(id(widget) for widget in controls) == owners
        island = deck.transport_island
        assert island.size() == (QSize(136, 24) if deck.width() < 900 else QSize(174, 30))
        assert deck.height() == 36
        assert window._v3_timeline_label_band.height() == 0
        assert deck.in_button.size() == (QSize(32, 24) if deck.width() < 900 else QSize(40, 28))
        assert deck.out_button.size() == (QSize(34, 24) if deck.width() < 900 else QSize(44, 28))
        assert deck.marks_group.isHidden()
        buttons = window._v3_transport_surface.buttons
        for index, button in enumerate(buttons):
            assert button.width() == button.height() == (24 if deck.width() < 900 else 30)
            assert island.rect().contains(button.geometry())
            assert button.graphicsEffect().isEnabled()
            assert button.graphicsEffect().opacity() == 0
            assert all(not button.geometry().intersects(other.geometry())
                       for other in buttons[index + 1:])
        for widget in controls:
            if widget.isVisible():
                ancestor = widget.parentWidget()
                while ancestor is not None:
                    region = widget.rect().translated(widget.mapTo(ancestor, QPoint()))
                    assert ancestor.rect().contains(region), widget.objectName()
                    if ancestor is window:
                        break
                    ancestor = ancestor.parentWidget()
        assert not deck.overflow_button.isHidden()
        assert not window.player.predicted_snap_button.isHidden()
        assert deck.position_zone.isHidden()
        assert deck.position_detail.isHidden()
        assert deck.jog_toggle.isHidden()
        assert any(action.text() == "Show jog wheel"
                   for action in deck.overflow_button.menu().actions())
        assert window.rect().contains(review.footer.geometry().translated(
            review.mapTo(window, QPoint())))
    review.set_rail_state(ReviewRailState())
    app.processEvents()
    assert window._v3_timeline_zoom_cluster.isVisible()
    assert deck.current_label.isHidden() and deck.position_detail.isHidden()


@pytest.mark.parametrize("app_exit", [False, True])
def test_close_project_refreshes_home_only_when_staying_in_app(tmp_path, app_exit):
    window = _window(tmp_path)
    session = ProjectSession.create("Close performance", tmp_path, tmp_path / "out")
    clip = Clip(start_ms=0, end_ms=5000, notes="Keep the analyst's work")
    session.add_clip(clip)
    session.save()
    path = session.db_path
    assert window._activate_session(session)
    QApplication.instance().processEvents()
    refreshes = []
    refresh = window.start_screen.refresh_recent
    window.start_screen.refresh_recent = lambda: (refreshes.append(True), refresh())[1]
    if app_exit:
        assert window.close()
        assert not window.isVisible()
        assert not refreshes
    else:
        assert window._close_project()
        assert window.stack.currentWidget() is window.start_screen
        assert refreshes
    assert window.session is None
    reopened = ProjectSession.open(path)
    assert reopened.get_clip(clip.id).notes == "Keep the analyst's work"
    reopened.close()


def test_refused_app_exit_keeps_review_and_resets_closing_state(tmp_path, monkeypatch):
    window = _window(tmp_path)
    session = ProjectSession.create("Refused close", tmp_path, tmp_path / "out")
    assert window._activate_session(session)
    session.dirty = True

    def fail_save():
        raise OSError("project is locked")

    monkeypatch.setattr(session, "save", fail_save)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    assert not window.close()
    assert window.isVisible()
    assert window.session is session
    assert not window._app_closing
    assert window.stack.currentWidget() is window.workspace
    monkeypatch.undo()


def test_thin_tag_header_preserves_live_tools_and_compact_menu_routes(tmp_path):
    from PySide6.QtWidgets import QStyle, QStyleOptionButton

    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    player, deck = window.player, window.control_center
    grid, header = player.attribute_grid, player.timeline_header
    collapse = player.tag_map_collapse_button
    controls = (grid.color_by, grid.heatmap_button, collapse)
    window._v3_review.set_rail_state(ReviewRailState(True, True))
    for width, height in ((1672, 941), (1248, 608), (1672, 941)):
        window.resize(width, height)
        app.processEvents()
        player.set_tag_map_collapsed(False, persist=False)
        app.processEvents()
        assert header.height() == 0
        assert window._v3_play_field_ribbon is None
        assert grid._field_surface is not None
        assert player.layout().indexOf(window._v3_tag_map_tools) == -1
        assert grid._review_body_top() == 32
        assert window.quick_tag_tray.manage_button.isHidden()
        assert player.quick_tag_slot.isHidden()
        assert grid.toolbar.isHidden()
        assert (grid.color_by, grid.heatmap_button, collapse) == controls
        assert grid.color_by.isHidden() and grid.heatmap_button.isHidden()
        assert window._v3_tag_map_tools.isHidden()
        map_action = next(action for action in window._v3_tools_menu.actions()
                          if action.text() == "Tag Map options")
        menu = map_action.menu()
        window._populate_map_menu(menu)
        assert [action.text() for action in menu.actions()][1:] == ["Clip bar color key", "Tag Map rows", "Heat Map", "Manage tags"]
        colors = menu.actions()[0].menu()
        colors.actions()[-1].trigger()
        assert grid.color_by.currentIndex() == grid.color_by.count()-1
        colors.actions()[0].trigger()
        for collapsed in (False, True, False):
            player.set_tag_map_collapsed(collapsed, persist=False)
            app.processEvents()
            assert collapse.arrowType() == Qt.ArrowType.NoArrow
            assert not collapse.icon().isNull()
            assert header.height() == 0
            assert not collapse.isVisible()
            window._v3_tools_menu.aboutToShow.emit()
            assert window._v3_tools_menu.actions()[-1].text() == ("Expand Tag Map" if collapsed else "Collapse Tag Map")
        assert player.quick_tag_slot.isHidden()

    # The menu forwards to the existing buttons, including their disabled state.
    # Observe those authorities without opening modal dialogs in this layout test.
    for title, button in (("Heat Map", grid.heatmap_button),
                          ("Manage tags", window.quick_tag_tray.manage_button)):
        button.clicked.disconnect()
        spy = QSignalSpy(button.clicked)
        button.setEnabled(True)
        window._populate_map_menu(menu)
        next(action for action in menu.actions() if action.text() == title).trigger()
        assert spy.count() == 1
        button.setEnabled(False)
        window._populate_map_menu(menu)
        assert not next(action for action in menu.actions() if action.text() == title).isEnabled()

    window.resize(1248, 608)
    app.processEvents()
    assert not deck.jog_toggle.isVisible()
    assert window._v3_timeline_zoom_cluster.isVisible()
    player.slider.viewport.set_source_range(0, 1_200_000)
    player._sync_timeline_viewport_controls()
    window._sync_timeline_menu_actions()
    before = player.timeline_zoom_slider.value()
    window.timeline_zoom_in_action.trigger()
    assert player.timeline_zoom_slider.value() == before + 1
    jog_action = next(action for action in deck.overflow_button.menu().actions()
                      if action.text() == "Show jog wheel")
    jog_action.trigger()
    app.processEvents()
    assert deck.jog_window.isVisible()
    jog_action.trigger()
    app.processEvents()
    assert not deck.jog_window.isVisible()


def test_play_stays_between_zoom_and_tools_with_either_panel_open(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    for width, height in ((1708, 921), (1248, 768)):
        window.resize(width, height)
        for ledger, details in ((False, False), (True, False), (False, True), (True, True)):
            window._v3_review.set_rail_state(ReviewRailState(ledger, details))
            app.processEvents()
            deck = window.control_center
            video = window.player.video_widget
            play_x = deck.play_btn.mapTo(window, deck.play_btn.rect().center()).x()
            zoom = window._v3_timeline_zoom_cluster
            zoom_right = zoom.mapTo(window, zoom.rect().topRight()).x()
            snap_left = window.player.predicted_snap_button.mapTo(window, QPoint()).x()
            assert zoom_right < play_x < snap_left
            if not window._v3_play_type_key.isVisible():
                assert "Clip colors: Run green" in deck.overflow_button.toolTip()
            assert deck.height() == 36
            for button in (deck.in_button, deck.out_button, window.player.predicted_snap_button,
                           deck.jog_toggle, deck.overflow_button, window.player.timeline_fit_play):
                if button.isVisible():
                    assert deck.rect().contains(button.rect().translated(button.mapTo(deck, QPoint())))


def test_keyboard_clip_loop_preserves_edits_and_native_field_keys(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    window = _window(tmp_path)
    session = ProjectSession.create("Keyboard review", tmp_path / "projects", tmp_path / "exports")
    clips = [session.add_clip(Clip(i*30_000, i*30_000+20_000,
                                  details={"run_pass": "Pass", "quarter": "Q1"})) for i in range(3)]
    window.session = session
    window.settings.review_mode = False
    window._refresh_clip_list()
    window.player._duration_changed(100_000)
    window.select_clip(clips[0].id)
    window.activateWindow()
    QTest.qWait(80)
    editor = window.clip_editor

    def press(key, modifiers=Qt.KeyboardModifier.NoModifier):
        QTest.keyClick(QApplication.focusWidget() or window, key, modifiers)
        QApplication.processEvents()

    def selected(index):
        assert window._selected_clip_id == clips[index].id
        assert window.player.attribute_grid._selected_clip_id == clips[index].id
        assert editor._clip.id == clips[index].id

    editor.notes_edit.setFocus()
    QTest.keyClicks(editor.notes_edit, "wb coverage")
    selected(0)
    assert editor.notes_edit.toPlainText() == "wb coverage"
    press(Qt.Key.Key_Escape)
    assert window.player.video_widget.hasFocus()
    press(Qt.Key.Key_W)
    selected(1)
    assert session.get_clip(clips[0].id).notes == "wb coverage"
    press(Qt.Key.Key_B)
    selected(0)
    press(Qt.Key.Key_B)
    selected(0)
    window._set_details_open(False)
    press(Qt.Key.Key_E)
    assert window._v3_review.details_rail.is_open()
    assert editor.quick_play_buttons["run"].hasFocus()
    press(Qt.Key.Key_Space)
    assert editor.detail_edits["run_pass"].currentText() == "Run"
    press(Qt.Key.Key_Tab)
    assert editor.quick_play_buttons["pass"].hasFocus()
    press(Qt.Key.Key_Space)
    assert editor.detail_edits["run_pass"].currentText() == "Pass"
    editor.quick_play.setFocus()
    press(Qt.Key.Key_W)
    selected(0)
    press(Qt.Key.Key_Escape)
    assert window.player.video_widget.hasFocus()

    editor.notes_edit.setFocus()
    editor.notes_edit.setPlainText("Saved with keyboard")
    press(Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    selected(1)
    assert session.get_clip(clips[0].id).notes == "Saved with keyboard"
    editor.notes_edit.setPlainText("Invalid draft stays here")
    window._return_focus_to_playback()
    with monkeypatch.context() as patch:
        patch.setattr(editor, "_apply", lambda: False)
        press(Qt.Key.Key_W)
        selected(1)
        assert editor.notes_edit.toPlainText() == "Invalid draft stays here"
    dialog = QDialog(window)
    dialog.setModal(True)
    dialog.show()
    dialog.activateWindow()
    QTest.qWait(50)
    QTest.keyClick(dialog, Qt.Key.Key_B)
    selected(1)
    dialog.reject()
    window.activateWindow()
    window._return_focus_to_playback()
    QTest.qWait(50)
    press(Qt.Key.Key_W)
    selected(2)
    press(Qt.Key.Key_W)
    selected(2)
    window.stack.setCurrentWidget(window.start_screen)
    QApplication.processEvents()
    assert all(not window._transport_shortcuts[key].isEnabled() for key in ("B", "W", "E", "Ctrl+Shift+Return"))
