"""The V3 console presents the existing wheel without taking playback ownership."""

import math
from types import SimpleNamespace

from PySide6.QtCore import QPoint, QPointF, Qt, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tapesift.ui_v2.dock_v2 import DockV2Deck
from tapesift.ui_v3.jog_console import V3JogConsole


def _console():
    app = QApplication.instance() or QApplication([])
    deck = DockV2Deck()
    calls = []
    names = ("jump_backward", "shuttle_reverse", "toggle_play", "shuttle_forward",
             "jump_forward", "_jog_frames_requested", "frame_step_backward",
             "frame_step_forward", "set_range_loop", "set_in_point",
             "set_out_point", "request_add_clip")
    player = SimpleNamespace(**{name: (lambda *args, n=name: calls.append((n, args)))
                                for name in names})
    deck.bind_player(player)
    deck.bind_player(player)
    wheel = deck.jog_ring
    console = V3JogConsole(deck)
    assert console.wheel is wheel is deck.jog_window.wheel
    assert deck.bound_player is player
    deck.show()
    deck.jog_toggle.setChecked(True)
    app.processEvents()
    calls.clear()
    return app, deck, console, calls


def _move(widget, position, global_position=None):
    position = QPointF(position)
    global_position = global_position or QPointF(widget.mapToGlobal(position.toPoint()))
    QApplication.sendEvent(widget, QMouseEvent(
        QEvent.Type.MouseMove, position, global_position,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def test_ring_jogs_header_moves_and_bound_controls_dispatch_once():
    app, deck, console, calls = _console()
    host, wheel = console.host, console.wheel
    assert (host.width(), host.height()) == (420, 246)
    assert host.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    initial = QPoint(host.pos())
    center = wheel.dial_center()
    for degrees, expected in ((15, 1), (-15, -1), (90, 4)):
        calls.clear()
        start = center + QPointF(80, 0)
        end = center + QPointF(80 * math.cos(math.radians(degrees)),
                              80 * math.sin(math.radians(degrees)))
        QTest.mousePress(wheel, Qt.MouseButton.LeftButton, pos=start.toPoint())
        _move(wheel, end)
        QTest.mouseRelease(wheel, Qt.MouseButton.LeftButton, pos=end.toPoint())
        assert calls == [("_jog_frames_requested", (expected,))]
        assert host.pos() == initial
        assert not wheel._dragging
    calls.clear()
    header = console.header
    start = QPoint(202, 15)
    origin = header.mapToGlobal(start)
    QTest.mousePress(header, Qt.MouseButton.LeftButton, pos=start)
    _move(header, start + QPoint(28, 16), QPointF(origin + QPoint(28, 16)))
    assert host.pos() == initial + QPoint(28, 16)
    QTest.mouseRelease(header, Qt.MouseButton.LeftButton, pos=start)
    assert host._has_user_position and not host._moving
    assert calls == []
    for button, command in ((console.backward, "frame_step_backward"),
                            (console.forward, "frame_step_forward")):
        calls.clear()
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        assert calls == []
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
        assert calls == [(command, ())]
        calls.clear()
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=QPoint(-12, -12))
        assert calls == []
    QTest.mouseClick(wheel, Qt.MouseButton.LeftButton, pos=center.toPoint())
    assert calls == [("toggle_play", ())]
    assert not wheel._playing
    assert all(icon.isValid() for icon in console.icons.values())
    assert DockV2Deck().jog_window.size().width() == 196


def test_console_mirrors_painted_readouts_and_actual_forward_or_reverse_state():
    app, deck, console, calls = _console()
    deck.set_position("00:00:16:00", 480)
    console.wheel.grab()
    assert console.timecode.text() == "00:00:16:00"
    assert console.frame.text() == "F 480"
    assert console.host.rate_label.text() == "PAUSED"
    deck.set_playing(True)
    deck.set_shuttle_rate(0)
    console.wheel.grab()
    assert console.host.rate_label.text() == "FWD 1x"
    deck.set_playing(False)
    deck.set_shuttle_rate(-2)
    console.wheel.grab()
    assert console.host.rate_label.text() == "REV 2x"
    deck.set_shuttle_rate(0)
    console.wheel.grab()
    assert console.host.rate_label.text() == "PAUSED"
    assert console.frame.text() == "F 480"
    assert calls == []


def test_hidden_disabled_and_canceled_presses_cannot_fire_on_reopen():
    app, deck, console, calls = _console()
    host, wheel = console.host, console.wheel
    center = wheel.dial_center().toPoint()
    for target, point in ((wheel, center), (console.backward, console.backward.rect().center()),
                          (console.forward, console.forward.rect().center())):
        for cancel in ("hide", "disable"):
            calls.clear()
            QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=point)
            if cancel == "hide":
                host.hide()
                host.show()
            else:
                target.setEnabled(False)
                target.setEnabled(True)
            app.processEvents()
            assert not wheel._hub_pressed
            QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=point)
            assert calls == []
    QTest.mousePress(wheel, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseRelease(wheel, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert calls == []
    QTest.mousePress(wheel, Qt.MouseButton.LeftButton, pos=center)
    QApplication.sendEvent(wheel, QEvent(QEvent.Type.UngrabMouse))
    QTest.mouseRelease(wheel, Qt.MouseButton.LeftButton, pos=center)
    assert calls == []
    # Placement is retained after the user moves the header; before that,
    # show_beside intentionally follows the launcher when its layout changes.
    header_point = QPoint(202, 15)
    QTest.mousePress(console.header, Qt.MouseButton.LeftButton, pos=header_point)
    _move(console.header, header_point + QPoint(20, 10))
    QTest.mouseRelease(console.header, Qt.MouseButton.LeftButton, pos=header_point)
    assert host._has_user_position
    position = QPoint(host.pos())
    QTest.mouseClick(console.close, Qt.MouseButton.LeftButton)
    assert not host.isVisible() and not deck.jog_toggle.isChecked()
    deck.jog_toggle.setChecked(True)
    app.processEvents()
    assert host.isVisible() and host.pos() == position
    assert not wheel._dragging and not wheel._hub_pressed
