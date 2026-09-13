"""Regression coverage for the three launch-selectable timeline designs."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from tapesift.core.config import AppSettings
from tapesift.ui_core.timeline_variants import (
    COMPACT,
    DUAL,
    FOCUS,
    normalize_timeline_variant,
    timeline_variant_from_args,
)
from tapesift.ui_core.video_player import VideoPlayer
from tapesift.ui_v2.control_center import ControlCenterDeck
from tapesift.ui_core.timeline import (
    C_COVERAGE_MISSED,
    C_COVERAGE_MISSED_HIGH,
    C_COVERAGE_MISSED_LOW,
    C_COVERAGE_RESOLVED,
    C_COVERAGE_SEPARATOR,
    C_SOURCE_BASE,
    Timeline,
    TimelineBlock,
    TimelineCoverage,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _player_harness(variant: str) -> tuple[QWidget, VideoPlayer]:
    """Build the player and its externally owned deck explicitly."""
    owner = QWidget()
    layout = QVBoxLayout(owner)
    player = VideoPlayer(AppSettings(), owner, timeline_variant=variant)
    deck = ControlCenterDeck(owner)
    player.attach_control_center(deck)
    layout.addWidget(player)
    layout.addWidget(deck)
    return owner, player


def _overlapping_blocks() -> list[TimelineBlock]:
    return [
        TimelineBlock(
            1_000_000, 1_040_000, True, "play-1", "pass",
            "Selected play"),
        TimelineBlock(
            1_020_000, 1_060_000, False, "play-2", "run",
            "Overlapping play"),
        TimelineBlock(
            1_200_000, 1_230_000, False, "play-3", "touchdown",
            "Later play"),
    ]


def test_launch_argument_accepts_equals_and_separate_forms():
    assert timeline_variant_from_args([]) == DUAL
    assert timeline_variant_from_args(
        ["--timeline-variant=compact"]) == COMPACT
    assert timeline_variant_from_args(
        ["--timeline-variant", "focus"]) == FOCUS
    assert normalize_timeline_variant("overview+detail") == DUAL


def test_compact_variant_expands_lanes_only_at_editing_zoom(qapp):
    timeline = Timeline(variant=COMPACT)
    timeline.resize(1000, timeline.height())
    timeline.setRange(0, 3_000_000)
    timeline.set_blocks(_overlapping_blocks())
    assert timeline.lane_count() == 1
    assert timeline.conflict_ranges() == ((1_020_000, 1_040_000),)
    timeline.set_zoom_factor(8, 1_030_000)
    assert timeline.lane_count() == 2


def test_compact_rail_paints_full_source_and_coverage_gaps(qapp):
    timeline = Timeline(variant=COMPACT)
    timeline.resize(1000, timeline.height())
    timeline.setRange(0, 100_000)
    timeline.set_coverage_segments([
        TimelineCoverage(20_000, 40_000, "possible_missed"),
        TimelineCoverage(60_000, 70_000, "separator"),
    ])

    image = timeline._render_static().toImage()
    y = timeline._band_bottom() - 2
    assert image.pixelColor(timeline._x_for(10_000), y) == C_SOURCE_BASE
    # At full-game scale fragments are only a one-pixel whisper, so they do
    # not read as ordinary play blocks.
    assert image.pixelColor(timeline._x_for(30_000), y) == C_SOURCE_BASE
    assert image.pixelColor(timeline._x_for(65_000), y) == \
        C_COVERAGE_SEPARATOR

    timeline.set_show_ignored_fragments(True)
    expanded = timeline._render_static().toImage()
    assert expanded.pixelColor(
        timeline._x_for(30_000), timeline._band_bottom() - 1
    ) == C_COVERAGE_MISSED


def test_clicking_possible_missed_gap_requests_focus(qapp):
    timeline = Timeline(variant=COMPACT)
    timeline.resize(1000, timeline.height())
    timeline.setRange(0, 100_000)
    timeline.set_coverage_segments([
        TimelineCoverage(20_000, 40_000, "possible_missed"),
    ])
    timeline.show()
    qapp.processEvents()
    activated = []
    timeline.coverageActivated.connect(
        lambda start, end, kind: activated.append((start, end, kind)))

    QTest.mouseClick(
        timeline,
        Qt.MouseButton.LeftButton,
        pos=QPoint(timeline._x_for(30_000), timeline._band_top() + 4),
    )

    assert activated == [(20_000, 40_000, "possible_missed")]
    timeline.hide()


def test_resolved_possible_missed_gap_is_restrained_and_not_clickable(qapp):
    timeline = Timeline(variant=COMPACT)
    timeline.resize(1000, timeline.height())
    timeline.setRange(0, 100_000)
    timeline.set_coverage_segments([{
        "start_ms": 20_000,
        "end_ms": 40_000,
        "kind": "possible_missed",
        "review_status": "dismissed",
    }])
    timeline.show()
    qapp.processEvents()
    activated = []
    timeline.coverageActivated.connect(
        lambda start, end, kind: activated.append((start, end, kind)))

    timeline.set_show_ignored_fragments(True)
    image = timeline._render_static().toImage()
    x = timeline._x_for(30_000)
    assert image.pixelColor(x, timeline._band_bottom() - 1) == \
        C_COVERAGE_RESOLVED
    QTest.mouseClick(
        timeline,
        Qt.MouseButton.LeftButton,
        pos=QPoint(x, timeline._band_top() + 4),
    )
    assert activated == []
    timeline.hide()


def test_gap_audit_priority_changes_emphasis_without_hiding_ranges(qapp):
    timeline = Timeline(variant=COMPACT)
    timeline.resize(1000, timeline.height())
    timeline.setRange(0, 100_000)
    timeline.set_coverage_segments([
        {
            "start_ms": 10_000,
            "end_ms": 30_000,
            "kind": "possible_missed",
            "review_status": "pending",
            "audit_level": "check_first",
        },
        {
            "start_ms": 60_000,
            "end_ms": 80_000,
            "kind": "possible_missed",
            "review_status": "pending",
            "audit_level": "low_signal",
        },
    ])

    timeline.set_show_ignored_fragments(True)
    image = timeline._render_static().toImage()
    bottom = timeline._band_bottom()
    assert image.pixelColor(timeline._x_for(20_000), bottom - 2) == \
        C_COVERAGE_MISSED_HIGH
    assert image.pixelColor(timeline._x_for(70_000), bottom - 1) == \
        C_COVERAGE_MISSED_LOW
    assert len(timeline.coverage_segments()) == 2


def test_focus_variant_magnifies_selected_neighborhood(qapp):
    timeline = Timeline(variant=FOCUS)
    timeline.resize(1000, timeline.height())
    timeline.setRange(0, 3_000_000)
    timeline.set_blocks(_overlapping_blocks())

    assert timeline.focus_range() is not None
    assert timeline.lane_count() == 2
    selected_width = timeline._x_for(1_030_000) - timeline._x_for(1_000_000)
    ordinary_width = timeline._x_for(300_000) - timeline._x_for(270_000)
    assert selected_width > ordinary_width * 10
    assert abs(timeline._ms_for(timeline._x_for(1_025_000)) - 1_025_000) < 5_000


@pytest.mark.parametrize("variant", [COMPACT, DUAL, FOCUS])
def test_video_player_builds_only_the_requested_surface(qapp, variant):
    owner, player = _player_harness(variant)
    try:
        # One timeline in every variant now. The overview is gated off
        # by SHOW_TIMELINE_OVERVIEW; Fit Game covers what it was for.
        assert player.timeline_overview is None
        assert player.slider.timeline_variant() == variant
        assert player.timeline_variant_label.text()
    finally:
        owner.deleteLater()


def test_player_opens_possible_missed_gap_into_editing_window(qapp):
    owner, player = _player_harness(COMPACT)
    try:
        player.slider.setRange(0, 300_000)
        player._coverage_activated(100_000, 110_000, "possible_missed")
        start, end = player.slider.visible_range()
        assert start < 100_000
        assert end > 110_000
        assert end - start < 300_000
    finally:
        owner.deleteLater()


def test_single_timeline_flag_can_restore_the_overview(qapp, monkeypatch):
    """The second surface is gated, not deleted - one flag brings it back."""
    from tapesift.ui_core import video_player as video_player_module

    monkeypatch.setattr(
        video_player_module, "SHOW_TIMELINE_OVERVIEW", True)
    owner, player = _player_harness(DUAL)
    try:
        assert player.timeline_overview is not None
    finally:
        owner.deleteLater()
