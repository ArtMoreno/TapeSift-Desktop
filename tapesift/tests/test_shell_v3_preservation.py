from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tapesift.core.config import AppSettings
from tapesift.ui_v2.attribute_grid import AttributeGrid
from tapesift.ui_v2.dock_v2 import DockV2Deck
from tapesift.ui_v2.jog_window import JogWindow
from tapesift.ui_v3.main_window import MainWindowV3


def test_v3_reuses_player_deck_wheel_and_tag_map(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindowV3(
        AppSettings(), workspace_state_path=tmp_path / "shell-v3.json")
    assert isinstance(window.control_center, DockV2Deck)
    assert isinstance(window.control_center.jog_window, JogWindow)
    assert isinstance(window.player.attribute_grid, AttributeGrid)
    assert window.control_center.jog_window.size().width() == 196
    assert window.control_center.jog_window.size().height() == 196
    assert window.player.telestration_rail.isHidden()
    assert window.control_center.parent() is not None
    assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint


def test_v3_keeps_one_player_and_one_attribute_grid(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindowV3(
        AppSettings(), workspace_state_path=tmp_path / "shell-v3.json")
    from tapesift.ui_core.video_player import VideoPlayer
    assert len(window.findChildren(VideoPlayer)) == 1
    assert len(window.findChildren(AttributeGrid)) == 1
