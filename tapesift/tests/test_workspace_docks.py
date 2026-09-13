"""Dock topology, recovery, and persistence for the V2 review workspace."""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QPoint, QRect, QSize, Qt  # noqa: E402
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDockWidget, QScrollArea  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.ui_core.clip_editor import COLLAPSED_WIDTH  # noqa: E402
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow  # noqa: E402
from tapesift.ui_core.video_player import VideoPlayer  # noqa: E402
from tapesift.ui_v2.main_window import MainWindowV2  # noqa: E402
from tapesift.ui_v2.control_center import ControlCenterDeck  # noqa: E402
from tapesift.ui_v2.dock_v2 import DockV2Deck  # noqa: E402
from tapesift.ui_v2 import workspace_docks as workspace_dock_helpers  # noqa: E402
from tapesift.ui_v2.workspace_docks import (  # noqa: E402
    decode_qbytearray,
    encode_qbytearray,
    geometry_has_reachable_title_bar,
    redock_offscreen_floating_docks,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    value = AppSettings(onboarding_seen=True, recent_projects=[])
    value.default_project_folder = str(tmp_path)
    value.default_output_folder = str(tmp_path / "exports")
    value.save = lambda *args, **kwargs: None
    return value


@pytest.fixture
def make_window(
        qapp, monkeypatch
) -> Callable[[AppSettings], MainWindowV2]:
    """Build windows and safely reclaim any floating native dock windows."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    windows: list[MainWindowV2] = []

    def build(value: AppSettings) -> MainWindowV2:
        # These tests are about saving and restoring the workspace, so they
        # opt into it. The app now opens clean by default (a stale maximized
        # flag in the saved blob was painting square corners on every launch).
        if getattr(value, "workspace_restore_layout", None) is not True:
            value.workspace_restore_layout = True
        window = MainWindowV2(value)
        windows.append(window)
        window.resize(1280, 800)
        window.stack.setCurrentWidget(window.workspace)
        window.show()
        qapp.processEvents()
        qapp.processEvents()
        return window

    yield build

    for window in reversed(windows):
        window._workspace_save_timer.stop()
        for dock in window.findChildren(QDockWidget):
            dock.showNormal()
            dock.hide()
            if dock.isFloating():
                dock.setFloating(False)
        window.player.unload()
        window.hide()
        window.deleteLater()
    qapp.processEvents()
    qapp.processEvents()


def _drain_events(qapp, count: int = 3) -> None:
    """Run deferred native-window transitions to completion."""
    for _index in range(count):
        qapp.processEvents()


def _float_dock(qapp, dock: QDockWidget) -> None:
    """Float one dock on the primary screen and apply deferred window hints."""
    available = qapp.primaryScreen().availableGeometry()
    dock.setFloating(True)
    dock.resize(
        min(760, max(320, available.width() - 80)),
        min(620, max(240, available.height() - 80)),
    )
    dock.move(available.left() + 40, available.top() + 40)
    dock.show()
    _drain_events(qapp)


def test_default_docks_have_stable_names_areas_and_authoritative_widgets(
        qapp, settings, make_window, monkeypatch):
    monkeypatch.delenv("TAPESIFT_DOCK_V2", raising=False)
    window = make_window(settings)
    docks = window._workspace_docks

    assert {
        key: dock.objectName() for key, dock in docks.items()
    } == {
        "player": "tapesift.dock.player",
        "clips": "tapesift.dock.clips",
        "play_details": "tapesift.dock.play_details",
    }
    assert window.dockWidgetArea(docks["player"]) == \
        Qt.DockWidgetArea.TopDockWidgetArea
    assert window.dockWidgetArea(docks["clips"]) == \
        Qt.DockWidgetArea.LeftDockWidgetArea
    assert window.dockWidgetArea(docks["play_details"]) == \
        Qt.DockWidgetArea.RightDockWidgetArea

    assert docks["player"].widget() is window._player_panel
    assert docks["clips"].widget() is window.clip_ledger_scroll
    assert docks["play_details"].widget() is window.clip_details_scroll
    assert isinstance(window.clip_ledger_scroll, QScrollArea)
    assert isinstance(window.clip_details_scroll, QScrollArea)
    assert window.clip_ledger_scroll.widget() is window.clip_list
    assert window.clip_details_scroll.widget() is window.clip_editor
    assert window.clip_ledger_scroll.verticalScrollBar() is not \
        window.clip_details_scroll.verticalScrollBar()
    assert window.centralWidget() is window.stack
    assert window.player.parentWidget() is window._player_panel
    assert window.findChildren(VideoPlayer) == [window.player]
    # Two media players, and exactly two: the workspace's, and the
    # library's own preview. This asserted one because it predates the
    # library preview - and while it was red it hid a third, a whole V1
    # library screen that V2 built, discarded and then kept forever with
    # a live decoder attached.
    players = window.findChildren(QMediaPlayer)
    assert window.player.player in players
    assert set(players) == {
        window.library_screen.preview_player, window.player.player}
    assert set(window.findChildren(QAudioOutput)) == {
        window.library_screen.preview_audio, window.player.audio}
    assert window.player.player.parent() is window.player
    assert window.player.audio.parent() is window.player
    # Quick Tags stay in the player and never become deck children.
    assert window.quick_tag_tray.parentWidget() is \
        window.player.quick_tag_slot
    assert window._player_panel.isAncestorOf(window.quick_tag_tray)
    assert not window.control_center.isAncestorOf(window.quick_tag_tray)

    control_dock = window._control_center_dock
    assert control_dock.objectName() == "tapesift.dock.control_center"
    assert window.dockWidgetArea(control_dock) == \
        Qt.DockWidgetArea.BottomDockWidgetArea
    assert control_dock.allowedAreas() == \
        Qt.DockWidgetArea.BottomDockWidgetArea
    assert control_dock.features() == \
        QDockWidget.DockWidgetFeature.NoDockWidgetFeatures
    assert type(window.control_center) is DockV2Deck
    strip_slot = window.player.control_strip_slot
    assert control_dock.widget() is None
    assert control_dock.minimumHeight() == 0
    assert control_dock.maximumHeight() == 0
    assert strip_slot.layout().count() == 1
    assert strip_slot.layout().itemAt(0).widget() is window.control_center
    assert window.control_center.parentWidget() is strip_slot
    assert window.player.isAncestorOf(window.control_center)
    assert window._player_panel.isAncestorOf(window.control_center)
    assert not control_dock.isAncestorOf(window.control_center)
    assert window.player.control_center is window.control_center
    assert window.player.transport_pill is window.control_center
    assert window.control_center.bound_player is window.player
    assert window.findChildren(ControlCenterDeck) == [window.control_center]
    assert window.proxy_banner_widget.parentWidget() is window._player_panel
    for key, dock in docks.items():
        assert dock.features() == \
            QDockWidget.DockWidgetFeature.NoDockWidgetFeatures, key
        assert dock.allowedAreas() == {
            "player": Qt.DockWidgetArea.TopDockWidgetArea,
            "clips": Qt.DockWidgetArea.LeftDockWidgetArea,
            "play_details": Qt.DockWidgetArea.RightDockWidgetArea,
        }[key]


def test_option4_review_geometry_centers_transport_without_overlap(
        qapp, settings, make_window):
    """The locked 1708px shell gives fixed sides and the rest to film."""
    window = make_window(settings)
    window.resize(1708, 920)
    window._resize_default_docks()
    _drain_events(qapp, 6)

    clips = window._workspace_docks["clips"]
    player = window._workspace_docks["player"]
    details = window._workspace_docks["play_details"]
    deck = window.control_center

    assert clips.width() == window.REVIEW_LEDGER_WIDTH == 340
    assert details.width() == window.REVIEW_INSPECTOR_WIDTH == 351
    # The application stylesheet narrows separators to 1px (1015px film
    # column); this unit fixture intentionally runs without the global sheet,
    # so Qt's two 5px native separators leave 1005px.
    assert player.width() >= 1004
    assert window.player.minimumWidth() == 0
    assert deck.minimumSizeHint().width() == 520
    assert deck.height() == 46
    assert deck.jog_toggle.isVisible()
    assert deck.play_btn.size() == QSize(44, 44)
    assert window.review_progress.isHidden()
    assert window.review_autosave_dot.isHidden()
    assert window.review_autosave_label.isHidden()
    assert window.review_pop_out_button.isHidden()
    assert window.player.telestration_rail.isHidden()
    assert window.player.view_strip.isHidden()
    assert deck.viewport_group.isVisible()
    assert window.player.timeline_zoom_out.isVisible()
    assert window.player.timeline_zoom_in.isVisible()
    assert window.player.timeline_fit_play.isVisible()
    assert window.player.predicted_snap_button.isVisible()
    assert window.player.predicted_snap_button.text() == "Snap"
    assert window.player.predicted_snap_button.parentWidget() is \
        deck.viewport_group
    for control in (
            window.player.timeline_zoom_out,
            window.player.timeline_zoom_in,
            window.player.timeline_fit_play):
        assert control.parentWidget() is deck.viewport_group
    assert window.player.attribute_grid.isVisible()

    play_center = deck.play_btn.mapTo(window, QPoint()).x() \
        + deck.play_btn.width() / 2
    film_center = player.mapTo(window, QPoint()).x() + player.width() / 2
    assert abs(play_center - film_center) <= 1


def test_legacy_control_center_rollback_restores_full_width_bottom_dock(
        qapp, settings, make_window, monkeypatch):
    monkeypatch.setenv("TAPESIFT_DOCK_V2", "0")
    window = make_window(settings)
    deck = window.control_center
    dock = window._control_center_dock
    strip_slot = window.player.control_strip_slot

    assert type(deck) is ControlCenterDeck
    assert dock.widget() is deck
    assert deck.parentWidget() is dock
    assert deck.sizeHint().height() == 197
    assert (dock.minimumHeight(), dock.maximumHeight(), dock.height()) == \
        (197, 197, 197)
    assert dock.isVisible()
    assert dock.width() >= window.width() - 2
    dock_top = dock.mapTo(window, dock.rect().topLeft()).y()
    for panel in window._workspace_docks.values():
        panel_bottom = panel.mapTo(
            window, panel.rect().bottomLeft()).y()
        assert panel_bottom <= dock_top + 1
    assert strip_slot.layout().count() == 0
    assert strip_slot.isHidden()
    assert not window.player.isAncestorOf(deck)
    assert window.player.control_center is deck
    assert window.player.transport_pill is deck
    assert deck.bound_player is window.player
    assert window.findChildren(ControlCenterDeck) == [deck]


def test_inspector_fold_releases_and_restores_the_wrapper_width(
        qapp, settings, make_window):
    """The QScrollArea must fold with the editor it contains."""
    window = make_window(settings)
    details = window._workspace_docks["play_details"]

    assert window.clip_details_scroll.minimumWidth() == \
        window.REVIEW_INSPECTOR_WIDTH
    assert window.clip_details_scroll.maximumWidth() == \
        window.REVIEW_INSPECTOR_WIDTH
    assert details.width() >= window.REVIEW_INSPECTOR_WIDTH

    window.clip_editor.set_collapsed(True)
    _drain_events(qapp, 5)

    assert window.clip_editor.width() == COLLAPSED_WIDTH
    assert window.clip_details_scroll.minimumWidth() == COLLAPSED_WIDTH
    assert details.width() <= COLLAPSED_WIDTH + 2

    window.clip_editor.set_collapsed(False)
    _drain_events(qapp, 5)

    assert window.clip_details_scroll.minimumWidth() == \
        window.REVIEW_INSPECTOR_WIDTH
    assert window.clip_details_scroll.maximumWidth() == \
        window.REVIEW_INSPECTOR_WIDTH
    assert details.width() >= window.REVIEW_INSPECTOR_WIDTH


def test_ledger_and_inspector_scroll_owners_are_independent(
        qapp, settings, make_window):
    """Each side column keeps its own live vertical scroll position."""
    window = make_window(settings)
    ledger_bar = window.clip_list.table.verticalScrollBar()
    details_bar = window.clip_editor.form_area.verticalScrollBar()

    # Force deterministic overflow without replacing either production
    # scroll owner.  The normal widgets decide their own scroll ranges once
    # populated with enough rows/content.
    window.clip_list.table.setRowCount(80)
    window.clip_editor.empty_state.hide()
    window.clip_editor.form_area.show()
    window.clip_editor.form_area.widget().setMinimumHeight(1800)
    _drain_events(qapp, 5)

    assert ledger_bar.maximum() > 0
    assert details_bar.maximum() > 0
    ledger_bar.setValue(min(111, ledger_bar.maximum()))
    _drain_events(qapp)
    assert details_bar.value() == 0
    ledger_value = ledger_bar.value()
    details_bar.setValue(min(97, details_bar.maximum()))
    _drain_events(qapp)
    assert ledger_bar.value() == ledger_value


def test_only_explicit_player_popout_gets_native_caption_controls(
        qapp, settings, make_window):
    window = make_window(settings)
    docks = window._workspace_docks
    widgets = {key: dock.widget() for key, dock in docks.items()}
    required_hints = (
        Qt.WindowType.WindowMinimizeButtonHint,
        Qt.WindowType.WindowMaximizeButtonHint,
        Qt.WindowType.WindowCloseButtonHint,
    )

    window.player_detach_button.click()
    _drain_events(qapp)
    player = docks["player"]
    assert player.isFloating()
    assert player.isVisible()
    assert player.widget() is widgets["player"]
    for hint in required_hints:
        assert player.windowFlags() & hint, hint

    for key in ("clips", "play_details"):
        dock = docks[key]
        dock.setFloating(True)
        _drain_events(qapp)
        assert not dock.isFloating(), key
        assert dock.widget() is widgets[key], key


def test_minimize_stays_logically_visible_and_does_not_pause_player(
        qapp, settings, make_window, monkeypatch):
    window = make_window(settings)
    stops: list[bool] = []
    monkeypatch.setattr(
        window.player, "shuttle_stop", lambda: stops.append(True))
    dock = window._workspace_docks["player"]
    _float_dock(qapp, dock)
    dock.showMinimized()
    _drain_events(qapp)

    assert dock.isMinimized()
    assert dock.isVisible()
    assert window._dock_visibility["player"]
    window._workspace_dock_visibility_changed("player", True)
    _drain_events(qapp)
    assert dock.isFloating()
    assert dock.isMinimized()
    window._workspace_save_timer.stop()
    window._save_workspace_now()
    assert settings.workspace_player_visible
    assert stops == []


def test_float_redock_hide_reopen_and_reset_reuse_the_same_widgets(
        qapp, settings, make_window):
    window = make_window(settings)
    docks = window._workspace_docks
    widgets = {key: dock.widget() for key, dock in docks.items()}
    player_dock = docks["player"]
    player_owners = (
        window.player,
        window.player.player,
        window.player.audio,
        window.player.slider,
        window.quick_tag_tray,
    )

    assert window.player_detach_button.text() == "Pop Out"
    assert window.player_detach_button.accessibleName() == \
        "Pop Out Video Player"
    window.player_detach_button.click()
    qapp.processEvents()
    qapp.processEvents()
    assert player_dock.isFloating()
    assert player_dock.isVisible()
    assert docks["clips"].height() > 150
    assert docks["play_details"].height() > 150
    assert window.player_detach_button.text() == "Dock Back"
    assert window.float_player_action.text() == "Dock Video Player Back"
    assert player_dock.widget() is window._player_panel
    assert (
        window.player,
        window.player.player,
        window.player.audio,
        window.player.slider,
        window.quick_tag_tray,
    ) == player_owners

    window.float_player_action.trigger()
    qapp.processEvents()
    assert not player_dock.isFloating()
    assert window.player_detach_button.text() == "Pop Out"
    assert window.dockWidgetArea(player_dock) == \
        Qt.DockWidgetArea.TopDockWidgetArea
    assert player_dock.widget() is widgets["player"]

    player_dock.setFloating(True)
    qapp.processEvents()
    player_dock.showFullScreen()
    _drain_events(qapp)
    window.player.timeline_follow_playhead.setChecked(False)

    window.reset_workspace_action.trigger()
    qapp.processEvents()
    qapp.processEvents()

    for key, dock in docks.items():
        assert dock.isVisible(), key
        assert not dock.isFloating(), key
        assert dock.windowState() == Qt.WindowState.WindowNoState, key
        assert dock.widget() is widgets[key]
    assert window._workspace_maximized_docks == set()
    assert not window._player_fullscreen
    assert window.dockWidgetArea(docks["clips"]) == \
        Qt.DockWidgetArea.LeftDockWidgetArea
    assert window.dockWidgetArea(docks["player"]) == \
        Qt.DockWidgetArea.TopDockWidgetArea
    assert window.dockWidgetArea(docks["play_details"]) == \
        Qt.DockWidgetArea.RightDockWidgetArea
    assert window.player.timeline_follow_playhead.isChecked()

    labels = [action.text() for action in window.window_menu.actions()]
    assert "Pop Out Video Player" in labels
    assert "Show Clips" not in labels
    assert "Show Play Details" not in labels
    assert "Show Tag Map" not in labels
    assert "Restore Minimized Popouts" not in labels
    assert "Reset Workspace" in labels


def test_workspace_settings_save_and_restore_round_trip(
        qapp, tmp_path, make_window):
    target = tmp_path / "settings.json"
    first = AppSettings(onboarding_seen=True, recent_projects=[])
    first.default_project_folder = str(tmp_path)
    first.default_output_folder = str(tmp_path / "exports")
    first.save = lambda: AppSettings.save(first, target)
    window = make_window(first)

    player_dock = window._workspace_docks["player"]
    available = qapp.primaryScreen().availableGeometry()
    player_dock.setFloating(True)
    player_dock.setGeometry(QRect(
        available.left() + 30,
        available.top() + 30,
        min(760, available.width() - 60),
        min(620, available.height() - 60),
    ))
    window.player.timeline_follow_playhead.setChecked(False)
    qapp.processEvents()
    window._workspace_save_timer.stop()
    window._save_workspace_now()

    loaded = AppSettings.load(target)
    assert decode_qbytearray(loaded.workspace_geometry_v1) is not None
    assert decode_qbytearray(loaded.workspace_state_v1) is not None
    assert loaded.workspace_player_visible
    assert loaded.workspace_clips_visible
    assert not loaded.timeline_follow_playhead

    window.hide()
    loaded.save = lambda *args, **kwargs: None
    restored = make_window(loaded)

    assert restored.dockWidgetArea(
        restored._workspace_docks["play_details"]
    ) == Qt.DockWidgetArea.RightDockWidgetArea
    assert restored._workspace_docks["player"].isVisible()
    assert restored._workspace_docks["player"].isFloating()
    assert restored._workspace_docks["player"].widget() \
        is restored._player_panel
    assert restored.stack.maximumHeight() > 0
    assert restored.stack.maximumWidth() == 0
    assert restored._workspace_docks["clips"].isVisible()
    assert not restored._workspace_docks["clips"].isFloating()
    assert not restored._workspace_docks["play_details"].isFloating()
    assert not restored.player.timeline_follow_playhead.isChecked()


def test_player_maximized_state_persists_while_utilities_stay_fixed(
        qapp, tmp_path, make_window):
    target = tmp_path / "window-state-settings.json"
    first = AppSettings(onboarding_seen=True, recent_projects=[])
    first.default_project_folder = str(tmp_path)
    first.default_output_folder = str(tmp_path / "exports")
    first.save = lambda: AppSettings.save(first, target)
    window = make_window(first)
    player = window._workspace_docks["player"]

    _float_dock(qapp, player)
    player.showMaximized()
    _drain_events(qapp)
    assert player.isMaximized()

    window._workspace_save_timer.stop()
    window._save_workspace_now()
    loaded = AppSettings.load(target)

    assert loaded.workspace_maximized_docks_v1 == ["player"]
    assert loaded.workspace_player_visible
    assert loaded.workspace_clips_visible

    window._workspace_save_timer.stop()
    window.hide()
    loaded.save = lambda *args, **kwargs: None
    restored = make_window(loaded)
    _drain_events(qapp, 5)
    restored_player = restored._workspace_docks["player"]
    restored_clips = restored._workspace_docks["clips"]

    assert restored_player.isFloating()
    assert restored_player.isVisible()
    assert restored_player.isMaximized()
    assert not restored_clips.isFloating()
    assert restored_clips.isVisible()
    assert not restored_clips.isMinimized()
    assert not restored_clips.isMaximized()


def test_player_close_redocks_and_central_axis_tracks_detach(
        qapp, settings, make_window, monkeypatch):
    """The empty center collapses on the axis useful to the dock topology."""
    window = make_window(settings)
    player_dock = window._workspace_docks["player"]
    stops: list[bool] = []
    monkeypatch.setattr(
        window.player, "shuttle_stop", lambda: stops.append(True))

    assert player_dock.isVisible()
    assert not window._player_placeholder.isVisibleTo(window.workspace)
    assert window.stack.maximumHeight() == 0
    assert window.stack.maximumWidth() > 0

    player_dock.setFloating(True)
    qapp.processEvents()
    qapp.processEvents()
    assert player_dock.isVisible()
    assert player_dock.isFloating()
    assert stops == []
    assert not window._player_placeholder.isVisibleTo(window.workspace)
    assert window.stack.maximumHeight() > 0
    assert window.stack.maximumWidth() == 0

    window.stack.setCurrentWidget(window.library_screen)
    qapp.processEvents()
    assert not player_dock.isVisible()
    assert window._dock_visibility["player"]
    assert window.stack.maximumHeight() > 0
    assert window.stack.maximumWidth() > 0
    assert not window._player_placeholder.isVisibleTo(window.workspace)

    window.stack.setCurrentWidget(window.workspace)
    qapp.processEvents()
    assert player_dock.isVisible()
    assert player_dock.isFloating()
    assert not window._player_placeholder.isVisibleTo(window.workspace)
    assert window.stack.maximumHeight() > 0
    assert window.stack.maximumWidth() == 0

    player_dock.close()
    _drain_events(qapp, 5)
    assert player_dock.isVisible()
    assert not player_dock.isFloating()
    assert stops == []
    assert window._dock_visibility["player"]
    assert not window._player_placeholder.isVisibleTo(window.workspace)
    assert window.stack.maximumHeight() == 0
    assert window.stack.maximumWidth() > 0


def test_legacy_hidden_panel_preferences_are_ignored(
        qapp, tmp_path, make_window):
    target = tmp_path / "settings.json"
    first = AppSettings(onboarding_seen=True, recent_projects=[])
    first.default_project_folder = str(tmp_path)
    first.default_output_folder = str(tmp_path / "exports")
    first.save = lambda: AppSettings.save(first, target)
    window = make_window(first)

    first.workspace_player_visible = False
    first.workspace_clips_visible = False
    first.workspace_play_details_visible = False
    window._workspace_save_timer.stop()
    window._save_workspace_now()

    loaded = AppSettings.load(target)
    assert loaded.workspace_player_visible
    assert loaded.workspace_clips_visible
    assert loaded.workspace_play_details_visible

    window.hide()
    loaded.save = lambda *args, **kwargs: None
    restored = make_window(loaded)

    assert all(
        dock.isVisible() for dock in restored._workspace_docks.values())
    assert all(
        not dock.isFloating()
        for key, dock in restored._workspace_docks.items()
        if key != "player"
    )


def test_settings_without_player_visibility_default_to_showing_it(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(
        '{"workspace_clips_visible": false}',
        encoding="utf-8",
    )

    loaded = AppSettings.load(target)

    assert loaded.workspace_player_visible
    assert not loaded.workspace_clips_visible
    assert loaded.workspace_maximized_docks_v1 == []


@pytest.mark.parametrize(
    ("geometry", "state"),
    [
        ("%%% not base64 %%%", ""),
        ("", encode_qbytearray(QByteArray(b"not a QMainWindow state"))),
    ],
)
def test_corrupt_workspace_values_fall_back_to_the_default_layout(
        qapp, settings, make_window, geometry, state):
    settings.workspace_geometry_v1 = geometry
    settings.workspace_state_v1 = state

    window = make_window(settings)
    docks = window._workspace_docks

    assert window.dockWidgetArea(docks["player"]) == \
        Qt.DockWidgetArea.TopDockWidgetArea
    assert window.dockWidgetArea(docks["clips"]) == \
        Qt.DockWidgetArea.LeftDockWidgetArea
    assert window.dockWidgetArea(docks["play_details"]) == \
        Qt.DockWidgetArea.RightDockWidgetArea
    assert all(not dock.isFloating() for dock in docks.values())
    assert docks["player"].isVisible()
    assert docks["player"].widget() is window._player_panel
    assert window.stack.maximumHeight() == 0
    assert decode_qbytearray(settings.workspace_geometry_v1) is not None
    assert decode_qbytearray(settings.workspace_state_v1) is not None


class _FakeScreen:
    def __init__(self, available: QRect) -> None:
        self._available = QRect(available)

    def availableGeometry(self) -> QRect:
        return QRect(self._available)


def test_offscreen_floating_player_is_redocked_without_recreating_playback(
        qapp, settings, make_window):
    primary = _FakeScreen(QRect(0, 0, 1920, 1080))
    secondary = _FakeScreen(QRect(1920, 0, 1920, 1080))
    window = make_window(settings)
    player_dock = window._workspace_docks["player"]
    owners = (
        window.player,
        window.player.player,
        window.player.audio,
        window.player.slider,
        window.quick_tag_tray,
    )

    player_dock.setFloating(True)
    player_dock.setGeometry(QRect(2100, 80, 760, 620))
    changed = redock_offscreen_floating_docks(
        window,
        {player_dock: Qt.DockWidgetArea.TopDockWidgetArea},
        [primary, secondary],
    )
    assert changed == []
    assert player_dock.isFloating()

    player_dock.setGeometry(QRect(5000, 5000, 760, 620))
    changed = redock_offscreen_floating_docks(
        window,
        {player_dock: Qt.DockWidgetArea.TopDockWidgetArea},
        [primary],
    )
    qapp.processEvents()

    assert changed == ["tapesift.dock.player"]
    assert not player_dock.isFloating()
    assert player_dock.isVisible()
    assert window.dockWidgetArea(player_dock) == \
        Qt.DockWidgetArea.TopDockWidgetArea
    assert player_dock.widget() is window._player_panel
    assert (
        window.player,
        window.player.player,
        window.player.audio,
        window.player.slider,
        window.quick_tag_tray,
    ) == owners
    assert window.stack.maximumHeight() == 0


def test_monitor_recovery_normalizes_fullscreen_player_and_saved_state(
        qapp, settings, make_window, monkeypatch):
    window = make_window(settings)
    player_dock = window._workspace_docks["player"]
    owners = (
        window.player,
        window.player.player,
        window.player.audio,
        window.player.slider,
        window.quick_tag_tray,
    )
    _float_dock(qapp, player_dock)
    player_dock.showFullScreen()
    _drain_events(qapp)
    window._workspace_maximized_docks.add("player")
    assert player_dock.isFullScreen()

    # Screen geometry is controlled by the platform plugin. Force only the
    # reachability verdict so this remains a deterministic recovery test.
    monkeypatch.setattr(
        workspace_dock_helpers,
        "geometry_has_reachable_title_bar",
        lambda _geometry, _screens=None: False,
    )
    window._recover_missing_monitor_docks()
    _drain_events(qapp)

    assert not player_dock.isFloating()
    assert player_dock.isVisible()
    assert player_dock.windowState() == Qt.WindowState.WindowNoState
    assert "player" not in window._workspace_maximized_docks
    assert not window._player_fullscreen
    assert player_dock.widget() is window._player_panel
    assert (
        window.player,
        window.player.player,
        window.player.audio,
        window.player.slider,
        window.quick_tag_tray,
    ) == owners
