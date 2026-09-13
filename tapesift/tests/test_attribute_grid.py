"""Attribute grid: one column per play, one row per attribute."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.models.clip import Clip  # noqa: E402
from tapesift.ui_core.timeline import (  # noqa: E402
    DENSITY_CHIP, DENSITY_TEXT)
import tapesift.ui_v2.attribute_grid as ag  # noqa: E402
from tapesift.ui_v2.attribute_grid import (  # noqa: E402
    COLLAPSED_H, DEFAULT_ROWS, MAX_VISIBLE_ROWS, PLAYER_COLORS, ROW_GAP,
    ROW_H, AttributeGrid, C_NO_PLAYER)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _clips() -> list[Clip]:
    return [
        Clip(start_ms=10_000, end_ms=20_000, clip_title="One",
             notes="Good protection",
             details={"run_pass": "Run", "result": "First Down",
                      "player_name": "Mark Fletcher Jr."}),
        Clip(start_ms=30_000, end_ms=40_000, clip_title="Two",
             details={"run_pass": "Pass", "result": "Incomplete",
                      "player_name": "Carson Beck"}),
        Clip(start_ms=80_000, end_ms=90_000, clip_title="Far"),
    ]


def _grid(qapp, visible=(0, 60_000)) -> AttributeGrid:
    grid = AttributeGrid()
    grid.resize(1000, grid.height())
    grid.set_clips(_clips())
    grid.set_visible_range(*visible)
    return grid


class TestColumns:
    def test_only_plays_in_the_visible_range_get_columns(self, qapp):
        grid = _grid(qapp)
        assert [c.clip_title for c in grid.visible_clips()] == ["One", "Two"]

    def test_columns_sit_under_the_time_they_describe(self, qapp):
        grid = _grid(qapp)
        one, two = grid.visible_clips()
        assert grid._x_for(one.start_ms) < grid._x_for(one.end_ms)
        assert grid._x_for(one.end_ms) < grid._x_for(two.start_ms)
        # The window's start pins to the plot origin, not to zero.
        assert grid._x_for(0) == grid.plot_left()

    def test_clicking_a_column_reports_its_clip(self, qapp):
        grid = _grid(qapp)
        one = grid.visible_clips()[0]
        middle = (grid._x_for(one.start_ms) + grid._x_for(one.end_ms)) // 2
        assert grid.clip_at(middle, grid._row_top(0) + 5) is one

    def test_the_gutter_is_not_a_column(self, qapp):
        grid = _grid(qapp)
        assert grid.clip_at(4, grid._row_top(0) + 5) is None


class TestRows:
    def test_every_default_row_reads_a_value(self, qapp):
        grid = _grid(qapp)
        clip = grid.visible_clips()[0]
        values = {row.key: row.value(clip) for row in DEFAULT_ROWS}
        assert values["primary_tag"] == "RUN"
        assert values["result"] == "1ST DOWN"
        assert values["review"] == "Logged"
        assert values["people"] == "Fletcher"
        assert values["notes"] == "Good protection"

    def test_an_unlogged_play_reads_empty_not_wrong(self, qapp):
        grid = _grid(qapp)
        far = grid._clips[-1]
        values = {row.key: row.value(far) for row in DEFAULT_ROWS}
        assert values["primary_tag"] == ""
        assert values["result"] == ""
        assert values["review"] == "Unlogged"

    def test_collapsing_a_row_leaves_a_stub_to_reopen_it(self, qapp):
        """The chevron has to survive the collapse or there is no way back."""
        grid = _grid(qapp)
        before = grid.height()

        grid.set_row_collapsed("notes", True)

        assert grid.is_collapsed("notes")
        assert "notes" not in {r.key for r in grid.visible_rows()}
        assert grid.height() == before - (ROW_H - COLLAPSED_H)
        # Still laid out, so its chevron is still clickable.
        stub = grid.row_at(
            grid._row_top([r.key for r in grid.rows()].index("notes")) + 2)
        assert stub is not None and stub.key == "notes"

        grid.set_row_collapsed("notes", False)
        assert grid.height() == before

    def test_the_panel_stops_growing_and_lets_itself_scroll(self, qapp):
        """Open rows must not push the film off the screen."""
        grid = _grid(qapp)
        rows = [f"r{i}" for i in range(MAX_VISIBLE_ROWS + 4)]
        grid._rows = tuple(
            type(DEFAULT_ROWS[0])(key, key, lambda _c: "", lambda _c: "#333")
            for key in rows)
        grid._apply_height()

        capped = grid.height()
        assert capped == ROW_GAP + MAX_VISIBLE_ROWS * (ROW_H + ROW_GAP)
        assert grid.content_height() > capped


class TestDensity:
    def test_chips_carry_no_text(self, qapp):
        grid = _grid(qapp)
        grid.set_density(DENSITY_CHIP)
        assert grid._density == DENSITY_CHIP

    def test_text_density_is_accepted(self, qapp):
        grid = _grid(qapp)
        grid.set_density(DENSITY_TEXT)
        assert grid._density == DENSITY_TEXT

    def test_painting_is_safe_at_both_densities_and_empty(self, qapp):
        from PySide6.QtGui import QPixmap
        grid = _grid(qapp)
        for density in (DENSITY_CHIP, DENSITY_TEXT):
            grid.set_density(density)
            grid.render(QPixmap(grid.width(), grid.height()))
        grid.set_clips([])
        grid.set_visible_range(0, 0)
        grid.render(QPixmap(grid.width(), grid.height()))


class TestChevrons:
    def test_clicking_the_gutter_collapses_and_reopens(self, qapp):
        from PySide6.QtCore import QEvent, QPointF, Qt as QtNs
        from PySide6.QtGui import QMouseEvent

        grid = _grid(qapp)
        keys = [r.key for r in grid.rows()]
        index = keys.index("result")

        def click():
            y = grid._row_top(index) + 4
            grid.mousePressEvent(QMouseEvent(
                QEvent.Type.MouseButtonPress, QPointF(12, y),
                QtNs.MouseButton.LeftButton, QtNs.MouseButton.LeftButton,
                QtNs.KeyboardModifier.NoModifier))

        click()
        assert grid.is_collapsed("result")
        click()
        assert not grid.is_collapsed("result")

    def test_a_gutter_click_never_selects_a_clip(self, qapp):
        from PySide6.QtCore import QEvent, QPointF, Qt as QtNs
        from PySide6.QtGui import QMouseEvent

        grid = _grid(qapp)
        seen: list[str] = []
        grid.clipActivated.connect(seen.append)

        grid.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(12, grid._row_top(0) + 4),
            QtNs.MouseButton.LeftButton, QtNs.MouseButton.LeftButton,
            QtNs.KeyboardModifier.NoModifier))

        assert seen == []


class TestConfidenceBar:
    def test_confidence_reads_a_ratio_and_a_label(self, qapp):
        grid = _grid(qapp)
        row = next(r for r in grid.rows() if r.key == "confidence")
        clip = grid.visible_clips()[0]

        clip.detection_lineage = {"confidence": 0.92}
        assert row.ratio is not None
        assert abs(row.ratio(clip) - 0.92) < 1e-6
        assert row.value(clip) == "92%"

    def test_percent_scale_is_accepted_too(self, qapp):
        grid = _grid(qapp)
        row = next(r for r in grid.rows() if r.key == "confidence")
        clip = grid.visible_clips()[0]

        clip.detection_lineage = {"confidence": 88}
        assert abs(row.ratio(clip) - 0.88) < 1e-6
        assert row.value(clip) == "88%"

    def test_a_clip_with_no_lineage_is_empty_not_zero_percent(self, qapp):
        grid = _grid(qapp)
        row = next(r for r in grid.rows() if r.key == "confidence")
        clip = grid.visible_clips()[0]

        clip.detection_lineage = {}
        assert row.value(clip) == ""
        assert row.ratio(clip) == 0.0


class TestCellEditing:
    """Choice cells are a shortcut to an existing edit, not a second one."""

    def _double(self, grid, x, y):
        from PySide6.QtCore import QEvent, QPointF, Qt as QtNs
        from PySide6.QtGui import QMouseEvent
        grid.mouseDoubleClickEvent(QMouseEvent(
            QEvent.Type.MouseButtonDblClick, QPointF(x, y),
            QtNs.MouseButton.LeftButton, QtNs.MouseButton.LeftButton,
            QtNs.KeyboardModifier.NoModifier))

    def _cell(self, grid, key):
        index = [r.key for r in grid.rows()].index(key)
        clip = grid.visible_clips()[0]
        x = (grid._x_for(clip.start_ms) + grid._x_for(clip.end_ms)) // 2
        return x, grid._row_top(index) + 4, clip

    def test_double_clicking_a_choice_cell_asks_to_edit_it(self, qapp):
        grid = _grid(qapp)
        seen: list[tuple[str, str]] = []
        grid.cellEditRequested.connect(
            lambda cid, key: seen.append((cid, key)))

        x, y, clip = self._cell(grid, "result")
        self._double(grid, x, y)

        assert seen == [(clip.id, "result")]

    def test_derived_and_free_text_rows_are_not_editable(self, qapp):
        grid = _grid(qapp)
        seen: list[tuple[str, str]] = []
        grid.cellEditRequested.connect(
            lambda cid, key: seen.append((cid, key)))

        for key in ("review", "notes", "confidence"):
            x, y, _clip = self._cell(grid, key)
            self._double(grid, x, y)

        assert seen == []

    def test_a_collapsed_row_offers_nothing(self, qapp):
        grid = _grid(qapp)
        grid.set_row_collapsed("result", True)
        assert grid.editable_row_at(
            grid._row_top([r.key for r in grid.rows()].index("result")) + 2) \
            is None

    def test_the_gutter_does_not_open_an_editor(self, qapp):
        grid = _grid(qapp)
        seen: list[tuple[str, str]] = []
        grid.cellEditRequested.connect(
            lambda cid, key: seen.append((cid, key)))

        _x, y, _clip = self._cell(grid, "result")
        self._double(grid, 10, y)

        assert seen == []

    def test_every_choice_writes_fields_the_projection_reads(self, qapp):
        """A choice that the row cannot read back would look like a no-op."""
        from tapesift.ui_v2.attribute_grid import EDIT_CHOICES

        grid = _grid(qapp)
        rows = {r.key: r for r in grid.rows()}
        clip = grid.visible_clips()[0]
        for key, choices in EDIT_CHOICES.items():
            for label, values in choices:
                clip.details = dict(values)
                shown = rows[key].value(clip)
                # A choice that writes nothing to every field is a clear,
                # and a blank cell is exactly what it should look like.
                # Anything else has to read back or it looks like a no-op.
                if not any(v.strip() for v in values.values()):
                    assert not shown, f"{key}={label} should clear the cell"
                else:
                    assert shown, f"{key}={label} projected to nothing"


class TestPlayerColours:
    """Colour by workload, so the players you see most cannot collide."""

    def _project(self, qapp, names):
        grid = AttributeGrid()
        grid.resize(1000, grid.height())
        clips = [
            Clip(start_ms=i * 1_000, end_ms=i * 1_000 + 500,
                 details={"player_name": name})
            for i, name in enumerate(names)
        ]
        grid.set_clips(clips)
        grid.set_visible_range(0, len(names) * 1_000)
        return grid

    def _colour(self, grid, name):
        row = next(r for r in grid.rows() if r.key == "people")
        return row.colour(
            Clip(start_ms=0, end_ms=1, details={"player_name": name}))

    def test_the_palette_offers_twenty_two_distinct_colours(self):
        assert len(PLAYER_COLORS) == 22
        assert len(set(PLAYER_COLORS)) == 22

    def test_the_busiest_players_take_the_first_colours_in_order(self, qapp):
        grid = self._project(
            qapp, ["Toney"] * 9 + ["Brown"] * 6 + ["Beck"] * 4 + ["Bell"])

        assert self._colour(grid, "Toney") == PLAYER_COLORS[0]
        assert self._colour(grid, "Brown") == PLAYER_COLORS[1]
        assert self._colour(grid, "Beck") == PLAYER_COLORS[2]
        assert self._colour(grid, "Bell") == PLAYER_COLORS[3]

    def test_a_full_roster_of_twenty_two_never_repeats(self, qapp):
        """The case hashing could not promise: no collisions inside the palette."""
        names = [f"Player {i:02d}" for i in range(22)]
        grid = self._project(qapp, [n for n in names for _ in range(2)])

        colours = [self._colour(grid, name) for name in names]
        assert len(set(colours)) == 22

    def test_beyond_the_palette_it_wraps_rather_than_going_blank(self, qapp):
        names = [f"Player {i:02d}" for i in range(25)]
        grid = self._project(qapp, names)

        for name in names:
            assert self._colour(grid, name) in PLAYER_COLORS

    def test_ties_break_deterministically(self, qapp):
        one = self._project(qapp, ["Alpha", "Beta", "Gamma"])
        two = self._project(qapp, ["Gamma", "Beta", "Alpha"])

        for name in ("Alpha", "Beta", "Gamma"):
            assert self._colour(one, name) == self._colour(two, name)

    def test_one_player_keeps_one_colour_however_it_is_typed(self, qapp):
        grid = self._project(qapp, ["Malachi Toney"] * 3 + ["Brown"])

        assert self._colour(grid, "Malachi Toney") ==             self._colour(grid, "malachi  toney")

    def test_no_player_is_not_given_a_player_colour(self, qapp):
        grid = self._project(qapp, ["Beck"])
        row = next(r for r in grid.rows() if r.key == "people")

        empty = row.colour(Clip(start_ms=0, end_ms=1))
        assert empty == C_NO_PLAYER
        assert empty not in PLAYER_COLORS

    def test_different_results_still_differ(self, qapp):
        grid = _grid(qapp)
        row = next(r for r in grid.rows() if r.key == "result")
        colours = {
            row.colour(Clip(start_ms=0, end_ms=1, details={"result": value}))
            for value in ("First Down", "Touchdown", "Sack",
                          "Interception", "Incompletion")}
        assert len(colours) >= 4, colours


def _lab(value: str) -> tuple[float, float, float]:
    """CIELab, so 'do these look alike' is measured rather than eyeballed."""
    colour = QColor(value)
    channels = []
    for raw in (colour.redF(), colour.greenF(), colour.blueF()):
        channels.append(
            raw / 12.92 if raw <= 0.04045
            else ((raw + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    x = (r * .4124 + g * .3576 + b * .1805) / .95047
    y = r * .2126 + g * .7152 + b * .0722
    z = (r * .0193 + g * .1192 + b * .9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _delta_e(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_lab(a), _lab(b))) ** 0.5


class TestPaletteSeparation:
    """Two players looking identical is the bug this palette exists to avoid."""

    def test_no_two_colours_are_perceptually_close(self):
        worst = min(
            (_delta_e(a, b), a, b)
            for i, a in enumerate(PLAYER_COLORS)
            for b in PLAYER_COLORS[i + 1:]
        )
        assert worst[0] >= 9.0, worst

    def test_neighbouring_ranks_are_far_apart(self):
        """Rank order is list order, so ranks 1 and 2 must not rhyme."""
        worst = min(
            (_delta_e(PLAYER_COLORS[i], PLAYER_COLORS[i + 1]), i)
            for i in range(len(PLAYER_COLORS) - 1)
        )
        assert worst[0] >= 35.0, worst

    def test_every_colour_is_bright_enough_for_a_dark_panel(self):
        for value in PLAYER_COLORS:
            assert _lab(value)[0] >= 44.0, value


class TestSituationRows:
    """The rows the inspector used to own alone, now readable across plays."""

    @staticmethod
    def _clip(index, **details):
        return Clip(start_ms=index * 1000, end_ms=index * 1000 + 800,
                    clip_number=index + 1, details=details)

    def test_the_grid_carries_the_situation_not_just_the_outcome(self):
        keys = [row.key for row in ag.DEFAULT_ROWS]
        for key in ("quarter", "down", "action"):
            assert key in keys

    def test_quarters_read_as_bands(self):
        """Four quarters, four colours - this is the whole point of the row."""
        seen = {
            ag._quarter_colour(self._clip(i, quarter=q))
            for i, q in enumerate(("Q1", "Q2", "Q3", "Q4"))
        }
        assert len(seen) == 4
        assert ag.C_NO_PLAYER not in seen

    def test_an_unlogged_quarter_is_not_given_a_colour(self):
        assert ag._quarter_colour(self._clip(0)) == ag.C_NO_PLAYER
        assert ag._quarter_colour(self._clip(0, quarter="")) == ag.C_NO_PLAYER

    def test_quarters_survive_lower_case(self):
        assert (ag._quarter_colour(self._clip(0, quarter="q3"))
                == ag._quarter_colour(self._clip(1, quarter="Q3")))

    def test_down_is_coloured_by_the_down_not_the_distance(self):
        """3rd & 1 and 3rd & 15 are the same down and must look it."""
        short = self._clip(0, down_distance="3rd & 1")
        long = self._clip(1, down_distance="3rd & 15")
        assert ag._down_colour(short) == ag._down_colour(long)
        assert ag._down_colour(short) != ag._down_colour(
            self._clip(2, down_distance="1st & 10"))

    def test_a_play_with_no_action_stays_dark(self):
        """Actions only pop because most plays are not one."""
        assert ag._action_colour(self._clip(0)) == ag.C_NO_PLAYER
        assert ag._action_colour(
            self._clip(1, action="Big Hit")) != ag.C_NO_PLAYER

    def test_read_values_open_closed(self):
        """A down and a yard line are read, not scanned, so they wait."""
        assert "down" in ag.DEFAULT_COLLAPSED
        assert "quarter" not in ag.DEFAULT_COLLAPSED
        assert "action" not in ag.DEFAULT_COLLAPSED

    def test_every_row_fits_without_silently_dropping_the_last_ones(self, qapp):
        """Nine open rows overran the ceiling and the tail just vanished."""
        grid = AttributeGrid()
        assert grid.content_height() <= grid._ceiling()
        for key in ag.DEFAULT_COLLAPSED:
            assert grid.is_collapsed(key)

    def test_opening_everything_is_still_allowed_to_scroll(self, qapp):
        grid = AttributeGrid()
        for key in list(ag.DEFAULT_COLLAPSED):
            grid.set_row_collapsed(key, False)
        assert grid.content_height() > grid._ceiling()
        assert grid.height() == grid._ceiling()


class TestGridEditing:
    """Iteration 2: the grid can set what it shows, including typed values."""

    def test_every_situation_row_can_be_edited(self):
        for key in ("quarter", "down", "action"):
            assert key in ag.EDIT_CHOICES

    @pytest.mark.parametrize("title, key, label", [("TD", "score", "TD"), ("Sack", "negative", "SACK")])
    def test_legacy_title_outcome_uses_category_color_without_reclassifying_structured_text(self, title, key, label):
        from tapesift.ui_v2.tag_readout import OUTCOME_COLORS, _outcome

        clip = Clip(0, 1000, clip_title=title)
        assert _outcome(clip) == (key, label)
        assert ag._result_colour(clip) == OUTCOME_COLORS[key]
        assert clip.details == {}
        clip.details["result"] = "Custom " + title
        assert _outcome(clip) == ("other", "OTHER")
        assert ag._result_colour(clip) == OUTCOME_COLORS["other"]

    def test_rows_that_only_report_stay_read_only(self):
        """People and confidence are derived, not chosen."""
        for key in ("people", "confidence", "review", "notes"):
            assert key not in ag.EDIT_CHOICES

    @pytest.mark.parametrize("typed,expected", [
        ("3rd & 7", "3rd & 7"),
        ("3 7", "3rd & 7"),
        ("3rd and goal", "3rd & Goal"),
        ("4th&G", "4th & Goal"),
        ("2ND & 15", "2nd & 15"),
        ("1st", "1st"),
        ("", ""),
        ("nonsense", ""),
        ("To Go Inches", "To Go Inches"),
        ("2nd & 105", "2nd & 105"),
        ("3rd & -7", ""),
        ("3rd & 7oops", ""),
    ])
    def test_a_down_is_taken_however_it_was_typed(self, typed, expected):
        assert ag.normalize_down_distance(typed) == expected

    def test_changing_the_down_keeps_the_distance(self):
        """3rd & 7 becoming 4th is still & 7 - that is the whole point."""
        assert ag.set_down_keeping_distance("3rd & 7", "4th") == "4th & 7"
        assert ag.set_down_keeping_distance("3rd & Goal", "4th") == "4th & Goal"
        assert ag.set_down_keeping_distance("To Go 105", "4th") == "4th & 105"
        assert ag.set_down_keeping_distance("To Go Inches", "4th") == "4th & Inches"

    def test_changing_the_down_with_no_distance_yet_just_sets_it(self):
        assert ag.set_down_keeping_distance("", "2nd") == "2nd"

    def test_the_typed_row_is_the_one_a_menu_cannot_finish(self):
        assert "down" in ag.EDIT_TYPED
        assert "quarter" not in ag.EDIT_TYPED


class TestKeyboard:
    """Iteration 3: log without reaching for the mouse."""

    @staticmethod
    def _grid_with(qapp, count=6):
        grid = AttributeGrid()
        grid.resize(1200, grid.height())
        clips = [
            Clip(start_ms=i * 1000, end_ms=i * 1000 + 900, clip_number=i + 1,
                 details={"quarter": "Q1", "down_distance": "1st & 10"})
            for i in range(count)
        ]
        grid.set_clips(clips)
        grid.set_visible_range(0, count * 1000)
        return grid

    @staticmethod
    def _key(grid, key):
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QKeyEvent
        grid.keyPressEvent(QKeyEvent(
            QKeyEvent.Type.KeyPress, key, _Qt.KeyboardModifier.NoModifier))

    def test_arrows_walk_the_grid(self, qapp):
        from PySide6.QtCore import Qt as _Qt
        grid = self._grid_with(qapp)
        self._key(grid, _Qt.Key.Key_Right)
        assert grid._cursor == (0, 0)
        self._key(grid, _Qt.Key.Key_Right)
        self._key(grid, _Qt.Key.Key_Down)
        assert grid._cursor == (1, 1)

    def test_the_cursor_cannot_walk_off_the_game(self, qapp):
        from PySide6.QtCore import Qt as _Qt
        grid = self._grid_with(qapp, count=3)
        for _ in range(10):
            self._key(grid, _Qt.Key.Key_Right)
        row, clip = grid._cursor
        assert clip == 2
        for _ in range(20):
            self._key(grid, _Qt.Key.Key_Down)
        assert grid._cursor[0] == len(grid.rows()) - 1

    def test_moving_across_plays_follows_the_selection(self, qapp):
        from PySide6.QtCore import Qt as _Qt
        grid = self._grid_with(qapp)
        seen: list[str] = []
        grid.clipActivated.connect(seen.append)
        self._key(grid, _Qt.Key.Key_Right)
        self._key(grid, _Qt.Key.Key_Right)
        assert len(seen) == 2

    def test_moving_between_rows_does_not_reselect(self, qapp):
        """Changing which attribute you are on is not changing play."""
        from PySide6.QtCore import Qt as _Qt
        grid = self._grid_with(qapp)
        self._key(grid, _Qt.Key.Key_Right)
        seen: list[str] = []
        grid.clipActivated.connect(seen.append)
        self._key(grid, _Qt.Key.Key_Down)
        assert seen == []

    def test_a_digit_picks_that_choice_on_the_focused_row(self, qapp):
        from PySide6.QtCore import Qt as _Qt
        grid = self._grid_with(qapp)
        picks: list[tuple[str, int]] = []
        grid.cellChoicePicked.connect(
            lambda _c, key, index: picks.append((key, index)))
        self._key(grid, _Qt.Key.Key_Right)
        self._key(grid, _Qt.Key.Key_3)
        assert picks == [("quarter", 2)]

    def test_a_digit_past_the_choices_does_nothing(self, qapp):
        from PySide6.QtCore import Qt as _Qt
        grid = self._grid_with(qapp)
        picks = []
        grid.cellChoicePicked.connect(
            lambda _c, key, index: picks.append(index))
        self._key(grid, _Qt.Key.Key_Right)
        self._key(grid, _Qt.Key.Key_9)
        assert picks == []

    def test_a_digit_on_a_read_only_row_does_nothing(self, qapp):
        from PySide6.QtCore import Qt as _Qt
        grid = self._grid_with(qapp)
        picks = []
        grid.cellChoicePicked.connect(
            lambda _c, key, index: picks.append(key))
        self._key(grid, _Qt.Key.Key_Right)
        people = [r.key for r in grid.rows()].index("people")
        grid.set_cursor_cell(people, 0)
        self._key(grid, _Qt.Key.Key_1)
        assert picks == []

    def test_enter_opens_the_full_editor_for_typed_rows(self, qapp):
        from PySide6.QtCore import Qt as _Qt
        grid = self._grid_with(qapp)
        asked: list[str] = []
        grid.cellEditRequested.connect(lambda _c, key: asked.append(key))
        down = [r.key for r in grid.rows()].index("down")
        grid.set_cursor_cell(down, 0)
        self._key(grid, _Qt.Key.Key_Return)
        assert asked == ["down"]

    def test_escape_puts_the_keyboard_down(self, qapp):
        from PySide6.QtCore import Qt as _Qt
        grid = self._grid_with(qapp)
        self._key(grid, _Qt.Key.Key_Right)
        self._key(grid, _Qt.Key.Key_Escape)
        assert grid._cursor is None
        assert grid.cursor_cell() is None

    def test_the_grid_can_take_focus_at_all(self, qapp):
        from PySide6.QtCore import Qt as _Qt
        grid = self._grid_with(qapp)
        assert grid.focusPolicy() == _Qt.FocusPolicy.StrongFocus


class TestPlayerFilter:
    """Show one player against the rest of the game."""

    @staticmethod
    def _grid(qapp, names):
        grid = AttributeGrid()
        grid.resize(1200, grid.height())
        clips = [
            Clip(start_ms=i * 1000, end_ms=i * 1000 + 900, clip_number=i + 1,
                 details={"player_name": n, "quarter": "Q1"})
            for i, n in enumerate(names)
        ]
        grid.set_clips(clips)
        grid.set_visible_range(0, len(names) * 1000)
        return grid, clips

    def test_nothing_is_filtered_until_you_ask(self, qapp):
        grid, clips = self._grid(qapp, ["Mark Fletcher", "Chamar Brown"])
        assert grid.player_filter() == ""
        assert all(grid._passes_player_filter(c) for c in clips)

    @pytest.mark.parametrize("width", [9, 84])
    @pytest.mark.parametrize("matches", [False, True])
    def test_review_tags_cover_the_field_even_when_player_filter_excludes_them(self, qapp, width, matches):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter

        grid, clips = self._grid(qapp, ["Mark Fletcher", "Chamar Brown"])
        grid.enable_review_style()
        grid._field_surface = object()
        grid.toggle_player_filter(clips[0])
        images = []
        for background in ("#ff00ff", "#00ff00"):
            image = QImage(100, 40, QImage.Format.Format_ARGB32)
            image.fill(QColor(background))
            painter = QPainter(image)
            grid._draw_ink_cell(painter, QRectF(4, 4, width, 28), "Pass",
                                [("Pass", "#3c99cd")], matches, False)
            painter.end()
            images.append(image.copy(4, 4, width, 28))
        assert images[0] == images[1], "Field background must never show through a populated tag"

    def test_isolating_a_player_leaves_only_their_plays_lit(self, qapp):
        grid, clips = self._grid(
            qapp, ["Mark Fletcher", "Chamar Brown", "Mark Fletcher"])
        grid.toggle_player_filter(clips[0])
        assert [grid._passes_player_filter(c) for c in clips] == [
            True, False, True]

    def test_isolating_the_same_player_twice_clears_it(self, qapp):
        grid, clips = self._grid(qapp, ["Mark Fletcher", "Chamar Brown"])
        grid.toggle_player_filter(clips[0])
        grid.toggle_player_filter(clips[0])
        assert grid.player_filter() == ""

    def test_a_spelling_variant_is_the_same_player_here_too(self, qapp):
        """The roster and the filter have to agree or the filter lies."""
        grid, clips = self._grid(
            qapp, ["Mark Fletcher", "M. Fletcher", "Chamar Brown"])
        grid.toggle_player_filter(clips[0])
        assert grid._passes_player_filter(clips[1])
        assert not grid._passes_player_filter(clips[2])

    def test_a_play_with_no_player_never_passes_a_filter(self, qapp):
        grid, clips = self._grid(qapp, ["Mark Fletcher", ""])
        grid.toggle_player_filter(clips[0])
        assert not grid._passes_player_filter(clips[1])

    def test_the_change_is_announced_once(self, qapp):
        grid, clips = self._grid(qapp, ["Mark Fletcher", "Chamar Brown"])
        seen: list[str] = []
        grid.playerFilterChanged.connect(seen.append)
        grid.toggle_player_filter(clips[0])
        grid.toggle_player_filter(clips[0])
        assert seen == ["mark_fletcher", ""]
