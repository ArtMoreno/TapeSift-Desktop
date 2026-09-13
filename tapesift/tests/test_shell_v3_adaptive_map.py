"""Saved map visibility releases video space without changing clips or controls."""
from copy import deepcopy
from itertools import product

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QWidget, QMenu, QStyle, QStyleOptionButton

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.tests.test_shell_v3_review import _window
from tapesift.ui_v3.dialog_surface import SettingsDialogV3
from tapesift.ui_v3.workspace_state import ReviewRailState


def test_every_row_selection_releases_height_and_preserves_toolbar_and_data(tmp_path):
    app = QApplication.instance() or QApplication([])
    previous_sheet, previous_font = app.styleSheet(), app.font()
    window = _window(tmp_path)
    session = ProjectSession.create("Row visibility QA", tmp_path / "projects", tmp_path / "exports")
    session.add_clip(Clip(0, 9000, details={"quarter": "Q1", "run_pass": "Pass",
        "down_distance": "2nd & 8", "result": "Gain; Completion; First Down",
        "player_name": "Cooper Barkate", "action": "Block"}, notes="Review block"))
    window.session = session
    window._refresh_clip_list()
    window._v3_review.set_rail_state(ReviewRailState(True, True))
    player, grid, deck = window.player, window.player.attribute_grid, window.control_center
    player.slider.setRange(0, 10_000)
    player.slider.fit_range(0, 10_000)
    grid.set_clips(session.clips)
    keys = [row.key for row in grid._all_review_rows]
    before = deepcopy([(c.id, c.start_ms, c.end_ms, c.details, c.tags, c.notes) for c in session.clips])
    controls = (player.timeline_fit_play, deck.in_button, deck.out_button,
        window._v3_timeline_zoom_cluster, *window._v3_transport_surface.buttons,
        player.predicted_snap_button, deck.overflow_button, window._v3_play_type_key)
    for width, height in ((1908, 960), (1250, 800), (1250, 768)):
        window.resize(width, height)
        app.processEvents()
        rail = window._v3_review
        assert rail.ledger_rail.width() == rail.details_rail.width()
        headers = [window.findChild(QWidget, name) for name in ("ClipLedgerHeader", "InspectorHeader")]
        assert headers[0].height() == headers[1].height()
        assert headers[0].mapTo(window, QPoint()).y() == headers[1].mapTo(window, QPoint()).y()
        for button in (*window.clip_editor.quarter_buttons.values(), *window.clip_editor.down_buttons.values()):
            option = QStyleOptionButton()
            button.initStyleOption(option)
            available = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
            assert available.width() >= button.fontMetrics().horizontalAdvance(button.text())
        geometry = None
        total_height = None
        for mask in product((False, True), repeat=7):
            shown = [key for key, checked in zip(keys, mask) if checked]
            window.settings.hidden_tag_map_rows = [key for key in keys if key not in shown]
            window._apply_v3_tag_map_rows()
            window._sync_v3_stage_chrome("review")
            deck.set_playing(bool(len(shown) % 2))
            app.processEvents()
            assert [row.key for row in grid.rows()] == shown
            expected = min(grid._review_height_limit, 66 + 38 * len(shown)) if shown else 0
            assert grid.height() == expected
            assert grid.isVisible() == bool(shown)
            for index, row in enumerate(grid.rows()):
                assert grid._row_top(index) == 34 + 38 * index
                assert grid._row_height(row) == 36
            measured = player.video_widget.height() + grid.height()
            if total_height is None:
                total_height = measured
            assert measured == total_height, (width, height, shown, measured, total_height)
            current = [(c.mapTo(deck, QPoint()).x(), c.width(), c.height()) for c in controls]
            if geometry is None:
                geometry = current
            assert current == geometry
            assert all(c.isVisible() for c in controls[:-1])
            if window._v3_play_type_key.isVisible():
                assert current[-1][0] + current[-1][1] <= deck.width()
            if shown:
                grid._move_cursor(1, 0)
                assert grid.cursor_cell()[0].key in shown
        assert before == [(c.id, c.start_ms, c.end_ms, c.details, c.tags, c.notes) for c in session.clips]
    # Tools remains usable after the all-hidden choice and persists the new mask.
    grid.set_hidden_review_rows([{}, [], "notes", "unknown"])
    assert "notes" not in [row.key for row in grid.rows()]
    for hidden in ([], keys):
        window._workspace_stage = "export"
        window.settings.hidden_tag_map_rows = hidden
        window._apply_v3_tag_map_rows()
        window._sync_v3_stage_chrome("export")
        assert grid.isHidden()
        window._workspace_stage = "review"
        window._sync_v3_stage_chrome("review")
        assert grid.isVisible() == (not hidden)
    window.settings.hidden_tag_map_rows = []
    window._apply_v3_tag_map_rows()
    for key in keys:
        window._set_tag_map_row_visible(key, False)
    assert AppSettings.load().hidden_tag_map_rows == keys
    menu = QMenu(window)
    window._populate_map_menu(menu)
    rows_menu = next(a.menu() for a in menu.actions() if a.text() == "Tag Map rows")
    assert all(not a.isChecked() for a in rows_menu.actions())
    rows_menu.actions()[0].trigger()
    app.processEvents()
    assert [row.key for row in grid.rows()] == [keys[0]]
    assert keys[0] not in AppSettings.load().hidden_tag_map_rows
    # Settings shares the same keys, while Cancel leaves the live preference alone.
    saved = list(window.settings.hidden_tag_map_rows)
    dialog = SettingsDialogV3(window.settings, window)
    assert dialog.tag_map_row_checks[keys[0]].isChecked()
    dialog.tag_map_row_checks[keys[0]].setChecked(False)
    dialog.reject()
    assert window.settings.hidden_tag_map_rows == saved
    dialog = SettingsDialogV3(window.settings, window)
    for check in dialog.tag_map_row_checks.values():
        check.setChecked(True)
    dialog.accept()
    window._apply_v3_tag_map_rows()
    assert window.settings.hidden_tag_map_rows == []
    assert AppSettings.load().hidden_tag_map_rows == []
    assert len(grid.rows()) == 7
    field = grid._field_surface
    assert len({field.quarter_color(q) for q in ("Q1", "Q2", "Q3", "Q4", "OT")}) == 5
    assert field.result_color("TD") == field.result_color("Touchdown")
    assert field.result_color("INT") == field.result_color("Interception")
    result_row = next(row for row in grid.rows() if row.key == "result")
    grid.color_by.setCurrentIndex(grid.color_by.findData("result"))
    assert grid.color_entries(session.clips[0]) == grid._lane_color_entries(result_row, session.clips[0])
    assert before == [(c.id, c.start_ms, c.end_ms, c.details, c.tags, c.notes) for c in session.clips]
    session.save()
    window.close()
    app.setStyleSheet(previous_sheet)
    app.setFont(previous_font)
