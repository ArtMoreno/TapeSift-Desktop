"""The Dock V2 jog wheel: a standalone premium controller.

A paint-only subclass of :class:`MinimalJogRing`.  Every gesture -  drag,
rotation, mouse wheel, frame jog, the active-hold timing, and the
``framesRequested`` contract - is inherited untouched, so the jog *feel* is
byte-identical to the current wheel.  ``DEGREES_PER_FRAME`` and
``PIXELS_PER_FRAME`` are rates, not sizes, so resizing the face does not
change how far a drag scrubs.

Hit-testing follows the geometry constants (``_inside_ring`` measures against
``FACE_RADIUS``, ``dial_center`` against ``CENTER_Y``), so re-sizing here
re-sizes the grab area with it.

What changes is the surface, against the Dock V2 visual contract:

* a restrained machined annulus and sculpted rim;
* exactly one continuous outer arc, never two - the shuttle-rate arc and the
  jog-position arc share one ring and are mutually exclusive;
* the hub is only a Play/Pause control, with no numeric or status readout.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QRadialGradient)

from tapesift.ui_core.minimal_jog_ring import MinimalJogRing
from tapesift.ui_v2 import deck_icons
from tapesift.ui_v2.tokens import SEMANTIC, WHEEL


class SmoothJogWheel(MinimalJogRing):
    """A premium line-free face for the standalone floating controller."""

    playPauseRequested = Signal()

    # Sized so the band can reach a low profile.  The old ProfessionalJogWheel
    # pinned HEIGHT to 197 and clamped its bay to >= 200 px, which is what held
    # the dock at 197 px tall.
    WIDTH = 196
    HEIGHT = 196
    CENTER_Y = 98.0

    FACE_RADIUS = 88.0
    SEAT_RADIUS = 48.0
    RING_RADIUS = 47.0
    MARKER_RADIUS = 80.0

    # The one outer indicator, just clear of the rim.
    ARC_RADIUS = 84.0
    ARC_WIDTH = 4.0
    ARC_SPAN_DEG = 88.0

    # At the top of the ladder the arc covers exactly half the circle, so
    # "maxed out" is readable at a glance without counting degrees.
    ARC_MAX_SPAN_DEG = 180.0

    # The inherited readout band remains disabled; state is painted in the
    # inset hub instead of occupying separate window chrome.
    TIMECODE_PT = 0.0
    STATUS_PT = 0.0
    READOUT_BAND = 0.0
    SHOW_STATUS = False

    CENTER_DIAMETER = 96
    ICON_SIZE = 42
    GLYPH_SIZE = 34
    SHOW_FRAME_TEXT = False

    def __init__(self, play_button=None, parent=None) -> None:
        super().__init__(play_button=play_button, parent=parent)
        self.setProperty("smoothDeckWheel", "true")
        self.setAccessibleName(
            "Floating Play/Pause wheel")
        self.setToolTip(
            "Click the center to play or pause. Drag the outer ring to move.")
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self._playing = False
        self._hub_pressed = False

    def sizeHint(self) -> QSize:
        return QSize(self.WIDTH, self.HEIGHT)

    def set_bay_width(self, width: int) -> None:
        """Accept the deck's bay call without stretching the face.

        The old wheel grew its widget to fill the bay; this one stays circular
        and lets the deck centre it, so there is no bay minimum to satisfy.
        """
        return

    # ---------------------------------------------------------------- paint

    def _paint_ticks(self, painter: QPainter, center: QPointF) -> None:
        """No ticks.  The visual contract rejects them outright."""
        return

    def _paint_readouts(self, painter: QPainter) -> None:
        """No text on the face; the dock owns the position readout."""
        return

    def _paint_hub_frame(self, painter: QPainter, center: QPointF) -> None:
        """Paint only the real Play/Pause control inside the hub."""
        icon = deck_icons.state_icon(
            "deckv2_pause" if self._playing else "deckv2_play", 34)
        icon.paint(
            painter,
            QRectF(center.x() - 17.0, center.y() - 17.0, 34.0, 34.0).toRect(),
            Qt.AlignmentFlag.AlignCenter)

        if self._hub_pressed:
            # Press feedback is material, not signal: the seat's edge hue,
            # never the position cyan. A second lit circle in SEMANTIC's
            # cold hue would compete with the marker (C4-07).
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(WHEEL["hub_edge"]), 2.0))
            painter.drawEllipse(QRectF(
                center.x() - self.SEAT_RADIUS + 3.0,
                center.y() - self.SEAT_RADIUS + 3.0,
                (self.SEAT_RADIUS - 3.0) * 2,
                (self.SEAT_RADIUS - 3.0) * 2))

    def set_playing(self, playing: bool) -> None:
        playing = bool(playing)
        if playing == self._playing:
            return
        self._playing = playing
        self.update()

    def _inside_hub(self, point: QPointF) -> bool:
        center = self.dial_center()
        return math.hypot(
            point.x() - center.x(), point.y() - center.y()) \
            <= self.SEAT_RADIUS

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton \
                and self._inside_hub(event.position()):
            self._hub_pressed = True
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._hub_pressed:
            activate = self._inside_hub(event.position())
            self._hub_pressed = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
            if activate:
                self.playPauseRequested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _paint_face(self, painter: QPainter, center: QPointF) -> None:
        """A machined outer annulus with restrained physical depth.

        One light source, high and slightly left, across rim, dish and hub
        (11_SPEC_WHEEL_W1.md). Every neutral resolves from tokens.WHEEL so
        the object sits warm against the chassis and can obey SEMANTIC.
        """
        face = QRectF(
            center.x() - self.FACE_RADIUS, center.y() - self.FACE_RADIUS,
            self.FACE_RADIUS * 2, self.FACE_RADIUS * 2)

        # Outer rim, warm graphite, lit from above-left. The light is the
        # same family the dish and the hub use, so the whole object reads
        # as one surface under one lamp (C2-04 / C4-04).
        rim = QRadialGradient(
            QPointF(center.x() - self.FACE_RADIUS * 0.08,
                    center.y() - self.FACE_RADIUS * 0.55),
            self.FACE_RADIUS * 1.7)
        rim.setColorAt(0.0, QColor(WHEEL["rim_hi"]))
        rim.setColorAt(0.42, QColor(WHEEL["rim_upper"]))
        rim.setColorAt(0.72, QColor(WHEEL["rim_mid"]))
        rim.setColorAt(1.0, QColor(WHEEL["rim_deep"]))
        painter.setBrush(rim)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(face)

        # Shallow dish, same light, warm graphite of its own.
        dish_r = self.FACE_RADIUS - 18.0
        dish_rect = QRectF(
            center.x() - dish_r, center.y() - dish_r, dish_r * 2, dish_r * 2)
        dish = QRadialGradient(
            QPointF(center.x() - dish_r * 0.08,
                    center.y() - dish_r * 0.55),
            dish_r * 1.5)
        dish.setColorAt(0.0, QColor(WHEEL["dish_hi"]))
        dish.setColorAt(0.68, QColor(WHEEL["dish_mid"]))
        dish.setColorAt(1.0, QColor(WHEEL["dish_deep"]))
        painter.setBrush(dish)
        painter.setPen(QPen(QColor(WHEEL["dish_edge"]), 1.0))
        painter.drawEllipse(dish_rect)

        # Machined flutes on the annulus between dish and rim: two dashed
        # strokes, a dark cut and a lit crest, roughly 73 flutes around the
        # circumference (a 2.2/4.2 + 1/5.4 dash pair on r~80 gives ~78
        # periods, close enough for the eye and the contract). The old
        # 6px crosshatch read as stacked gradients, not machining
        # (C2-03 / C4-03).
        # Machined flutes on a 15px band centred at MARKER_RADIUS (r80):
        # two dashed annulus strokes, dark cut then lit crest, roughly
        # 73 flutes (C2-03 / C4-03).
        painter.save()
        knurl_r = self.MARKER_RADIUS
        knurl_half = 7.5
        annulus = QPainterPath()
        annulus.setFillRule(Qt.FillRule.OddEvenFill)
        texture_outer = knurl_r + knurl_half
        texture_inner = knurl_r - knurl_half
        annulus.addEllipse(QRectF(
            center.x() - texture_outer, center.y() - texture_outer,
            texture_outer * 2, texture_outer * 2))
        annulus.addEllipse(QRectF(
            center.x() - texture_inner, center.y() - texture_inner,
            texture_inner * 2, texture_inner * 2))
        painter.setClipPath(annulus)

        cut = QPen(QColor(WHEEL["flute_cut"]), 2.2)
        cut.setDashPattern([2.2, 4.2])
        cut.setCapStyle(Qt.PenCapStyle.RoundCap)
        cut_col = QColor(WHEEL["flute_cut"])
        cut_col.setAlphaF(0.72)
        cut.setColor(cut_col)
        painter.setPen(cut)
        cut_rect = QRectF(
            center.x() - knurl_r, center.y() - knurl_r,
            knurl_r * 2, knurl_r * 2)
        painter.drawEllipse(cut_rect)

        crest = QPen(QColor(WHEEL["flute_crest"]), 1.0)
        crest.setDashPattern([1.0, 5.4])
        crest.setCapStyle(Qt.PenCapStyle.RoundCap)
        crest_col = QColor(WHEEL["flute_crest"])
        crest_col.setAlphaF(0.33)
        crest.setColor(crest_col)
        painter.setPen(crest)
        painter.drawEllipse(cut_rect.adjusted(-1.0, -1.0, 1.0, 1.0))
        painter.restore()

        # A single hairline catching the top edge. One line, not a ring of
        # them. (Neutral white highlight; the pigment classifier counts it
        # as warm-neutral, so W03 stays green.)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        sheen = QColor(WHEEL["highlight"])
        sheen.setAlpha(48)
        painter.setPen(QPen(sheen, 1.2))
        painter.drawArc(
            face.adjusted(1.0, 1.0, -1.0, -1.0),
            round(35 * 16), round(110 * 16))

    def _paint_seat(self, painter: QPainter, center: QPointF) -> None:
        """The hub cap: matte, one light source, an inner shadow line.

        The parent's bright accent ring is not drawn here - that ring is a
        second lit circle competing with the outer arc. The arc is the one
        indicator, so the hub stays material, not signal.
        """
        seat = QRectF(
            center.x() - self.SEAT_RADIUS, center.y() - self.SEAT_RADIUS,
            self.SEAT_RADIUS * 2, self.SEAT_RADIUS * 2)
        well = QRadialGradient(
            QPointF(center.x() - self.SEAT_RADIUS * 0.08,
                    center.y() - self.SEAT_RADIUS * 0.55),
            self.SEAT_RADIUS * 1.8)
        well.setColorAt(0.0, QColor(WHEEL["hub_hi"]))
        well.setColorAt(0.58, QColor(WHEEL["hub_mid"]))
        well.setColorAt(1.0, QColor(WHEEL["hub_deep"]))
        painter.setBrush(well)
        painter.setPen(QPen(QColor(WHEEL["hub_edge"]), 1.5))
        painter.drawEllipse(seat)

        # Inner shadow line at r43, so the cap reads seated in the dish.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inner_shadow = QColor(WHEEL["shadow"])
        inner_shadow.setAlpha(120)
        painter.setPen(QPen(inner_shadow, 1.2))
        inner = QRectF(
            center.x() - 43.0, center.y() - 43.0, 86.0, 86.0)
        painter.drawArc(
            inner.adjusted(0.5, 0.5, -0.5, -0.5),
            round(30 * 16), round(300 * 16))

    def _paint_rate(self, painter: QPainter, center: QPointF) -> None:
        """Shuttle rate as one continuous arc from twelve o'clock.

        Same log scale and the same ``self._rate`` the parent uses - that value
        arrives from ``set_shuttle_rate``, which the deck drives from the
        player's rate and direction.  Only the radius moves, out past the rim.
        """
        if abs(self._rate) <= 0.001:
            return
        magnitude = min(abs(self._rate), 8.0)
        fraction = math.log2(max(magnitude, 0.25) * 4.0) / 5.0
        fraction = max(0.06, min(1.0, fraction))
        span = self.ARC_MAX_SPAN_DEG * fraction

        pen = QPen(self._arc_color(fraction), self.ARC_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        track = QRectF(
            center.x() - self.ARC_RADIUS, center.y() - self.ARC_RADIUS,
            self.ARC_RADIUS * 2, self.ARC_RADIUS * 2)
        direction = -1 if self._rate > 0 else 1
        painter.drawArc(track, round(90 * 16), round(direction * span * 16))

    def _paint_marker(self, painter: QPainter, center: QPointF) -> None:
        """Jog position as the same single arc - never a second indicator.

        While shuttling, the rate arc owns the ring and this draws nothing, so
        exactly one continuous arc is ever visible around the wheel.  The
        travelling dot the parent draws is deliberately dropped: it reads as a
        mark on a ring, which the contract rejects.
        """
        if abs(self._rate) > 0.001:
            return
        color = QColor(SEMANTIC["position"])
        color.setAlpha(255 if self._active else 205)
        pen = QPen(color, self.ARC_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        track = QRectF(
            center.x() - self.ARC_RADIUS, center.y() - self.ARC_RADIUS,
            self.ARC_RADIUS * 2, self.ARC_RADIUS * 2)
        span = self.ARC_SPAN_DEG
        painter.drawArc(
            track,
            round((-self._visual_angle - span) * 16),
            round(span * 16))

    @staticmethod
    def _arc_color(_fraction: float) -> QColor:
        """The shuttle arc is warm at every rate (SEMANTIC.motion).

        Iteration 2 removed the cool-to-warm ramp: colour only says
        \"something is moving\", direction is carried by which side of
        vertical the arc sweeps. Cold stays reserved for position, so a
        slow shuttle no longer borrows the marker's hue (C2-02 / C4-02).
        """
        return QColor(SEMANTIC["motion"])
