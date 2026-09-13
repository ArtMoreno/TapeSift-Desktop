"""The standard V3 drawing must leave native button dispatch authoritative."""

from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tapesift.ui_v2.dock_v2 import DockV2Deck
from tapesift.ui_v3.theme import V3_REVIEW_CONTROL_CENTER_MATERIAL
from tapesift.ui_v3.transport_surface import V3TransportSurface


def test_live_transport_feedback_keeps_single_and_canceled_dispatch():
    app = QApplication.instance() or QApplication([])
    deck = DockV2Deck()
    deck.setStyleSheet(deck.styleSheet() + V3_REVIEW_CONTROL_CENTER_MATERIAL)
    renderer = V3TransportSurface(deck)
    calls = []
    names = ("jump_backward", "shuttle_reverse", "toggle_play", "shuttle_forward",
             "jump_forward", "_jog_frames_requested", "frame_step_backward",
             "frame_step_forward", "set_range_loop", "set_in_point",
             "set_out_point", "request_add_clip")
    player = SimpleNamespace(**{name: (lambda *args, n=name: calls.append(n))
                                for name in names})
    deck.bind_player(player)
    deck.bind_player(player)
    deck.resize(1100, deck.DECK_HEIGHT)
    deck.show()
    app.processEvents()
    geometry = tuple(b.geometry() for b in renderer.buttons)
    expected = ("frame_step_backward", "shuttle_reverse", "toggle_play",
                "shuttle_forward", "frame_step_forward")
    surface = deck.transport_island

    for index, button in enumerate(renderer.buttons):
        calls.clear()
        deck.play_btn.clearFocus()
        QTest.mouseMove(deck, QPoint(1, 1))
        app.processEvents()
        rest = surface.grab().toImage()
        QTest.mouseMove(button, button.rect().center())
        app.processEvents()
        hover = surface.grab().toImage()
        assert button.underMouse()
        assert rest.copy(geometry[index]) != hover.copy(geometry[index])
        for neighbor in (i for i in range(5) if i != index):
            # The established hit rectangles overlap by two pixels at Play.
            inner = geometry[neighbor].adjusted(3, 3, -3, -3)
            assert rest.copy(inner) == hover.copy(inner)
        assert calls == []

        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert button.isDown()
        assert surface.grab().toImage().copy(geometry[index]) != hover.copy(geometry[index])
        assert calls == []
        assert not surface._playing
        outside = QPoint(-15, -20)
        QTest.mouseMove(button, outside)
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=outside)
        app.processEvents()
        assert not button.isDown()
        assert calls == []
        assert not surface._playing

        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert calls == [expected[index]]
        # A command request alone cannot predict the player-reported glyph.
        assert not surface._playing
        calls.clear()
        button.setEnabled(False)
        app.processEvents()
        assert surface.grab().toImage().copy(geometry[index]) != hover.copy(geometry[index])
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert calls == []
        button.setEnabled(True)
        assert tuple(b.geometry() for b in renderer.buttons) == geometry
    assert deck.bound_player is player
    assert all(icon.isValid() for icon in renderer.icons.values())


def test_live_play_focus_and_reported_state_survive_geometry_reapplication():
    app = QApplication.instance() or QApplication([])
    deck = DockV2Deck()
    renderer = V3TransportSurface(deck)
    deck.resize(1100, deck.DECK_HEIGHT)
    deck.show()
    app.processEvents()
    buttons = renderer.buttons
    geometry = tuple(b.geometry() for b in buttons)
    for playing in (True, False, True, False):
        deck.set_playing(playing)
        deck._apply_premium_geometry()
        deck.play_btn.setFocus(Qt.FocusReason.TabFocusReason)
        app.processEvents()
        focused = deck.transport_island.grab().toImage()
        assert deck.play_btn.hasFocus()
        deck.play_btn.clearFocus()
        app.processEvents()
        assert focused != deck.transport_island.grab().toImage()
        assert deck.play_btn.graphicsEffect().isEnabled()
        assert deck.play_btn.graphicsEffect().opacity() == 0
        assert renderer.surface._playing == playing
        assert deck.play_btn.accessibleName() == (
            "Pause" if playing else "Play") + " playback (Space)"
        assert renderer.buttons == buttons
        assert tuple(b.geometry() for b in buttons) == geometry
