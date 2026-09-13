"""Timeline widget: coordinate safety and cache behaviour.

The regression here is real: clip blocks reach the timeline before the
video's duration does, so the range is briefly empty. Unclamped, a clip
600 seconds in produced an x of ~600 million and overflowed the int Qt's
drawing calls take, crashing on every paint.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QMouseEvent, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.ui_core.timeline import (  # noqa: E402
    BAND_BOTTOM_PAD, BAND_H, BAND_TOP, C_BG, HEIGHT, LANE_GAP, PLAY_COLORS,
    PLAY_LEGEND, RAIL_RESERVE, REVIEW_COLORS,
    UNCLAIMED_RAIL_GAP, UNCLAIMED_RAIL_H,
    CELL_TEXT_MIN_PX, DENSITY_CHIP, DENSITY_SPARSE, DENSITY_TEXT,
    TIMELINE_COLOR_MODES, Timeline, TimelineBlock,
    assign_lanes, block_contains, blocks_overlap, build_timeline_legend,
    classify_play_kind, period_label, timeline_category,
)
from tapesift.ui_core import timeline as timeline_module  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def timeline(qapp):
    t = Timeline()
    t.resize(800, HEIGHT)
    return t


def paint(t: Timeline) -> None:
    t.render(QPixmap(max(1, t.width()), HEIGHT))


class WheelProbe:
    def __init__(
            self, delta: int, x: float,
            modifiers: Qt.KeyboardModifier =
            Qt.KeyboardModifier.NoModifier) -> None:
        self._delta = delta
        self._x = x
        self._modifiers = modifiers
        self.accepted = False
        self.ignored = False

    def angleDelta(self) -> QPoint:
        return QPoint(0, self._delta)

    def position(self) -> QPointF:
        return QPointF(self._x, 0)

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


class TestCoordinateSafety:
    def test_blocks_before_duration_known_do_not_crash(self, timeline):
        """The actual crash: opening a project drew blocks at range (0,0).

        Built directly rather than through paint(), because Qt swallows
        exceptions raised inside paintEvent - which is exactly why this
        showed up as a logged OverflowError instead of a hard failure.
        """
        # Real numbers matter: with an empty range the x scales with the
        # timestamp, and a clip 48 minutes into a 50-minute film blew past
        # the 32-bit int Qt's drawing calls take.
        timeline.setRange(0, 0)
        timeline.set_blocks([(2_900_000, 2_928_000, False),
                             (3_000_000, 3_040_000, True)])
        timeline.set_marks(2_950_000, 2_990_000)
        timeline._build_static()          # used to raise OverflowError

    def test_x_stays_inside_the_widget(self, timeline):
        timeline.setRange(0, 3_000_000)
        for ms in (-10_000_000, 0, 1_500_000, 3_000_000, 99_000_000):
            x = timeline._x_for(ms)
            assert 0 <= x <= timeline.width(), (ms, x)

    def test_empty_range_pins_to_zero(self, timeline):
        timeline.setRange(0, 0)
        assert timeline._x_for(500_000) == 0

    def test_out_of_range_blocks_are_clamped_not_wild(self, timeline):
        timeline.setRange(0, 60_000)
        timeline.set_blocks([(-5_000, 999_999_999, False)])
        timeline._build_static()

    def test_marks_beyond_the_film_are_safe(self, timeline):
        timeline.setRange(0, 60_000)
        timeline.set_marks(10**9, 10**9 + 1000)
        timeline._build_static()

    def test_zoom_uses_visible_window_without_changing_film_range(
            self, timeline):
        timeline.setRange(0, 100_000)
        timeline.setValue(50_000)

        timeline.set_zoom_factor(4, 50_000)

        assert timeline.minimum() == 0
        assert timeline.maximum() == 100_000
        assert timeline.visible_range() == (37_500, 62_500)
        assert timeline._x_for(37_500) == 0
        assert timeline._x_for(62_500) == timeline.width() - 1
        assert 49_900 <= timeline._ms_for(timeline.width() / 2) <= 50_100

    def test_zoomed_timeline_follows_playhead_outside_visible_window(
            self, timeline):
        timeline.setRange(0, 100_000)
        timeline.setValue(50_000)
        timeline.set_zoom_factor(4, 50_000)

        timeline.setValue(90_000)

        start, end = timeline.visible_range()
        assert start <= 90_000 <= end
        assert end - start == 25_000

    def test_reset_zoom_restores_full_game(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_zoom_factor(8, 50_000)

        timeline.reset_zoom()

        assert timeline.visible_range() == (0, 100_000)

    def test_visible_range_signal_fires_only_when_static_viewport_moves(
            self, timeline):
        changes = []
        timeline.visibleRangeChanged.connect(
            lambda start, end: changes.append((start, end)))
        timeline.setRange(0, 100_000)
        changes.clear()

        timeline.setValue(50_000)
        assert changes == []

        timeline.set_zoom_factor(4, 50_000)
        assert changes == [(37_500, 62_500)]

        timeline.setValue(60_000)
        assert changes == [(37_500, 62_500)]

        timeline.setValue(90_000)
        assert changes == [
            (37_500, 62_500),
            (75_000, 100_000),
        ]

    def test_timeline_exposes_the_authoritative_viewport(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.setValue(40_000)
        timeline.set_zoom_factor(2, 40_000)

        assert timeline.viewport.source_range() == (0, 100_000)
        assert timeline.viewport.visible_range() == timeline.visible_range()
        assert timeline.viewport.playhead_ms == timeline.value()


class TestWheelRouting:
    def test_ctrl_wheel_emits_cursor_time_without_seeking(
            self, timeline):
        timeline.setRange(0, 100_000)
        anchor_x = 200
        expected_anchor = timeline._ms_for(anchor_x)
        zooms, seeks, frames = [], [], []
        timeline.wheel_zoom.connect(
            lambda notches, anchor: zooms.append((notches, anchor)))
        timeline.wheel_seek.connect(seeks.append)
        timeline.wheel_frame.connect(frames.append)
        event = WheelProbe(
            120, anchor_x, Qt.KeyboardModifier.ControlModifier)

        timeline.wheelEvent(event)

        assert zooms == [(1, expected_anchor)]
        assert seeks == []
        assert frames == []
        assert event.accepted
        assert not event.ignored

    def test_shift_and_plain_wheel_keep_existing_routes(self, timeline):
        seeks, frames, zooms = [], [], []
        timeline.wheel_seek.connect(seeks.append)
        timeline.wheel_frame.connect(frames.append)
        timeline.wheel_zoom.connect(
            lambda notches, anchor: zooms.append((notches, anchor)))

        timeline.wheelEvent(WheelProbe(
            -120, 300, Qt.KeyboardModifier.ShiftModifier))
        timeline.wheelEvent(WheelProbe(120, 300))

        assert frames == [-1]
        assert seeks == [1]
        assert zooms == []

    def test_ctrl_takes_precedence_when_shift_is_also_held(
            self, timeline):
        zooms, frames = [], []
        timeline.wheel_zoom.connect(
            lambda notches, anchor: zooms.append((notches, anchor)))
        timeline.wheel_frame.connect(frames.append)
        modifiers = (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.ShiftModifier
        )

        timeline.wheelEvent(WheelProbe(-120, 400, modifiers))

        assert zooms == [(-1, timeline._ms_for(400))]
        assert frames == []

    def test_zero_delta_is_ignored(self, timeline):
        event = WheelProbe(0, 200)

        timeline.wheelEvent(event)

        assert event.ignored
        assert not event.accepted


class TestSnapping:
    def test_snapping_targets_clips_quarters_and_in_out_marks(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(20_000, 30_000, clip_id="play"),
        ])
        timeline.set_period_markers([50_000])
        timeline.set_marks(70_000, 80_000)

        assert timeline._snap_ms(20_500) == 20_000
        assert timeline._snap_ms(49_500) == 50_000
        assert timeline._snap_ms(70_500) == 70_000
        assert timeline._snap_ms(22_000) == 22_000

    def test_predicted_snap_is_a_pointer_snap_target(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(
                20_000, 40_000, clip_id="play",
                predicted_snap_ms=27_000, snap_eligible=True),
        ])

        assert timeline._snap_ms(27_500) == 27_000

    def test_alt_and_snap_toggle_bypass_snapping(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(20_000, 30_000, clip_id="play"),
        ])

        assert timeline._snap_ms(
            20_500, Qt.KeyboardModifier.AltModifier) == 20_500
        timeline.set_snapping_enabled(False)
        assert timeline._snap_ms(20_500) == 20_500

    def test_snap_tolerance_stays_constant_in_pixels_when_zoomed(
            self, timeline):
        timeline.setRange(0, 100_000)
        timeline.setValue(25_000)
        timeline.set_blocks([
            TimelineBlock(20_000, 30_000, clip_id="play"),
        ])

        assert timeline._snap_ms(20_500) == 20_000
        timeline.set_zoom_factor(4, 25_000)
        assert timeline._snap_ms(20_500) == 20_500
        assert timeline._snap_ms(20_200) == 20_000


def test_timeline_perf_probe_wraps_complete_render(timeline, monkeypatch):
    active = []
    rendered_while_active = []

    class Probe:
        def __init__(self, name):
            assert name == "timeline_redraw"

        def __enter__(self):
            active.append(True)

        def __exit__(self, *_args):
            active.pop()

    original = timeline._render_static

    def render():
        rendered_while_active.append(bool(active))
        return original()

    monkeypatch.setattr(timeline_module, "PerfTimer", Probe)
    monkeypatch.setattr(timeline, "_render_static", render)
    timeline._build_static()
    assert rendered_while_active == [True]


def test_quarter_markers_create_film_time_segments(timeline):
    timeline.setRange(0, 60_000)
    timeline.set_period_markers([15_000, 30_000, 45_000])

    assert timeline._period_boundaries() == [
        0, 15_000, 30_000, 45_000, 60_000]
    assert timeline._period_index_for(0) == 0
    assert timeline._period_index_for(29_999) == 1
    assert timeline._period_index_for(30_000) == 2
    timeline._build_static()


def test_period_labels_support_overtime():
    assert [period_label(index) for index in range(7)] == [
        "Q1", "Q2", "Q3", "Q4", "OT", "2OT", "3OT"]


def test_play_legend_has_text_for_every_timeline_color():
    items = {kind: (label, colour) for kind, label, colour in PLAY_LEGEND}
    assert set(PLAY_COLORS) <= set(items)
    assert items["run"][0] == "Run"
    assert items["pass"][0] == "Pass"
    assert items[""][0] == "Unlabelled"
    assert all(label and colour.isValid() for label, colour in items.values())


def test_timeline_color_modes_are_stable_and_user_facing():
    assert TIMELINE_COLOR_MODES == (
        ("play_type", "Play Type"),
        ("result", "Result"),
        ("primary_tag", "Primary Tag"),
        ("personnel", "Personnel"),
        ("review_status", "Review Status"),
    )


def test_timeline_color_mode_persists_in_app_settings(tmp_path):
    target = tmp_path / "settings.json"
    AppSettings(timeline_color_by="personnel").save(target)
    assert AppSettings.load(target).timeline_color_by == "personnel"


@pytest.mark.parametrize(("mode", "details", "tags", "expected"), [
    ("play_type", {"run_pass": "Pass"}, [], ("pass", "Pass")),
    ("result", {"result": "First Down"}, [], ("first_down", "First Down")),
    ("result", {"result": "Reception; First Down"}, [],
     ("first_down", "First Down")),
    ("primary_tag", {}, ["Explosive Play", "Q2"],
     ("explosive_play", "Explosive Play")),
    ("personnel", {"off_personnel": "11 Personnel"}, [],
     ("11_personnel", "11 Personnel")),
    ("personnel", {"def_personnel": "Nickel"}, [], ("nickel", "Nickel")),
    ("result", {}, [], ("", "Unlabelled")),
])
def test_color_mode_categories(mode, details, tags, expected):
    assert timeline_category(mode, details, tags) == expected


@pytest.mark.parametrize(("enabled", "needs_fix", "details", "expected"), [
    (True, False, {"quarter": "Q1"}, ("logged", "Logged")),
    (True, False, {}, ("unlogged", "Unlogged")),
    (True, True, {"quarter": "Q1"}, ("needs_fix", "Needs Fix")),
    (False, True, {}, ("excluded", "Excluded")),
])
def test_review_status_category_precedence(
        enabled, needs_fix, details, expected):
    assert timeline_category(
        "review_status", details, [], enabled=enabled,
        needs_fix=needs_fix) == expected


def test_dynamic_legend_is_alphabetical_and_always_explains_gray():
    legend = build_timeline_legend(
        "result",
        {"touchdown": "Touchdown", "completion": "Completion"})
    assert [label for _key, label, _colour in legend] == [
        "Completion", "Touchdown", "Unlabelled"]
    assert all(colour.isValid() for _key, _label, colour in legend)


def test_review_legend_uses_fixed_status_colors():
    legend = build_timeline_legend("review_status", {})
    assert {
        key: colour for key, _label, colour in legend
    } == REVIEW_COLORS


class TestOverlapLanes:
    """A play must never be hidden behind another play.

    The old timeline drew every block into one 10px band, so an overlap and a
    single long play were pixel-identical. That made it impossible to tell a
    detector merge from a drawing artefact by eye.
    """

    def test_adjacent_plays_share_a_lane_and_keep_the_original_height(
            self, timeline):
        timeline.setRange(0, 60_000)
        timeline.set_blocks([
            TimelineBlock(0, 10_000, clip_id="a"),
            TimelineBlock(10_000, 20_000, clip_id="b"),
        ])

        assert [block.lane for block in timeline._blocks] == [0, 0]
        assert timeline.conflict_ranges() == ()
        assert timeline.lane_count() == 1
        # No quarter markers, so the rail collapses and the strip is shorter
        # than the historic full-rail height.
        # The unclaimed-footage rail sits below the lanes.
        rail = UNCLAIMED_RAIL_GAP + UNCLAIMED_RAIL_H
        assert timeline.height() == (
            HEIGHT - RAIL_RESERVE + (BAND_H - 10) + rail)

        timeline.set_period_markers([20_000, 40_000])
        assert timeline.height() == HEIGHT + (BAND_H - 10) + rail

    def test_one_frame_apart_is_not_a_conflict(self, timeline):
        timeline.setRange(0, 60_000)
        timeline.set_blocks([
            TimelineBlock(0, 10_000, clip_id="a"),
            TimelineBlock(10_033, 20_000, clip_id="b"),
        ])

        assert timeline.conflict_ranges() == ()
        assert timeline.lane_count() == 1

    def test_partial_overlap_is_stacked_and_reported(self, timeline):
        timeline.setRange(0, 60_000)
        timeline.set_blocks([
            TimelineBlock(10_000, 20_000, clip_id="a"),
            TimelineBlock(15_000, 25_000, clip_id="b"),
        ])

        assert [block.lane for block in timeline._blocks] == [0, 1]
        assert timeline.conflict_ranges() == ((15_000, 20_000),)
        assert timeline.lane_count() == 2
        one_lane = (timeline._band_top() + BAND_H + BAND_BOTTOM_PAD
                    + UNCLAIMED_RAIL_GAP + UNCLAIMED_RAIL_H)
        assert timeline.height() == one_lane + BAND_H + LANE_GAP

    def test_contained_play_gets_its_own_lane(self, timeline):
        timeline.setRange(0, 120_000)
        timeline.set_blocks([
            TimelineBlock(0, 100_000, clip_id="long"),
            TimelineBlock(20_000, 40_000, clip_id="short"),
        ])

        assert [block.lane for block in timeline._blocks] == [0, 1]
        assert timeline.conflict_ranges() == ((20_000, 40_000),)

    def test_three_competing_detections_use_three_lanes(self, timeline):
        timeline.setRange(0, 60_000)
        timeline.set_blocks([
            TimelineBlock(10_000, 30_000, clip_id="a"),
            TimelineBlock(12_000, 32_000, clip_id="b"),
            TimelineBlock(14_000, 34_000, clip_id="c"),
        ])

        assert sorted(block.lane for block in timeline._blocks) == [0, 1, 2]
        # The contested range runs from the second start to the last time two
        # plays still claim the same frame, not to the final end.
        assert timeline.conflict_ranges() == ((12_000, 32_000),)

    def test_a_lane_is_reused_once_its_play_has_ended(self, timeline):
        timeline.setRange(0, 120_000)
        timeline.set_blocks([
            TimelineBlock(0, 30_000, clip_id="a"),
            TimelineBlock(20_000, 40_000, clip_id="b"),
            TimelineBlock(50_000, 60_000, clip_id="c"),
        ])

        assert [block.lane for block in timeline._blocks] == [0, 1, 0]

    def test_more_than_three_simultaneous_plays_keep_every_block(self):
        blocks = [
            TimelineBlock(1_000 * index, 100_000, clip_id=str(index))
            for index in range(6)
        ]
        lanes, conflicts = assign_lanes(blocks)

        assert len(lanes) == 6
        assert set(lanes) == {0, 1, 2}
        assert conflicts


class TestBlockSeparation:
    def test_touching_neighbours_keep_a_visible_gap_column(self, timeline):
        """1px is ~2.7s on a full game: without this two plays read as one."""
        timeline.setRange(0, 2_400_000)
        timeline.set_blocks([
            TimelineBlock(0, 600_000, clip_id="a", colour="#46e485"),
            TimelineBlock(600_100, 1_200_000, clip_id="b", colour="#46e485"),
        ])

        image = timeline._render_static().toImage()
        middle = timeline._band_top() + BAND_H // 2
        boundary_x = timeline._x_for(600_100)

        assert image.pixelColor(boundary_x - 1, middle) == C_BG
        assert image.pixelColor(boundary_x - 40, middle) != C_BG
        assert image.pixelColor(boundary_x + 40, middle) != C_BG


class TestLaneAwareSelection:
    def test_a_contained_play_no_longer_steals_the_long_play(self, timeline):
        timeline.setRange(0, 120_000)
        timeline.set_blocks([
            TimelineBlock(0, 100_000, clip_id="long"),
            TimelineBlock(20_000, 40_000, clip_id="short"),
        ])
        x = timeline._x_for(30_000)

        long_lane = timeline._lane_top(0) + BAND_H // 2
        short_lane = timeline._lane_top(1) + BAND_H // 2

        assert timeline._block_at(x, long_lane).clip_id == "long"
        assert timeline._block_at(x, short_lane).clip_id == "short"

    def test_a_shared_boundary_belongs_to_the_later_play(self):
        """Half-open ranges: end_ms is the first millisecond NOT in the play."""
        first = TimelineBlock(0, 30_000, clip_id="a")
        second = TimelineBlock(30_000, 60_000, clip_id="b")

        assert not block_contains(first, 30_000)
        assert block_contains(first, 29_999)
        assert block_contains(second, 30_000)
        assert not blocks_overlap(first, second)

    def test_pointer_outside_the_band_matches_no_lane(self, timeline):
        timeline.setRange(0, 60_000)
        timeline.set_blocks([TimelineBlock(0, 30_000, clip_id="a")])

        assert timeline._lane_at(0) is None
        assert timeline._lane_at(timeline._band_top() + 1) == 0


class TestZoomedRuler:
    @pytest.mark.parametrize(("span", "expected"), [
        (60_000, (5_000, 15_000)),
        (30_000, (5_000, 15_000)),
        (8_000, (1_000, 5_000)),
        (3_000, (1_000, 1_000)),
    ])
    def test_fine_tick_spacing_matches_the_visible_window(
            self, timeline, span, expected):
        timeline.setRange(0, span)

        assert timeline._fine_tick_interval() == expected

    def test_frame_level_zoom_renders_without_a_bare_groove(self, timeline):
        timeline.setRange(0, 600_000)
        timeline.set_frame_duration_ms(1000 / 29.97)
        timeline.setValue(300_000)
        timeline.set_zoom_factor(400, 300_000)

        assert timeline.visible_range()[1] - timeline.visible_range()[0] \
            <= 2_000
        image = timeline._render_static().toImage()
        groove_row = [
            image.pixelColor(x, timeline._groove_top() + 7)
            for x in range(timeline.width())
        ]
        assert any(colour != C_BG for colour in groove_row)


class TestCache:
    def test_lanes_do_not_break_the_playhead_fast_path(self, timeline):
        """JKL contract: moving the playhead must never rebuild the cache."""
        timeline.setRange(0, 600_000)
        timeline.set_blocks([
            TimelineBlock(0, 100_000, clip_id="long"),
            TimelineBlock(20_000, 40_000, clip_id="short"),
        ])
        paint(timeline)
        first = timeline._static
        assert first is not None

        for pos in range(0, 600_000, 10_000):
            timeline.setValue(pos)
            paint(timeline)

        assert timeline._static is first

    def test_playhead_movement_does_not_rebuild_the_cache(self, timeline):
        timeline.setRange(0, 3_000_000)
        timeline.set_blocks([(i * 60_000, i * 60_000 + 28_000, False)
                             for i in range(20)])
        paint(timeline)
        first = timeline._static
        assert first is not None
        for pos in range(0, 3_000_000, 50_000):
            timeline.setValue(pos)
            paint(timeline)
        assert timeline._static is first

    def test_content_change_rebuilds(self, timeline):
        timeline.setRange(0, 60_000)
        paint(timeline)
        first = timeline._static
        timeline.set_blocks([(0, 5_000, False)])
        assert timeline._static is None       # invalidated
        paint(timeline)
        assert timeline._static is not first

    def test_value_is_clamped_to_range(self, timeline):
        timeline.setRange(0, 1000)
        timeline.setValue(99_999)
        assert timeline.value() == 1000
        timeline.setValue(-50)
        assert timeline.value() == 0


class TestBlockInteraction:
    def test_selected_clip_id_updates_only_selection_state(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(
                10_000, 20_000, clip_id="run", kind="run",
                title="Inside zone"),
            TimelineBlock(
                30_000, 40_000, clip_id="pass", kind="pass",
                title="Deep post"),
        ])

        timeline.set_selected_clip_id("pass")

        assert [block.selected for block in timeline._blocks] == [False, True]
        assert [
            (block.clip_id, block.kind, block.title)
            for block in timeline._blocks
        ] == [
            ("run", "run", "Inside zone"),
            ("pass", "pass", "Deep post"),
        ]

    def test_selected_block_start_edge_drags_as_trim(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(
                20_000, 40_000, selected=True,
                clip_id="selected", title="Inside zone"),
        ])
        previews, finished, activated = [], [], []
        timeline.trimPreview.connect(
            lambda clip_id, edge, ms:
            previews.append((clip_id, edge, ms)))
        timeline.trimFinished.connect(
            lambda clip_id, edge, ms:
            finished.append((clip_id, edge, ms)))
        timeline.blockActivated.connect(
            lambda clip_id, ms: activated.append((clip_id, ms)))
        start = QPoint(timeline._x_for(20_000), timeline._band_top() + 4)
        end = QPoint(timeline._x_for(15_000), timeline._band_top() + 4)

        QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(timeline, end)
        QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=end)

        assert activated == []
        assert previews
        assert finished
        assert finished[-1][0:2] == ("selected", "start")
        assert 14_800 <= finished[-1][2] <= 15_200
        block = timeline._blocks[0]
        assert block.start_ms == finished[-1][2]
        assert block.end_ms == 40_000

    def test_selected_block_end_edge_respects_minimum_duration(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(
                20_000, 40_000, selected=True, clip_id="selected"),
        ])
        finished = []
        timeline.trimFinished.connect(
            lambda clip_id, edge, ms:
            finished.append((clip_id, edge, ms)))
        start = QPoint(timeline._x_for(40_000), timeline._band_top() + 4)
        end = QPoint(timeline._x_for(20_000), timeline._band_top() + 4)

        QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(timeline, end)
        QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=end)

        assert finished[-1][0:2] == ("selected", "end")
        assert finished[-1][2] - timeline._blocks[0].start_ms >= 250

    def test_pressing_trim_handle_without_drag_keeps_boundary(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(
                20_000, 40_000, selected=True, clip_id="selected"),
        ])
        finished = []
        timeline.trimFinished.connect(
            lambda clip_id, edge, ms:
            finished.append((clip_id, edge, ms)))

        QTest.mouseClick(
            timeline, Qt.MouseButton.LeftButton,
            pos=QPoint(timeline._x_for(20_000), timeline._band_top() + 4))

        assert finished == [("selected", "start", 20_000)]
        assert timeline._blocks[0].start_ms == 20_000

    def test_clicking_block_activates_clip_without_scrubbing(self, timeline):
        timeline.setRange(0, 100_000)
        block = TimelineBlock(20_000, 40_000, clip_id="clip-2", kind="run")
        timeline.set_blocks([block])
        activated, moved, pressed = [], [], []
        timeline.blockActivated.connect(
            lambda clip_id, clicked_ms: activated.append((clip_id, clicked_ms)))
        timeline.sliderMoved.connect(moved.append)
        timeline.sliderPressed.connect(lambda: pressed.append(True))

        QTest.mouseClick(
            timeline, Qt.MouseButton.LeftButton,
            pos=QPoint(timeline._x_for(30_000), timeline._band_top() + 4))

        assert len(activated) == 1
        assert activated[0][0] == "clip-2"
        assert 29_000 <= activated[0][1] <= 31_000
        assert moved == []
        assert pressed == []
        assert not timeline.isSliderDown()

    def test_selected_clip_wins_when_blocks_overlap(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(20_000, 80_000, selected=True,
                          clip_id="selected-long"),
            TimelineBlock(35_000, 45_000, clip_id="short-overlap"),
        ])
        activated = []
        timeline.blockActivated.connect(
            lambda clip_id, clicked_ms: activated.append((clip_id, clicked_ms)))

        QTest.mouseClick(
            timeline, Qt.MouseButton.LeftButton,
            pos=QPoint(timeline._x_for(40_000), timeline._band_top() + 4))

        assert activated[0][0] == "selected-long"
        assert 39_000 <= activated[0][1] <= 41_000

    def test_selected_hit_padding_cannot_steal_adjacent_full_game_clip(
        self,
        timeline,
    ):
        timeline.setRange(0, 2_570_501)
        selected = TimelineBlock(
            957_957, 973_472, selected=True, clip_id="accepted")
        adjacent = TimelineBlock(
            973_973, 993_993, clip_id="next-pending")
        timeline.set_blocks([selected, adjacent])
        x = timeline._x_for(adjacent.start_ms) + 1

        assert selected.end_ms < timeline._ms_for(x) < adjacent.end_ms
        assert timeline._block_at(x).clip_id == "next-pending"

    def test_right_click_requests_menu_at_exact_clip_position(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(20_000, 80_000, selected=True,
                          clip_id="selected-long"),
        ])
        requested = []
        timeline.contextRequested.connect(
            lambda clip_id, clicked_ms, global_pos:
            requested.append((clip_id, clicked_ms, global_pos)))
        local_pos = QPoint(timeline._x_for(55_000), 8)
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, local_pos,
            timeline.mapToGlobal(local_pos))

        QApplication.sendEvent(timeline, event)

        assert requested[0][0] == "selected-long"
        assert 54_000 <= requested[0][1] <= 56_000

    def test_clicking_empty_groove_still_scrubs(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([])
        moved = []
        timeline.sliderMoved.connect(moved.append)
        QTest.mouseClick(
            timeline, Qt.MouseButton.LeftButton,
            pos=QPoint(timeline._x_for(70_000), 8))
        assert moved
        assert 69_000 <= moved[-1] <= 71_000

    def test_clicking_near_boundary_snaps_playhead(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(20_000, 30_000, clip_id="play"),
        ])

        QTest.mouseClick(
            timeline, Qt.MouseButton.LeftButton,
            pos=QPoint(timeline._x_for(20_500), 8))

        assert timeline.value() == 20_000

    def test_drag_moves_visual_playhead_immediately(self, timeline):
        timeline.setRange(0, 100_000)
        start = QPoint(timeline._x_for(20_000), 8)
        end = QPoint(timeline._x_for(75_000), 8)

        QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(timeline, end)

        assert timeline.isSliderDown()
        assert 74_000 <= timeline.value() <= 76_000

        QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=end)
        assert not timeline.isSliderDown()
        assert 74_000 <= timeline.value() <= 76_000

    def test_playhead_has_wide_grab_area_over_clip_block(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.setValue(30_000)
        timeline.set_blocks([
            TimelineBlock(20_000, 40_000, clip_id="under-playhead"),
        ])
        activated, moved = [], []
        timeline.blockActivated.connect(
            lambda clip_id, clicked_ms:
            activated.append((clip_id, clicked_ms)))
        timeline.sliderMoved.connect(moved.append)

        QTest.mouseClick(
            timeline, Qt.MouseButton.LeftButton,
            pos=QPoint(timeline._x_for(30_000), timeline._band_top() + 4))

        assert activated == []
        assert moved
        assert 29_000 <= moved[-1] <= 31_000

    def test_dragging_colored_block_scrubs_instead_of_selecting(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.setValue(0)
        timeline.set_blocks([
            TimelineBlock(20_000, 80_000, clip_id="colored-block"),
        ])
        activated, moved = [], []
        timeline.blockActivated.connect(
            lambda clip_id, clicked_ms:
            activated.append((clip_id, clicked_ms)))
        timeline.sliderMoved.connect(moved.append)
        start = QPoint(timeline._x_for(30_000), timeline._band_top() + 4)
        end = QPoint(timeline._x_for(70_000), timeline._band_top() + 4)

        QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(timeline, end)

        assert activated == []
        assert moved
        assert timeline.isSliderDown()
        assert 69_000 <= timeline.value() <= 71_000

        QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=end)
        assert activated == []
        assert not timeline.isSliderDown()

    def test_semantic_fill_remains_visible_when_selected(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(20_000, 40_000, selected=True,
                          clip_id="run", kind="run")])
        image = timeline._build_static().toImage()
        centre = image.pixelColor(timeline._x_for(30_000), timeline._band_top() + 5)
        assert centre == PLAY_COLORS["run"]

    def test_block_can_carry_color_for_non_play_type_mode(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(
                20_000, 40_000, clip_id="completion",
                kind="completion", colour="#36c8d8",
                category_label="Completion")])
        image = timeline._build_static().toImage()
        centre = image.pixelColor(timeline._x_for(30_000), timeline._band_top() + 5)
        assert centre.name() == "#36c8d8"

    def test_block_hover_names_type_title_and_time(self, timeline):
        timeline.setRange(0, 100_000)
        timeline.set_blocks([
            TimelineBlock(20_000, 40_000, clip_id="run", kind="run",
                          title="Inside zone")])
        timeline.show()
        QApplication.processEvents()

        # Send the move to this widget directly. QTest's global synthetic
        # cursor can remain captured by a floating native dock when Qt test
        # modules share one offscreen application.
        local = QPointF(
            timeline._x_for(30_000), timeline._band_top() + 4)
        global_pos = QPointF(timeline.mapToGlobal(local.toPoint()))
        QApplication.sendEvent(
            timeline,
            QMouseEvent(
                QEvent.Type.MouseMove,
                local,
                global_pos,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        QApplication.processEvents()

        assert "Run" in timeline.toolTip()
        assert "Inside zone" in timeline.toolTip()
        assert "00:20" in timeline.toolTip()


class TestPlayClassification:
    @pytest.mark.parametrize(("details", "tags", "title", "expected"), [
        ({"run_pass": "Run"}, [], "", "run"),
        ({"run_pass": "Pass"}, [], "", "pass"),
        ({"run_pass": "Run", "play_type": "RPO"}, [], "", "rpo"),
        ({"run_pass": "Pass", "play_type": "RPO"}, [], "", "rpo"),
        ({"run_pass": "Pass", "play_type": "RPO",
          "result": "Reception; Penalty"}, [], "", "penalty"),
        ({"run_pass": "Pass", "result": "Touchdown"}, [], "", "touchdown"),
        ({"run_pass": "Pass", "result": "Interception"}, [], "", "interception"),
        ({"run_pass": "Pass", "highlight": "Sack"}, [], "", "sack"),
        ({}, ["Rushing TD"], "", "touchdown"),
        ({}, [], "Q3 screen completion", "pass"),
        ({}, [], "Unlogged play", ""),
    ])
    def test_category_precedence(self, details, tags, title, expected):
        assert classify_play_kind(details, tags, title) == expected

    def test_rpo_and_penalty_have_distinct_valid_timeline_colors(self):
        assert PLAY_COLORS["rpo"].isValid()
        assert PLAY_COLORS["penalty"].isValid()
        assert PLAY_COLORS["rpo"] != PLAY_COLORS["run"]
        assert PLAY_COLORS["rpo"] != PLAY_COLORS["pass"]
        assert PLAY_COLORS["penalty"] != PLAY_COLORS["rpo"]

    def test_penalty_is_yellow_and_touchdown_is_pink(self):
        assert PLAY_COLORS["penalty"].name() == "#d9b43b"
        assert PLAY_COLORS["touchdown"].name() == "#e779b8"


def _press(position: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonPress, position,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)


class TestUnclaimedRail:
    """Footage no play covers is the thing you have to hunt for today."""

    def _timeline(self, blocks, duration=60_000):
        timeline = Timeline()
        timeline.setRange(0, duration)
        timeline.set_blocks(blocks)
        timeline.resize(800, timeline.height())
        return timeline

    def test_gaps_between_plays_are_reported(self, qapp):
        timeline = self._timeline([
            TimelineBlock(5_000, 15_000, clip_id="a"),
            TimelineBlock(30_000, 40_000, clip_id="b"),
        ])
        assert timeline.unclaimed_ranges() == (
            (0, 5_000), (15_000, 30_000), (40_000, 60_000))

    def test_touching_and_overlapping_plays_leave_no_gap(self, qapp):
        timeline = self._timeline([
            TimelineBlock(0, 20_000, clip_id="a"),
            TimelineBlock(20_000, 40_000, clip_id="b"),
            TimelineBlock(35_000, 60_000, clip_id="c"),
        ])
        assert timeline.unclaimed_ranges() == ()

    def test_sub_second_seams_are_not_offered(self, qapp):
        """A 300ms seam between adjacent plays is not footage to reclaim."""
        timeline = self._timeline([
            TimelineBlock(0, 20_000, clip_id="a"),
            TimelineBlock(20_300, 60_000, clip_id="b"),
        ])
        assert timeline.unclaimed_ranges() == ()

    def test_clicking_a_gap_emits_its_range(self, qapp):
        timeline = self._timeline([
            TimelineBlock(0, 20_000, clip_id="a"),
            TimelineBlock(40_000, 60_000, clip_id="b"),
        ])
        seen: list[tuple[int, int]] = []
        timeline.unclaimedActivated.connect(
            lambda a, b: seen.append((a, b)))

        rail_y = timeline._unclaimed_rail_top() + 2
        x = timeline._x_for(30_000)
        timeline.mousePressEvent(_press(QPointF(x, rail_y)))

        assert seen == [(20_000, 40_000)]

    def test_clicking_a_covered_stretch_of_rail_does_nothing(self, qapp):
        timeline = self._timeline([TimelineBlock(0, 60_000, clip_id="a")])
        seen: list[tuple[int, int]] = []
        timeline.unclaimedActivated.connect(
            lambda a, b: seen.append((a, b)))

        rail_y = timeline._unclaimed_rail_top() + 2
        timeline.mousePressEvent(
            _press(QPointF(timeline._x_for(30_000), rail_y)))

        assert seen == []


class TestCellDensity:
    """The contract an aligned attribute grid reads before it draws."""

    def _timeline(self, count, duration=600_000, width=1200):
        timeline = Timeline()
        timeline.setRange(0, duration)
        step = duration // max(1, count)
        # Plays occupy most of their slot, so the painted width scales
        # with how many share the film - which is what density measures.
        timeline.set_blocks([
            TimelineBlock(i * step, i * step + int(step * 0.7),
                          clip_id=f"c{i}")
            for i in range(count)
        ])
        timeline.resize(width, timeline.height())
        return timeline

    def test_a_handful_of_plays_leaves_room_for_words(self, qapp):
        timeline = self._timeline(8)
        assert timeline.visible_block_count() == 8
        assert timeline.cell_width_px() >= CELL_TEXT_MIN_PX
        assert timeline.cell_density() == DENSITY_TEXT

    def test_a_whole_game_falls_back_to_chips(self, qapp):
        timeline = self._timeline(40)
        assert timeline.cell_density() == DENSITY_CHIP

    def test_a_very_dense_game_is_sparse(self, qapp):
        timeline = self._timeline(200)
        assert timeline.cell_density() == DENSITY_SPARSE

    def test_zooming_in_moves_chips_to_text(self, qapp):
        timeline = self._timeline(40)
        assert timeline.cell_density() == DENSITY_CHIP

        timeline.set_zoom_factor(12.0, anchor_ms=300_000)

        assert timeline.visible_block_count() < 40
        assert timeline.cell_density() == DENSITY_TEXT

    def test_density_change_is_announced_once(self, qapp):
        timeline = self._timeline(40)
        seen: list[str] = []
        timeline.cellDensityChanged.connect(seen.append)

        timeline.set_zoom_factor(12.0, anchor_ms=300_000)
        assert seen == [DENSITY_TEXT]

        # Staying inside the same band says nothing further.
        timeline.set_zoom_factor(13.0, anchor_ms=300_000)
        assert seen == [DENSITY_TEXT]

        timeline.reset_zoom()
        assert seen == [DENSITY_TEXT, DENSITY_CHIP]

    def test_ensure_readable_zooms_in_only_as_far_as_needed(self, qapp):
        timeline = self._timeline(40)
        before = timeline.viewport.zoom_factor

        assert timeline.ensure_readable_cells(anchor_ms=300_000) is True

        assert timeline.cell_density() == DENSITY_TEXT
        assert timeline.viewport.zoom_factor > before
        # Already readable: it does not keep zooming.
        assert timeline.ensure_readable_cells(anchor_ms=300_000) is False

    def test_zooming_out_is_never_blocked(self, qapp):
        """Chips are the fallback, so the whole game stays reachable."""
        timeline = self._timeline(40)
        timeline.ensure_readable_cells(anchor_ms=300_000)

        timeline.reset_zoom()

        assert timeline.visible_range() == (0, 600_000)
        assert timeline.cell_density() == DENSITY_CHIP


class TestEdgeScroll:
    """Dragging the playhead past an edge pans the film instead of stopping."""

    def _dragging(self, qapp, zoom=8.0):
        timeline = Timeline()
        timeline.setRange(0, 600_000)
        timeline.resize(1000, timeline.height())
        timeline.set_zoom_factor(zoom, anchor_ms=300_000)
        timeline._pressed = True
        return timeline

    def test_inside_the_widget_nothing_scrolls(self, qapp):
        timeline = self._dragging(qapp)
        timeline._update_edge_scroll(500)
        assert not timeline._edge_scroll_timer.isActive()
        assert timeline.edge_scroll_step_ms() == 0

    def test_past_the_right_edge_pans_forward(self, qapp):
        timeline = self._dragging(qapp)
        timeline._update_edge_scroll(timeline.width() + 20)
        assert timeline._edge_scroll_timer.isActive()
        assert timeline.edge_scroll_step_ms() > 0

    def test_past_the_left_edge_pans_backward(self, qapp):
        timeline = self._dragging(qapp)
        timeline._update_edge_scroll(-20)
        assert timeline.edge_scroll_step_ms() < 0

    def test_pushing_further_scrolls_faster_but_is_capped(self, qapp):
        timeline = self._dragging(qapp)
        timeline._update_edge_scroll(timeline.width() + 5)
        gentle = timeline.edge_scroll_step_ms()
        timeline._update_edge_scroll(timeline.width() + 140)
        hard = timeline.edge_scroll_step_ms()
        timeline._update_edge_scroll(timeline.width() + 4000)
        absurd = timeline.edge_scroll_step_ms()

        assert hard > gentle
        assert absurd == hard, "the ramp has to stop somewhere"

    def test_the_rate_is_proportional_to_zoom(self, qapp):
        """A frame matters at high zoom; distance matters at low zoom."""
        far = self._dragging(qapp, zoom=1.0)
        far._update_edge_scroll(far.width() + 20)
        close = self._dragging(qapp, zoom=16.0)
        close._update_edge_scroll(close.width() + 20)

        assert far.edge_scroll_step_ms() > close.edge_scroll_step_ms()

    def test_a_step_moves_the_film_and_carries_the_playhead(self, qapp):
        timeline = self._dragging(qapp)
        before = timeline.visible_range()
        timeline._update_edge_scroll(timeline.width() + 40)

        timeline._edge_scroll_step()

        after = timeline.visible_range()
        assert after[0] > before[0]
        assert timeline.value() == after[1]

    def test_it_stops_at_the_end_of_the_film(self, qapp):
        timeline = self._dragging(qapp)
        timeline._update_edge_scroll(timeline.width() + 4000)
        for _ in range(400):
            timeline._edge_scroll_step()

        assert timeline.visible_range()[1] == 600_000
        assert not timeline._edge_scroll_timer.isActive()

    def test_releasing_the_mouse_stops_it(self, qapp):
        timeline = self._dragging(qapp)
        timeline._update_edge_scroll(timeline.width() + 40)
        assert timeline._edge_scroll_timer.isActive()

        timeline._pressed = False
        timeline._edge_scroll_step()

        assert not timeline._edge_scroll_timer.isActive()
