"""Pure tests for the authoritative source-time viewport model."""

from __future__ import annotations

import math

import pytest

from tapesift.ui_core.timeline_viewport import SourceTimeViewport


@pytest.fixture
def viewport() -> SourceTimeViewport:
    model = SourceTimeViewport()
    model.set_source_range(0, 100_000)
    return model


def test_fit_game_uses_the_complete_source(viewport):
    viewport.set_zoom_factor(4, 50_000)

    assert viewport.fit_game()
    assert viewport.visible_range() == (0, 100_000)
    assert viewport.zoom_factor == 1.0


def test_fit_play_includes_context_and_clamps_to_source(viewport):
    assert viewport.fit_range(
        2_000, 12_000,
        context_before_ms=10_000,
        context_after_ms=10_000,
    )

    assert viewport.visible_range() == (0, 22_000)


def test_invalid_fit_range_is_a_noop(viewport):
    before = viewport.visible_range()

    assert not viewport.fit_range(20_000, 20_000)
    assert viewport.visible_range() == before

def test_zoom_preserves_anchor_screen_ratio(viewport):
    anchor = 75_000
    before = viewport.time_to_position(anchor, 1001)

    viewport.set_zoom_factor(4, anchor)

    assert viewport.visible_range() == (56_250, 81_250)
    assert viewport.time_to_position(anchor, 1001) == before


def test_zoom_clamps_cleanly_at_source_beginning(viewport):
    viewport.set_zoom_factor(4, 0)

    assert viewport.visible_range() == (0, 25_000)


def test_zoom_clamps_cleanly_at_source_end(viewport):
    viewport.set_zoom_factor(4, 100_000)

    assert viewport.visible_range() == (75_000, 100_000)


def test_zoom_never_crosses_minimum_visible_duration():
    viewport = SourceTimeViewport(minimum_visible_duration_ms=15_000)
    viewport.set_source_range(0, 600_000)

    viewport.set_zoom_factor(10_000, 300_000)

    assert viewport.visible_duration_ms == 15_000


def test_pan_moves_only_viewport_and_clamps_at_both_edges(viewport):
    viewport.set_zoom_factor(4, 50_000)
    playhead = viewport.playhead_ms

    assert viewport.pan_by(-1_000_000)
    assert viewport.visible_range() == (0, 25_000)
    assert viewport.playhead_ms == playhead
    assert viewport.pan_by(1_000_000)
    assert viewport.visible_range() == (75_000, 100_000)
    assert viewport.playhead_ms == playhead


def test_pan_fraction_uses_current_visible_duration(viewport):
    viewport.set_zoom_factor(4, 50_000)

    viewport.pan_fraction(0.5)

    assert viewport.visible_range() == (50_000, 75_000)


def test_playhead_follow_recenters_only_after_leaving_view(viewport):
    viewport.set_playhead(50_000, reveal=False)
    viewport.set_zoom_factor(4, 50_000)

    changed, moved = viewport.set_playhead(60_000)
    assert changed
    assert not moved
    assert viewport.visible_range() == (37_500, 62_500)

    changed, moved = viewport.set_playhead(90_000)
    assert changed
    assert moved
    assert viewport.visible_range() == (75_000, 100_000)


def test_follow_can_be_disabled_without_losing_playhead(viewport):
    viewport.set_playhead(50_000, reveal=False)
    viewport.set_zoom_factor(4, 50_000)
    viewport.follow_playhead_enabled = False

    changed, moved = viewport.set_playhead(90_000)

    assert changed
    assert not moved
    assert viewport.playhead_ms == 90_000
    assert viewport.visible_range() == (37_500, 62_500)


def test_duration_change_fits_game_and_clamps_playhead(viewport):
    viewport.set_playhead(90_000, reveal=False)
    viewport.set_zoom_factor(4, 90_000)

    viewport.set_source_range(0, 60_000)

    assert viewport.source_range() == (0, 60_000)
    assert viewport.visible_range() == (0, 60_000)
    assert viewport.playhead_ms == 60_000


def test_source_switch_does_not_leak_previous_viewport():
    viewport = SourceTimeViewport()
    viewport.set_source_range(10_000, 110_000)
    viewport.set_zoom_factor(4, 60_000)

    viewport.set_source_range(500_000, 800_000)

    assert viewport.source_range() == (500_000, 800_000)
    assert viewport.visible_range() == (500_000, 800_000)
    assert viewport.playhead_ms == 500_000


@pytest.mark.parametrize(("time_ms", "expected_x"), [
    (0, 0),
    (50_000, 500),
    (100_000, 1000),
])
def test_time_to_position_mapping(viewport, time_ms, expected_x):
    assert viewport.time_to_position(time_ms, 1001) == expected_x


@pytest.mark.parametrize("time_ms", [0, 1, 12_345, 50_000, 99_999, 100_000])
def test_time_position_round_trip_is_within_one_pixel(
        viewport, time_ms):
    width = 1001
    x = viewport.time_to_position(time_ms, width)
    restored = viewport.position_to_time(x, width)
    ms_per_pixel = viewport.visible_duration_ms / (width - 1)

    assert abs(restored - time_ms) <= math.ceil(ms_per_pixel)


def test_zero_width_and_zero_duration_mapping_is_safe():
    viewport = SourceTimeViewport()
    viewport.set_source_range(0, 0)

    assert viewport.time_to_position(500_000, 0) == 0
    assert viewport.position_to_time(500, 0) == 0
    assert viewport.visible_range() == (0, 0)


def test_reversed_visible_range_still_preserves_invariants(viewport):
    viewport.set_visible_range(80_000, 20_000)

    start, end = viewport.visible_range()
    assert 0 <= start < end <= 100_000
    assert end - start == viewport.minimum_visible_duration_ms


def test_nonfinite_zoom_and_pan_fail_safe(viewport):
    viewport.set_zoom_factor(float("nan"), 50_000)
    before = viewport.visible_range()

    assert viewport.zoom_factor == 1.0
    assert not viewport.pan_fraction(float("inf"))
    assert viewport.visible_range() == before
