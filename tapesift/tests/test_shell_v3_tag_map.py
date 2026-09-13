"""V3 Tag Map geometry and saved-data preservation contracts."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from tapesift.models.clip import Clip
from tapesift.ui_core.clip_editor import ClipEditor
from tapesift.ui_v2.attribute_grid import AttributeGrid


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _logical_frame(grid):
    # QWidget coordinates are logical pixels; headed Windows grabs may be 150% DPI.
    image = grid.grab().toImage().scaled(grid.size())
    image.setDevicePixelRatio(1)
    return image


def test_duration_chips_stretch_with_zoom_and_keep_adjacent_hits_separate(app):
    grid = AttributeGrid()
    grid.enable_review_style()
    grid.resize(1000, 346)
    first = Clip(start_ms=10_000, end_ms=20_000, clip_number=1)
    second = Clip(start_ms=20_000, end_ms=40_000, clip_number=2)
    grid.set_clips([first, second])
    grid.set_visible_range(0, 100_000)
    y = grid._row_top(0) + 15
    assert grid._x_for(20_000) > grid._x_for(10_000) + 50
    assert grid.clip_span(first)[1] == grid.clip_span(second)[0]
    wide = grid.clip_span(first)[1] - grid.clip_span(first)[0]
    for clip in (first, second):
        left, right = grid.clip_span(clip)
        assert right - left > 50
        assert grid.clip_at(left + (right - left) * .9, y).id == clip.id
        assert grid.clip_at((left + right) / 2, y).id == clip.id
    assert grid.clip_at(grid._x_for(70_000), y) is None
    assert grid.clip_at(grid._x_for(20_000), 50) is None  # Ruler is not a cell.
    grid.set_visible_range(0, 50_000)
    zoomed = grid.clip_span(first)[1] - grid.clip_span(first)[0]
    assert abs(zoomed - wide * 2) <= 1
    grid.set_visible_range(15_000, 30_000)
    assert grid.clip_span(first)[0] == grid.plot_left()
    assert grid.clip_span(second)[1] == grid.plot_left() + grid.plot_width() - 1
    assert grid.clip_at(grid.clip_span(second)[0], y).id == second.id
    assert (first.start_ms, first.end_ms, second.start_ms, second.end_ms) == (
        10_000, 20_000, 20_000, 40_000)


def test_color_modes_and_tooltip_retain_independent_and_unknown_saved_values(app):
    grid = AttributeGrid()
    grid.enable_review_style()
    clip = Clip(start_ms=1000, end_ms=2000, clip_number=14, details={"run_pass": "Pass", "play_type": "RPO",
        "play_action": "Play Action", "result": "Completion; First Down; Analyst result",
        "player_name": "Mark Fletcher Jr.", "secondary_player": "Carson Beck"})
    blank = Clip(start_ms=3000, end_ms=4000, clip_title="Touchdown", details={})
    grid.set_clips([clip, blank])
    grid.color_by.setCurrentIndex(grid.color_by.findData("result"))
    assert [value for value, _ in grid.color_entries(clip)] == [
        "Completion", "First Down", "Analyst result"]
    assert grid.color_entries(blank) == []
    text = grid.tooltip_for_clip(clip)
    assert all(value in text for value in ("Pass", "RPO", "Play Action", "Completion",
        "First Down", "Analyst result", "Carson Beck"))
    grid.color_by.setCurrentIndex(grid.color_by.findData("people"))
    assert len(grid.color_entries(clip)) == 1  # Primary-player workload stays primary.
    assert grid.color_entries(blank) == []


def test_saved_quarterback_appears_in_people_without_changing_primary_workload(app):
    from tapesift.ui_v3.clip_details import ClipDetailsV3

    clip = Clip(1000, 3000, details={"player_name": "Mark Fletcher",
                "other_players": "Cam Ward, Isaiah Horton"})
    grid = AttributeGrid()
    grid.enable_review_style()
    grid.set_clips([clip])
    grid.color_by.setCurrentIndex(grid.color_by.findData("people"))
    workload_before = grid.color_entries(clip)
    row = next(row for row in grid.rows() if row.key == "people")
    editor = ClipDetailsV3()
    editor.set_analyst_mode(True)
    editor.set_clip(clip)
    editor.detail_edits["quarterback"].setText("#10 Cam Ward")
    assert "QB" not in grid._review_value(row, clip)
    assert editor._apply()
    readout = grid._review_value(row, clip)
    assert "QB #10 Cam Ward" in readout and readout.count("Cam Ward") == 1
    assert "Mark Fletcher" in readout and "Isaiah Horton" in readout
    assert "QB #10 Cam Ward" in grid.tooltip_for_clip(clip)
    grid.set_clips([clip])
    assert grid.color_entries(clip) == workload_before
    assert clip.details["player_name"] == "Mark Fletcher"
    editor.close()
    grid.close()


def test_field_quarter_groups_join_same_quarter_but_stop_at_unlogged_clips(app):
    grid = AttributeGrid()
    clips = [Clip(0, 1000, details={"quarter": "Q2"}),
             Clip(2000, 3000, details={"quarter": "Q2"}),
             Clip(4000, 5000), Clip(6000, 7000, details={"quarter": "Q2"}),
             Clip(8000, 9000, details={"quarter": "Q3"})]
    grid.set_clips(clips)
    assert grid.quarter_spans(join_clip_gaps=True) == [(0, 3000, "Q2"),
            (6000, 7000, "Q2"), (8000, 9000, "Q3")]
    assert len(grid.quarter_spans()) == 4
    grid.close()


def test_saved_quarters_do_not_fill_unknown_or_assume_equal_film_sections(app):
    grid = AttributeGrid()
    grid.enable_review_style()
    grid.set_clips([
        Clip(start_ms=1000, end_ms=2000, details={"quarter": "Q1"}),
        Clip(start_ms=3000, end_ms=4000, details={"quarter": "Q1"}),
        Clip(start_ms=5000, end_ms=6000),
        Clip(start_ms=7000, end_ms=8000, details={"quarter": "Q1"}),
        Clip(start_ms=9000, end_ms=10000, details={"quarter": "OT"}),
    ])
    assert grid.quarter_spans() == [(1000, 2000, "Q1"), (3000, 4000, "Q1"), (7000, 8000, "Q1"), (9000, 10000, "OT")]

    grid.set_clips([
        Clip(start_ms=0, end_ms=10000, details={"quarter": "Q1"}),
        Clip(start_ms=2000, end_ms=4000, details={"quarter": "Q1"}),
        Clip(start_ms=10000, end_ms=12000, details={"quarter": "Q1"}),
    ])
    assert grid.quarter_spans() == [(0, 12000, "Q1")]


def test_short_map_scrolls_to_its_last_lane_without_changing_selection(app):
    grid = AttributeGrid()
    grid.enable_review_style()
    clip = Clip(start_ms=10_000, end_ms=20_000)
    grid.set_clips([clip])
    grid.set_visible_range(0, 60_000)
    grid.set_selected_clip_id(clip.id)
    grid.setFixedHeight(174)
    grid.resize(568, 174)
    grid.show()
    app.processEvents()
    assert grid.lane_scroll.maximum() > 0
    grid.lane_scroll.setFocus()
    QTest.keyClick(grid.lane_scroll, Qt.Key.Key_End)
    app.processEvents()
    ys = [y for y in range(grid.height()) if grid.row_at(y) and grid.row_at(y).key == "notes"]
    assert ys
    QTest.mouseClick(grid, Qt.MouseButton.LeftButton, pos=QPoint(12, ys[len(ys)//2]))
    assert grid.is_collapsed("notes")
    grid.set_row_collapsed("notes", False)
    assert grid._selected_clip_id == clip.id
    assert grid._visible_range == (0, 60_000)
    grid.set_cursor_cell(0, 0)
    assert grid.lane_scroll.value() == 0
    last = len(grid.rows()) - 1
    grid.set_cursor_cell(last, 0)
    assert grid._row_top(last) + grid._row_height(grid.rows()[last]) <= grid.height() - 32
    grid.close()


def test_review_map_keeps_actions_but_omits_status_and_confidence_without_deleting_data(app):
    from tapesift.ui_v3.clip_details import ClipDetailsV3

    grid = AttributeGrid()
    assert {"review", "confidence"} <= {row.key for row in grid.rows()}
    grid.enable_review_style()
    assert not {"review", "confidence"} & {row.key for row in grid.rows()}
    clip = Clip(0, 1000, detection_lineage={"confidence": 0.8})
    editor = ClipDetailsV3()
    editor.set_analyst_mode(True)
    editor.set_clip(clip)
    editor.detail_edits["action"].setText("Block")
    assert editor._apply()
    grid.set_clips([clip])
    row = next(row for row in grid.rows() if row.key == "action")
    assert grid._review_value(row, clip) == "Block"
    assert clip.enabled and clip.detection_lineage == {"confidence": 0.8}
    editor.close()
    grid.close()


def test_editor_collection_preserves_opaque_keys_but_honors_live_removal_and_blanks(app):
    editor = ClipEditor()
    clip = Clip(start_ms=1000, end_ms=2000, details={"run_pass": "Pass", "result": "Completion; First Down",
                         "analyst_custom": "  exact ; value  "})
    editor.set_clip(clip)
    editor.detail_edits["result"].setText("")
    collected = editor._collect_details()
    assert collected["analyst_custom"] == "  exact ; value  "
    assert "result" not in collected
    assert clip.details["result"] == "Completion; First Down"  # Collection is read-only.
    del clip.details["analyst_custom"]
    assert "analyst_custom" not in editor._collect_details()
    editor.close()


def test_result_legend_rolls_up_categories_without_collapsing_recorded_stripes(app):
    from tapesift.services.heatmap_palette import RESULT_COLORS as OUTCOME_COLORS

    grid = AttributeGrid()
    grid.enable_review_style()
    clips = [Clip(0, 1000, details={"result": value}) for value in (
        "Completion; First Down", "Touchdown", "Safety", "TD", "Custom Sack")]
    before = [dict(clip.details) for clip in clips]
    grid.set_clips(clips)
    grid.color_by.setCurrentIndex(grid.color_by.findData("result"))
    assert grid.color_entries(clips[0]) == [
        ("Completion", OUTCOME_COLORS["complete"]), ("First Down", OUTCOME_COLORS["first_down"])]
    assert grid.color_entries(clips[1]) == [("Touchdown", OUTCOME_COLORS["score"])]
    assert grid.color_entries(clips[2]) == [("Safety", OUTCOME_COLORS["score"])]
    assert grid.color_entries(clips[3]) == [("TD", OUTCOME_COLORS["other"])]
    assert grid.color_entries(clips[4]) == [("Custom Sack", OUTCOME_COLORS["other"])]
    assert grid.legend.toolTip() == "Complete · First Down · Score · Other · Unknown / empty"
    assert [clip.details for clip in clips] == before
    grid.close()


def test_external_tools_release_height_without_moving_lane_hits_away_from_paint(app):
    grid = AttributeGrid()
    grid.enable_review_style()
    clip = Clip(start_ms=10_000, end_ms=20_000,
                details={"quarter": "Q1", "result": "Completion"})
    grid.set_clips([clip])
    grid.set_visible_range(0, 60_000)
    grid.set_selected_clip_id(clip.id)
    grid.resize(1000, grid.height())
    grid.show()
    app.processEvents()
    before_height, before_top = grid.height(), grid._row_top(0)
    before_body = _logical_frame(grid).copy(0, 80, grid.width(), grid.height() - 112)
    scroll_maximum = grid.lane_scroll.maximum()
    header = QWidget()
    controls = (grid.color_by, grid.heatmap_button)
    for control in controls:
        control.setParent(header)
    grid.set_review_toolbar_external()
    app.processEvents()
    try:
        assert grid.height() == before_height - 34
        assert grid.toolbar.isHidden()
        assert grid._row_top(0) == before_top - 34
        assert grid.lane_scroll.maximum() == scroll_maximum
        assert _logical_frame(grid).copy(0, 46, grid.width(), grid.height() - 78) == before_body
        x = sum(grid.clip_span(clip)) // 2
        assert grid.clip_at(x, grid._row_top(0) + 15) is clip
        assert grid.clip_at(x, 45) is None
        last = len(grid.rows()) - 1
        grid.set_cursor_cell(last, 0)
        assert grid._row_top(last) + grid._row_height(grid.rows()[last]) <= grid.height() - 32
        grid.set_cursor_cell(0, 0)
        assert grid.lane_scroll.value() == 0
        grid.color_by.setCurrentIndex(grid.color_by.findData("result"))
        assert "Complete" in grid.legend.toolTip()
        assert (grid.color_by, grid.heatmap_button) == controls
    finally:
        grid.close()
        header.close()


@pytest.mark.parametrize("details, expected", [
    ({"play_type": "Run"}, None),
    ({"play_type": "Pass"}, None),
    ({"play_type": "Custom Screen"}, None),
    ({"run_pass": "Custom Run", "play_type": "Custom RPO"}, None),
    ({"play_type": "RPO"}, ("rpo", "RPO")),
    ({"play_type": "Screen"}, ("screen", "SCREEN")),
    ({"play_type": "Custom Screen", "run_pass": "Run"}, ("run", "RUN")),
])
def test_play_projection_only_classifies_known_axis_values(details, expected):
    from tapesift.ui_v2.tag_readout import _play_type

    assert _play_type(details) == expected


@pytest.mark.parametrize("family, expected", [
    ("Special", ("special", "SPECIAL")),
    ("No Play", ("no_play", "NO PLAY")),
    ("Custom Special", None),
])
def test_known_special_families_have_colors_without_guessing_custom_values(family, expected):
    from tapesift.ui_v2.tag_readout import PLAY_TYPE_COLORS, _play_type

    found = _play_type({"run_pass": family})
    assert found == expected
    if found:
        assert found[0] in PLAY_TYPE_COLORS


@pytest.mark.parametrize("concept", ["RPO", "Screen"])
@pytest.mark.parametrize("family, expected", [("No Play", "no_play"), ("Special", "special")])
def test_explicit_non_offensive_family_takes_precedence_over_retained_concept(family, concept, expected):
    from tapesift.ui_v2.tag_readout import _play_type

    assert _play_type({"run_pass": family, "play_type": concept})[0] == expected


def test_native_default_lanes_paint_their_own_facts_and_each_result(app):
    from PySide6.QtGui import QColor, QFont, QFontMetrics
    from tapesift.services.heatmap_palette import RESULT_COLORS as OUTCOME_COLORS, TYPE_COLORS as PLAY_TYPE_COLORS

    clip = Clip(1000, 7000, details={
        "quarter": "Q2", "down_distance": "3rd & 8", "run_pass": "Pass",
        "play_type": "Dropback", "play_action": "Play Action",
        "result": "Completion; First Down", "player_name": "Mark Fletcher Jr.",
        "other_players": "Carson Beck", "action": "Pressure"})
    before = dict(clip.details)
    # The painter has its own font; QApplication's font can differ after V3 loads.
    cell_font = QFont(["IBM Plex Sans", "Segoe UI"])
    cell_font.setPixelSize(13)
    cell_metrics = QFontMetrics(cell_font)
    grid = AttributeGrid()
    grid.enable_review_style()
    grid.resize(1000, 346)
    grid.set_clips([clip])
    grid.set_visible_range(0, 10_000)
    grid.show()
    app.processEvents()
    assert grid.color_by.currentData() == "lane"
    frame = _logical_frame(grid)
    left, _right = grid.clip_span(clip)
    row_indices = {row.key: i for i, row in enumerate(grid.rows())}
    assert grid._review_value(grid.rows()[row_indices["primary_tag"]], clip) == "Pass · Dropback · Play Action"
    assert grid._review_value(grid.rows()[row_indices["people"]], clip) == "Mark Fletcher Jr. · Carson Beck"

    def assert_rail(key, color, part=0, parts=1):
        index = row_indices[key]
        row = grid.rows()[index]
        height = grid._row_height(row)
        entries = grid._lane_color_entries(row, clip)
        widths = [cell_metrics.horizontalAdvance(label) + 14 for label, _ in entries]
        # The new rail spans the top of each horizontal result/player segment.
        fraction = sum(widths[:part]) / sum(widths) if parts > 1 else 0
        x = int(left + (_right-left-2) * fraction) + 4
        y = int(grid._row_top(index) + 5)
        actual = frame.pixelColor(x, y)
        expected = QColor(color)
        for channel in ("red", "green", "blue"):
            assert abs(getattr(actual, channel)() - getattr(expected, channel)()) <= 2, (key, actual.name(), color)

    assert_rail("quarter", "#589bce")
    assert_rail("down", "#536b5f")
    assert_rail("primary_tag", PLAY_TYPE_COLORS["pass"])
    assert_rail("result", OUTCOME_COLORS["complete"], 0, 2)
    assert_rail("result", OUTCOME_COLORS["first_down"], 1, 2)
    assert_rail("people", grid._people_colour(clip))
    assert "Result: Complete" in grid.legend.toolTip()
    assert "Result: First Down" in grid.legend.toolTip()
    assert clip.details == before
    grid.close()


def test_zoomed_native_cells_show_text_and_keep_it_inside_the_clip(app):
    clip = Clip(2000, 5000, details={"quarter": "Q2", "down_distance": "3rd & 8"})
    grid = AttributeGrid()
    grid.enable_review_style()
    grid.resize(1000, 346)
    grid.set_clips([clip])
    grid.show()

    def light_pixels(frame, left, right, top, bottom):
        return sum(min(frame.pixelColor(x, y).getRgb()[:3]) >= 195
                   for x in range(max(grid.plot_left(), left), min(grid.width() - 12, right))
                   for y in range(top, bottom))

    row_index = next(i for i, row in enumerate(grid.rows()) if row.key == "down")
    top = grid._row_top(row_index) + 6
    grid.set_visible_range(0, 10_000)
    app.processEvents()
    left, right = grid.clip_span(clip)
    assert light_pixels(_logical_frame(grid), left + 4, right, top, top + 18) > 12

    grid.set_visible_range(0, 1_000_000)
    app.processEvents()
    left, right = grid.clip_span(clip)
    assert light_pixels(_logical_frame(grid), left, right + 2, top, top + 18) == 0

    # A clipped leading edge gains text only in the visible part of its span.
    grid.set_visible_range(4000, 6000)
    app.processEvents()
    left, right = grid.clip_span(clip)
    frame = _logical_frame(grid)
    assert left == grid.plot_left()
    assert light_pixels(frame, left + 4, right, top, top + 18) > 12
    assert light_pixels(frame, right + 2, grid.width() - 12, top, top + 18) == 0
    assert (clip.start_ms, clip.end_ms) == (2000, 5000)
    grid.close()


def test_explicit_color_comparison_survives_refresh_zoom_and_lane_restore(app):
    grid = AttributeGrid()
    grid.enable_review_style()
    clip = Clip(1000, 2000, details={"run_pass": "Pass", "result": "TD; Sack"})
    before = dict(clip.details)
    grid.set_clips([clip])
    grid.color_by.setCurrentIndex(grid.color_by.findData("result"))
    colors = grid.color_entries(clip)
    grid.set_visible_range(0, 10_000)
    grid.set_clips([clip])
    assert grid.color_by.currentData() == "result"
    assert grid.color_entries(clip) == colors
    grid.color_by.setCurrentIndex(grid.color_by.findData("lane"))
    result_row = next(row for row in grid.rows() if row.key == "result")
    assert grid._lane_color_entries(result_row, clip) == colors
    assert clip.details == before
    grid.close()
