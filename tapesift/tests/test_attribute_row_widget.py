"""One attribute, one row: presets and a typed field are one value."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.ui_v2.attribute_row_widget import (  # noqa: E402
    LABEL_W, ROW_H, AttributeRowWidget)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _to_go(qapp) -> AttributeRowWidget:
    row = AttributeRowWidget(
        "To go", ("5", "10", "15"), entry=True, placeholder="#")
    row.resize(280, ROW_H)
    return row


class TestChoices:
    def test_picking_a_preset_sets_the_value(self, qapp):
        row = _to_go(qapp)
        row.buttons["10"].click()
        assert row.value() == "10"
        assert row.buttons["10"].isChecked()

    def test_only_one_preset_is_ever_checked(self, qapp):
        row = _to_go(qapp)
        row.buttons["5"].click()
        row.buttons["15"].click()
        assert row.value() == "15"
        assert [c for c, b in row.buttons.items() if b.isChecked()] == ["15"]

    def test_the_label_column_is_fixed_so_rows_line_up(self, qapp):
        """Inline, every label takes the same column - that is the read."""
        row = _to_go(qapp)
        other = AttributeRowWidget("Quarter", ("Q1", "Q2", "Q3", "Q4"))
        for widget in (row, other):
            widget.set_stack_override(False)
            widget.resize(400, ROW_H)
            widget._place()
        assert row.label.width() == other.label.width() == LABEL_W

    def test_stacking_gives_the_label_its_own_line(self, qapp):
        """Narrow, the label goes on top so the cells keep their words."""
        row = AttributeRowWidget("Quarter", ("Q1", "Q2", "Q3", "Q4"))
        row.set_stack_override(True)
        row.resize(200, row.heightForWidth(200))
        row._place()
        assert row.is_stacked()
        assert row.label.width() == 200
        assert row.heightForWidth(200) > ROW_H

    def test_no_cell_ever_reaches_past_the_row(self, qapp):
        """The bug this fixes: cells ran off the panel's right edge."""
        row = AttributeRowWidget("Quarter", ("Q1", "Q2", "Q3", "Q4"))
        for width in (400, 330, 308, 260, 220, 160, 120, 80):
            row.resize(width, row.heightForWidth(width))
            row._place()
            for cell in row._cells():
                assert cell.geometry().right() < width, (width, cell.text())
                assert cell.geometry().left() >= 0

    def test_a_row_can_shrink_far_enough_for_the_dock_to_follow(self, qapp):
        """A large minimum is what made the panel clip instead of restack."""
        row = AttributeRowWidget("Quarter", ("Q1", "Q2", "Q3", "Q4"))
        assert row.minimumSizeHint().width() <= 60


class TestTypedEntry:
    def test_a_typed_number_becomes_the_value(self, qapp):
        row = _to_go(qapp)
        row.entry.setText("7")
        row.entry.editingFinished.emit()
        assert row.value() == "7"

    def test_typing_clears_the_preset(self, qapp):
        row = _to_go(qapp)
        row.buttons["10"].click()
        row.entry.setText("7")
        row.entry.editingFinished.emit()
        assert not any(b.isChecked() for b in row.buttons.values())

    def test_picking_a_preset_clears_the_field(self, qapp):
        """One value, two ways in - never both showing something."""
        row = _to_go(qapp)
        row.entry.setText("7")
        row.entry.editingFinished.emit()
        row.buttons["10"].click()
        assert row.value() == "10"
        assert row.entry.text() == ""

    def test_typing_a_preset_number_lights_that_preset(self, qapp):
        row = _to_go(qapp)
        row.entry.setText("10")
        row.entry.editingFinished.emit()
        assert row.buttons["10"].isChecked()
        assert row.entry.text() == ""

    def test_g_means_goal(self, qapp):
        """Inside the five the distance is not a distance."""
        for typed in ("g", "G", " goal ", "Goal"):
            assert AttributeRowWidget.normalize_entry(typed) == "Goal"

    def test_junk_is_reduced_to_its_digits_or_dropped(self, qapp):
        assert AttributeRowWidget.normalize_entry("7 yds") == "7"
        assert AttributeRowWidget.normalize_entry("abc") == ""
        assert AttributeRowWidget.normalize_entry("") == ""

    def test_goal_survives_a_round_trip(self, qapp):
        row = _to_go(qapp)
        row.entry.setText("g")
        row.entry.editingFinished.emit()
        assert row.value() == "Goal"
        assert row.entry.text() == "Goal"


class TestSignal:
    def test_a_change_is_announced_once(self, qapp):
        row = _to_go(qapp)
        seen: list[str] = []
        row.valueChanged.connect(seen.append)

        row.buttons["5"].click()
        row.buttons["5"].click()
        row.buttons["10"].click()

        assert seen == ["5", "10"]

    def test_setting_the_same_value_says_nothing(self, qapp):
        row = _to_go(qapp)
        row.set_value("10")
        seen: list[str] = []
        row.valueChanged.connect(seen.append)
        row.set_value("10")
        assert seen == []


class TestTextEntry:
    """Field position is typed, not counted, so it keeps what was written."""

    @staticmethod
    def _ball_on(qapp):
        return AttributeRowWidget(
            "Ball on", ("Own", "Mid", "Opp"), entry=True,
            entry_mode="text", placeholder="yd")

    def test_a_typed_spot_survives_its_letters(self, qapp):
        row = self._ball_on(qapp)
        row.entry.setText("Own 35")
        row.entry.editingFinished.emit()
        assert row.value() == "Own 35"

    def test_a_distance_row_still_keeps_only_the_number(self, qapp):
        row = _to_go(qapp)
        row.entry.setText("Own 35")
        row.entry.editingFinished.emit()
        assert row.value() == "35"

    def test_goal_is_a_distance_word_not_a_place(self, qapp):
        assert AttributeRowWidget.normalize_entry("g") == "Goal"
        assert AttributeRowWidget.normalize_entry("g", "text") == "g"

    def test_a_preset_still_clears_the_typed_spot(self, qapp):
        row = self._ball_on(qapp)
        row.entry.setText("Own 35")
        row.entry.editingFinished.emit()
        row.buttons["Mid"].click()
        assert row.value() == "Mid"
        assert row.entry.text() == ""
