"""Professional full-height jog wheel for the window-owned control deck.

The base :class:`MinimalJogRing` remains the input and state authority.  This
module changes only its deck geometry and painting so the selected console
layout can be reproduced without forking drag, wheel, or frame-step behavior.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QPolygonF,
    QRadialGradient,
)

from tapesift.ui_core.minimal_jog_ring import MinimalJogRing
from tapesift.ui_v2.tokens import MOTION_RAMP, SEMANTIC


class ProfessionalJogWheel(MinimalJogRing):
    """A 165 px vernier face centered in the deck's full-height wheel bay."""

    WIDTH = 242
    HEIGHT = 197
    CENTER_Y = 82.5
    CENTER_DIAMETER = 54

    # The selected second layout's visible outside face is exactly 165 px.
    FACE_RADIUS = 82.5
    SEAT_RADIUS = 29.0
    RING_RADIUS = 27.0
    TICK_OUTER = 76.0
    TICK_MINOR = 4.0
    TICK_MAJOR = 8.0
    TICK_STEP_DEG = 5.0
    MAJOR_EVERY = 6
    MARKER_RADIUS = 65.0
    MARKER_DOT = 2.8
    ARC_SPAN_DEG = 48.0
    SHUTTLE_ARC_DEGREES = (
        (1.0, 90.0),
        (2.0, 180.0),
        (4.0, 270.0),
        (8.0, 360.0),
    )

    TIMECODE_PT = 0.0
    STATUS_PT = 6.4
    READOUT_BAND = 0.0
    SHOW_STATUS = True

    def __init__(self, play_button=None, parent=None) -> None:
        super().__init__(play_button=play_button, parent=parent)
        self.setProperty("professionalDeckWheel", "true")
        self.setAccessibleName("Professional jog and shuttle wheel")

    def set_bay_width(self, width: int) -> None:
        """Follow the responsive bay without changing the 165 px face."""
        width = max(200, int(width))
        if self.width() == width and self.height() == self.HEIGHT:
            return
        self.setFixedSize(width, self.HEIGHT)
        self.updateGeometry()

    @staticmethod
    def _point(center: QPointF, radius: float, angle: float) -> QPointF:
        radians = math.radians(angle)
        return QPointF(
            center.x() + math.cos(radians) * radius,
            center.y() + math.sin(radians) * radius,
        )

    @staticmethod
    def _mono(size: float, *, bold: bool = False) -> QFont:
        font = QFont(["Consolas", "Cascadia Mono", "Courier New"])
        font.setPointSizeF(size)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setBold(bold)
        return font

    def _paint_shadow(self, painter: QPainter, center: QPointF) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 95))
        painter.drawEllipse(QRectF(
            center.x() - 78.0, center.y() - 74.0,
            162.0, 164.0))
        painter.restore()

    def _paint_face(self, painter: QPainter, center: QPointF) -> None:
        face = QRectF(
            center.x() - self.FACE_RADIUS,
            center.y() - self.FACE_RADIUS,
            self.FACE_RADIUS * 2.0,
            self.FACE_RADIUS * 2.0,
        )
        dish = QRadialGradient(
            QPointF(center.x() - 18.0, center.y() - 24.0),
            self.FACE_RADIUS * 1.35,
        )
        dish.setColorAt(0.0, QColor("#343638"))
        dish.setColorAt(0.30, QColor("#242628"))
        dish.setColorAt(0.72, QColor("#17191B"))
        dish.setColorAt(1.0, QColor("#080A0B"))
        painter.setBrush(dish)
        painter.setPen(QPen(QColor("#050607"), 2.0))
        painter.drawEllipse(face.adjusted(1.0, 1.0, -1.0, -1.0))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        for inset, colour, width in (
            (3.5, "#424548", 1.2),
            (7.5, "#0A0B0C", 3.0),
            (12.0, "#55585A", 1.0),
            (18.0, "#111315", 1.2),
        ):
            painter.setPen(QPen(QColor(colour), width))
            painter.drawEllipse(face.adjusted(inset, inset, -inset, -inset))

        # Fine concentric machining marks create detail without raster texture.
        painter.setPen(QPen(QColor(112, 116, 118, 28), 0.65))
        for radius in range(34, 61, 3):
            painter.drawEllipse(QRectF(
                center.x() - radius, center.y() - radius,
                radius * 2.0, radius * 2.0))

    def _paint_ticks(self, painter: QPainter, center: QPointF) -> None:
        # The selected console reference uses a physical vernier collar rather
        # than a printed scale. Build each of its 72 teeth as an inset facet,
        # then split the facet into highlight and shadow faces so the ring reads
        # as machined/raised even at the compact 165 px size.
        for index in range(int(360 / self.TICK_STEP_DEG)):
            angle = index * self.TICK_STEP_DEG
            left_angle = angle - 1.65
            right_angle = angle + 1.65
            inner_radius = 66.0
            outer_radius = 75.5
            inner_left = self._point(center, inner_radius, left_angle)
            outer_left = self._point(center, outer_radius, left_angle)
            outer_mid = self._point(center, outer_radius, angle)
            outer_right = self._point(center, outer_radius, right_angle)
            inner_right = self._point(center, inner_radius, right_angle)
            inner_mid = self._point(center, inner_radius, angle)

            base = QColor("#303336" if index % 2 == 0 else "#25282A")
            painter.setPen(QPen(QColor("#080A0B"), 0.7))
            painter.setBrush(base)
            painter.drawPolygon(QPolygonF((
                inner_left, outer_left, outer_right, inner_right,
            )))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(158, 163, 166, 118))
            painter.drawPolygon(QPolygonF((
                inner_left, outer_left, outer_mid, inner_mid,
            )))
            painter.setBrush(QColor(0, 0, 0, 105))
            painter.drawPolygon(QPolygonF((
                inner_mid, outer_mid, outer_right, inner_right,
            )))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#55595C"), 0.9))
        painter.drawEllipse(QRectF(
            center.x() - 76.2, center.y() - 76.2, 152.4, 152.4))
        painter.setPen(QPen(QColor("#090A0B"), 1.4))
        painter.drawEllipse(QRectF(
            center.x() - 65.2, center.y() - 65.2, 130.4, 130.4))

        # Crisp scale marks sit above the raised collar and remain legible.
        for index in range(int(360 / self.TICK_STEP_DEG)):
            angle = index * self.TICK_STEP_DEG
            major = index % self.MAJOR_EVERY == 0
            outer = self.TICK_OUTER
            inner = outer - (self.TICK_MAJOR if major else self.TICK_MINOR)
            colour = QColor("#D1D3D2" if major else "#777B7C")
            colour.setAlpha(205 if major else 145)
            pen = QPen(colour, 1.35 if major else 0.85)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawLine(
                self._point(center, outer, angle),
                self._point(center, inner, angle),
            )

    def _paint_seat(self, painter: QPainter, center: QPointF) -> None:
        outer = QRectF(
            center.x() - self.SEAT_RADIUS,
            center.y() - self.SEAT_RADIUS,
            self.SEAT_RADIUS * 2.0,
            self.SEAT_RADIUS * 2.0,
        )
        hub = QRadialGradient(
            QPointF(center.x() - 7.0, center.y() - 8.0),
            self.SEAT_RADIUS * 1.4,
        )
        hub.setColorAt(0.0, QColor("#25282A"))
        hub.setColorAt(0.66, QColor("#101214"))
        hub.setColorAt(1.0, QColor("#050607"))
        painter.setBrush(hub)
        painter.setPen(QPen(QColor("#020303"), 2.0))
        painter.drawEllipse(outer)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#444749"), 1.0))
        painter.drawEllipse(outer.adjusted(4.0, 4.0, -4.0, -4.0))

    def _paint_marker(self, painter: QPainter, center: QPointF) -> None:
        angle = self._visual_angle
        colour = QColor("#57D8F1" if self._active else "#2AAFCB")
        colour.setAlpha(245 if self._active else 205)
        pen = QPen(colour, 3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(
            self._point(center, 45.0, angle),
            self._point(center, 70.0, angle),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#A5F1FF" if self._active else "#42C7DF"))
        tip = self._point(center, 71.5, angle)
        painter.drawEllipse(tip, self.MARKER_DOT, self.MARKER_DOT)

    def _paint_rate(self, painter: QPainter, center: QPointF) -> None:
        magnitude = abs(self._rate)
        if magnitude > 0.001:
            level_index, (_, span) = min(
                enumerate(self.SHUTTLE_ARC_DEGREES),
                key=lambda item: abs(item[1][0] - magnitude),
            )
            colour_index = min(
                len(MOTION_RAMP) - 1,
                max(0, int(round(
                    (level_index / 3.0) * (len(MOTION_RAMP) - 1)))),
            )
            pen = QPen(QColor(MOTION_RAMP[colour_index]), 3.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(pen)
            radius = self.FACE_RADIUS - 3.0
            track = QRectF(
                center.x() - radius, center.y() - radius,
                radius * 2.0, radius * 2.0,
            )
            painter.drawArc(
                track,
                round(90.0 * 16),
                round((-span if self._rate > 0 else span) * 16),
            )
        self._paint_speed_legend(painter, center)

    def _paint_speed_legend(self, painter: QPainter, center: QPointF) -> None:
        painter.setFont(self._mono(6.0, bold=True))
        magnitude = abs(self._rate)
        direction = -1 if self._rate < 0 else (1 if self._rate > 0 else 0)
        rest = QColor("#8E6728")
        live = QColor("#F0A63C")

        side_width = max(28.0, (self.width() - 169.0) / 2.0)
        left = QRectF(0.0, 40.0, side_width, 18.0)
        right = QRectF(self.width() - side_width, 40.0, side_width, 18.0)
        painter.setPen(live if direction < 0 else rest)
        painter.drawText(left, Qt.AlignmentFlag.AlignCenter, "REV")
        painter.setPen(live if direction > 0 else rest)
        painter.drawText(right, Qt.AlignmentFlag.AlignCenter, "FWD")

        rows = ((1.0, 78.0), (2.0, 54.0), (4.0, 119.0), (8.0, 145.0))
        for speed, y in rows:
            active = abs(magnitude - speed) < 0.01
            painter.setPen(live if active and direction < 0 else rest)
            painter.drawText(
                QRectF(0.0, y, side_width, 15.0),
                Qt.AlignmentFlag.AlignCenter, f"{speed:g}×")
            painter.setPen(live if active and direction > 0 else rest)
            painter.drawText(
                QRectF(self.width() - side_width, y, side_width, 15.0),
                Qt.AlignmentFlag.AlignCenter, f"{speed:g}×")

    def _paint_hub_frame(self, painter: QPainter, center: QPointF) -> None:
        if self.play_button is not None or not self._frame_text:
            return
        painter.setFont(self._mono(11.0))
        painter.setPen(QColor("#EEF1EF"))
        painter.drawText(
            QRectF(center.x() - 27.0, center.y() - 12.0, 54.0, 24.0),
            Qt.AlignmentFlag.AlignCenter,
            self._frame_text,
        )

    def _paint_readouts(self, painter: QPainter) -> None:
        # Timecode remains in POSITION. The wheel's lower strip reports the
        # frame-jog or live shuttle state exactly as shown in the selected dock.
        painter.setFont(self._mono(self.STATUS_PT, bold=True))
        if abs(self._rate) > 0.001:
            painter.setPen(QColor("#F0A63C"))
            status = self._rate_label()
        else:
            painter.setPen(QColor("#43CDE8" if self._active else "#6AAAB5"))
            status = "JOG ACTIVE" if self._active else "JOG ±1 FRAME"
        painter.drawText(
            QRectF(0.0, self.height() - 15.0, self.width(), 13.0),
            Qt.AlignmentFlag.AlignCenter,
            status,
        )
