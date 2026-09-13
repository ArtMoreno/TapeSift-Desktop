"""The jog wheel as a floating panel.

The wheel is the largest object in the transport and the one used least often
per session: it earns its size while you are scrubbing a play and costs a
hundred pixels of film height the rest of the time. Floating it keeps the
gesture exactly as it was and returns the band to a thin strip.

This is a `Qt.Tool` window rather than a popup: it stays open until it is
closed, so it can be parked on a second monitor and left there.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from tapesift.ui_v2.smooth_wheel import SmoothJogWheel

class JogWindow(QWidget):
    """A transparent top-level host: visually the knob floats by itself."""

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(parent, flags)
        self.setObjectName("JogWindow")
        self.setWindowTitle("Play / Pause")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)
        self._moving = False
        self._move_pointer_origin = QPoint()
        self._move_window_origin = QPoint()
        self._has_user_position = False

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        # Kept as a hidden compatibility/status sink for existing callers and
        # tests. The visible state is painted inside the wheel hub.
        self.rate_label = QLabel("PAUSED", self)
        self.rate_label.setObjectName("JogWindowRate")
        self.rate_label.hide()

        self.wheel = SmoothJogWheel(parent=self)
        self.wheel.setToolTip(
            "Click the center to play or pause. Drag the outer ring to move. "
            "Toggle JOG to hide.")
        self.wheel.installEventFilter(self)
        column.addWidget(self.wheel)
        self.setFixedSize(self.wheel.sizeHint())

    def set_rate(self, rate: float) -> None:
        """Mirror the engine's rate onto the wheel arc and the readout."""
        self.wheel.set_shuttle_rate(rate)
        if abs(float(rate or 0.0)) <= 0.001:
            self.rate_label.setText("PAUSED")
        else:
            direction = "FWD" if rate > 0 else "REV"
            self.rate_label.setText(f"{direction} {abs(rate):g}x")

    def show_beside(self, anchor: QWidget) -> None:
        """Open beside the strip once; preserve later analyst placement."""
        self.adjustSize()
        if not self._has_user_position:
            corner = anchor.mapToGlobal(QPoint(0, 0))
            proposed = QPoint(
                corner.x() + (anchor.width() - self.width()) // 2,
                corner.y() - self.height() - 24,
            )
            screen = anchor.screen()
        else:
            proposed = self.pos()
            screen = QGuiApplication.screenAt(
                proposed + QPoint(self.width() // 2, self.height() // 2))
            if screen is None:
                screen = self.screen()
        self.move(self._clamped_position(proposed, screen))
        self.show()
        self.raise_()

    def _clamped_position(self, proposed: QPoint, screen) -> QPoint:
        if screen is None:
            return proposed
        available = screen.availableGeometry()
        return QPoint(
            min(max(available.left() + 8, proposed.x()),
                available.right() - self.width() - 8),
            min(max(available.top() + 8, proposed.y()),
                available.bottom() - self.height() - 8),
        )

    def eventFilter(self, watched, event) -> bool:
        if watched is self.wheel:
            kind = event.type()
            if kind == QEvent.Type.MouseButtonPress:
                # The center is the Play/Pause button. The rest of the wheel
                # is a plain grab surface, so moving it needs no modifier.
                move_gesture = event.button() == Qt.MouseButton.RightButton \
                    or (event.button() == Qt.MouseButton.LeftButton
                        and not self.wheel._inside_hub(event.position()))
            else:
                move_gesture = False
            if move_gesture:
                self._moving = True
                self._move_pointer_origin = event.globalPosition().toPoint()
                self._move_window_origin = self.pos()
                self.wheel.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return True
            if kind == QEvent.Type.MouseMove and self._moving:
                delta = event.globalPosition().toPoint() \
                    - self._move_pointer_origin
                self.move(self._move_window_origin + delta)
                self._has_user_position = True
                event.accept()
                return True
            if kind == QEvent.Type.MouseButtonRelease and self._moving:
                self._moving = False
                self.wheel.setCursor(Qt.CursorShape.OpenHandCursor)
                screen = QGuiApplication.screenAt(
                    event.globalPosition().toPoint()) or self.screen()
                self.move(self._clamped_position(self.pos(), screen))
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def hideEvent(self, event) -> None:
        self._moving = False
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._moving = False
        self.closed.emit()
        super().closeEvent(event)
