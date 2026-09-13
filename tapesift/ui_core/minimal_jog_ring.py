"""Small, stateless jog input wrapped around the existing Play button."""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (
    QColor, QFont, QMouseEvent, QPainter, QPen, QRadialGradient, QWheelEvent)
from PySide6.QtWidgets import QGridLayout, QToolButton, QWidget

from tapesift.ui_v2.tokens import MOTION_RAMP, SEMANTIC, WHEEL

DEFAULT_FRAME_MS = 1000 / 30


def format_jog_timecode(position_ms: int, frame_ms: float | None = None) -> str:
    """Nominal non-drop HH:MM:SS:FF, using the same frame as the counter."""
    frame_ms = frame_ms or DEFAULT_FRAME_MS
    # Native PTS is truncated to milliseconds; recover the frame index before
    # splitting timecode so e.g. 4033 ms at 30 fps still names frame 121.
    frame_count = round(max(0, position_ms) / frame_ms)
    nominal_fps = max(1, round(1000 / frame_ms))
    total_seconds, frame = divmod(frame_count, nominal_fps)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame:02d}"


class MinimalJogRing(QWidget):
    """Emit relative frame requests without owning playback or playhead state."""

    framesRequested = Signal(int)
    interactionFinished = Signal()

    WIDTH = 132
    HEIGHT = 152
    # The dial is not centred in the widget: the two readouts own the strips
    # above and below it, which keeps them clear of the travelling marker.
    CENTER_Y = 78.0
    CENTER_DIAMETER = 50

    DEGREES_PER_FRAME = 10.0
    PIXELS_PER_FRAME = 24.0
    MAX_FRAMES_PER_EVENT = 4
    ACTIVE_HOLD_MS = 700

    # Dial geometry, all measured from the dial centre.
    FACE_RADIUS = 55.0
    SEAT_RADIUS = 27.0
    RING_RADIUS = 26.0
    TICK_OUTER = 52.5
    TICK_MINOR = 3.8
    TICK_MAJOR = 6.4
    TICK_STEP_DEG = 4.0
    MAJOR_EVERY = 5
    MARKER_RADIUS = 40.0
    LIT_SPREAD_DEG = 30.0
    MARKER_DOT = 3.2
    ARC_SPAN_DEG = 42.0

    # The Play glyph is drawn at the dial's scale, not the transport keys'.
    # Reusing their 18px icon would make the larger seat read as a smaller
    # button, which is the opposite of the point.
    ICON_SIZE = 26
    GLYPH_SIZE = 20

    TIMECODE_PT = 10.5
    STATUS_PT = 7.4
    READOUT_BAND = 19.0
    SHOW_STATUS = True

    def __init__(self, play_button: QToolButton | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MinimalJogRing")
        self.setAccessibleName("Jog playback frame by frame")
        self.setToolTip(
            "Drag clockwise or counterclockwise, or scroll, to jog frames")
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        # The hub is a grab surface, not a button. Play used to live inside
        # it, which meant the natural place to take hold of a jog wheel was
        # also the control that started playback - reach for the wheel,
        # start the film. Every console this borrows from puts transport
        # keys beside the wheel for exactly that reason.
        #
        # Still optional rather than removed: the dial is used on more than
        # one screen, and a caller that genuinely wants a seated button can
        # still pass one.
        self.play_button = play_button
        if self.play_button is None:
            self._build_empty_hub()
            return
        self.play_button.setFixedSize(
            self.CENTER_DIAMETER, self.CENTER_DIAMETER)
        # The seat is painted by this widget, so the button contributes only
        # its glyph. Nothing is left for an app-level rule to square off.
        # The selector stays unqualified because a widget stylesheet only
        # reaches that widget - naming it would tie the dial to one screen's
        # object names, and it is used on both Review and Library.
        self.play_button.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)
        if self.play_button is not None:
            self.play_button.installEventFilter(self)

        layout = QGridLayout(self)
        top = int(self.CENTER_Y) - self.CENTER_DIAMETER // 2
        layout.setContentsMargins(
            0, top, 0, self.HEIGHT - top - self.CENTER_DIAMETER)
        layout.addWidget(
            self.play_button, 0, 0,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._finish_setup()

    def _build_empty_hub(self) -> None:
        """No seated button: the hub is bare and the whole face is grabbable."""
        self._finish_setup()

    def _finish_setup(self) -> None:

        self._dragging = False
        self._last_pointer_angle = 0.0
        self._drag_remainder = 0.0
        self._wheel_remainder = 0.0
        self._visual_angle = -90.0
        self._timecode = "--:--:--:--"
        self._frame_text = ""
        self._active = False
        # Shuttle rate is display-only here. The dial does not drive
        # shuttling - the transport keys do - but it is where the eye
        # already is, so it is where the rate belongs.
        self._rate = 0.0

        # Wheel jogging has no release event, so the active readout is held
        # briefly and then falls back on its own.
        self._active_hold = QTimer(self)
        self._active_hold.setSingleShot(True)
        self._active_hold.setInterval(self.ACTIVE_HOLD_MS)
        self._active_hold.timeout.connect(self._release_active)

    def sizeHint(self) -> QSize:
        return QSize(self.WIDTH, self.HEIGHT)

    def dial_center(self) -> QPointF:
        return QPointF(self.width() / 2.0, self.CENTER_Y)

    def set_timecode(self, text: str) -> None:
        """Show the caller's authoritative playhead readout above the dial."""
        if text == self._timecode:
            return
        self._timecode = text
        self.update()

    # ------------------------------------------------------------------ paint

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = self.dial_center()

        self._paint_shadow(painter, center)
        self._paint_face(painter, center)
        self._paint_ticks(painter, center)
        self._paint_seat(painter, center)
        self._paint_rate(painter, center)
        self._paint_marker(painter, center)
        self._paint_hub_frame(painter, center)
        self._paint_readouts(painter)

    def _paint_shadow(self, painter: QPainter, center: QPointF) -> None:
        """Contact shadow: a dark core under the bottom edge that fades.

        Spec 11_SPEC_WHEEL_W1.md: dy 7, black ~62% near the contact, then
        fade. Max opaque radius must stay <= FACE_RADIUS + 16 (W01).
        The old radial glow read as a lens, not as the object sitting
        on the desktop (C4-05).
        """
        core = QColor(WHEEL["shadow"])
        glow = QRadialGradient(
            QPointF(center.x(), center.y() + 7.0), self.FACE_RADIUS * 1.05)
        core.setAlpha(int(255 * 0.62))
        glow.setColorAt(0.0, core)
        fade = QColor(WHEEL["shadow"])
        fade.setAlpha(int(255 * 0.28))
        glow.setColorAt(0.55, fade)
        gone = QColor(WHEEL["shadow"])
        gone.setAlpha(0)
        glow.setColorAt(1.0, gone)
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(
            QRectF(
                center.x() - self.FACE_RADIUS * 0.95,
                center.y() + 7.0 - self.FACE_RADIUS * 0.35,
                self.FACE_RADIUS * 1.9,
                self.FACE_RADIUS * 0.85))

    def _paint_face(self, painter: QPainter, center: QPointF) -> None:
        """One shallow dished disc, in the same material as the transport keys."""
        face = QRectF(
            center.x() - self.FACE_RADIUS, center.y() - self.FACE_RADIUS,
            self.FACE_RADIUS * 2, self.FACE_RADIUS * 2)
        dish = QRadialGradient(center, self.FACE_RADIUS)
        dish.setColorAt(0.0, QColor("#24261f"))
        dish.setColorAt(0.72, QColor("#1c1e17"))
        dish.setColorAt(1.0, QColor("#131510"))
        painter.setBrush(dish)
        painter.setPen(QPen(QColor("#3e4038"), 1.0))
        painter.drawEllipse(face.adjusted(0.5, 0.5, -0.5, -0.5))

    def _paint_ticks(self, painter: QPainter, center: QPointF) -> None:
        """A fixed scale; the marker lights the ticks it passes."""
        minor = QColor("#4a4d42")
        major = QColor("#6b6f61")
        lit = QColor(SEMANTIC["position"])

        steps = int(round(360.0 / self.TICK_STEP_DEG))
        for index in range(steps):
            angle = index * self.TICK_STEP_DEG
            is_major = index % self.MAJOR_EVERY == 0
            length = self.TICK_MAJOR if is_major else self.TICK_MINOR
            color = QColor(major if is_major else minor)

            # Ticks near the marker take on its colour, which is what makes a
            # static scale read as motion while the dial is being turned.
            nearness = self._angular_distance(angle, self._visual_angle)
            if nearness < self.LIT_SPREAD_DEG:
                blend = (1.0 - nearness / self.LIT_SPREAD_DEG) ** 1.4
                if not self._active:
                    blend *= 0.4
                color = self._mix(color, lit, blend)
                length += 1.5 * blend

            radians = math.radians(angle)
            cos_a, sin_a = math.cos(radians), math.sin(radians)
            pen = QPen(color, 1.3 if is_major else 1.0)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(
                    center.x() + cos_a * self.TICK_OUTER,
                    center.y() + sin_a * self.TICK_OUTER),
                QPointF(
                    center.x() + cos_a * (self.TICK_OUTER - length),
                    center.y() + sin_a * (self.TICK_OUTER - length)))

    def _paint_seat(self, painter: QPainter, center: QPointF) -> None:
        """The hub. A well when a button is seated in it, otherwise a plain
        machined face - the surface you take hold of to jog."""
        seat = QRectF(
            center.x() - self.SEAT_RADIUS, center.y() - self.SEAT_RADIUS,
            self.SEAT_RADIUS * 2, self.SEAT_RADIUS * 2)
        well = QRadialGradient(
            QPointF(center.x(), center.y() - self.SEAT_RADIUS * 0.4),
            self.SEAT_RADIUS * 1.6)
        well.setColorAt(0.0, QColor("#242619"))
        well.setColorAt(1.0, QColor("#0c0d0a"))
        painter.setBrush(well)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(seat)

        ring = QRectF(
            center.x() - self.RING_RADIUS, center.y() - self.RING_RADIUS,
            self.RING_RADIUS * 2, self.RING_RADIUS * 2)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(
            SEMANTIC["position"] if self._active
            else SEMANTIC["position_dim"]), 2.2))
        painter.drawEllipse(ring)

    def _paint_marker(self, painter: QPainter, center: QPointF) -> None:
        track = QRectF(
            center.x() - self.MARKER_RADIUS, center.y() - self.MARKER_RADIUS,
            self.MARKER_RADIUS * 2, self.MARKER_RADIUS * 2)
        arc = QPen(QColor(
            SEMANTIC["position"] if self._active
            else SEMANTIC["position_dim"]), 2.4)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(arc)
        span = self.ARC_SPAN_DEG
        painter.drawArc(
            track,
            round((-self._visual_angle - span) * 16),
            round(span * 16),
        )

        radians = math.radians(self._visual_angle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(
            "#a8e4f2" if self._active else SEMANTIC["position"]))
        painter.drawEllipse(
            QPointF(
                center.x() + math.cos(radians) * self.MARKER_RADIUS,
                center.y() + math.sin(radians) * self.MARKER_RADIUS),
            self.MARKER_DOT, self.MARKER_DOT)

    def _paint_rate(self, painter: QPainter, center: QPointF) -> None:
        """Shuttle rate as an arc growing from twelve o'clock.

        Direction is which way the arc sweeps; speed is how far it
        travels and how hot it burns. Drawn outside the tick ring so it
        never competes with the jog marker - one is where you are, the
        other is how fast you are leaving.
        """
        if abs(self._rate) <= 0.001:
            return
        magnitude = min(abs(self._rate), 8.0)
        # Log scale: 1x..8x is four doublings, and a linear arc would
        # spend most of its travel between 4x and 8x where it matters
        # least.
        fraction = math.log2(max(magnitude, 0.25) * 4.0) / 5.0
        fraction = max(0.06, min(1.0, fraction))
        span = 150.0 * fraction
        step = min(len(MOTION_RAMP) - 1, int(fraction * len(MOTION_RAMP)))
        pen = QPen(QColor(MOTION_RAMP[step]), 3.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        radius = self.FACE_RADIUS - 1.5
        track = QRectF(
            center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        if self._rate > 0:
            painter.drawArc(track, round(90 * 16), round(-span * 16))
        else:
            painter.drawArc(track, round(90 * 16), round(span * 16))

    def _rate_label(self) -> str:
        magnitude = abs(self._rate)
        text = f"{magnitude:g}×"
        return f"{'FWD' if self._rate > 0 else 'REV'} {text}"

    def set_shuttle_rate(self, rate: float) -> None:
        """Display the current shuttle rate. 0 means stopped."""
        rate = float(rate or 0.0)
        if abs(rate - self._rate) < 0.001:
            return
        self._rate = rate
        self.update()

    def set_frame_number(self, text: str) -> None:
        """Frame counter for the hub. Only drawn when no button is seated."""
        if text == self._frame_text:
            return
        self._frame_text = text
        self.update()

    def _paint_hub_frame(self, painter: QPainter, center: QPointF) -> None:
        if self.play_button is not None or not self._frame_text:
            return
        font = QFont(["Consolas", "Cascadia Mono", "Courier New"])
        font.setPointSizeF(9.0)
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)
        painter.setPen(QColor(
            SEMANTIC["position"] if self._active else "#8e9a8c"))
        painter.drawText(
            QRectF(center.x() - self.SEAT_RADIUS,
                   center.y() - 8.0,
                   self.SEAT_RADIUS * 2, 16.0),
            Qt.AlignmentFlag.AlignCenter,
            self._frame_text)

    def _paint_readouts(self, painter: QPainter) -> None:
        # Tabular type, so the frame counter does not jitter as digits change.
        timecode_font = QFont(["Consolas", "Cascadia Mono", "Courier New"])
        timecode_font.setPointSizeF(self.TIMECODE_PT)
        timecode_font.setStyleHint(QFont.StyleHint.Monospace)
        timecode_font.setLetterSpacing(
            QFont.SpacingType.AbsoluteSpacing, 0.4)
        painter.setFont(timecode_font)
        painter.setPen(QColor("#e6f4f8" if self._active else "#96907f"))
        painter.drawText(
            QRectF(0.0, 0.0, self.width(), self.READOUT_BAND),
            Qt.AlignmentFlag.AlignCenter,
            self._timecode,
        )

        if not self.SHOW_STATUS:
            return

        status_font = QFont(["Rajdhani", "Segoe UI"])
        status_font.setPointSizeF(self.STATUS_PT)
        status_font.setBold(True)
        status_font.setLetterSpacing(
            QFont.SpacingType.AbsoluteSpacing, 1.0)
        painter.setFont(status_font)
        # Rate wins the status line when shuttling: while the film is
        # running at 4x, "±1 FRAME" is not the useful fact. Text, not
        # only colour - the ramp is the glanceable version of this, and
        # colour alone would fail in a bright room or for a colour-blind
        # reader.
        if abs(self._rate) > 0.001:
            painter.setPen(QColor(SEMANTIC["motion_hi"]))
            status = self._rate_label()
        else:
            painter.setPen(QColor(
                SEMANTIC["position"] if self._active else "#7d857e"))
            status = "JOG ACTIVE" if self._active else "±1 FRAME"
        painter.drawText(
            QRectF(0.0, self.height() - 13.0, self.width(), 12.0),
            Qt.AlignmentFlag.AlignCenter,
            status,
        )

    # ------------------------------------------------------------------ input

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or \
                not self._inside_ring(event.position()):
            super().mousePressEvent(event)
            return
        self._dragging = True
        self._drag_remainder = 0.0
        self._last_pointer_angle = self._pointer_angle(event.position())
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._set_active(True)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging:
            super().mouseMoveEvent(event)
            return
        if not event.buttons() & Qt.MouseButton.LeftButton:
            self.cancel_interaction()
            event.accept()
            return

        current = self._pointer_angle(event.position())
        delta = (current - self._last_pointer_angle + 180.0) % 360.0 - 180.0
        self._last_pointer_angle = current
        self._visual_angle = (self._visual_angle + delta) % 360.0
        self._drag_remainder += delta
        frames = math.trunc(self._drag_remainder / self.DEGREES_PER_FRAME)
        frames = max(
            -self.MAX_FRAMES_PER_EVENT,
            min(self.MAX_FRAMES_PER_EVENT, frames),
        )
        if frames:
            self._drag_remainder -= frames * self.DEGREES_PER_FRAME
            self.framesRequested.emit(frames)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self.cancel_interaction(notify=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self._handle_wheel(event):
            super().wheelEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.play_button and event.type() == QEvent.Type.Wheel:
            return self._handle_wheel(event)
        return super().eventFilter(watched, event)

    def hideEvent(self, event) -> None:
        self.cancel_interaction()
        # Nothing may still be pending once the dial leaves the screen; a
        # timer that outlives the widget is a teardown hazard.
        self._active_hold.stop()
        self._active = False
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.cancel_interaction()
        super().changeEvent(event)

    def cancel_interaction(self, *, notify: bool = False) -> None:
        was_dragging = self._dragging
        self._dragging = False
        self._drag_remainder = 0.0
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if was_dragging:
            self._active_hold.start()
        if notify and was_dragging:
            self.interactionFinished.emit()

    def _handle_wheel(self, event: QWheelEvent) -> bool:
        pixels = event.pixelDelta()
        if not pixels.isNull():
            raw_delta = pixels.y() or -pixels.x()
            units = raw_delta / self.PIXELS_PER_FRAME
        else:
            angles = event.angleDelta()
            raw_delta = angles.y() or -angles.x()
            units = raw_delta / 120.0
        if not raw_delta:
            event.ignore()
            return False

        units = max(
            -self.MAX_FRAMES_PER_EVENT,
            min(self.MAX_FRAMES_PER_EVENT, units),
        )
        self._wheel_remainder += units
        frames = math.trunc(self._wheel_remainder)
        if frames:
            self._wheel_remainder -= frames
            self._visual_angle = (
                self._visual_angle + frames * self.DEGREES_PER_FRAME
            ) % 360.0
            self.framesRequested.emit(frames)
            self._set_active(True)
            self._active_hold.start()
            self.update()
        event.accept()
        return True

    # ----------------------------------------------------------------- helpers

    def _release_active(self) -> None:
        self._set_active(False)

    def _set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        if active:
            self._active_hold.stop()
        self.update()

    @staticmethod
    def _angular_distance(first: float, second: float) -> float:
        return abs((first - second + 180.0) % 360.0 - 180.0)

    @staticmethod
    def _mix(base: QColor, target: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, amount))
        return QColor(
            round(base.red() + (target.red() - base.red()) * amount),
            round(base.green() + (target.green() - base.green()) * amount),
            round(base.blue() + (target.blue() - base.blue()) * amount),
        )

    def _inside_ring(self, point: QPointF) -> bool:
        """Only the dial face grabs a drag; the readout strips do not."""
        center = self.dial_center()
        return math.hypot(
            point.x() - center.x(),
            point.y() - center.y(),
        ) <= self.FACE_RADIUS

    def _pointer_angle(self, point: QPointF) -> float:
        center = self.dial_center()
        return math.degrees(math.atan2(
            point.y() - center.y(),
            point.x() - center.x(),
        ))


class CompactJogRing(MinimalJogRing):
    """The same dial at preview scale, for panels that only have a strip.

    Everything behaves identically; only the geometry and type shrink. The
    status line is dropped because at this size it would be unreadable, and
    the timecode is the readout that actually earns its space.
    """

    WIDTH = 68
    HEIGHT = 76
    CENTER_Y = 43.0
    CENTER_DIAMETER = 28

    FACE_RADIUS = 31.0
    SEAT_RADIUS = 15.5
    RING_RADIUS = 14.5
    TICK_OUTER = 29.5
    TICK_MINOR = 2.2
    TICK_MAJOR = 3.6
    TICK_STEP_DEG = 5.0
    MAJOR_EVERY = 5
    MARKER_RADIUS = 22.5
    MARKER_DOT = 2.0
    ARC_SPAN_DEG = 46.0

    ICON_SIZE = 18
    GLYPH_SIZE = 15

    TIMECODE_PT = 5.5
    READOUT_BAND = 11.0
    SHOW_STATUS = False
