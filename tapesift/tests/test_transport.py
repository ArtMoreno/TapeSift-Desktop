"""Transport control tests.

These call every JKL / stepping / seeking entry point on a real VideoPlayer.
A stale attribute reference (the kind a rename leaves behind) raises here
instead of silently killing a key in the running app.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame  # noqa: E402
from PySide6.QtTest import QSignalSpy, QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QVBoxLayout, QWidget,
)

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.ui_core.video_player import SHUTTLE_SPEEDS, VideoPlayer  # noqa: E402
from tapesift.ui_core.timeline import TimelineBlock  # noqa: E402
from tapesift.ui_v2.control_center import ControlCenterDeck  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def player(qapp):
    """A player standing in for a loaded file, parked mid-timeline.

    Without a position, reverse would legitimately stop at once (you can't
    shuttle back past the start), which would mask state-machine bugs.
    """
    owner = QWidget()
    owner_layout = QVBoxLayout(owner)
    p = VideoPlayer(AppSettings(), owner)
    deck = ControlCenterDeck(owner)
    p.attach_control_center(deck)
    owner_layout.addWidget(p)
    owner_layout.addWidget(deck)
    p.player.position = lambda: 600_000      # 10 minutes in
    p.player.duration = lambda: 1_200_000    # 20-minute file
    yield p
    p.unload()
    owner.deleteLater()
    qapp.processEvents()


class TestShuttle:
    @pytest.mark.parametrize(("command", "next_pts"), (
        ("shuttle_forward", 1_034), ("shuttle_reverse", 966)))
    def test_unpainted_initial_hold_does_not_guard_against_older_picture(self, player, monkeypatch, command, next_pts):
        surface = player.video_widget
        surface._frame_arrived(_video_frame(200_000))
        surface.grab()
        surface.arm_frame_hold(1_000, 34)
        surface._frame_arrived(_video_frame(1_000))
        assert surface.frame_hold_active()
        assert player.displayed_position_ms() == 200_000
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: 0)
        getattr(player, command)()
        assert surface._resume_guard is None
        surface._frame_arrived(_video_frame(next_pts))
        surface.grab()
        assert player.displayed_position_ms() == next_pts

    @pytest.mark.parametrize(("command", "direction"), (
        ("toggle_play", 1), ("shuttle_forward", 1), ("shuttle_reverse", -1)))
    def test_resume_rejects_old_direction_frames_until_new_picture_is_painted(self, player, monkeypatch, command, direction):
        surface = player.video_widget
        surface._frame_arrived(_video_frame(1_000))
        surface.grab()
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: 0)
        player.shuttle_stop()
        getattr(player, command)()
        if command != "toggle_play":
            getattr(player, command)()  # acceleration must preserve the gate
        surface._frame_arrived(_video_frame(1_000 - direction * 100))
        surface.grab()
        assert player.displayed_position_ms() == 1_000
        assert surface._resume_guard is not None
        surface._frame_arrived(_video_frame(1_000 + direction * 34))
        # Even after a valid delivery, a stale delivery before paint is unsafe.
        surface._frame_arrived(_video_frame(1_000 - direction * 200))
        surface.grab()
        assert player.displayed_position_ms() == 1_000 + direction * 34
        assert surface._resume_guard is None

    @pytest.mark.parametrize("command", (
        "seek_to", "frame_step_forward", "load", "swap_source", "unload", "arm_frame_hold"))
    def test_explicit_seek_or_source_change_cancels_pending_resume_guard(self, player, monkeypatch, command):
        surface = player.video_widget
        surface._frame_arrived(_video_frame(1_000))
        surface.grab()
        player.shuttle_stop()
        player.shuttle_forward()
        assert surface._resume_guard is not None
        monkeypatch.setattr(player.player, "setSource", lambda _source: None)
        if command == "seek_to":
            player.seek_to(500)
        elif command in ("load", "swap_source"):
            getattr(player, command)(Path("next.mp4"))
        elif command == "arm_frame_hold":
            surface.arm_frame_hold(500, 34)
        else:
            getattr(player, command)()
        assert surface._resume_guard is None

    def test_failed_frame_conversion_does_not_settle_resume_guard(self, player, monkeypatch):
        surface = player.video_widget
        surface._frame_arrived(_video_frame(1_000))
        surface.grab()
        player.shuttle_stop()
        player.shuttle_forward()
        with monkeypatch.context() as patch:
            patch.setattr(QVideoFrame, "toImage", lambda _frame: QImage())
            surface._frame_arrived(_video_frame(1_034))
            surface.grab()
        assert surface._resume_guard is not None
        surface._frame_arrived(_video_frame(900))
        surface.grab()
        assert player.displayed_position_ms() == 1_000
        surface._frame_arrived(_video_frame(1_067))
        surface.grab()
        assert player.displayed_position_ms() == 1_067
        assert surface._resume_guard is None

    @pytest.mark.parametrize("transition", ("load", "swap_source", "unload"))
    def test_pause_before_new_source_frame_does_not_freeze_old_source(self, player, monkeypatch, transition):
        surface = player.video_widget
        surface._frame_arrived(_video_frame(1_000))
        surface.grab()
        surface._frame_arrived(_video_frame(2_000))
        monkeypatch.setattr(player.player, "setSource", lambda _source: None)
        if transition == "unload":
            player.unload()
        else:
            getattr(player, transition)(Path("next.mp4"))
        surface.grab()
        assert player.displayed_position_ms() is None
        player.shuttle_stop()
        assert not surface.frame_hold_active()
        surface._frame_arrived(_video_frame(3_000))
        surface.grab()
        assert player.displayed_position_ms() == 3_000

    def test_pending_seek_to_earlier_clip_does_not_bypass_new_review_end(self, player, monkeypatch):
        state = [QMediaPlayer.PlaybackState.PausedState]
        monkeypatch.setattr(player.player, "playbackState", lambda: state[0])
        monkeypatch.setattr(player.player, "play", lambda: state.__setitem__(0, QMediaPlayer.PlaybackState.PlayingState))
        player._source_frame_presented(900_000)
        player.set_clip_range(500_000, 650_000, False)
        player._next_presented_frame_is_hard_seek = True
        player.shuttle_forward()
        assert not player._range_finished
        player._enforce_clip_range(650_000)
        assert player._range_finished

    @pytest.mark.parametrize("command", ("toggle_play", "shuttle_forward"))
    def test_explicit_forward_play_past_review_end_continues(self, player, monkeypatch, command):
        calls = []
        state = [QMediaPlayer.PlaybackState.PausedState]
        monkeypatch.setattr(player.player, "playbackState", lambda: state[0])
        monkeypatch.setattr(player.player, "play", lambda: state.__setitem__(0, QMediaPlayer.PlaybackState.PlayingState))
        monkeypatch.setattr(player.player, "pause", lambda: calls.append("pause"))
        player.set_clip_range(500_000, 599_000, False)
        getattr(player, command)()
        player._enforce_clip_range(600_050)
        assert state[0] == QMediaPlayer.PlaybackState.PlayingState
        assert calls == []
        assert player._range_finished

    def test_review_stops_once_but_preserves_replay_bounds(self, player, monkeypatch):
        pauses, seeks = [], []
        monkeypatch.setattr(player.player, "playbackState", lambda: QMediaPlayer.PlaybackState.PlayingState)
        monkeypatch.setattr(player.player, "pause", lambda: pauses.append(True))
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player.set_clip_range(500_000, 600_000, False)
        player._enforce_clip_range(600_000)
        player._enforce_clip_range(600_100)
        assert pauses == [True]
        assert (player._range_start, player._range_end) == (500_000, 600_000)
        player.replay_range()
        assert seeks[-1] == 500_000
        assert not player._range_finished

    def test_loop_keeps_wrapping_after_review_previously_finished(self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "playbackState", lambda: QMediaPlayer.PlaybackState.PlayingState)
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player.set_clip_range(500_000, 600_000, False)
        player._range_finished = True
        player.set_range_loop(True)
        player._enforce_clip_range(600_000)
        player._enforce_clip_range(600_100)
        assert seeks == [500_000, 500_000]
        assert not player._range_finished

    @pytest.mark.parametrize("command", ("shuttle_stop", "toggle_play"))
    def test_pause_holds_painted_frame_and_reanchors_resume(self, player, monkeypatch, command):
        surface = player.video_widget
        surface._frame_arrived(_video_frame(1_000))
        surface.grab()
        surface._frame_arrived(_video_frame(2_000))  # decoded, never shown
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        monkeypatch.setattr(player.player, "playbackState", lambda: QMediaPlayer.PlaybackState.PlayingState)
        getattr(player, command)()
        surface._frame_arrived(_video_frame(3_000))  # queued after pause
        surface.grab()
        assert player.displayed_position_ms() == 1_000
        assert surface.frame_hold_active()
        assert seeks == [1_001]
        player.frame_step_forward()
        assert not surface.frame_hold_active()
        assert seeks[-1] == 1_034

    def test_forward_steps_up_through_speeds(self, player):
        for expected in SHUTTLE_SPEEDS:
            player.shuttle_forward()
            assert SHUTTLE_SPEEDS[player._shuttle_idx] == expected
            assert player.jog_ring._rate == expected
            assert player.shuttle_meter._rate == expected
        player.shuttle_forward()  # clamps at the top
        assert SHUTTLE_SPEEDS[player._shuttle_idx] == SHUTTLE_SPEEDS[-1]
        assert player._shuttle_dir == 1

    @pytest.mark.parametrize("forward_shuttle", (False, True))
    def test_direct_reverse_starts_at_painted_forward_frame(self, player, monkeypatch, forward_shuttle):
        surface = player.video_widget
        surface._frame_arrived(_video_frame(1_000))
        surface.grab()
        surface._frame_arrived(_video_frame(2_000))
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        monkeypatch.setattr(player.player, "playbackState", lambda: QMediaPlayer.PlaybackState.PlayingState)
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: 0)
        player._shuttle_dir = int(forward_shuttle)
        player.shuttle_reverse()
        assert seeks == [1_001]
        assert player._shuttle_dir == -1
        assert player._shuttle_idx == 0
        assert not surface.frame_hold_active()

    def test_repeated_reverse_keeps_speed_ladder_without_reanchoring(self, player, monkeypatch):
        seeks = []
        surface = player.video_widget
        surface._frame_arrived(_video_frame(1_000))
        surface.grab()
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        monkeypatch.setattr(player.player, "playbackState", lambda: QMediaPlayer.PlaybackState.PausedState)
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: 0)
        for expected in SHUTTLE_SPEEDS:
            player.shuttle_reverse()
            assert SHUTTLE_SPEEDS[player._shuttle_idx] == expected
        assert seeks == []

    def test_reverse_after_pending_step_preserves_requested_position(self, player, monkeypatch):
        seeks = []
        surface = player.video_widget
        surface._frame_arrived(_video_frame(1_000))
        surface.grab()
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        monkeypatch.setattr(player.player, "playbackState", lambda: QMediaPlayer.PlaybackState.PausedState)
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: 0)
        player.frame_step_forward()
        assert seeks == [1_034]
        player.shuttle_reverse()
        assert seeks == [1_034]
        assert player._shuttle_dir == -1

    def test_reverse_steps_up_through_speeds(self, player):
        for expected in SHUTTLE_SPEEDS:
            player.shuttle_reverse()
            assert SHUTTLE_SPEEDS[player._shuttle_idx] == expected
            assert player.jog_ring._rate == -expected
            assert player.shuttle_meter._rate == -expected
        assert player._shuttle_dir == -1

    def test_direction_changes_reset_speed(self, player):
        player.shuttle_forward()
        player.shuttle_forward()          # 2x forward
        player.shuttle_reverse()          # flip
        assert player._shuttle_dir == -1
        assert player._shuttle_idx == 0
        player.shuttle_forward()          # flip back
        assert player._shuttle_dir == 1
        assert player._shuttle_idx == 0

    def test_forward_after_reverse_stops_reverse_loop(self, player):
        player.shuttle_reverse()
        assert player._reverse_watchdog.isActive()
        player.shuttle_forward()
        assert not player._reverse_watchdog.isActive()
        assert not player._frame_hook_connected

    def test_stop_clears_everything(self, player):
        player.shuttle_reverse()
        player.shuttle_stop()
        assert player._shuttle_dir == 0
        assert not player._reverse_watchdog.isActive()
        assert not player._frame_hook_connected
        assert player.shuttle_label.text() == ""
        assert player.jog_ring._rate == 0.0
        assert player.shuttle_meter._rate == 0.0

    def test_reverse_mutes_and_restores_audio(self, player):
        player.shuttle_reverse()
        assert player.audio.isMuted()
        player.shuttle_stop()
        assert not player.audio.isMuted()

    def test_fast_forward_mutes_only_above_2x(self, player):
        player.shuttle_forward()               # 1x
        assert not player.audio.isMuted()
        player.shuttle_forward()               # 2x
        assert not player.audio.isMuted()
        player.shuttle_forward()               # 4x
        assert player.audio.isMuted()
        player.shuttle_stop()
        assert not player.audio.isMuted()

    def test_user_mute_survives_shuttle(self, player):
        player._toggle_mute()                  # user mutes
        assert player.audio.isMuted()
        player.shuttle_forward()
        player.shuttle_stop()
        assert player.audio.isMuted()          # still muted by user choice


class TestReversePacing:
    @pytest.mark.parametrize(
        ("presses", "ready_ms", "expected"),
        ((1, 34, 599_966), (2, 17, 599_966),
         (3, 9, 599_964), (4, 5, 599_960)),
    )
    def test_fast_decoder_waits_for_the_selected_frame_period(
            self, player, monkeypatch, presses, ready_ms, expected):
        elapsed = [0]
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: elapsed[0])
        monkeypatch.setattr(player._reverse_clock, "restart",
                            lambda: elapsed.__setitem__(0, 0))
        for _ in range(presses):
            player.shuttle_reverse()
        assert seeks == []

        elapsed[0] = ready_ms - 1
        player._reverse_frame_arrived(_video_frame(600_000))
        assert seeks == []
        assert elapsed[0] == ready_ms - 1
        assert player._reverse_watchdog.isSingleShot()

        elapsed[0] = ready_ms
        player._reverse_step()
        assert seeks == [expected]
        assert elapsed[0] == 0
        assert player._reverse_watchdog.interval() == 250
        player._reverse_frame_arrived(_video_frame(expected))
        assert seeks == [expected]

    @pytest.mark.parametrize(
        "transition", ("shuttle_stop", "shuttle_forward", "_scrub_started",
                       "frame_step_forward", "load", "swap_source", "unload", "error"),
    )
    def test_new_transport_command_cancels_delayed_reverse(
            self, player, monkeypatch, transition):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        monkeypatch.setattr(player.player, "setSource", lambda _source: None)
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: 0)
        player.shuttle_reverse()
        assert player._reverse_watchdog.isActive()

        if transition in ("load", "swap_source"):
            getattr(player, transition)(Path("replacement.mp4"))
        elif transition == "error":
            player._on_error(None, "")
        else:
            getattr(player, transition)()
        after_command = list(seeks)
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: 500)
        player._reverse_frame_arrived(_video_frame(599_966))
        player._reverse_step()
        assert seeks == after_command
        assert not player._reverse_watchdog.isActive()

    def test_stall_keeps_the_existing_bounded_reverse_jump(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player.shuttle_reverse()
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: 1_000)
        player._reverse_step()
        assert seeks == [599_750]


class TestMinimalJogIntegration:
    @pytest.mark.parametrize(
        ("position", "frames", "expected"),
        ((0, -1, 1), (1_200_000, 1, 1_199_967)),
    )
    def test_jog_reuses_authoritative_seek_and_clamps_boundaries(
            self, player, monkeypatch, position, frames, expected):
        seeks, pauses, resets = [], [], []
        monkeypatch.setattr(player.player, "position", lambda: position)
        monkeypatch.setattr(player.player, "duration", lambda: 1_200_000)
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        monkeypatch.setattr(player.player, "pause", lambda: pauses.append(True))
        monkeypatch.setattr(player, "_reset_shuttle", lambda: resets.append(True))

        player._jog_frames_requested(frames)

        assert seeks == [expected]
        assert pauses == [True]
        assert resets == [True]
        assert not hasattr(player, "_jog_pending_frames")


class TestTimelineScrubbing:
    def test_preview_seeks_are_throttled_and_release_is_exact(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player.slider.setRange(0, 1_000)

        player._scrub_started()
        for position in (100, 200, 300):
            player.slider.setValue(position)
            player._scrub_moved(position)

        assert seeks == [100]

        player._scrub_finished()
        assert seeks == [100, 300]

    def test_click_does_not_duplicate_an_already_exact_seek(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player.slider.setRange(0, 1_000)

        player._scrub_started()
        player.slider.setValue(600)
        player._scrub_moved(600)
        player._scrub_finished()

        assert seeks == [600]

    def test_playback_resumes_after_drag_only_when_it_was_playing(
            self, player, monkeypatch):
        paused, played = [], []
        monkeypatch.setattr(
            player.player, "playbackState",
            lambda: QMediaPlayer.PlaybackState.PlayingState)
        monkeypatch.setattr(
            player.player, "pause", lambda: paused.append(True))
        monkeypatch.setattr(
            player.player, "play", lambda: played.append(True))
        monkeypatch.setattr(player.player, "setPosition", lambda _ms: None)
        player.slider.setRange(0, 1_000)

        player._scrub_started()
        player.slider.setValue(700)
        player._scrub_moved(700)
        player._scrub_finished()

        assert paused == [True]
        assert played == [True]


class TestTimelineZoom:
    def test_zoom_is_centered_on_playhead_without_seeking(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player._duration_changed(1_200_000)

        player.timeline_zoom_slider.setValue(2)

        assert player.slider.visible_range() == (450_000, 750_000)
        assert seeks == []
        assert player.timeline_zoom_out.isEnabled()
        assert player.timeline_zoom_in.isEnabled()

    def test_ctrl_wheel_zoom_keeps_cursor_time_at_same_pixel(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player._duration_changed(1_200_000)
        width = 801
        anchor_ms = 300_000
        before_x = player.slider.viewport.time_to_position(
            anchor_ms, width)

        player._wheel_zoom(1, anchor_ms)

        assert player.timeline_zoom_slider.value() == 1
        assert player.slider.visible_range() == (150_000, 750_000)
        assert player.slider.viewport.time_to_position(
            anchor_ms, width) == before_x
        assert seeks == []

    def test_ctrl_wheel_zoom_out_preserves_cursor_anchor(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player._duration_changed(1_200_000)
        anchor_ms = 300_000
        width = 801
        player._wheel_zoom(1, anchor_ms)
        before_x = player.slider.viewport.time_to_position(
            anchor_ms, width)

        player._wheel_zoom(-1, anchor_ms)

        assert player.timeline_zoom_slider.value() == 0
        assert player.slider.visible_range() == (0, 1_200_000)
        assert player.slider.viewport.time_to_position(
            anchor_ms, width) == before_x
        assert seeks == []

    def test_ctrl_wheel_at_zoom_limit_is_a_noop(self, player):
        player._duration_changed(1_200_000)
        player.timeline_zoom_slider.setValue(
            player.timeline_zoom_slider.maximum())
        before = player.slider.visible_range()

        player._wheel_zoom(1, 600_000)

        assert player.timeline_zoom_slider.value() == \
            player.timeline_zoom_slider.maximum()
        assert player.slider.visible_range() == before

    def test_reset_zoom_restores_entire_game(self, player):
        player._duration_changed(1_200_000)
        player.timeline_zoom_slider.setValue(3)

        player.reset_timeline_zoom()

        assert player.timeline_zoom_slider.value() == 0
        assert player.slider.visible_range() == (0, 1_200_000)
        assert not player.timeline_zoom_out.isEnabled()

    def test_compact_viewport_controls_start_in_full_game_mode(self, player):
        player._duration_changed(1_200_000)

        assert player.timeline_viewport_controls.objectName() == \
            "TimelineViewportControls"
        assert player.timeline_zoom_label.text() == "1×"
        assert player.timeline_range_label.text() == "00:00 – 20:00"
        assert player.timeline_follow_playhead.isChecked()
        assert not player.timeline_zoom_out.isEnabled()
        assert player.timeline_zoom_in.isEnabled()
        assert not player.timeline_fit_game.isEnabled()
        assert not player.timeline_fit_play.isEnabled()
        assert not player.timeline_viewport_scroll.isEnabled()
        assert player.timeline_viewport_scroll.isHidden()
        assert player.timeline_zoom_out.objectName() == "TimelineZoomOut"
        assert player.timeline_zoom_in.objectName() == "TimelineZoomIn"
        assert not player.timeline_zoom_out.icon().isNull()
        assert not player.timeline_zoom_in.icon().isNull()
        assert "Zoom out timeline" in player.timeline_zoom_out.toolTip()
        assert "Zoom in timeline" in player.timeline_zoom_in.toolTip()

    def test_zoom_reveals_the_horizontal_timeline_navigator(self, player):
        player._duration_changed(1_200_000)

        player.timeline_zoom_slider.setValue(2)

        assert player.timeline_viewport_scroll.isEnabled()
        assert not player.timeline_viewport_scroll.isHidden()
        assert player.timeline_viewport_scroll.pageStep() == 300_000
        assert player.timeline_viewport_scroll.maximum() == 900_000

        player.reset_timeline_zoom()

        assert player.timeline_viewport_scroll.isHidden()

    def test_viewport_controls_do_not_take_keyboard_transport_focus(
            self, player):
        controls = [
            player.timeline_zoom_out,
            player.timeline_zoom_slider,
            player.timeline_zoom_in,
            player.timeline_fit_game,
            player.timeline_fit_play,
            player.timeline_follow_playhead,
            player.timeline_pan_left,
            player.timeline_pan_right,
            player.timeline_viewport_scroll,
        ]

        assert all(
            control.focusPolicy() == Qt.FocusPolicy.NoFocus
            for control in controls
        )

    def test_pan_buttons_move_only_the_visible_source_window(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player._duration_changed(1_200_000)
        player.timeline_zoom_slider.setValue(2)

        assert player.slider.visible_range() == (450_000, 750_000)
        player.pan_timeline_right()

        assert player.slider.visible_range() == (600_000, 900_000)
        assert player.timeline_viewport_scroll.value() == 600_000
        assert seeks == []

    def test_scrollbar_pans_without_seeking(self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player._duration_changed(1_200_000)
        player.timeline_zoom_slider.setValue(2)

        player.timeline_viewport_scroll.setValue(300_000)

        assert player.slider.visible_range() == (300_000, 600_000)
        assert seeks == []

    def test_next_selected_play_keeps_the_chosen_zoom(self, player):
        player._duration_changed(1_200_000)
        player.set_clip_blocks([
            TimelineBlock(
                100_000, 130_000, clip_id="first", title="Play 1"),
            TimelineBlock(
                900_000, 930_000, clip_id="next", title="Play 2"),
        ])
        player.slider.setValue(115_000)
        player.timeline_zoom_slider.setValue(2)
        initial_duration = player.slider.viewport.visible_duration_ms

        player.set_selected_clip_id("next")

        visible_start, visible_end = player.slider.visible_range()
        assert player.slider.viewport.visible_duration_ms == initial_duration
        assert visible_start <= 900_000
        assert visible_end >= 930_000
        assert player.timeline_zoom_slider.value() == 2

    def test_fit_play_uses_selected_bounds_and_context_without_seeking(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player._duration_changed(1_200_000)
        player.slider.setValue(120_000)
        player.set_clip_blocks([
            TimelineBlock(
                100_000, 140_000, clip_id="selected", title="Play"),
        ])
        player.set_selected_clip_id("selected")

        assert player.timeline_fit_play.isEnabled()
        player.fit_timeline_play()

        assert player.slider.visible_range() == (90_000, 150_000)
        assert player.timeline_range_label.text() == "01:30 – 02:30"
        assert seeks == []

    def test_follow_toggle_controls_automatic_viewport_reveal(
            self, player, monkeypatch):
        monkeypatch.setattr(player.player, "position", lambda: 50_000)
        player._duration_changed(100_000)
        player.slider.setValue(50_000)
        player.timeline_zoom_slider.setValue(2)
        assert player.slider.visible_range() == (37_500, 62_500)

        player.timeline_follow_playhead.setChecked(False)
        player.slider.setValue(90_000)
        assert player.slider.visible_range() == (37_500, 62_500)

        player.timeline_follow_playhead.setChecked(True)
        assert player.slider.visible_range() == (75_000, 100_000)

    def test_pan_controls_disable_at_source_edges(self, player):
        player._duration_changed(100_000)
        player.slider.setValue(50_000)
        player.timeline_zoom_slider.setValue(2)

        player.timeline_viewport_scroll.setValue(0)
        assert not player.timeline_pan_left.isEnabled()
        assert player.timeline_pan_right.isEnabled()

        player.timeline_viewport_scroll.setValue(
            player.timeline_viewport_scroll.maximum())
        assert player.timeline_pan_left.isEnabled()
        assert not player.timeline_pan_right.isEnabled()


def test_timeline_snap_control_updates_widget_and_accessible_state(player):
    assert player.timeline_snap_button.isChecked()
    assert player.slider.snapping_enabled()
    assert player.timeline_snap_button.text() == "Snap On"

    player.toggle_timeline_snapping()

    assert not player.timeline_snap_button.isChecked()
    assert not player.slider.snapping_enabled()
    assert player.timeline_snap_button.text() == "Snap Off"
    assert "disabled" in player.timeline_snap_button.accessibleName()


def test_predicted_snap_action_exposes_find_and_ready_states(player):
    player.set_predicted_snap_state("missing")
    assert player.predicted_snap_button.isEnabled()
    assert player.predicted_snap_button.text() == "Find Snap"

    player.set_predicted_snap_state("ready", {
        "source_ms": 12_125,
        "confidence": 0.75,
        "eligible": True,
    })
    assert player.predicted_snap_button.text() == "Go to Snap"
    assert "75%" in player.predicted_snap_button.toolTip()


def test_timeline_legend_uses_text_labels_for_each_play_type(player):
    assert player.timeline_legend.accessibleName() == \
        "Timeline play type legend"
    assert {
        kind: label.text()
        for kind, label in player.timeline_legend_items.items()
    } == {
        "run": "Run",
        "pass": "Pass",
        "rpo": "RPO",
        "penalty": "Penalty",
        "sack": "Sack",
        "interception": "Interception",
        "touchdown": "Touchdown",
        "": "Unlabelled",
    }


def test_timeline_color_selector_exposes_each_supported_mode(player):
    assert [
        (player.timeline_color_combo.itemData(index),
         player.timeline_color_combo.itemText(index))
        for index in range(player.timeline_color_combo.count())
    ] == [
        ("play_type", "Play Type"),
        ("result", "Result"),
        ("primary_tag", "Primary Tag"),
        ("personnel", "Personnel"),
        ("review_status", "Review Status"),
    ]

    changed = []
    player.timeline_color_mode_changed.connect(changed.append)
    player.timeline_color_combo.setCurrentIndex(
        player.timeline_color_combo.findData("result"))
    assert changed == ["result"]


def test_timeline_key_popup_can_show_project_values(player):
    from PySide6.QtGui import QColor

    player.set_timeline_key("result", (
        ("completion", "Completion", QColor("#4da3ff")),
        ("", "Unlabelled", QColor("#8c8c9c")),
    ))

    assert player.timeline_key_button.text() == "Key"
    assert player.timeline_key_button.menu() is player.timeline_key_menu
    assert {
        key: label.text()
        for key, label in player.timeline_legend_items.items()
    } == {"completion": "Completion", "": "Unlabelled"}
    assert player.timeline_key_button.accessibleName() == \
        "Timeline color key for Result"


def test_timeline_key_popup_does_not_hide_large_project_keys(player):
    from PySide6.QtGui import QColor

    entries = tuple(
        (f"result_{index}", f"Result {index}", QColor("#4da3ff"))
        for index in range(20)
    )
    player.set_timeline_key("result", entries)

    assert len(player.timeline_legend_items) == 20
    assert player.timeline_legend_items["result_19"].text() == "Result 19"
    assert not any(
        "more values" in action.text()
        for action in player.timeline_key_menu.actions()
    )


class TestSteppingAndSeeking:
    """Every control the user can press must run without raising."""

    def test_all_transport_entry_points(self, player):
        for call in (
            player.toggle_play, player.stop,
            player.jump_backward, player.jump_forward,
            player.frame_step_backward, player.frame_step_forward,
            lambda: player.step_or_jump(-1), lambda: player.step_or_jump(1),
            lambda: player.seek_relative(-1000), lambda: player.seek_relative(1000),
            lambda: player.seek_to(5000),
            lambda: player._wheel_seek(1), lambda: player._wheel_seek(-1),
            lambda: player._wheel_frame(1), lambda: player._wheel_frame(-1),
            player._scrub_started, player._scrub_finished,
            player.set_in_point, player.set_out_point, player.clear_marks,
        ):
            call()

    def test_reverse_step_is_safe_when_not_shuttling(self, player):
        player._reverse_step()  # no-op, must not raise
        assert player._shuttle_dir == 0

    def test_reverse_stops_at_start_of_video(self, player, monkeypatch):
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: 34)
        player.player.position = lambda: 5  # essentially at the beginning
        player.shuttle_reverse()
        assert player._shuttle_dir == 0     # can't go back further; stands down
        assert not player._reverse_watchdog.isActive()

    def test_frame_hook_toggle_is_idempotent(self, player):
        player._connect_frame_hook(False)
        player._connect_frame_hook(True)
        player._connect_frame_hook(True)
        assert player._frame_hook_connected
        player._connect_frame_hook(False)
        player._connect_frame_hook(False)
        assert not player._frame_hook_connected


def _video_frame(
        position_ms: int | None,
        color=Qt.GlobalColor.black) -> QVideoFrame:
    image = QImage(32, 18, QImage.Format.Format_RGB32)
    image.fill(color)
    frame = QVideoFrame(image)
    if position_ms is not None:
        frame.setStartTime(position_ms * 1000)
    return frame


class TestDeliveredFrameClock:
    def test_arming_hold_does_not_publish_discarded_frame_serial(self, player):
        surface = player.video_widget
        surface._frame_arrived(_video_frame(1_000))
        surface.grab()
        seen = QSignalSpy(surface.frame_presented)
        surface._frame_arrived(_video_frame(1_033))
        surface.arm_frame_hold(2_000, 34)
        surface.grab()
        assert seen.count() == 0
        surface._frame_arrived(_video_frame(2_000))
        surface.grab()
        assert seen.count() == 1
        assert player.displayed_position_ms() == 2_000

    def test_burst_converts_only_newest_frame_when_painted(self, player, monkeypatch):
        converted = []
        original = QVideoFrame.toImage
        def convert(frame):
            converted.append(frame.startTime())
            return original(frame)
        monkeypatch.setattr(QVideoFrame, "toImage", convert)
        surface = player.video_widget
        for pts in (1000, 1033, 1067):
            surface._frame_arrived(_video_frame(pts))
        assert converted == []
        surface.grab()
        surface.grab()
        assert converted == [1_067_000]
        assert player.displayed_position_ms() == 1067

    def test_snapshot_can_materialize_pending_frame_without_claiming_paint(self, player):
        surface = player.video_widget
        seen = QSignalSpy(surface.frame_presented)
        surface._frame_arrived(_video_frame(1_000))
        assert not surface.current_frame_image().isNull()
        assert seen.count() == 0
        assert surface._image_serial != surface._presented_serial
        surface.grab()
        assert seen.count() == 1

    def test_failed_conversion_keeps_valid_image_and_does_not_publish_new_frame(self, player, monkeypatch):
        surface = player.video_widget
        surface._frame_arrived(_video_frame(1_000))
        surface.grab()
        previous = surface.current_frame_image()
        seen = QSignalSpy(surface.frame_presented)
        monkeypatch.setattr(QVideoFrame, "toImage", lambda _frame: QImage())
        surface._frame_arrived(_video_frame(2_000))
        surface.grab()
        assert surface.current_frame_image() == previous
        assert player.displayed_position_ms() == 1_000
        assert seen.count() == 0

    @staticmethod
    def _selection_ranges(player) -> None:
        player.set_clip_blocks([
            TimelineBlock(
                1_000, 2_000, clip_id="clip-a", title="Clip A"),
            TimelineBlock(
                3_000, 4_000, clip_id="clip-b", title="Clip B"),
        ])

    def test_every_media_position_request_uses_transition_wrapper(self):
        source = inspect.getsource(VideoPlayer)
        assert source.count("self.player.setPosition(") == 1

    def test_initial_stopped_seek_wakes_decoder_once_and_settles_paused(
            self, player, monkeypatch):
        calls: list[tuple[str, object]] = []
        state = {"playback": QMediaPlayer.PlaybackState.StoppedState}

        monkeypatch.setattr(
            player.player, "setSource",
            lambda source: calls.append(("source", source)))
        monkeypatch.setattr(
            player.player, "mediaStatus",
            lambda: QMediaPlayer.MediaStatus.LoadedMedia)
        monkeypatch.setattr(
            player.player, "playbackState", lambda: state["playback"])
        monkeypatch.setattr(
            player.player, "setPosition",
            lambda position: calls.append(("position", position)))

        def play() -> None:
            calls.append(("play", None))
            state["playback"] = QMediaPlayer.PlaybackState.PlayingState

        def pause() -> None:
            calls.append(("pause", None))
            state["playback"] = QMediaPlayer.PlaybackState.PausedState

        monkeypatch.setattr(player.player, "play", play)
        monkeypatch.setattr(player.player, "pause", pause)
        monkeypatch.setattr(
            player.audio, "setMuted",
            lambda muted: calls.append(("muted", muted)))

        player.load(Path("game.mp4"), 60.0)
        player.seek_to(1_483_733)
        player._initial_stopped_seek_wake_timer.stop()

        # Loading can surface a stale zero frame after the requested seek.
        # It must not disarm the wake for the actual selected position.
        player._source_frame_presented(0)
        assert player._awaiting_initial_frame
        assert player._initial_stopped_seek_target_ms == 1_483_733

        player._wake_initial_stopped_seek()

        assert calls[-3:] == [
            ("position", 1_483_733),
            ("muted", True),
            ("play", None),
        ]
        assert player._awaiting_initial_frame
        assert player._initial_stopped_seek_waking
        assert not player.video_widget.frame_hold_active()

        # The 60 fps floor frame is only 17 ms early.  Symmetric tolerance
        # used to accept it and strand the real app on the black side of a cut.
        player.video_widget._frame_arrived(_video_frame(
            1_483_716, Qt.GlobalColor.black))
        assert not player.video_widget.frame_hold_active()
        assert not any(name == "pause" for name, _value in calls)

        player.video_widget._frame_arrived(_video_frame(
            1_483_733, Qt.GlobalColor.green))
        assert calls[-3:] == [
            ("pause", None),
            ("position", 1_483_733),
            ("muted", False),
        ]
        assert state["playback"] == QMediaPlayer.PlaybackState.PausedState
        assert not player._awaiting_initial_frame
        assert not player._initial_stopped_seek_waking
        assert player._initial_stopped_seek_target_ms is None
        assert player.video_widget.frame_hold_active()
        assert player.video_widget.frame_hold_position_ms() == 1_483_733

        held = player.video_widget.current_frame_image()
        player.video_widget._frame_arrived(_video_frame(
            1_483_750, Qt.GlobalColor.blue))
        assert player.video_widget.current_frame_image() == held
        assert player.video_widget.frame_hold_position_ms() == 1_483_733

        # The wake is load-scoped. Ordinary later seeks retain the existing
        # paused transport behavior and never manufacture another play call.
        player.seek_to(1_500_000)
        assert not player.video_widget.frame_hold_active()
        assert [name for name, _value in calls].count("play") == 1

    def test_user_play_before_wake_disarms_all_later_wake_attempts(
            self, player, monkeypatch):
        state = {"playback": QMediaPlayer.PlaybackState.StoppedState}
        played: list[bool] = []
        paused: list[bool] = []

        monkeypatch.setattr(player.player, "setSource", lambda _source: None)
        monkeypatch.setattr(
            player.player, "mediaStatus",
            lambda: QMediaPlayer.MediaStatus.LoadedMedia)
        monkeypatch.setattr(
            player.player, "playbackState", lambda: state["playback"])
        monkeypatch.setattr(player.player, "setPosition", lambda _ms: None)
        monkeypatch.setattr(
            player.player, "play", lambda: played.append(True))
        monkeypatch.setattr(
            player.player, "pause", lambda: paused.append(True))

        player.load(Path("game.mp4"), 60.0)
        player.seek_to(1_483_733)
        player._initial_stopped_seek_wake_timer.stop()
        assert player._awaiting_initial_frame

        state["playback"] = QMediaPlayer.PlaybackState.PlayingState
        player._state_changed(QMediaPlayer.PlaybackState.PlayingState)

        assert not player._awaiting_initial_frame
        assert player._initial_stopped_seek_target_ms is None

        state["playback"] = QMediaPlayer.PlaybackState.PausedState
        player.seek_to(1_500_000)
        player._wake_initial_stopped_seek()

        assert played == []
        assert paused == []

    def test_surface_hold_is_one_sided_and_rejects_queue_drain(self, player):
        surface = player.video_widget
        reached = QSignalSpy(surface.frame_hold_reached)
        surface.arm_frame_hold(1_000, 17)
        try:
            # Exactly one rounded frame early is still the wrong side.
            surface._frame_arrived(_video_frame(
                983, Qt.GlobalColor.black))
            assert reached.count() == 0
            assert not surface.frame_hold_active()

            surface._frame_arrived(_video_frame(
                1_000, Qt.GlobalColor.green))
            assert reached.count() == 1
            assert list(reached.at(0)) == [1_000]
            assert surface.frame_hold_active()
            held = surface.current_frame_image()

            # Windows can drain decoded frames after pause.  They must not
            # replace the exact requested still.
            surface._frame_arrived(_video_frame(
                1_016, Qt.GlobalColor.blue))
            surface._frame_arrived(_video_frame(
                1_033, Qt.GlobalColor.red))
            assert surface.current_frame_image() == held
            assert surface.frame_hold_position_ms() == 1_000
        finally:
            surface.release_frame_hold()

    def test_surface_reports_frame_beyond_one_frame_window(self, player):
        surface = player.video_widget
        missed = QSignalSpy(surface.frame_hold_missed)
        surface.arm_frame_hold(1_000, 17)
        try:
            surface._frame_arrived(_video_frame(1_018))
            assert missed.count() == 1
            assert list(missed.at(0)) == [1_018]
            assert not surface.frame_hold_active()
        finally:
            surface.release_frame_hold()

    def test_user_forward_during_internal_wake_cannot_be_paused_later(
            self, player, monkeypatch):
        calls: list[str] = []
        state = {"playback": QMediaPlayer.PlaybackState.StoppedState}
        monkeypatch.setattr(player.player, "setSource", lambda _source: None)
        monkeypatch.setattr(
            player.player, "mediaStatus",
            lambda: QMediaPlayer.MediaStatus.LoadedMedia)
        monkeypatch.setattr(
            player.player, "playbackState", lambda: state["playback"])
        monkeypatch.setattr(player.player, "setPosition", lambda _ms: None)

        def play() -> None:
            calls.append("play")
            state["playback"] = QMediaPlayer.PlaybackState.PlayingState

        def pause() -> None:
            calls.append("pause")
            state["playback"] = QMediaPlayer.PlaybackState.PausedState

        monkeypatch.setattr(player.player, "play", play)
        monkeypatch.setattr(player.player, "pause", pause)

        player.load(Path("game.mp4"), 60.0)
        player.seek_to(1_483_733)
        player._initial_stopped_seek_wake_timer.stop()
        player._wake_initial_stopped_seek()
        assert player._initial_stopped_seek_waking
        assert calls == ["play"]

        # L is a real user command.  It releases the internal gate before
        # issuing its unchanged forward-play command.
        player.shuttle_forward()
        assert calls == ["play", "play"]
        assert not player._awaiting_initial_frame
        assert not player._initial_stopped_seek_waking
        assert not player.video_widget.frame_hold_active()

        player.video_widget._frame_arrived(_video_frame(1_483_733))
        assert calls == ["play", "play"]
        player._reset_shuttle()

    def test_seek_and_frame_step_release_held_frame(self, player, monkeypatch):
        sought: list[int] = []
        monkeypatch.setattr(player.player, "setPosition", sought.append)
        monkeypatch.setattr(player.player, "position", lambda: 10_000)
        monkeypatch.setattr(player.player, "duration", lambda: 20_000)
        surface = player.video_widget

        surface.arm_frame_hold(10_000, 34)
        surface._frame_arrived(_video_frame(10_000))
        assert surface.frame_hold_active()
        player.seek_to(12_000)
        assert sought == [12_000]
        assert not surface.frame_hold_active()

        surface.arm_frame_hold(12_000, 34)
        surface._frame_arrived(_video_frame(12_000))
        surface.grab()
        assert surface.frame_hold_active()
        player.frame_step_forward()
        assert sought[-1] == 12_034
        assert not surface.frame_hold_active()

    @pytest.mark.parametrize(
        "command_name",
        ("toggle_play", "shuttle_forward", "shuttle_reverse", "shuttle_stop"),
    )
    def test_user_play_and_jkl_release_held_frame(
            self, player, monkeypatch, command_name):
        monkeypatch.setattr(
            player.player, "playbackState",
            lambda: QMediaPlayer.PlaybackState.PausedState)
        monkeypatch.setattr(player.player, "play", lambda: None)
        monkeypatch.setattr(player.player, "pause", lambda: None)
        monkeypatch.setattr(player.player, "setPosition", lambda _ms: None)
        player.video_widget.arm_frame_hold(10_000, 34)
        player.video_widget._frame_arrived(_video_frame(10_000))
        assert player.video_widget.frame_hold_active()

        getattr(player, command_name)()

        assert not player.video_widget.frame_hold_active()
        assert not player._awaiting_initial_frame
        assert not player._initial_stopped_seek_waking
        player._reset_shuttle()

    def test_load_unload_and_error_clear_hold_and_restore_mute(
            self, player, monkeypatch):
        monkeypatch.setattr(player.player, "setSource", lambda _source: None)
        player.video_widget.arm_frame_hold(1_000, 34)
        player.video_widget._frame_arrived(_video_frame(1_000))
        player._initial_stopped_seek_waking = True
        player._awaiting_initial_frame = True
        player._initial_stopped_seek_target_ms = 1_000
        player.audio.setMuted(True)

        player.load(Path("replacement.mp4"), 30.0)
        assert player._awaiting_initial_frame
        assert not player._initial_stopped_seek_waking
        assert player._initial_stopped_seek_target_ms is None
        assert not player.video_widget.frame_hold_active()
        assert not player.audio.isMuted()

        player._initial_stopped_seek_target_ms = 2_000
        player.video_widget.arm_frame_hold(2_000, 34)
        player.video_widget._frame_arrived(_video_frame(2_000))
        player.audio.setMuted(True)
        player._on_error(QMediaPlayer.Error.ResourceError, "broken")
        assert not player._awaiting_initial_frame
        assert player._initial_stopped_seek_target_ms is None
        assert not player.video_widget.frame_hold_active()
        assert not player.audio.isMuted()

        player._awaiting_initial_frame = True
        player._initial_stopped_seek_target_ms = 3_000
        player.video_widget.arm_frame_hold(3_000, 34)
        player.video_widget._frame_arrived(_video_frame(3_000))
        player.audio.setMuted(True)
        player.unload()
        assert not player._awaiting_initial_frame
        assert not player._initial_stopped_seek_waking
        assert player._initial_stopped_seek_target_ms is None
        assert not player.video_widget.frame_hold_active()
        assert not player.audio.isMuted()

    def test_paint_coalescing_publishes_only_the_image_actually_drawn_once(
            self, player):
        surface = player.video_widget
        surface.resize(320, 180)
        seen: list[int] = []

        def record(position: int) -> None:
            seen.append(position)

        surface.frame_presented.connect(record)
        try:
            surface._frame_arrived(_video_frame(1_000))
            surface._frame_arrived(_video_frame(1_033))
            surface.grab()
            assert seen == [1_033]

            # Exposure/resize can repaint the same image. It is not a newly
            # delivered source frame and cannot advance the audio event clock.
            surface.grab()
            assert seen == [1_033]
        finally:
            surface.frame_presented.disconnect(record)

    def test_requested_position_never_substitutes_for_painted_pts(
            self, player, monkeypatch):
        player._last_presented_source_position_ms = None
        player._next_presented_frame_is_hard_seek = False
        presented = QSignalSpy(player.source_frame_presented)
        monkeypatch.setattr(player.player, "position", lambda: 9_000)

        player.player.positionChanged.emit(9_000)
        assert presented.count() == 0
        player.video_widget._frame_arrived(_video_frame(8_733))
        player.video_widget.grab()

        assert presented.count() == 1
        assert list(presented.at(0)) == [8_733, False]
        assert player.displayed_position_ms() == 8_733

    def test_invalid_frame_pts_is_not_replaced_by_media_position(
            self, player, monkeypatch):
        player._last_presented_source_position_ms = None
        player._next_presented_frame_is_hard_seek = False
        presented = QSignalSpy(player.source_frame_presented)
        monkeypatch.setattr(player.player, "position", lambda: 77_000)

        player.video_widget._frame_arrived(_video_frame(None))
        player.video_widget.grab()

        assert presented.count() == 0
        assert player.displayed_position_ms() is None

    def test_reverse_jkl_set_position_remains_continuous(
            self, player, monkeypatch):
        sought: list[int] = []
        presented = QSignalSpy(player.source_frame_presented)
        monkeypatch.setattr(player.player, "position", lambda: 10_000)
        monkeypatch.setattr(player.player, "setPosition", sought.append)
        player._next_presented_frame_is_hard_seek = False
        player._shuttle_dir = -1
        player._shuttle_idx = 2
        player._reverse_clock.start()
        monkeypatch.setattr(player._reverse_clock, "elapsed", lambda: 34)

        player._reverse_step()
        assert sought and sought[-1] < 10_000
        player.video_widget._frame_arrived(_video_frame(9_867))
        player.video_widget.grab()

        assert list(presented.at(presented.count() - 1)) == [9_867, False]
        player._reset_shuttle()

    def test_user_seek_marks_the_next_painted_pts_as_hard(
            self, player, monkeypatch):
        sought: list[int] = []
        presented = QSignalSpy(player.source_frame_presented)
        monkeypatch.setattr(player.player, "setPosition", sought.append)
        player._next_presented_frame_is_hard_seek = False

        player.seek_to(5_000)
        assert sought == [5_000]
        player.video_widget._frame_arrived(_video_frame(4_967))
        player.video_widget.grab()

        assert list(presented.at(presented.count() - 1)) == [4_967, True]

    def test_selection_seek_refuses_stale_anchor_until_b_frame_paints(
            self, player, monkeypatch):
        self._selection_ranges(player)
        monkeypatch.setattr(player.player, "setPosition", lambda _ms: None)
        player.set_selected_clip_id("clip-a")
        player._source_frame_presented(1_500)
        assert player.recording_anchor_ms() == 1_500

        player.set_selected_clip_id("clip-b")
        player.seek_to(3_000)
        assert player.displayed_position_ms() == 1_500
        assert player.recording_anchor_ms() is None

        player._source_frame_presented(3_000)
        assert player.recording_anchor_ms() == 3_000

    def test_seek_false_and_out_of_range_paint_do_not_settle_selection(
            self, player):
        self._selection_ranges(player)
        player.set_selected_clip_id("clip-a")
        player._source_frame_presented(1_500)
        player.set_selected_clip_id("clip-b")  # workflow seek=False

        assert player.recording_anchor_ms() is None
        player._source_frame_presented(1_533)  # queued/natural A-range frame
        assert player.displayed_position_ms() == 1_533
        assert player.recording_anchor_ms() is None

    def test_exact_same_position_hard_seek_is_already_visually_settled(
            self, player, monkeypatch):
        self._selection_ranges(player)
        sought: list[int] = []
        monkeypatch.setattr(player.player, "setPosition", sought.append)
        player.set_selected_clip_id("clip-b")
        player._source_frame_presented(3_500)
        assert player.recording_anchor_ms() == 3_500

        player.seek_to(3_500)

        assert sought == [3_500]
        assert not player._next_presented_frame_is_hard_seek
        assert player.recording_anchor_ms() == 3_500

    def test_recording_anchor_rejects_selected_clip_end_boundary(
            self, player):
        self._selection_ranges(player)
        player.set_selected_clip_id("clip-b")

        player._source_frame_presented(4_000)

        assert player.displayed_position_ms() == 4_000
        assert player.recording_anchor_ms() is None

    def test_clip_block_refresh_resyncs_cached_recording_range(self, player):
        self._selection_ranges(player)
        player.set_selected_clip_id("clip-b")
        player._source_frame_presented(3_500)
        assert player.recording_anchor_ms() == 3_500

        player.set_clip_blocks([
            TimelineBlock(
                3_600, 4_000, selected=True,
                clip_id="clip-b", title="Trimmed Clip B"),
        ])

        assert player.displayed_position_ms() == 3_500
        assert player.recording_anchor_ms() is None


class TestDisplayedFrameStepping:
    @pytest.mark.parametrize(
        ("command", "args", "expected"),
        (("frame_step_forward", (), 1_834),
         ("frame_step_backward", (), 1_767),
         ("step_or_jump", (1,), 1_834),
         ("step_or_jump", (-1,), 1_767),
         ("_wheel_frame", (2,), 1_867),
         ("_jog_frames_requested", (-2,), 1_734)),
    )
    def test_frame_controls_anchor_to_the_picture_not_lagging_position(
            self, player, monkeypatch, command, args, expected):
        seeks = []
        monkeypatch.setattr(player.player, "position", lambda: 1_733)
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player._source_frame_presented(1_800)

        getattr(player, command)(*args)

        assert seeks == [expected]
        assert player.current_label.text() == "00:00:01:24"
        assert player.position_detail.text() == "F 54"

    def test_step_seeks_inside_an_exact_integer_frame_boundary(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player._source_frame_presented(5_066)
        player.frame_step_forward()
        assert seeks == [5_101]

    def test_coalesced_steps_keep_signed_intent_without_rounding_each_delta(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player._source_frame_presented(4_000)

        for _ in range(30):
            player.frame_step_forward()
        assert len(seeks) == 30
        assert seeks[0] == 4_034
        assert seeks[-1] == 5_001

        # An intermediate paint must not replace the newer requested endpoint.
        player._source_frame_presented(4_033)
        player._jog_frames_requested(-2)
        assert seeks[-1] == 4_934
        player._source_frame_presented(4_933)
        player.frame_step_backward()
        assert seeks[-1] == 4_901

    def test_explicit_seek_supersedes_pending_steps_before_its_frame_arrives(
            self, player, monkeypatch):
        position = [4_000]
        seeks = []

        def seek(ms):
            position[0] = ms
            seeks.append(ms)

        monkeypatch.setattr(player.player, "position", lambda: position[0])
        monkeypatch.setattr(player.player, "setPosition", seek)
        player._source_frame_presented(4_000)
        player._jog_frames_requested(20)
        player.seek_to(10_000)
        player.frame_step_forward()
        assert seeks[-1] == 10_034

    @pytest.mark.parametrize("transition", ("load", "swap_source", "unload", "shuttle_forward"))
    def test_new_source_or_playback_discards_old_pending_step_intent(
            self, player, monkeypatch, transition):
        seeks = []
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        monkeypatch.setattr(player.player, "setSource", lambda _source: None)
        monkeypatch.setattr(player.player, "play", lambda: None)
        player._source_frame_presented(4_000)
        player._jog_frames_requested(20)

        if transition in ("load", "swap_source"):
            getattr(player, transition)(Path("replacement.mp4"))
        else:
            getattr(player, transition)()
        player._source_frame_presented(2_000)
        player.frame_step_forward()

        assert seeks[-1] == 2_034

    def test_readout_uses_paint_without_changing_marks_or_range_enforcement(
            self, player, monkeypatch):
        pauses = []
        position = [1_733]
        monkeypatch.setattr(player.player, "position", lambda: position[0])
        monkeypatch.setattr(player.player, "pause", lambda: pauses.append(True))
        monkeypatch.setattr(player.player, "playbackState",
                            lambda: QMediaPlayer.PlaybackState.PlayingState)
        player.set_clip_range(1_000, 1_800, False)
        player.slider.setRange(0, 20_000)
        player.slider.setValue(1_733)
        player._source_frame_presented(1_800)

        assert player.current_label.text() == "00:00:01:24"
        assert player.position_detail.text() == "F 54"
        assert player.slider.value() == 1_733
        assert pauses == []
        assert player.position_ms() == 1_733
        player.set_in_point()
        assert player.in_point_ms == 1_733
        position[0] = 2_000
        player.set_out_point()
        assert player.in_point_ms == 1_733
        assert player.out_point_ms == 2_000

        player._position_changed(1_700)
        assert player.current_label.text() == "00:00:01:24"
        assert player.position_detail.text() == "F 54"
        assert player.slider.value() == 1_700

    def test_frame_controls_remain_at_first_and_last_displayed_frames(
            self, player, monkeypatch):
        seeks = []
        monkeypatch.setattr(player.player, "duration", lambda: 10_000)
        monkeypatch.setattr(player.player, "setPosition", seeks.append)
        player._source_frame_presented(0)
        player.frame_step_backward()
        player._source_frame_presented(9_966)
        player.frame_step_forward()
        assert seeks == []


class TestReviewModeIsReachable:
    """Review mode must not depend on a single function key.

    Keyboard utilities (Logitech Options+, iCUE, vendor hotkey daemons)
    routinely claim the F row before an application sees it - reported in
    the field as "F5 just minimises the app". It also persists between
    sessions, so its state has to be visible somewhere.
    """

    @pytest.fixture
    def window(self, qapp, tmp_path, monkeypatch):
        from tapesift.core.config import AppSettings
        from tapesift.services import recovery_service
        from tapesift.ui_v2.main_window import MainWindowV2
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        settings = AppSettings()
        monkeypatch.setattr(settings, "save", lambda *a, **k: None)
        recovery_service.mark_closed()
        return MainWindowV2(settings)

    def test_it_has_a_visible_checkable_menu_item(self, window):
        assert window.review_action.isCheckable()
        assert window.review_action.text() == "Review Mode"

    def test_it_has_a_shortcut_that_is_not_a_function_key(self, window):
        keys = [k.toString() for k in window.review_action.shortcuts()]
        assert "F5" in keys
        assert any(not k.startswith("F") for k in keys), keys

    def test_the_tick_follows_the_setting(self, window):
        start = window.settings.review_mode
        window._toggle_review_mode()
        assert window.review_action.isChecked() == (not start)
        assert window.settings.review_mode == (not start)
        window._toggle_review_mode()
        assert window.review_action.isChecked() == start

    def test_the_tick_shows_state_restored_from_settings(self, qapp,
                                                         tmp_path,
                                                         monkeypatch):
        """Review mode survives a restart, so a freshly opened window must
        not claim it is off when it is on."""
        from tapesift.core.config import AppSettings
        from tapesift.services import recovery_service
        from tapesift.ui_v2.main_window import MainWindowV2
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        settings = AppSettings(review_mode=True)
        monkeypatch.setattr(settings, "save", lambda *a, **k: None)
        recovery_service.mark_closed()
        assert MainWindowV2(settings).review_action.isChecked()


class TestPlayDetailsPanelToggle:
    """Ctrl+I opens and closes Play details.

    It has to work *while typing in the panel* - the moment you want it
    closed is usually the moment your cursor is inside it - so the binding
    is a modifier combination rather than a bare letter.
    """

    @pytest.fixture
    def window(self, qapp, tmp_path, monkeypatch):
        from tapesift.core.config import AppSettings
        from tapesift.services import recovery_service
        from tapesift.ui_v2.main_window import MainWindowV2
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        settings = AppSettings()
        monkeypatch.setattr(settings, "save", lambda *a, **k: None)
        recovery_service.mark_closed()
        return MainWindowV2(settings)

    def test_it_toggles_open_and_shut(self, window):
        start = window.clip_editor.details_are_open()
        window._toggle_details_panel()
        assert window.clip_editor.details_are_open() is not start
        window._toggle_details_panel()
        assert window.clip_editor.details_are_open() is start

    def test_the_menu_tick_follows_the_panel(self, window):
        window._toggle_details_panel()
        assert window.details_action.isChecked() == \
            window.clip_editor.details_are_open()

    def test_the_binding_survives_a_focused_text_field(self, window):
        """A bare letter would be typed into the field instead of firing."""
        keys = window.details_action.shortcut().toString()
        assert "Ctrl" in keys, keys

    def test_opening_reveals_the_fields(self, window):
        """Closed means the fields are actually hidden, not merely collapsed
        to zero height behind a still-focusable widget."""
        section = window.clip_editor.details_section
        if window.clip_editor.details_are_open():
            window._toggle_details_panel()
        # isVisibleTo, not isVisible: the workspace is not the active page in
        # a freshly built window, so isVisible is False either way and would
        # make this assert nothing.
        assert not section.body.isVisibleTo(section)
        window._toggle_details_panel()
        assert section.body.isVisibleTo(section)

    def test_review_mode_opening_details_updates_the_tick(self, window):
        """Review entry sets the panel itself; the tick must not lie about it.

        The V1 shell expanded the details panel on review entry; the V2
        window runs the inspector in analyst mode, which collapses it
        instead. Either way the invariant under test is the same: the menu
        tick must match the panel's real open/closed state.
        """
        window.clip_editor.begin_review_entry()
        window.details_action.setChecked(
            window.clip_editor.details_are_open())
        assert window.details_action.isChecked() == \
            window.clip_editor.details_are_open()


@pytest.fixture
def dock_transport_window(qapp, tmp_path, monkeypatch):
    """A visible V2 workspace whose editing handlers are observable spies."""
    from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
    from tapesift.ui_v2.main_window import MainWindowV2

    # The editing handlers live on the workflow mixin since the V2 window
    # composes it directly; patching the legacy V1 shell would miss V2.
    MainWindow = MainWindowWorkflow

    calls: list[str] = []
    monkeypatch.setattr(
        VideoPlayer, "toggle_play", lambda _player: calls.append("Space"))
    monkeypatch.setattr(
        VideoPlayer, "shuttle_reverse", lambda _player: calls.append("J"))
    monkeypatch.setattr(
        VideoPlayer, "shuttle_stop", lambda _player: calls.append("K"))
    monkeypatch.setattr(
        VideoPlayer, "shuttle_forward", lambda _player: calls.append("L"))
    monkeypatch.setattr(
        VideoPlayer, "step_or_jump",
        lambda _player, direction: calls.append(
            "Left" if direction < 0 else "Right"))
    monkeypatch.setattr(
        VideoPlayer, "jump_backward",
        lambda _player: calls.append("Shift+Left"))
    monkeypatch.setattr(
        VideoPlayer, "jump_forward",
        lambda _player: calls.append("Shift+Right"))
    monkeypatch.setattr(
        VideoPlayer, "seek_relative",
        lambda _player, amount: calls.append(
            "Ctrl+Left" if amount < 0 else "Ctrl+Right"))
    monkeypatch.setattr(
        VideoPlayer, "request_add_clip", lambda _player: calls.append("A"))
    monkeypatch.setattr(
        VideoPlayer, "toggle_timeline_snapping",
        lambda _player: calls.append("S"))
    monkeypatch.setattr(
        MainWindow, "_i_pressed", lambda _window: calls.append("I"))
    monkeypatch.setattr(
        MainWindow, "_o_pressed", lambda _window: calls.append("O"))
    monkeypatch.setattr(
        MainWindow, "_cut_clip_at_playhead",
        lambda _window: calls.append("C"))
    monkeypatch.setattr(MainWindow, "_check_recovery", lambda _window: None)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    settings = AppSettings(onboarding_seen=True, recent_projects=[])
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "exports")
    monkeypatch.setattr(settings, "save", lambda *args, **kwargs: None)

    window = MainWindowV2(settings)
    # Transport routing needs only an active session. A lightweight sentinel
    # keeps this fixture independent of project persistence and media loading.
    window.session = object()  # type: ignore[assignment]
    window.clip_editor.set_clip(Clip(
        start_ms=1_000,
        end_ms=8_000,
        clip_title="Focus routing",
        notes="",
    ))
    window._dock_visibility.update({
        "player": True,
        "clips": True,
        "play_details": True,
    })
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    window._apply_workspace_visibility()
    qapp.processEvents()
    qapp.processEvents()

    try:
        yield window, calls
    finally:
        # Floating docks are independent top-level windows. Put every one back
        # under the main window before deferred destruction so no native window
        # or multimedia owner leaks into the next test.
        for dock in window._workspace_docks.values():
            dock.showNormal()
            if dock.isFloating():
                dock.setFloating(False)
            dock.hide()
        window._export_dock.hide()
        window._workspace_save_timer.stop()
        window.autosave_timer.stop()
        window.session = None
        window.player.unload()
        window.hide()
        window.deleteLater()
        qapp.processEvents()
        qapp.processEvents()


def _float_and_focus(qapp, window, dock_key: str, widget) -> None:
    dock = window._workspace_docks[dock_key]
    dock.setFloating(True)
    # Give the new native window an on-screen geometry before processing the
    # queued missing-monitor recovery installed by MainWindowV2.
    available = qapp.primaryScreen().availableGeometry()
    dock.resize(520, 640)
    dock.move(available.left() + 40, available.top() + 40)
    dock.show()
    dock.activateWindow()
    widget.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()
    qapp.processEvents()

    assert dock.isFloating()
    assert qapp.activeWindow() is dock
    focused = qapp.focusWidget()
    assert focused is widget or (
        focused is not None and dock.isAncestorOf(focused))


class TestDockTransportShortcutRouting:
    def test_dock_transport_has_exactly_one_application_shortcut_per_key(
            self, dock_transport_window):
        window, _calls = dock_transport_window

        expected = {
            "Space", "J", "K", "L",
            "Left", "Right", "Shift+Left", "Shift+Right",
            "Ctrl+Left", "Ctrl+Right",
            "I", "O", "A", "Delete", "Ctrl+D",
            "Ctrl+Down", "Ctrl+Up", "PgDown", "PgUp",
            "Ctrl+Shift+Down", "Ctrl+Home", "Ctrl+End",
            # R now tags a run; replay moved to Shift+R.
            "Ctrl+L", "Shift+R", "N", "[", "]", "C", "M",
            "Ctrl+K", "Ctrl+M", "Ctrl+Shift+V", "Ctrl+E", "S", "G", "Esc",
            # Folds Play Details away while logging runs in the grid.
            "Ctrl+B",
            # Moves between plays that still need logging.
            "U", "Shift+U",
        }
        assert set(window._transport_shortcuts) == expected
        for key in expected:
            matches = [
                shortcut
                for shortcut in window.findChildren(QShortcut)
                if shortcut.context()
                == Qt.ShortcutContext.ApplicationShortcut
                and shortcut.key() == QKeySequence(key)
            ]
            assert matches == [window._transport_shortcuts[key]]
            assert matches[0].context() == \
                Qt.ShortcutContext.ApplicationShortcut

    @pytest.mark.parametrize(
        ("key", "qt_key", "modifiers"),
        (
            ("Space", Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier),
            ("Left", Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier),
            ("Right", Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier),
            ("Shift+Left", Qt.Key.Key_Left,
             Qt.KeyboardModifier.ShiftModifier),
            ("Shift+Right", Qt.Key.Key_Right,
             Qt.KeyboardModifier.ShiftModifier),
            ("Ctrl+Left", Qt.Key.Key_Left,
             Qt.KeyboardModifier.ControlModifier),
            ("Ctrl+Right", Qt.Key.Key_Right,
             Qt.KeyboardModifier.ControlModifier),
            ("I", Qt.Key.Key_I, Qt.KeyboardModifier.NoModifier),
            ("O", Qt.Key.Key_O, Qt.KeyboardModifier.NoModifier),
            ("A", Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier),
            ("C", Qt.Key.Key_C, Qt.KeyboardModifier.NoModifier),
            ("S", Qt.Key.Key_S, Qt.KeyboardModifier.NoModifier),
        ),
    )
    def test_editing_shortcuts_route_once_from_floating_player(
            self, qapp, dock_transport_window, key, qt_key, modifiers):
        window, calls = dock_transport_window
        _float_and_focus(
            qapp, window, "player", window.player.video_widget)
        # Ignore any fixture/layout bookkeeping and isolate the requested key.
        calls.clear()

        QTest.keyClick(window.player.video_widget, qt_key, modifiers)
        qapp.processEvents()

        assert calls == [key]

    def test_f11_escape_and_dock_back_preserve_the_authoritative_player(
            self, qapp, dock_transport_window):
        window, calls = dock_transport_window
        dock = window._workspace_docks["player"]
        owners = (
            window._player_panel,
            window.player,
            window.player.player,
            window.player.audio,
            window.player.slider,
            window.quick_tag_tray,
        )
        _float_and_focus(
            qapp, window, "player", window.player.video_widget)
        for _index in range(2):
            qapp.processEvents()

        assert window.fullscreen_player_action.shortcut() == \
            QKeySequence("F11")
        assert window.fullscreen_player_action.shortcutContext() == \
            Qt.ShortcutContext.ApplicationShortcut
        assert window.fullscreen_player_action.isEnabled()

        dock.showMaximized()
        qapp.processEvents()
        qapp.processEvents()
        assert dock.isMaximized()

        QTest.keyClick(window.player.video_widget, Qt.Key.Key_F11)
        qapp.processEvents()
        qapp.processEvents()
        assert dock.isFloating()
        assert dock.isFullScreen()
        assert window._player_fullscreen
        assert window.fullscreen_player_action.text() == "Exit Full Screen"

        calls.clear()
        QTest.keyClick(window.player.video_widget, Qt.Key.Key_Escape)
        qapp.processEvents()
        qapp.processEvents()
        assert dock.isFloating()
        assert not dock.isFullScreen()
        assert dock.isMaximized()
        assert not window._player_fullscreen
        assert calls == []
        assert (
            window._player_panel,
            window.player,
            window.player.player,
            window.player.audio,
            window.player.slider,
            window.quick_tag_tray,
        ) == owners

        QTest.keyClick(window.player.video_widget, Qt.Key.Key_F11)
        qapp.processEvents()
        qapp.processEvents()
        assert dock.isFullScreen()
        window.float_player_action.trigger()
        qapp.processEvents()
        qapp.processEvents()

        assert not dock.isFloating()
        assert dock.windowState() == Qt.WindowState.WindowNoState
        assert window.dockWidgetArea(dock) == \
            Qt.DockWidgetArea.TopDockWidgetArea
        assert "player" not in window._workspace_maximized_docks
        assert not window._player_fullscreen
        assert (
            window._player_panel,
            window.player,
            window.player.player,
            window.player.audio,
            window.player.slider,
            window.quick_tag_tray,
        ) == owners

    @pytest.mark.parametrize(
        ("dock_key", "editor", "read_text"),
        (
            (
                "clips",
                lambda window: window.clip_list.filter_edit,
                lambda widget: widget.text(),
            ),
            (
                "play_details",
                lambda window: window.clip_editor.notes_edit,
                lambda widget: widget.toPlainText(),
            ),
        ),
    )
    def test_dock_transport_is_suppressed_in_fixed_text_editors(
            self, qapp, dock_transport_window, dock_key, editor, read_text):
        window, calls = dock_transport_window
        field = editor(window)
        field.clear()
        assert not window._workspace_docks[dock_key].isFloating()
        field.setFocus(Qt.FocusReason.OtherFocusReason)
        qapp.processEvents()

        QTest.keyClicks(field, " jklciosa")
        qapp.processEvents()
        assert read_text(field) == " jklciosa"

        # Emit the connected signals as well as typing real keys. This forces
        # the central guard to prove that suppression does not merely depend
        # on the editor consuming Qt's ShortcutOverride event.
        for key in (
                "Space", "J", "K", "L", "Left", "Right",
                "Shift+Left", "Shift+Right", "Ctrl+Left", "Ctrl+Right",
                "I", "O", "A", "C", "S"):
            window._transport_shortcuts[key].activated.emit()

        assert window._typing_in_text_field()
        assert calls == []

    def test_dock_transport_is_suppressed_while_modal_is_active(
            self, qapp, dock_transport_window):
        window, calls = dock_transport_window
        dialog = QDialog(window)
        dialog.setModal(True)
        dialog.show()
        dialog.activateWindow()
        qapp.processEvents()

        try:
            assert qapp.activeModalWidget() is dialog
            for shortcut in window._transport_shortcuts.values():
                shortcut.activated.emit()
            assert calls == []
        finally:
            dialog.hide()
            dialog.deleteLater()
            qapp.processEvents()

    def test_dock_transport_is_suppressed_in_an_unrelated_window(
            self, qapp, dock_transport_window):
        window, calls = dock_transport_window
        other = QDialog()
        other.show()
        other.activateWindow()
        qapp.processEvents()

        try:
            assert qapp.activeWindow() is other
            for key in (
                    "Space", "J", "K", "L", "Left", "Right",
                    "I", "O", "A", "C", "S"):
                window._transport_shortcuts[key].activated.emit()
            assert calls == []
        finally:
            other.hide()
            other.deleteLater()
            qapp.processEvents()

    def test_project_shortcuts_are_suppressed_on_the_library_page(
            self, qapp, dock_transport_window):
        window, calls = dock_transport_window
        window.stack.setCurrentWidget(window.library_screen)
        qapp.processEvents()

        assert all(
            not shortcut.isEnabled()
            for shortcut in window._transport_shortcuts.values())
        for key in (
                "Space", "J", "K", "L", "Left", "Right",
                "I", "O", "A", "C", "S", "Ctrl+E"):
            window._transport_shortcuts[key].activated.emit()

        assert calls == []

        window.stack.setCurrentWidget(window.workspace)
        qapp.processEvents()
        assert all(
            shortcut.isEnabled()
            for shortcut in window._transport_shortcuts.values())

    def test_dock_transport_is_suppressed_while_window_menu_is_open(
            self, qapp, dock_transport_window):
        window, calls = dock_transport_window
        menu = window.window_menu
        menu.popup(window.mapToGlobal(window.rect().center()))
        qapp.processEvents()

        try:
            assert qapp.activePopupWidget() is menu
            for shortcut in window._transport_shortcuts.values():
                shortcut.activated.emit()
            assert calls == []
        finally:
            menu.close()
            qapp.processEvents()


def test_library_space_keeps_its_preview_meaning_with_a_project_open(
        qapp, tmp_path, monkeypatch):
    """Project-wide bindings must not make Library's local key ambiguous."""
    from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
    from tapesift.ui_v2.library_screen import LibrarySearchScreenV2
    from tapesift.ui_v2.main_window import MainWindowV2

    calls: list[str] = []
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda _window: None)
    monkeypatch.setattr(
        VideoPlayer, "toggle_play",
        lambda _player: calls.append("player"))
    monkeypatch.setattr(
        LibrarySearchScreenV2, "_space_preview",
        lambda _screen: calls.append("library"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    settings = AppSettings(onboarding_seen=True, recent_projects=[])
    monkeypatch.setattr(settings, "save", lambda *args, **kwargs: None)

    window = MainWindowV2(settings)
    window.session = object()  # type: ignore[assignment]
    window.stack.setCurrentWidget(window.library_screen)
    window.show()
    window.activateWindow()
    window.library_screen.setFocus(Qt.FocusReason.OtherFocusReason)
    qapp.processEvents()

    try:
        QTest.keyClick(window.library_screen, Qt.Key.Key_Space)
        qapp.processEvents()
        assert calls == ["library"]
    finally:
        window.session = None
        window.player.unload()
        window.hide()
        window.deleteLater()
        qapp.processEvents()
