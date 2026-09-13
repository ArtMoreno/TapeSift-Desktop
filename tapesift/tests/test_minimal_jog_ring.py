"""Focused interaction contract for the minimal playback jog ring."""

from __future__ import annotations

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402

from tapesift.ui_core.minimal_jog_ring import (  # noqa: E402
    MinimalJogRing, format_jog_timecode,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ring(qapp):
    play_button = QToolButton()
    play_button.setObjectName("TransportPlayPause")
    widget = MinimalJogRing(play_button)
    yield widget
    widget.deleteLater()
    qapp.processEvents()


def _wheel_event(*, angle_y: int = 0, pixel_y: int = 0) -> QWheelEvent:
    return QWheelEvent(
        QPointF(34, 34),
        QPointF(34, 34),
        QPoint(0, pixel_y),
        QPoint(0, angle_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )


def _on_ring(widget: MinimalJogRing, degrees: float) -> QPointF:
    """A point on the dial's drag band, independent of its current size."""
    center = widget.dial_center()
    radius = widget.FACE_RADIUS - 6.0
    radians = math.radians(degrees)
    return QPointF(
        center.x() + math.cos(radians) * radius,
        center.y() + math.sin(radians) * radius,
    )


def _mouse_event(
        event_type: QEvent.Type, point: QPointF,
        button: Qt.MouseButton, buttons: Qt.MouseButton) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        point,
        point,
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def test_wheel_detents_emit_exact_signed_frames(ring):
    frames = []
    ring.framesRequested.connect(frames.append)

    forward = _wheel_event(angle_y=120)
    backward = _wheel_event(angle_y=-120)
    ring.wheelEvent(forward)
    ring.wheelEvent(backward)

    assert frames == [1, -1]
    assert forward.isAccepted()
    assert backward.isAccepted()


def test_high_resolution_wheel_delta_is_accumulated(ring):
    frames = []
    ring.framesRequested.connect(frames.append)

    ring.wheelEvent(_wheel_event(angle_y=60))
    assert frames == []
    ring.wheelEvent(_wheel_event(angle_y=60))

    assert frames == [1]


def test_drag_emits_frame_and_release_clears_capture_state(ring):
    frames = []
    finished = []
    ring.framesRequested.connect(frames.append)
    ring.interactionFinished.connect(lambda: finished.append(True))

    start = _on_ring(ring, -90.0)
    end = _on_ring(ring, -90.0 + ring.DEGREES_PER_FRAME * 1.5)
    press = _mouse_event(
        QEvent.Type.MouseButtonPress, start,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    move = _mouse_event(
        QEvent.Type.MouseMove, end,
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    release = _mouse_event(
        QEvent.Type.MouseButtonRelease, end,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)

    ring.mousePressEvent(press)
    assert ring._dragging
    ring.mouseMoveEvent(move)
    ring.mouseReleaseEvent(release)

    assert frames == [1]
    assert finished == [True]
    assert not ring._dragging
    assert ring.cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_center_play_button_keeps_its_native_click(ring, qapp):
    clicks = []
    ring.play_button.clicked.connect(lambda: clicks.append(True))
    ring.show()
    qapp.processEvents()

    QTest.mouseClick(ring.play_button, Qt.MouseButton.LeftButton)

    assert clicks == [True]


def test_disable_cancels_an_active_drag(ring):
    press = _mouse_event(
        QEvent.Type.MouseButtonPress, _on_ring(ring, -90.0),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    ring.mousePressEvent(press)
    assert ring._dragging

    ring.setEnabled(False)

    assert not ring._dragging
    assert ring.cursor().shape() == Qt.CursorShape.OpenHandCursor


@pytest.mark.parametrize(
    ("position", "frame_ms", "expected"),
    ((0, 1000 / 30, "00:00:00:00"),
     (33, 1000 / 30, "00:00:00:01"),
     (966, 1000 / 30, "00:00:00:29"),
     (4_033, 1000 / 30, "00:00:04:01"),
     (3_599_966, 1000 / 30, "00:59:59:29"),
     (1_001, 1001 / 30, "00:00:01:00"),
     (60_060, 1001 / 30, "00:01:00:00"),
     (16, 1001 / 60, "00:00:00:01"),
     (60_060, 1001 / 60, "00:01:00:00")),
)
def test_timecode_names_the_same_frame_at_integer_and_fractional_rates(
        position, frame_ms, expected):
    assert format_jog_timecode(position, frame_ms) == expected
