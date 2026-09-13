"""Persistent Telestration rail and collapsible Tag Map workspace."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QVBoxLayout, QWidget,
)

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.ui_core.video_player import VideoPlayer  # noqa: E402
from tapesift.ui_v2.control_center import ControlCenterDeck  # noqa: E402


@pytest.fixture(scope="module")
def qapp_guard():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def player(qapp_guard):
    settings = AppSettings()
    settings.save = lambda *args, **kwargs: None
    owner = QWidget()
    owner_layout = QVBoxLayout(owner)
    value = VideoPlayer(settings, owner)
    deck = ControlCenterDeck(owner)
    value.attach_control_center(deck)
    owner_layout.addWidget(value)
    owner_layout.addWidget(deck)
    yield value
    value.unload()
    owner.deleteLater()
    qapp_guard.processEvents()


def test_starts_with_tag_map_expanded(player):
    assert not player.tag_map_collapsed()
    assert not player.attribute_grid.isHidden()
    assert not player.timeline_header.isHidden()


def test_view_controls_precede_tag_map_header_and_grid(player):
    layout = player.layout()
    assert layout.indexOf(player.view_strip) < \
        layout.indexOf(player.timeline_header) < \
        layout.indexOf(player.attribute_grid)


def test_obsolete_workspace_mode_is_removed(player):
    assert not hasattr(player, "workspace_mode_group")
    assert not hasattr(player, "analysis_panel")
    assert not hasattr(player, "set_workspace_mode")


def test_one_persistent_telestration_rail_sits_beside_film(player):
    rails = player.findChildren(QWidget, "TelestrationRail")
    assert rails == [player.telestration_rail]
    assert player.telestration_rail.parentWidget() is player
    assert player.video_widget.parentWidget() is player


def test_collapse_hides_complete_tag_map_but_keeps_header(player):
    player.set_tag_map_collapsed(True)
    assert player.tag_map_collapsed()
    assert player.attribute_grid.isHidden()
    assert not player.timeline_header.isHidden()
    assert player.tag_map_collapse_button.text().startswith("EXPAND")


def test_expand_restores_tag_map(player):
    player.set_tag_map_collapsed(True)
    player.set_tag_map_collapsed(False)
    assert not player.tag_map_collapsed()
    assert not player.attribute_grid.isHidden()
    assert player.tag_map_collapse_button.text().startswith("COLLAPSE")


def test_responsive_fold_is_transient_and_manual_fold_remains_authoritative(
        player):
    player.settings.workspace_tag_map_collapsed = False
    # The rail is greyed until a play owns the strokes.
    player.set_telestration_enabled(True)
    # The three legacy keys are gone; the library arms every shape now.
    player._shape_library_picked("route_arrow")
    snap = player.timeline_snap_button.isChecked()

    player._set_responsive_tag_map_collapsed(True)
    assert player._responsive_tag_map_collapsed
    assert not player.tag_map_collapsed()
    assert player.settings.workspace_tag_map_collapsed is False
    assert player.attribute_grid.isHidden()
    assert player.tag_map_collapse_button.isChecked()
    # The control stays operable while the window is short: a visible
    # EXPAND button that does nothing when clicked reads as broken.
    assert player.tag_map_collapse_button.isEnabled()
    assert player.video_widget.tool() == "route_arrow"
    assert player.timeline_snap_button.isChecked() is snap

    player._set_responsive_tag_map_collapsed(False)
    assert not player._responsive_tag_map_collapsed
    assert not player.tag_map_collapsed()
    assert not player.attribute_grid.isHidden()
    assert player.tag_map_collapse_button.isEnabled()

    player.set_tag_map_collapsed(True)
    player._set_responsive_tag_map_collapsed(True)
    player._set_responsive_tag_map_collapsed(False)
    assert player.tag_map_collapsed()
    assert player.settings.workspace_tag_map_collapsed is True
    assert player.attribute_grid.isHidden()
    assert player.tag_map_collapse_button.isChecked()
    assert player.tag_map_collapse_button.isEnabled()


def test_quick_tags_follow_whole_tag_map_collapse(player):
    tray = QWidget()
    player.mount_quick_tags(tray)
    assert not player.quick_tag_slot.isHidden()
    player.set_tag_map_collapsed(True)
    assert player.quick_tag_slot.isHidden()
    player.set_tag_map_collapsed(False)
    assert not player.quick_tag_slot.isHidden()
    assert tray.parentWidget() is player.quick_tag_slot


def test_rapid_collapse_expand_is_stable(player):
    for _index in range(20):
        player.toggle_tag_map_collapsed()
    assert not player.tag_map_collapsed()
    assert not player.attribute_grid.isHidden()


def test_tag_map_keeps_its_direct_parent(player):
    player.toggle_tag_map_collapsed()
    player.toggle_tag_map_collapsed()
    assert player.attribute_grid.parent() is player


def test_snap_and_telestration_state_are_untouched(player):
    assert player.timeline_snap_button.isChecked()
    # The rail is greyed until a play owns the strokes.
    player.set_telestration_enabled(True)
    player._shape_library_picked("route_arrow")
    assert player.video_widget.tool() == "route_arrow"
    player.set_tag_map_collapsed(True)
    assert player.timeline_snap_button.isChecked()
    assert player.video_widget.tool() == "route_arrow"


def test_saved_collapse_preference_is_restored(qapp_guard):
    settings = AppSettings(workspace_tag_map_collapsed=True)
    settings.save = lambda *args, **kwargs: None
    owner = QWidget()
    owner_layout = QVBoxLayout(owner)
    value = VideoPlayer(settings, owner)
    deck = ControlCenterDeck(owner)
    value.attach_control_center(deck)
    owner_layout.addWidget(value)
    owner_layout.addWidget(deck)
    try:
        assert value.tag_map_collapsed()
        assert value.attribute_grid.isHidden()
        assert value.tag_map_collapse_button.isChecked()
    finally:
        value.unload()
        owner.deleteLater()
        qapp_guard.processEvents()


def test_expanding_beats_the_short_window_fold(player):
    """Asking for the Tag Map wins over the height heuristic.

    The fold is a sensible default on a short window, not a veto. It used
    to disable the button, so a visible EXPAND did nothing when clicked.
    """
    player._set_responsive_tag_map_collapsed(True)
    assert player._tag_map_effectively_collapsed()
    assert player.tag_map_collapse_button.isEnabled()

    player.set_tag_map_collapsed(False)
    assert not player._responsive_tag_map_collapsed
    assert not player._tag_map_effectively_collapsed()
    assert not player.attribute_grid.isHidden()
