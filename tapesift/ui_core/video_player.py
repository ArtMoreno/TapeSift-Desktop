"""Video preview player with transport controls and in/out point marking.

Transport is the highest-priority system here: playback, JKL shuttle, and
frame stepping must stay immediate. Position updates do the minimum UI work,
and reverse shuttle is state-based - it never queues seeks faster than the
decoder delivers frames.
"""

from __future__ import annotations

import math

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QElapsedTimer, QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer,
    QUrl, Signal)

from tapesift.ui_core.layout_ownership import detach_widget
from tapesift.core.perf import PerfTimer
from tapesift.ui_v2 import deck_icons
from tapesift.models.telestration import (
    DEFAULT_INK, INK, Mark, MarkKind, is_path_kind, shape_style,
    to_film, to_widget)
from tapesift.ui_v2.shape_picker import (
    ShapePicker, shape_state_icon)

#: The shape the library key shows before anything is chosen.
DEFAULT_SHAPE_TOOL = "route_arrow"
from PySide6.QtGui import (
    QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap,
    QPolygonF, QRadialGradient)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSlider, QMenu, QScrollBar, QSizePolicy,
    QStyle, QToolButton, QVBoxLayout, QWidget,
    QWidgetAction,
)

from tapesift.core.config import AppSettings
from tapesift.services import background_service, filename_service
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_core.timeline import (
    TIMELINE_COLOR_MODE_LABELS, TIMELINE_COLOR_MODES, Timeline, TimelineBlock,
    TimelineCoverage, build_timeline_legend,
)
from tapesift.ui_core.timeline_overview import TimelineOverview
from tapesift.ui_v2.attribute_grid import AttributeGrid
from tapesift.ui_core.minimal_jog_ring import format_jog_timecode
from tapesift.ui_core.timeline_variants import (
    DUAL,
    VARIANTS,
    normalize_timeline_variant,
)
from tapesift.ui_v2.control_center import (
    CIRCULAR_CONTROL_STYLE,
    DECK_KEY_QSS,
)
from tapesift.ui_v2.icon_utils import magnifier_icon
from tapesift.ui_v2.tokens import COLORS

if TYPE_CHECKING:
    from tapesift.ui_v2.control_center import ControlCenterDeck

#: One shared content edge for the whole region (10_SPEC_BAND_B.md):
#: every row starts at x=26 and ends at width-26, flush to the window.
#: The band no longer insets by the telestration rail; the rail stays on
#: the film row above, and the deck mounts flush so its island centres
#: on the window - the drawing's thesis, which the old rail inset broke.
_FILM_COLUMN_INSET = 0

PLAYBACK_SPEEDS = [0.25, 0.5, 1.0, 1.5, 2.0]
SHUTTLE_SPEEDS = [1.0, 2.0, 4.0, 8.0]
REVERSE_WATCHDOG_MS = 250  # only fires if a seek delivers no frame at all
SCRUB_PREVIEW_INTERVAL_MS = 24
# Windows' media backend updates position while stopped but does not decode the
# first image. Give a backend that can paint while stopped a brief chance before
# waking it; the wake itself settles paused before control returns to the user.
INITIAL_STOPPED_FRAME_WAKE_DELAY_MS = 100
INITIAL_STOPPED_FRAME_HOLD_TIMEOUT_MS = 2_000
TIMELINE_ZOOM_FACTORS = (1, 2, 4, 8, 16, 32, 64)
FIT_PLAY_CONTEXT_MS = 10_000
DUAL_DETAIL_CONTEXT_MS = 60_000
TIMELINE_PAN_FRACTION = 0.5
ICON_ROOT = Path(__file__).resolve().parents[1] / "resources" / "icons"
# One timeline, not two. The dual variant used to stack a full-game
# overview above the editable timeline, which meant two time axes, two
# playheads and two things to keep in sync. Fit Game on the one timeline
# does what the overview was for. The widget and every call site are kept
# intact behind this flag so the second surface is one line away.
SHOW_TIMELINE_OVERVIEW = False
VOLUME_ICON = ICON_ROOT / "tapesift-volume.png"
VOLUME_MUTED_ICON = ICON_ROOT / "tapesift-volume-muted.png"

# Qt lays the 1 px QSS border outside these swatch dimensions, so 16 px
# below produces the locked 18 px outer chassis used by the rail geometry.
TELESTRATION_RAIL_QSS = """
QFrame#TelestrationRail {
    background: #1a1b18;
    border: 1px solid #3b3b32;
    border-radius: 0px;
}
QFrame#TelestrationRail QToolButton[deckKey="true"] {
    background: #111416;
    border: 1px solid #33383A;
    border-radius: 0px;
    color: #9AA694;
    padding: 0px;
}
QFrame#TelestrationRail QToolButton[deckKey="true"]:hover {
    background: #1B1E20;
    border-color: #50565A;
}
QFrame#TelestrationRail QToolButton[deckKey="true"]:pressed {
    background: #101214;
    border-color: #E8A33D;
}
QFrame#TelestrationRail QToolButton[deckKey="true"]:checked {
    background: #111A15;
    border-color: #39E07A;
    color: #39E07A;
}
QFrame#TelestrationRail QToolButton[deckKey="true"]:focus {
    border-color: #E8A33D;
}
QFrame#TelestrationRail QToolButton[deckKey="true"]:disabled {
    background: #23272A;
    border-color: #23272A;
    color: #4E584A;
}
QFrame#TelestrationRail QToolButton[deckSwatch="true"] {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 0px;
    padding: 0px;
    min-width: 16px;
    max-width: 16px;
    min-height: 16px;
    max-height: 16px;
}
QFrame#TelestrationRail QToolButton[deckSwatch="true"]:hover {
    background: #202326;
    border-color: #4A4E52;
}
QFrame#TelestrationRail QToolButton[deckSwatch="true"]:checked {
    background: #111A15;
}
QFrame#TelestrationRail QToolButton[deckSwatch="true"][inkColor="gold"]:checked {
    border-color: #FFD24A;
}
QFrame#TelestrationRail QToolButton[deckSwatch="true"][inkColor="cyan"]:checked {
    border-color: #5AD6F0;
}
QFrame#TelestrationRail QToolButton[deckSwatch="true"][inkColor="red"]:checked {
    border-color: #FF6B5E;
}
QFrame#TelestrationRail QToolButton[deckSwatch="true"]:focus {
    border-color: #E8A33D;
}
"""


def telestration_state_icon(name: str, size: int = 16) -> QIcon:
    """Rail icon with neutral rest, green selection, and quiet disable."""
    icon = QIcon()
    rest = deck_icons.load(name, "#9AA694", size).pixmap(size, size)
    hover = deck_icons.load(name, "#E6EBE0", size).pixmap(size, size)
    checked = deck_icons.load(name, "#39E07A", size).pixmap(size, size)
    disabled = deck_icons.load(name, "#4E584A", size).pixmap(size, size)
    icon.addPixmap(rest, QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(hover, QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(checked, QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(checked, QIcon.Mode.Active, QIcon.State.On)
    icon.addPixmap(disabled, QIcon.Mode.Disabled, QIcon.State.Off)
    return icon

def deck_glyph(kind: str, colour: str = "#9AA694", size: int = 15) -> QIcon:
    """Draw the deck's tool glyphs rather than label them.

    Text captions in fixed-width buttons is how "Circle" became "C...e" -
    the control fighting its own label. An arrow, a line and a circle need
    no caption, and drawing them here avoids shipping icon assets for six
    shapes.
    """
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(colour), 2.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    box = size * 2
    pad = box * 0.22

    if kind == "arrow":
        painter.drawLine(QPointF(pad, box - pad), QPointF(box - pad, pad))
        painter.drawLine(QPointF(box - pad, pad),
                         QPointF(box - pad - box * 0.26, pad))
        painter.drawLine(QPointF(box - pad, pad),
                         QPointF(box - pad, pad + box * 0.26))
    elif kind == "line":
        painter.drawLine(QPointF(pad, box - pad), QPointF(box - pad, pad))
    elif kind == "circle":
        painter.drawEllipse(
            QRectF(pad, pad + box * 0.08, box - pad * 2, box - pad * 2 - box * 0.16))
    elif kind == "undo":
        painter.drawArc(
            QRectF(pad, pad + box * 0.1, box - pad * 2, box - pad * 2), 30 * 16, 240 * 16)
        painter.drawLine(QPointF(pad + box * 0.02, pad + box * 0.1),
                         QPointF(pad + box * 0.22, pad + box * 0.02))
        painter.drawLine(QPointF(pad + box * 0.02, pad + box * 0.1),
                         QPointF(pad + box * 0.22, pad + box * 0.28))
    elif kind == "clear":
        painter.drawLine(QPointF(pad, pad), QPointF(box - pad, box - pad))
        painter.drawLine(QPointF(box - pad, pad), QPointF(pad, box - pad))
    elif kind == "step_back":
        painter.drawLine(QPointF(box - pad * 1.4, pad), QPointF(pad * 1.6, box / 2))
        painter.drawLine(QPointF(pad * 1.6, box / 2), QPointF(box - pad * 1.4, box - pad))
        painter.drawLine(QPointF(pad, pad), QPointF(pad, box - pad))
    elif kind == "step_fwd":
        painter.drawLine(QPointF(pad * 1.4, pad), QPointF(box - pad * 1.6, box / 2))
        painter.drawLine(QPointF(box - pad * 1.6, box / 2), QPointF(pad * 1.4, box - pad))
        painter.drawLine(QPointF(box - pad, pad), QPointF(box - pad, box - pad))
    elif kind == "loop":
        painter.drawArc(QRectF(pad, pad, box - pad * 2, box - pad * 2), 0, 360 * 16)
        painter.drawLine(QPointF(box / 2 - box * 0.1, pad * 0.7),
                         QPointF(box / 2 + box * 0.06, pad))
        painter.drawLine(QPointF(box / 2 - box * 0.1, pad * 1.4),
                         QPointF(box / 2 + box * 0.06, pad))
    painter.end()
    return QIcon(pixmap)


class StepVideoWidget(QWidget):
    """Video surface where the mouse wheel steps frame by frame.

    Paints frames itself from a QVideoSink rather than being a QVideoWidget.

    QVideoWidget renders onto its own native surface, and nothing can be
    composited above it - a floating transport was tried and abandoned for
    exactly that reason, and telestration needs ink on top of moving film.
    Three approaches were tested on real footage in a real window
    (docs/spikes/): a raised transparent widget draws nothing, a
    QGraphicsVideoItem shrinks the picture inside a black border, and this
    one works, in both directions, including under reverse shuttle.

    The cost is that presentation is now ours, on the frame path. Two rules
    keep that honest, both measurable:

    * scale during the blit. drawImage(target_rect, image) is one operation
      the paint engine can hardware-accelerate; scaling to a pixmap first
      and then drawing it is two, and it was the expensive call in the
      spike.
    * only ask for smooth scaling when the picture is still. While film is
      moving nobody can see the difference, and it is the most expensive
      flag in the loop.

    What it buys is `frame_rect()`: the exact rectangle the frame occupies
    on screen, which makes film coordinates arithmetic we control. Strokes
    stored in film coordinates survive zoom - the one telestration decision
    that is expensive to change later.
    """

    wheel_step = Signal(int)
    clicked = Signal()
    marksChanged = Signal()
    # Millisecond PTS of the frame that completed this widget's paint path.
    # -1 means the backend supplied no QVideoFrame timestamp; VideoPlayer
    # refuses to mislabel it with a leading QMediaPlayer position.
    frame_presented = Signal(int)
    # The initial stopped-seek gate is deliberately on the sink path, before
    # paint coalescing can hide which native frame satisfied the request.
    frame_hold_reached = Signal(int)
    frame_hold_missed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Clicking the video returns keyboard control to playback (spec 1.4).
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._frame_arrived)
        self._image: QImage | None = None
        self._image_position_ms = -1
        self._image_serial = 0
        self._presented_serial = 0
        self._converted_serial = 0
        self._pending_frame: QVideoFrame | None = None
        self._painted_image: QImage | None = None
        self._painted_position_ms = -1
        self._resume_guard: tuple[int, int] | None = None
        self._frame_hold_target_us: int | None = None
        self._frame_hold_deadline_us: int | None = None
        self._frame_hold_active = False
        self._frame_rect = QRectF()
        self._moving = False
        # Telestration. `_tool` is None unless a tool has been picked, and
        # while it is None every mouse event behaves exactly as before -
        # clicking the film still hands keyboard control back to playback,
        # which is muscle memory worth not breaking.
        self._tool: str | None = None
        self._ink = DEFAULT_INK
        self._marks: list[Mark] = []
        self._drawing: tuple[float, float] | None = None
        self._drag_to: tuple[float, float] | None = None

    def videoSink(self) -> QVideoSink:
        """The sink the player should render into.

        Named to match QVideoWidget so callers that only wanted a sink -
        the reverse-shuttle frame hook, chiefly - do not care which surface
        they are talking to.
        """
        return self._sink

    def frame_rect(self) -> QRectF:
        """Where the frame sits on this widget, letterboxing included."""
        return QRectF(self._frame_rect)

    def current_frame_image(self) -> QImage:
        """Return a detached source-resolution frame for read-only preview.

        Export Package reads this snapshot; it never subscribes to the sink
        and therefore cannot alter playback, reverse shuttle, or frame pacing.
        """

        self._materialize_pending_frame()
        image = self._image
        return QImage() if image is None or image.isNull() else image.copy()

    def hold_current_frame(self) -> bool:
        """Keep the painted picture while a paused decoder drains its queue."""
        if self._painted_image is None or self._painted_position_ms < 0:
            return False
        self.discard_unpainted_frames()
        self._frame_hold_active = True
        return True

    def discard_unpainted_frames(self) -> None:
        """Discard superseded deliveries without inventing a new paint event."""
        self._pending_frame = None
        self._image = self._painted_image
        self._image_position_ms = self._painted_position_ms
        self._image_serial = self._converted_serial = self._presented_serial
        self.clear_resume_guard()

    def clear_resume_guard(self) -> None:
        self._resume_guard = None

    def arm_frame_hold(self, target_ms: int, tolerance_ms: int) -> None:
        """Hold the first native frame on or just after an initial seek.

        Windows may deliver the frame immediately before a seek target, then
        drain already-queued frames after pause().  Arming on the sink path
        rejects the preceding frame and freezes the accepted image before
        either condition can replace it.
        """
        target_us = max(0, int(target_ms)) * 1000
        self._frame_hold_target_us = target_us
        self._frame_hold_deadline_us = (
            target_us + max(1, int(tolerance_ms)) * 1000)
        self._frame_hold_active = False
        self.discard_unpainted_frames()

    def release_frame_hold(self, resume_direction: int = 0) -> None:
        """Let ordinary playback and seeking replace the held image again."""
        self._resume_guard = (self._painted_position_ms, resume_direction) \
            if resume_direction and self._frame_hold_active \
            and self._image_serial == self._presented_serial \
            and self._image_position_ms == self._painted_position_ms \
            and self._painted_position_ms >= 0 else None
        self._frame_hold_target_us = None
        self._frame_hold_deadline_us = None
        self._frame_hold_active = False

    def frame_hold_active(self) -> bool:
        return self._frame_hold_active

    def frame_hold_position_ms(self) -> int | None:
        if not self._frame_hold_active or self._image_position_ms < 0:
            return None
        return self._image_position_ms

    def set_moving(self, moving: bool) -> None:
        """Tell the surface whether film is in motion.

        Smooth scaling is the expensive part of the blit and is invisible
        while the picture is moving, so it is spent only on the still frame
        the analyst is actually looking at.
        """
        self._moving = bool(moving)

    def _frame_arrived(self, frame) -> None:
        if not frame.isValid():
            return
        if self._frame_hold_active:
            # pause() does not flush Windows' decoded queue.  The target image
            # is already safe; queued frames must not move the visible film.
            return
        # At 8x, converting every decoded frame can starve paint and key events.
        # Retain only the newest frame; update() coalesces conversion with paint.
        start_time_us = int(frame.startTime())
        if self._resume_guard is not None and start_time_us >= 0:
            anchor, direction = self._resume_guard
            # setPosition cannot retract deliveries already queued by Qt.
            if (start_time_us // 1000 - anchor) * direction < 0:
                return
        self._pending_frame = QVideoFrame(frame)
        self._image_serial += 1
        self.update()
        target_us = self._frame_hold_target_us
        deadline_us = self._frame_hold_deadline_us
        if target_us is None or start_time_us < target_us:
            return
        # Initial seek holds must secure their target before a later delivery
        # replaces it, even if the widget has not had a chance to paint yet.
        if not self._materialize_pending_frame():
            return
        if deadline_us is not None and start_time_us <= deadline_us:
            self._frame_hold_active = True
            self.frame_hold_reached.emit(self._image_position_ms)
            return
        # A decoder jump larger than one source frame is not the requested
        # still.  Report it once so VideoPlayer can stop its internal wake
        # instead of leaving muted playback running indefinitely.
        self._frame_hold_target_us = None
        self._frame_hold_deadline_us = None
        self.frame_hold_missed.emit(self._image_position_ms)

    def _materialize_pending_frame(self) -> bool:
        frame = self._pending_frame
        if frame is None:
            return False
        self._pending_frame = None
        image = frame.toImage()
        if image.isNull():
            self._image_serial = self._converted_serial
            return False
        self._image = image
        pts = int(frame.startTime())
        self._image_position_ms = pts // 1000 if pts >= 0 else -1
        self._converted_serial = self._image_serial
        return True

    def paintEvent(self, _event) -> None:
        self._materialize_pending_frame()
        painter = QPainter(self)
        image = self._image
        if image is None or image.isNull():
            radius = max(self.width(), self.height()) * 1.05
            vignette = QRadialGradient(self.rect().center(), radius)
            vignette.setColorAt(0.0, QColor("#111110"))
            vignette.setColorAt(0.72, QColor("#10100f"))
            vignette.setColorAt(1.0, QColor("#0f0f0f"))
            painter.fillRect(self.rect(), vignette)
            self._frame_rect = QRectF()
            painter.end()
            return
        painter.fillRect(self.rect(), QColor("#0c0d0a"))
        image_serial = self._image_serial
        image_position_ms = self._image_position_ms
        area = QRectF(self.rect())
        scale = min(area.width() / image.width(),
                    area.height() / image.height())
        width, height = image.width() * scale, image.height() * scale
        self._frame_rect = QRectF(
            area.x() + (area.width() - width) / 2.0,
            area.y() + (area.height() - height) / 2.0,
            width, height)
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform, not self._moving)
        painter.drawImage(self._frame_rect, image)
        self._paint_marks(painter)
        painter.end()
        self._painted_image = image
        self._painted_position_ms = image_position_ms
        if image_serial != self._presented_serial:
            self._presented_serial = image_serial
            self.clear_resume_guard()
            self.frame_presented.emit(image_position_ms)

    # ---------- telestration ----------

    def set_tool(self, tool):
        """Arm a drawing tool, or None to hand the mouse back to playback."""
        self._tool = tool
        self._drawing = None
        self._drag_to = None
        self.setCursor(Qt.CursorShape.CrossCursor if tool
                       else Qt.CursorShape.ArrowCursor)

    def tool(self):
        return self._tool

    def set_ink(self, ink: str) -> None:
        self._ink = ink if ink in INK else DEFAULT_INK

    def ink(self) -> str:
        return self._ink

    def set_marks(self, marks) -> None:
        # A selection change can land between mouse press and release. The
        # incoming clip owns the marks being loaded, so the old clip's
        # half-built gesture must not survive long enough to be released into
        # it. Tool and ink are analyst choices and deliberately persist.
        self._drawing = None
        self._drag_to = None
        self._marks = list(marks)
        self.update()

    def marks(self):
        return list(self._marks)

    def undo_mark(self) -> bool:
        if not self._marks:
            return False
        self._marks.pop()
        self.update()
        self.marksChanged.emit()
        return True

    def clear_marks(self) -> bool:
        if not self._marks:
            return False
        self._marks.clear()
        self.update()
        self.marksChanged.emit()
        return True

    def _paint_marks(self, painter: QPainter) -> None:
        rect = self._frame_rect
        if rect.isEmpty():
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for mark in self._marks:
            self._paint_one(
                painter, mark.kind.value, mark.points, mark.ink, rect)
        # The stroke being dragged right now, drawn the same way, so what
        # you release is exactly what you were shown.
        if self._drawing and self._drag_to:
            self._paint_one(painter, self._tool or "arrow",
                            [self._drawing, self._drag_to], self._ink, rect)

    def _paint_one(self, painter, kind, points, ink, rect) -> None:
        """Draw one mark from its style-table entry.

        Every stroke is drawn twice: a dark halo under a bright core. That is
        how a broadcast telestrator stays readable crossing both a white
        jersey and dark grass - without it gold disappears into turf.
        """
        if len(points) < 2:
            return
        try:
            mark_kind = MarkKind(kind)
        except (TypeError, ValueError):
            mark_kind = MarkKind.ARROW
        style = shape_style(mark_kind)
        colour = QColor(INK.get(ink, INK[DEFAULT_INK]))
        pts = [QPointF(*to_widget(x, y, rect)) for x, y in points]
        box = QRectF(pts[0], pts[-1]).normalized()

        core = 4.4 if style.heavy else 3.6
        for pen_colour, pen_width in ((QColor(0, 0, 0, 150), core + 5.4),
                                      (colour, core)):
            pen = QPen(pen_colour, pen_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if style.dashed:
                # Dash lengths are in pen widths, so the pattern reads the
                # same at any stroke size, and the halo uses the identical
                # pattern so it never shows through the gaps.
                pen.setDashPattern([2.6, 2.0])
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # The halo's glyphs are a hair larger than the core's so they
            # read as an outline, never as a separate dark shape.
            glyph = core + (2.0 if pen_width > core else 0.0)
            self._paint_primitive(
                painter, style, pts, box, pen_colour, glyph)

    def _paint_primitive(self, painter, style, pts, box, colour, width):
        primitive = style.primitive
        if primitive == "path":
            path = QPainterPath(pts[0])
            for point in pts[1:]:
                path.lineTo(point)
            painter.drawPath(path)
            return
        if primitive == "ellipse":
            painter.drawEllipse(box)
            return
        if primitive == "box":
            painter.drawRect(box)
            return
        if primitive == "marker":
            self._paint_marker(painter, style, pts, box, colour, width)
            return
        if primitive == "curve":
            self._paint_curve(painter, style, pts, colour, width)
            return
        start, end = pts[0], pts[-1]
        painter.drawLine(start, end)
        if style.arrow_end:
            self._paint_head(painter, start, end, colour, width)
        if style.arrow_start:
            self._paint_head(painter, end, start, colour, width)
        if style.tee_end:
            self._paint_cross(painter, start, end, colour, width, 1.0)
        if style.bar_end:
            self._paint_cross(painter, start, end, colour, width, 0.8, 5.0)
        if style.end_ticks:
            self._paint_cross(painter, end, start, colour, width, 1.0)
            self._paint_cross(painter, start, end, colour, width, 1.0)

    def _paint_curve(self, painter, style, pts, colour, width) -> None:
        """A bowed path between the two dragged points.

        The bow is perpendicular to the chord, so a route curves the same way
        no matter which direction it was drawn in.
        """
        start, end = pts[0], pts[-1]
        dx, dy = end.x() - start.x(), end.y() - start.y()
        control = QPointF(
            (start.x() + end.x()) / 2.0 - dy * style.bow,
            (start.y() + end.y()) / 2.0 + dx * style.bow)
        path = QPainterPath(start)
        path.quadTo(control, end)
        painter.drawPath(path)
        if style.arrow_end:
            self._paint_head(painter, control, end, colour, width)

    def _paint_cross(self, painter, start, end, colour, width,
                     scale=1.0, offset=0.0) -> None:
        """A bar across the line at its end - blocking tee, tick, or bar."""
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        # Fixed length, deliberately not scaled by the pen: the halo pen is
        # fatter, and a longer halo bar would poke out past the coloured one
        # as a dark stub instead of outlining it.
        size = 11.0 * scale
        cx = end.x() - math.cos(angle) * width * offset
        cy = end.y() - math.sin(angle) * width * offset
        nx, ny = -math.sin(angle), math.cos(angle)
        painter.drawLine(
            QPointF(cx - nx * size, cy - ny * size),
            QPointF(cx + nx * size, cy + ny * size))

    def _paint_marker(self, painter, style, pts, box, colour, width) -> None:
        """Composite glyphs that live inside the dragged box."""
        name = style.marker
        centre = box.center()
        if name == "x":
            painter.drawLine(box.topLeft(), box.bottomRight())
            painter.drawLine(box.topRight(), box.bottomLeft())
            return
        if name == "triangle":
            painter.drawPolygon(QPolygonF([
                QPointF(centre.x(), box.top()),
                box.bottomRight(), box.bottomLeft()]))
            return
        if name in ("number", "label"):
            # The ring/box only. Typing into it is a later feature; drawing a
            # placeholder character would put text on the film nobody chose.
            if name == "number":
                painter.drawEllipse(box)
            else:
                painter.drawRect(box)
            return
        if name == "spotlight":
            painter.drawEllipse(box)
            # Rays read as "look here" without dimming the frame, which would
            # fight the film itself.
            rx, ry = box.width() / 2.0, box.height() / 2.0
            for step in range(8):
                angle = math.radians(step * 45.0)
                ca, sa = math.cos(angle), math.sin(angle)
                painter.drawLine(
                    QPointF(centre.x() + ca * rx * 1.18,
                            centre.y() + sa * ry * 1.18),
                    QPointF(centre.x() + ca * rx * 1.42,
                            centre.y() + sa * ry * 1.42))
            return
        if name == "double_team":
            apex = QPointF(centre.x(), box.top())
            painter.drawLine(box.bottomLeft(), apex)
            painter.drawLine(box.bottomRight(), apex)
            self._paint_cross(
                painter, box.bottomLeft(), apex, colour, width, 1.4)
            return
        if name == "coverage":
            # The assignment itself: a defender tied to the man covered.
            radius = max(4.0, min(box.width(), box.height()) * 0.22)
            first = QPointF(box.left() + radius, box.top() + radius)
            second = QPointF(box.right() - radius, box.bottom() - radius)
            painter.drawEllipse(first, radius, radius)
            painter.drawLine(first, second)
            painter.drawLine(
                QPointF(second.x() - radius, second.y() - radius),
                QPointF(second.x() + radius, second.y() + radius))
            painter.drawLine(
                QPointF(second.x() + radius, second.y() - radius),
                QPointF(second.x() - radius, second.y() + radius))
            return
        painter.drawLine(pts[0], pts[-1])

    @staticmethod
    def _paint_head(painter, start, end, colour, width) -> None:
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        size = max(11.0, width * 3.4)
        spread = math.radians(26)
        head = QPolygonF([
            end,
            QPointF(end.x() - size * math.cos(angle - spread),
                    end.y() - size * math.sin(angle - spread)),
            QPointF(end.x() - size * math.cos(angle + spread),
                    end.y() - size * math.sin(angle + spread)),
        ])
        painter.setBrush(colour)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(head)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y() or -event.angleDelta().x()
        if delta:
            self.wheel_step.emit(1 if delta > 0 else -1)
            event.accept()
        else:
            event.ignore()

    def mousePressEvent(self, event) -> None:
        if (self._tool and event.button() == Qt.MouseButton.LeftButton
                and not self._frame_rect.isEmpty()):
            pos = event.position()
            self._drawing = to_film(pos.x(), pos.y(), self._frame_rect)
            self._drag_to = self._drawing
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drawing is not None:
            pos = event.position()
            self._drag_to = to_film(pos.x(), pos.y(), self._frame_rect)
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drawing is not None and self._drag_to is not None:
            start, end = self._drawing, self._drag_to
            self._drawing = None
            self._drag_to = None
            # A click without a drag is not a mark. Without this, every
            # stray click on the film would leave a zero-length stroke
            # behind that nobody can see, and undo would appear to do
            # nothing until it had eaten them all.
            if (abs(end[0] - start[0]) > 0.004
                    or abs(end[1] - start[1]) > 0.004):
                # Every tool name is a kind name now, so there is no
                # lookup table left to fall out of date.
                try:
                    kind = MarkKind(self._tool or "")
                except (TypeError, ValueError):
                    kind = MarkKind.ARROW
                self._marks.append(Mark(kind, [start, end], ink=self._ink))
                self.marksChanged.emit()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _VideoPlayerAttributeGrid(AttributeGrid):
    """Keep the Tag Map's natural height without exporting it as a floor.

    The player owns the transient short-window fold. A soft minimum lets the
    top-level window reach that resize event first, while the grid still asks
    for its complete natural height whenever the expanded layout has room.
    """

    def _natural_height(self) -> int:
        return min(self.content_height(), self._ceiling())

    def _apply_height(self) -> None:
        self.setMinimumHeight(0)
        self.setMaximumHeight(self._natural_height())
        self.updateGeometry()

    def _uses_dock_v2(self) -> bool:
        # The legacy ControlCenterDeck (mounted only under TAPESIFT_DOCK_V2=0)
        # lives in a bottom QDockWidget whose width floor is set by this grid;
        # dropping it here let the workspace panels extend into the dock.
        # DockV2Deck sizes itself and needs no floor, which is the whole
        # reason item 1 of ITERATION_2_SPEC removed it. Import lazily to stay
        # clear of the ui_v2 <-> ui_core import edge.
        from tapesift.ui_v2.dock_v2 import dock_v2_enabled
        return dock_v2_enabled()

    def sizeHint(self) -> QSize:
        # ITERATION_2_SPEC item 1 + E repair R-1, corrected by R-2: pin a
        # 1200px floor only for the legacy deck. That floor, plus the
        # telestration-rail inset, is how the region minimum became 1215 and
        # made every 1180 cell clamp; under DockV2Deck the slot follows the
        # deck and the floor is dropped. There is deliberately NO
        # minimumSizeHint override here: the baseline never had one, and the
        # 1200-wide minimum let the workspace panels extend past the legacy
        # bottom dock (test_legacy_control_center_rollback…, 5/5 fail).
        if self._uses_dock_v2():
            return QSize(0, self._natural_height())
        return QSize(1200, self._natural_height())


class VideoPlayer(QWidget):
    in_point_set = Signal(int)    # ms
    out_point_set = Signal(int)    # ms
    add_clip_requested = Signal(int, int, str)  # in_ms, out_ms, typed name
    clip_block_activated = Signal(str, int)      # clip id, clicked timestamp
    clip_trim_started = Signal(str, str, int)    # clip id, edge, timestamp
    clip_trim_preview = Signal(str, str, int)
    clip_trim_finished = Signal(str, str, int)
    timeline_context_requested = Signal(str, int, QPoint)
    #: A gap on the unclaimed rail was clicked: (start_ms, end_ms).
    unclaimed_footage_activated = Signal(int, int)
    #: A grid cell asked to be changed: (clip_id, row key).
    grid_cell_edit_requested = Signal(str, str)
    grid_cell_choice_picked = Signal(str, str, int)
    grid_player_filter_changed = Signal(str)
    timeline_color_mode_changed = Signal(str)
    predicted_snap_requested = Signal()
    clicked = Signal()             # video surface clicked -> return focus to playback
    # User-authored surface edits only. Loading a clip's stored marks through
    # set_telestration_marks() is deliberately silent, so selecting a play can
    # never make the project dirty merely by repainting its annotations.
    telestration_marks_changed = Signal()
    # The source PTS of a frame the analyst actually saw, plus whether that
    # frame answered a hard seek. QMediaPlayer.positionChanged is deliberately
    # not exposed as presentation truth because it can lead decoder delivery.
    source_frame_presented = Signal(int, bool)

    def __init__(
            self, settings: AppSettings, parent=None,
            timeline_variant: str = DUAL) -> None:
        super().__init__(parent)
        self.settings = settings
        self.timeline_variant = normalize_timeline_variant(timeline_variant)
        self._control_center: ControlCenterDeck | None = None
        self._selected_timeline_clip_id: str | None = None
        self._selection_epoch = 0
        self._last_presented_selection_epoch: int | None = None
        self._selected_clip_range_ms: tuple[int, int] | None = None
        self.in_point_ms: int | None = None
        self.out_point_ms: int | None = None
        self.frame_duration_ms: float = 1000 / 30  # updated when metadata loads

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(self.settings.volume / 100)

        self.video_widget = StepVideoWidget(self)
        self.video_widget.setMinimumHeight(240)
        self.video_widget.wheel_step.connect(self._wheel_frame)
        self.video_widget.clicked.connect(self.clicked)
        self.video_widget.marksChanged.connect(
            self._surface_marks_changed)
        self.player.setVideoOutput(self.video_widget.videoSink())
        self._last_presented_source_position_ms: int | None = None
        self._pending_step_frame: int | None = None
        self._next_presented_frame_is_hard_seek = False
        self._awaiting_initial_frame = False
        self._initial_stopped_seek_waking = False
        self._initial_stopped_seek_target_ms: int | None = None
        self._initial_stopped_seek_wake_timer = QTimer(self)
        self._initial_stopped_seek_wake_timer.setSingleShot(True)
        self._initial_stopped_seek_wake_timer.setInterval(
            INITIAL_STOPPED_FRAME_WAKE_DELAY_MS)
        self._initial_stopped_seek_wake_timer.timeout.connect(
            self._wake_initial_stopped_seek)
        self._initial_stopped_seek_hold_timer = QTimer(self)
        self._initial_stopped_seek_hold_timer.setSingleShot(True)
        self._initial_stopped_seek_hold_timer.setInterval(
            INITIAL_STOPPED_FRAME_HOLD_TIMEOUT_MS)
        self._initial_stopped_seek_hold_timer.timeout.connect(
            self._initial_stopped_seek_hold_timed_out)
        self.video_widget.frame_hold_reached.connect(
            self._initial_stopped_seek_frame_held)
        self.video_widget.frame_hold_missed.connect(
            self._initial_stopped_seek_frame_missed)
        self.video_widget.frame_presented.connect(
            self._source_frame_presented)
        self._was_playing_before_scrub = False
        self._scrub_preview_clock = QElapsedTimer()
        self._last_scrub_seek_ms: int | None = None

        # Forward uses playbackRate. Reverse waits for decoded delivery AND
        # the selected source-frame period: a fast decoder must not turn 1x
        # into its maximum decode speed. One timer handles that wait and the
        # existing stalled-decoder retry; K/source changes cancel both.
        self._shuttle_dir = 0     # -1 reverse, 0 off, +1 forward
        self._shuttle_idx = 0     # index into SHUTTLE_SPEEDS
        self._reverse_watchdog = QTimer(self)
        self._reverse_watchdog.setSingleShot(True)
        self._reverse_watchdog.setTimerType(Qt.TimerType.PreciseTimer)
        self._reverse_watchdog.setInterval(REVERSE_WATCHDOG_MS)
        self._reverse_watchdog.timeout.connect(self._reverse_step)
        self._reverse_clock = QElapsedTimer()
        self._frame_hook_connected = False
        self._user_muted = False       # the mute the user chose
        self._transport_muted = False  # temporary mute during shuttle

        # Background media work (proxies, play detection, thumbnails) is
        # suspended whenever the transport is in use, and only resumes after
        # it has been idle for a moment - so a batch of background jobs can
        # never make scrubbing choppy again.
        # Clip-range playback (review mode): 0 means "no range, play freely".
        self._range_start = 0
        self._range_end = 0
        self._range_loop = False
        self._range_finished = False
        self._quick_tags_mounted = False

        self._transport_busy = False
        self._transport_idle_timer = QTimer(self)
        self._transport_idle_timer.setSingleShot(True)
        self._transport_idle_timer.setInterval(1500)
        self._transport_idle_timer.timeout.connect(self._transport_went_idle)

        # Cache to avoid needless label repaints during transport.
        self._last_time_text = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Drawing lives in one place: a persistent rail beside the film.  The
        # StepVideoWidget and its QVideoSink stay authoritative and unchanged;
        # this row only gives the existing surface a sibling.
        self.telestration_rail = self._build_telestration_rail()
        film_row = QHBoxLayout()
        film_row.setContentsMargins(0, 0, 0, 0)
        film_row.setSpacing(5)
        film_row.addWidget(self.telestration_rail)
        film_row.addWidget(self.video_widget, 1)
        layout.addLayout(film_row, 1)

        # One compact source-viewport row. These controls move only the
        # visual window over source time; none of them seeks the player or
        # changes a clip boundary.
        self.timeline_viewport_controls = QWidget()
        self.timeline_viewport_controls.setObjectName(
            "TimelineViewportControls")
        viewport_controls = QHBoxLayout(self.timeline_viewport_controls)
        viewport_controls.setContentsMargins(0, 0, 0, 0)
        viewport_controls.setSpacing(4)
        self.timeline_zoom_out = self._viewport_button(
            "", "Zoom out timeline (show more time)",
            self.zoom_timeline_out, width=28)
        self.timeline_zoom_out.setObjectName("TimelineZoomOut")
        self.timeline_zoom_out.setProperty("circularControl", "true")
        self.timeline_zoom_out.setStyleSheet(CIRCULAR_CONTROL_STYLE)
        self.timeline_zoom_out.setIcon(magnifier_icon("out"))
        self.timeline_zoom_out.setIconSize(QSize(18, 18))
        viewport_controls.addWidget(self.timeline_zoom_out)
        self.timeline_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_zoom_slider.setProperty("timelineZoom", "true")
        self.timeline_zoom_slider.setRange(
            0, len(TIMELINE_ZOOM_FACTORS) - 1)
        self.timeline_zoom_slider.setValue(0)
        self.timeline_zoom_slider.setFixedWidth(96)
        self.timeline_zoom_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.timeline_zoom_slider.setAccessibleName("Timeline zoom")
        self.timeline_zoom_slider.setToolTip(
            "Zoom around the playhead | Ctrl+0 shows the full game")
        self.timeline_zoom_slider.valueChanged.connect(
            self._timeline_zoom_changed)
        viewport_controls.addWidget(self.timeline_zoom_slider)
        self.timeline_zoom_in = self._viewport_button(
            "", "Zoom in timeline (show less time)",
            self.zoom_timeline_in, width=28)
        self.timeline_zoom_in.setObjectName("TimelineZoomIn")
        self.timeline_zoom_in.setProperty("circularControl", "true")
        self.timeline_zoom_in.setStyleSheet(CIRCULAR_CONTROL_STYLE)
        self.timeline_zoom_in.setIcon(magnifier_icon("in"))
        self.timeline_zoom_in.setIconSize(QSize(18, 18))
        viewport_controls.addWidget(self.timeline_zoom_in)
        self.timeline_zoom_label = QLabel("1×")
        self.timeline_zoom_label.setProperty("viewportValue", "true")
        self.timeline_zoom_label.setAccessibleName("Current timeline zoom")
        self.timeline_zoom_label.setFixedWidth(36)
        self.timeline_zoom_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        viewport_controls.addWidget(self.timeline_zoom_label)
        viewport_controls.addSpacing(6)
        self.timeline_range_label = QLabel("00:00 – 00:00")
        self.timeline_range_label.setProperty("viewportRange", "true")
        self.timeline_range_label.setAccessibleName(
            "Visible timeline source range")
        self.timeline_range_label.setToolTip(
            "Source time currently visible in the scrubber")
        self.timeline_range_label.setMinimumWidth(124)
        self.timeline_range_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        viewport_controls.addWidget(self.timeline_range_label)
        self.timeline_fit_game = self._viewport_button(
            "Fit Game", "Show the full video", self.reset_timeline_zoom,
            width=66)
        self.timeline_fit_game.setProperty("fitViewport", "true")
        viewport_controls.addWidget(self.timeline_fit_game)
        self.timeline_fit_play = self._viewport_button(
            "Fit Play", "Fit the selected play with surrounding context",
            self.fit_timeline_play, width=62)
        self.timeline_fit_play.setProperty("fitViewport", "true")
        self.timeline_fit_play.setEnabled(False)
        viewport_controls.addWidget(self.timeline_fit_play)
        self.timeline_variant_label = QLabel(
            VARIANTS[self.timeline_variant].title.upper())
        self.timeline_variant_label.setProperty("role", "eyebrow")
        self.timeline_variant_label.setAccessibleName(
            f"Timeline design {VARIANTS[self.timeline_variant].title}")
        self.timeline_variant_label.setToolTip(
            VARIANTS[self.timeline_variant].description)
        viewport_controls.addSpacing(6)
        viewport_controls.addWidget(self.timeline_variant_label)
        self.timeline_fragments_button = self._viewport_button(
            "Fragments",
            "Show preserved unclassified detector fragments",
            None,
            width=96,
        )
        self.timeline_fragments_button.setCheckable(True)
        self.timeline_fragments_button.setChecked(
            bool(self.settings.timeline_show_ignored_fragments))
        self.timeline_fragments_button.toggled.connect(
            self._timeline_fragments_toggled)
        self.timeline_fragments_button.hide()
        viewport_controls.addWidget(self.timeline_fragments_button)
        viewport_controls.addStretch(1)
        self.timeline_follow_playhead = self._viewport_button(
            "Follow Playhead",
            "Keep the playhead visible during playback",
            None,
            width=112,
        )
        self.timeline_follow_playhead.setProperty(
            "followPlayhead", "true")
        self.timeline_follow_playhead.setCheckable(True)
        self.timeline_follow_playhead.setChecked(
            bool(self.settings.timeline_follow_playhead))
        self.timeline_follow_playhead.toggled.connect(
            self._timeline_follow_changed)
        viewport_controls.addWidget(self.timeline_follow_playhead)
        self.timeline_pan_left = self._viewport_button(
            "<", "Move the visible range earlier",
            self.pan_timeline_left, width=24)
        self.timeline_pan_right = self._viewport_button(
            ">", "Move the visible range later",
            self.pan_timeline_right, width=24)
        viewport_controls.addWidget(self.timeline_pan_left)
        viewport_controls.addWidget(self.timeline_pan_right)
        layout.addWidget(self.timeline_viewport_controls)

        self.timeline_overview: TimelineOverview | None = None
        if SHOW_TIMELINE_OVERVIEW and self.timeline_variant == DUAL:
            self.timeline_overview = TimelineOverview(self)
            self.timeline_overview.seekRequested.connect(self.seek_to)
            self.timeline_overview.visibleStartRequested.connect(
                self._overview_visible_start_requested)
            overview_row = QHBoxLayout()
            overview_row.setContentsMargins(26, 0, 26, 0)
            overview_row.addWidget(self.timeline_overview)
            layout.addLayout(overview_row)

        # Scrubber row
        scrub_row = QHBoxLayout()
        scrub_row.setContentsMargins(0, 0, 0, 0)
        # No spacing: the gutter's own right margin is the gap, and any
        # extra here would offset the timeline from the grid below it.
        scrub_row.setSpacing(0)
        self.slider = Timeline(variant=self.timeline_variant)
        # Quarter boundaries still drive snapping and project metadata, but
        # Review no longer spends a rail repeating the quarter choices that
        # already live in Play Details.
        self.slider.set_period_rail_visible(False)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._scrub_moved)
        self.slider.sliderPressed.connect(self._scrub_started)
        self.slider.sliderReleased.connect(self._scrub_finished)
        self.slider.wheel_seek.connect(self._wheel_seek)
        self.slider.wheel_frame.connect(self._wheel_frame)
        self.slider.wheel_zoom.connect(self._wheel_zoom)
        self.slider.blockActivated.connect(self.clip_block_activated.emit)
        self.slider.trimStarted.connect(self.clip_trim_started.emit)
        self.slider.trimPreview.connect(self.clip_trim_preview.emit)
        self.slider.trimFinished.connect(self.clip_trim_finished.emit)
        self.slider.coverageActivated.connect(self._coverage_activated)
        self.slider.unclaimedActivated.connect(
            self.unclaimed_footage_activated.emit)
        self.slider.contextRequested.connect(self.timeline_context_requested.emit)
        self.slider.visibleRangeChanged.connect(
            self._timeline_visible_range_changed)
        self.slider.set_follow_playhead(
            self.timeline_follow_playhead.isChecked())
        self.slider.set_show_ignored_fragments(
            self.timeline_fragments_button.isChecked())
        self.slider.setToolTip(
            "Drag to scrub • wheel to jump • Shift+wheel to frame step • "
            "Ctrl+wheel to zoom at pointer")
        self.total_label = QLabel("00:00")
        self.total_label.setFixedWidth(36)
        self.total_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # One content edge for the scrub too: 10_SPEC_BAND_B.md puts every
        # row at x=26..width-26. The old 168px timeline_gutter aligned the
        # slider to the attribute grid's GUTTER_W below; the grid keeps its
        # own gutter and the band no longer borrows it, so the scrub joins
        # the shared edge. The total-duration label moved to the view rail.
        scrub_row.setContentsMargins(26, 0, 26, 0)
        self.timeline_scrub_layout = scrub_row
        scrub_row.setSpacing(0)
        scrub_row.addWidget(self.slider, 1)
        layout.addLayout(scrub_row)

        self.attribute_grid = _VideoPlayerAttributeGrid(self)
        self.attribute_grid.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.attribute_grid.clipActivated.connect(
            self._grid_clip_activated)
        self.attribute_grid.cellEditRequested.connect(
            self.grid_cell_edit_requested.emit)
        self.attribute_grid.cellChoicePicked.connect(
            self.grid_cell_choice_picked.emit)
        self.attribute_grid.playerFilterChanged.connect(
            self.grid_player_filter_changed.emit)
        self.slider.cellDensityChanged.connect(
            self.attribute_grid.set_density)
        self.slider.visibleRangeChanged.connect(
            self.attribute_grid.set_visible_range)
        self.slider.visibleRangeChanged.connect(
            self._update_timeline_range_label)
        self.attribute_grid.set_density(self.slider.cell_density())
        self.attribute_grid.set_visible_range(
            *self.slider.visible_range())
        # The Tag Map is one persistent workspace, not one side of a mode
        # switch.  Its direct-child grid keeps the teardown order proven safe
        # with the live media player; the sibling header owns Quick Tags and a
        # whole-panel fold that returns height to the film.
        self.timeline_header = QWidget(self)
        self.timeline_header.setObjectName("TagMapHeader")
        self._timeline_header_layout = QHBoxLayout(self.timeline_header)
        self._timeline_header_layout.setContentsMargins(8, 2, 4, 2)
        self._timeline_header_layout.setSpacing(6)
        tag_map_label = QLabel("TAG MAP")
        tag_map_label.setObjectName("TagMapHeading")
        tag_map_label.setProperty("role", "eyebrow")
        self._timeline_header_layout.addWidget(tag_map_label)

        self.quick_tag_slot = QWidget(self.timeline_header)
        self.quick_tag_slot.setObjectName("TagMapQuickTagSlot")
        self.quick_tag_slot.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        slot_layout = QHBoxLayout(self.quick_tag_slot)
        slot_layout.setContentsMargins(0, 0, 0, 0)
        slot_layout.setSpacing(0)
        self.quick_tag_slot.hide()
        self._timeline_header_layout.addWidget(self.quick_tag_slot, 1)

        self.tag_map_collapse_button = QToolButton(self.timeline_header)
        self.tag_map_collapse_button.setObjectName("TagMapCollapse")
        self.tag_map_collapse_button.setProperty("timelineKey", "true")
        self.tag_map_collapse_button.setCheckable(True)
        self.tag_map_collapse_button.setFixedHeight(28)
        self.tag_map_collapse_button.setMinimumWidth(82)
        self.tag_map_collapse_button.setToolTip(
            "Collapse or expand the complete Tag Map")
        self.tag_map_collapse_button.setAccessibleName("Collapse Tag Map")
        self.tag_map_collapse_button.toggled.connect(
            self.set_tag_map_collapsed)
        self._timeline_header_layout.addWidget(
            self.tag_map_collapse_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.timeline_header)
        layout.addWidget(self.attribute_grid)

        # The window's transport strip mounts in the film column, above the
        # Tag Map, via mount_control_strip(). The slot exists whether or not
        # a strip is mounted; it is hidden until one is. Inserted before the
        # Tag Map header so the deck sits between the scrub row and the rail.
        self.control_strip_slot = QWidget(self)
        self.control_strip_slot.setObjectName("PlayerStripSlot")
        strip_slot_layout = QHBoxLayout(self.control_strip_slot)
        strip_slot_layout.setContentsMargins(0, 0, 0, 0)
        strip_slot_layout.setSpacing(0)
        self.control_strip_slot.hide()
        # ITERATION_1_SPEC item 2. Two 1px rules give the region its own
        # edges: without them the four bands run together and the band
        # bleeds into the tag map below and the film above. They are also
        # the 2px that make the region 154 rather than 152.
        self._region_rule_top = self._region_rule()
        self._region_rule_bottom = self._region_rule()
        layout.insertWidget(
            layout.indexOf(self.timeline_header), self._region_rule_top)
        layout.insertWidget(
            layout.indexOf(self.timeline_header), self.control_strip_slot)
        layout.insertWidget(
            layout.indexOf(self.timeline_header), self._region_rule_bottom)

        self._responsive_tag_map_collapsed = False
        self.set_tag_map_collapsed(bool(getattr(
            self.settings, "workspace_tag_map_collapsed", False)),
            persist=False)

        # Native horizontal navigation for every zoomed source range. The
        # thumb fills the track at Fit Game and shrinks with the visible span.
        self.timeline_viewport_scroll = QScrollBar(
            Qt.Orientation.Horizontal, self)
        self.timeline_viewport_scroll.setObjectName(
            "TimelineViewportScroll")
        self.timeline_viewport_scroll.setFocusPolicy(
            Qt.FocusPolicy.NoFocus)
        self.timeline_viewport_scroll.setAccessibleName(
            "Move the visible timeline range")
        self.timeline_viewport_scroll.setToolTip(
            "Zoom in, then drag to move earlier or later without seeking")
        self.timeline_viewport_scroll.valueChanged.connect(
            self._timeline_scroll_changed)
        viewport_scroll_row = QHBoxLayout()
        viewport_scroll_row.setContentsMargins(42, 0, 42, 0)
        viewport_scroll_row.addWidget(self.timeline_viewport_scroll)
        layout.addLayout(viewport_scroll_row)

        # Compact timeline key and presentation selector. The menu expands only
        # when needed, leaving the player more vertical room for the film.
        self.timeline_legend = QWidget()
        self.timeline_legend.setObjectName("TimelineLegend")
        self.timeline_legend.setAccessibleName("Timeline play type legend")
        legend = QHBoxLayout(self.timeline_legend)
        legend.setContentsMargins(0, 0, 0, 0)
        legend.setSpacing(4)
        self.timeline_key_menu = QMenu(self.timeline_legend)
        self.timeline_key_button = QToolButton()
        self.timeline_key_button.setText("Key")
        self.timeline_key_button.setObjectName("TimelineKeyLegend")
        self.timeline_key_button.setProperty("timelineKey", "true")
        self.timeline_key_button.setFixedSize(50, 20)
        self.timeline_key_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.timeline_key_button.setMenu(self.timeline_key_menu)
        legend.addWidget(self.timeline_key_button)
        self.timeline_color_combo = QComboBox()
        self.timeline_color_combo.setProperty("timelineColor", "true")
        self.timeline_color_combo.setMinimumWidth(96)
        self.timeline_color_combo.setMaximumWidth(112)
        self.timeline_color_combo.setAccessibleName("Color timeline blocks by")
        self.timeline_color_combo.setToolTip(
            "Choose which clip metadata controls timeline block colors")
        for mode, text in TIMELINE_COLOR_MODES:
            self.timeline_color_combo.addItem(text, mode)
        initial_mode = self.settings.timeline_color_by
        if initial_mode not in TIMELINE_COLOR_MODE_LABELS:
            initial_mode = "play_type"
        initial_index = self.timeline_color_combo.findData(initial_mode)
        self.timeline_color_combo.setCurrentIndex(max(0, initial_index))
        self.timeline_color_combo.currentIndexChanged.connect(
            self._timeline_color_mode_selected)
        # The combo stays the authoritative control - every caller and test
        # still drives it - but it is no longer a 100px widget parked in the
        # strip. Its choices are offered inside the Key popup, which already
        # announces the active mode in its header.
        self.timeline_color_combo.hide()
        self.timeline_snap_button = QToolButton()
        self.timeline_snap_button.setProperty("timelineKey", "true")
        self.timeline_snap_button.setCheckable(True)
        self.timeline_snap_button.setChecked(True)
        self.timeline_snap_button.setObjectName("TimelineSnapToggle")
        self.timeline_snap_button.setFixedSize(68, 20)
        self.timeline_snap_button.toggled.connect(
            self._timeline_snapping_toggled)
        legend.addWidget(self.timeline_snap_button)
        self._timeline_snapping_toggled(True)
        self.timeline_legend_items: dict[str, QLabel] = {}
        self.set_timeline_key(
            initial_mode, build_timeline_legend(initial_mode, {}))

        left_group = QWidget(self)
        left_group.setObjectName("TimelineViewControls")
        left_controls = QHBoxLayout(left_group)
        left_controls.setContentsMargins(0, 0, 0, 0)
        left_controls.setSpacing(4)
        # The merged scrub no longer carries a gutter for the total-duration
        # label (it sits on the shared x=26 edge now), so the label parks at
        # the rail's left edge; the range label owns the right.
        left_controls.addWidget(self.total_label)

        self.shuttle_label = QLabel("")
        self.shuttle_label.setProperty("role", "subtle")
        self.shuttle_label.setToolTip("Shuttle: J reverse, K stop, L forward - "
                                      "tap again to speed up")
        self.shuttle_label.hide()

        self.timeline_zoom_out.setFixedSize(30, 20)
        # The zoom slider is the left flank's elastic element: it absorbs
        # whatever width the flank has spare, so the row never carries a gap.
        self.timeline_zoom_slider.setMinimumWidth(56)
        self.timeline_zoom_slider.setMaximumWidth(16777215)
        self.timeline_zoom_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.timeline_zoom_in.setFixedSize(30, 20)
        # Sized from the polished label rather than a hardcoded width.
        # 46 and 42 were narrower than 'Fit Game' and 'Fit Play' need
        # once the stylesheet's padding is counted, so both elided -
        # 'Fit Play' rendered as 'Fit_lay' on the strip.
        # ITERATION_1_SPEC item 6: rail controls are 20px, which is the one
        # height on the scale below the deck's 32. They are named so the
        # contract can declare the rail a tight cluster - a rail deliberately
        # under the touch floor is a decision, and a decision has to be
        # nameable to be checked.
        self.timeline_fit_game.setObjectName("TimelineFitGame")
        self.timeline_fit_play.setObjectName("TimelineFitPlay")
        for control in (self.timeline_fit_game, self.timeline_fit_play):
            control.ensurePolished()
            control.setFixedSize(
                max(46, control.sizeHint().width()), 20)
        # The zoom slider is gone from the row: it spent ~96px on a
        # control people nudge. The two round buttons do the same job,
        # and the slider stays alive and authoritative for callers and
        # tests that drive zoom directly.
        self.timeline_zoom_slider.hide()
        detach_widget(self.timeline_zoom_out)
        detach_widget(self.timeline_zoom_in)
        left_controls.addWidget(self.timeline_zoom_out)
        left_controls.addWidget(self.timeline_zoom_in)
        left_controls.addSpacing(8)
        for control in (
                self.timeline_fit_game,
                self.timeline_fit_play):
            detach_widget(control)
            left_controls.addWidget(control)
        self.predicted_snap_button = self._btn(
            "Snap",
            "Select a play to find or jump to its predicted snap (G)",
            self.predicted_snap_requested.emit,
        )
        self.predicted_snap_button.setObjectName("PredictedSnapAction")
        self.predicted_snap_button.setProperty("transport", "true")
        self.predicted_snap_button.setFixedSize(84, 20)
        left_controls.addWidget(self.predicted_snap_button)
        self.set_predicted_snap_state("disabled")
        left_controls.addSpacing(5)

        self.volume_btn = QToolButton()
        self.volume_btn.setObjectName("VolumeControl")
        self.volume_btn.setProperty("iconLibrary", "Segoe Fluent Icons")
        self.volume_btn.setProperty("iconAsset", VOLUME_ICON.name)
        self.volume_btn.setIcon(QIcon(str(VOLUME_ICON)))
        self.volume_btn.setIconSize(QSize(18, 18))
        self.volume_btn.setProperty("mediaControl", "true")
        self.volume_btn.setStyleSheet(CIRCULAR_CONTROL_STYLE)
        self.volume_btn.setFixedSize(38, 38)
        self.volume_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume_btn.setAccessibleName("Volume and mute")
        self.volume_btn.setToolTip("Volume and mute")
        self.volume_btn.clicked.connect(self._show_volume_popup)
        left_controls.addWidget(self.volume_btn)
        self._build_volume_popup()

        self.inline_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.inline_volume_slider.setObjectName("InlineVolumeSlider")
        self.inline_volume_slider.setRange(0, 100)
        self.inline_volume_slider.setValue(self.settings.volume)
        self.inline_volume_slider.setFixedWidth(62)
        self.inline_volume_slider.setToolTip("Volume")
        self.inline_volume_slider.valueChanged.connect(self._volume_changed)
        left_controls.addWidget(self.inline_volume_slider)
        self.inline_volume_slider.hide()

        self.speed_combo = QComboBox()
        for speed in PLAYBACK_SPEEDS:
            self.speed_combo.addItem(f"{speed:g}x", speed)
        default_idx = PLAYBACK_SPEEDS.index(self.settings.default_playback_speed) \
            if self.settings.default_playback_speed in PLAYBACK_SPEEDS else 2
        self.speed_combo.setCurrentIndex(default_idx)
        self.speed_combo.currentIndexChanged.connect(self._speed_changed)
        self.speed_combo.setToolTip("Playback speed")
        self.speed_combo.setProperty("transport", "true")
        self.speed_combo.setFixedWidth(62)
        left_controls.addWidget(self.speed_combo)
        self.speed_combo.hide()
        self.timeline_range_label = QLabel("")
        self.timeline_range_label.setObjectName("TimelineRangeLabel")
        self.timeline_range_label.setProperty("role", "subtle")
        # Key and Snap are timeline-view settings like zoom and fit, so they
        # join that group instead of sitting among the marking controls.
        left_controls.addSpacing(6)
        left_controls.addWidget(self.timeline_legend)
        left_controls.addStretch(1)
        left_controls.addWidget(self.timeline_range_label)
        # Everything that changes what you SEE lives in one strip above
        # the transport, which also gives the timeline and the attribute
        # grid a boundary that does a job instead of a divider line.
        self.view_strip = QWidget(self)
        self.view_strip.setObjectName("TimelineViewStrip")
        view_row = QHBoxLayout(self.view_strip)
        # ITERATION_1_SPEC item 2 and 3. 34px tall, and its content starts
        # on the shared x=26 edge like every other row (10_SPEC_BAND_B.md
        # item 3): _FILM_COLUMN_INSET is 0 since iteration 3 dropped the
        # telestration-rail inset from the band. 7px top/bottom keeps 20px
        # controls inside 34px.
        self.view_strip.setFixedHeight(34)
        view_row.setContentsMargins(
            _FILM_COLUMN_INSET + 26, 7, 26, 7)
        view_row.setSpacing(0)
        view_row.addWidget(left_group)
        # View controls describe the timeline above the Tag Map, so they must
        # precede both the Tag Map header and its grid in the vertical flow.
        layout.insertWidget(
            layout.indexOf(self.timeline_header), self.view_strip)

        # Naming is behavioral state, not part of the window-owned deck.
        # The field stays hidden because naming happens in the inspector, but
        # request_add_clip() and research workflows retain the same API.
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("Clip name - Enter to add")
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.returnPressed.connect(self.request_add_clip)
        self.name_edit.textChanged.connect(self._update_filename_preview)
        self.name_edit.hide()

        # Film-side telestration reuses the reserved key/swatch treatment.
        # The six-zone control center applies its complete sheet to itself.
        self.setStyleSheet(self.styleSheet() + DECK_KEY_QSS)

        # Range text, the internal variant name, follow mode and pan buttons
        # remain live for menu/keyboard routing, but their former row is gone.
        # The visible zoom controls live in the view strip above the Tag Map.
        # The slim viewport navigator is synchronized below and appears only
        # while zoomed, so Fit Game preserves the standard compact layout.
        self.timeline_viewport_controls.hide()
        self.timeline_viewport_scroll.hide()

        # The filename preview only earns a line when there is one to show.
        self.filename_preview = QLabel("")
        self.filename_preview.setProperty("role", "subtle")
        self.filename_preview.setContentsMargins(4, 0, 0, 0)
        self.filename_preview.hide()
        layout.addWidget(self.filename_preview)

        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(
            self._initial_stopped_seek_media_status_changed)
        self.player.errorOccurred.connect(self._on_error)
        self.error_label = QLabel("")
        self.error_label.setProperty("role", "warning")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self._sync_timeline_viewport_controls()

    @property
    def control_center(self) -> ControlCenterDeck | None:
        """The explicitly attached presentation deck, if an owner supplied one."""
        return self._control_center

    def attach_control_center(self, deck: ControlCenterDeck) -> None:
        """Bind a window/research-owned deck to this single media authority.

        A bare VideoPlayer intentionally has no transport widget tree.  The
        owner must construct one deck and attach it; attaching a different
        second tree is rejected so playback and telemetry can never fork.
        """
        if self._control_center is deck:
            return
        if self._control_center is not None:
            raise RuntimeError("VideoPlayer already has a control center")
        deck.bind_player(self)
        self._control_center = deck
        self._control_center_aliases = tuple(deck.COMPATIBILITY_ALIASES)
        # Temporary access compatibility for callers that manipulate player
        # controls directly.  The objects remain owned by the window's deck.
        self.transport_pill = deck
        for name in self._control_center_aliases:
            setattr(self, name, getattr(deck, name))
        deck.destroyed.connect(self._control_center_destroyed)
        position = self.displayed_position_ms()
        if position is None:
            position = 0
        deck.set_position(
            self._jog_timecode(position),
            round(position / max(0.001, self.frame_duration_ms)),
        )
        deck.set_marks(self.in_point_ms, self.out_point_ms)
        deck.set_shuttle_rate(0.0)
        deck.set_playing(
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState)

    def _control_center_destroyed(self, *_args) -> None:
        self._control_center = None
        self.transport_pill = None
        for name in getattr(self, "_control_center_aliases", ()):
            setattr(self, name, None)

    def _set_play_glyph(self, standard_pixmap, optical_offset=QPoint(0, 0)):
        """Swap the play key between its two states, from the deck set."""
        playing = standard_pixmap == QStyle.StandardPixmap.SP_MediaPause
        deck = self._control_center
        if deck is not None:
            deck.set_playing(playing)

    def _region_rule(self) -> QFrame:
        """A 1px hairline between two bands of the transport region."""
        rule = QFrame(self)
        rule.setObjectName("TransportRegionRule")
        rule.setFrameShape(QFrame.Shape.NoFrame)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {COLORS['line']};")
        return rule

    def mount_control_strip(self, widget: QWidget | None) -> None:
        """Put the window's transport strip in the film column.

        Passing None empties the slot again. The widget stays owned by the
        window, exactly as the deck was when it lived in a bottom dock.
        """
        slot = self.control_strip_slot.layout()
        while slot.count():
            item = slot.takeAt(0)
            existing = item.widget()
            if existing is not None:
                existing.setParent(None)
        if widget is None:
            self.control_strip_slot.hide()
            return
        # Mount flush: iteration 3 pins every row to the shared x=26
        # content edge (10_SPEC_BAND_B.md), so the deck spans the window
        # and its island centres on the window, not on the telestration
        # rail's picture column. _FILM_COLUMN_INSET is 0.
        slot.setContentsMargins(_FILM_COLUMN_INSET, 0, 0, 0)
        slot.addWidget(widget)
        self.control_strip_slot.show()

    def mount_quick_tags(self, widget: QWidget | None) -> None:
        """Put the owner's quick-tag rail in the Tag Map header.

        Passing None empties the slot again, which is what specialized review
        sessions that hide the everyday rail need.
        """
        slot = self.quick_tag_slot.layout()
        while slot.count():
            item = slot.takeAt(0)
            existing = item.widget()
            if existing is not None:
                existing.setParent(None)
        if widget is None:
            self._quick_tags_mounted = False
            self.quick_tag_slot.hide()
            return
        slot.addWidget(widget)
        widget.show()
        self._quick_tags_mounted = True
        self.quick_tag_slot.setVisible(
            not self._tag_map_effectively_collapsed())

    def mount_timeline_controls(self, controls: QWidget) -> None:
        """Show another panel's controls on the timeline's own header row."""
        self._timeline_header_layout.insertWidget(
            max(1, self._timeline_header_layout.count() - 1), controls)
        controls.show()

    def unmount_timeline_controls(self, controls: QWidget) -> None:
        """Release controls back to whoever owns them."""
        self._timeline_header_layout.removeWidget(controls)

    def _update_timeline_range_label(self, *_args) -> None:
        """Say how much of the film the window is showing."""
        start, end = self.slider.visible_range()
        total = max(0, self.slider.maximum() - self.slider.minimum())
        if total <= 0 or end <= start:
            self.timeline_range_label.setText("")
            return
        shown = end - start
        if shown >= total:
            self.timeline_range_label.setText(
                f"WHOLE GAME · {round(total / 60_000)} MIN")
            return
        self.timeline_range_label.setText(
            f"SHOWING {max(1, round(shown / 60_000))}"
            f" OF {max(1, round(total / 60_000))} MIN")

    def _grid_clip_activated(self, clip_id: str) -> None:
        """A grid column was clicked: select that play.

        Routed through the same signal the timeline blocks use, so the
        grid is another way to reach an existing action rather than a
        second selection path.
        """
        for block in self.slider.blocks():
            if block.clip_id == clip_id:
                self.clip_block_activated.emit(clip_id, block.start_ms)
                return

    def _build_volume_popup(self) -> None:
        """A vertical volume control that only exists while it is open."""
        self.volume_popup = QFrame(self, Qt.WindowType.Popup)
        self.volume_popup.setObjectName("VolumePopup")
        self.volume_popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup_layout = QVBoxLayout(self.volume_popup)
        popup_layout.setContentsMargins(8, 7, 8, 7)
        popup_layout.setSpacing(5)

        caption = QLabel("VOL")
        caption.setProperty("role", "legendTitle")
        caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        popup_layout.addWidget(caption)

        self.volume_slider = QSlider(Qt.Orientation.Vertical)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.settings.volume)
        self.volume_slider.setFixedHeight(88)
        self.volume_slider.setToolTip("Volume")
        self.volume_slider.valueChanged.connect(self._volume_changed)
        popup_layout.addWidget(
            self.volume_slider, 0, Qt.AlignmentFlag.AlignHCenter)

        self.mute_btn = self._btn("Mute", "Mute/unmute audio",
                                  self._toggle_mute)
        self.mute_btn.setProperty("transport", "true")
        self.mute_btn.setFixedWidth(52)
        popup_layout.addWidget(self.mute_btn)
        self.volume_popup.hide()

    def _show_volume_popup(self) -> None:
        self.volume_popup.adjustSize()
        anchor = self.volume_btn.mapToGlobal(
            self.volume_btn.rect().topLeft())
        self.volume_popup.move(
            anchor.x() - 10, anchor.y() - self.volume_popup.height() - 6)
        self.volume_popup.show()

    def _btn(self, text: str, tooltip: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setToolTip(tooltip)
        b.clicked.connect(slot)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep shortcuts working
        return b

    def _viewport_button(
            self, text: str, tooltip: str, slot,
            *, width: int) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setProperty("viewportControl", "true")
        button.setFixedWidth(width)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        if slot is not None:
            button.clicked.connect(slot)
        return button

    def resizeEvent(self, event) -> None:
        """Keep the editing controls usable when both side panels are open."""
        super().resizeEvent(event)
        if not hasattr(self, "timeline_legend"):
            return
        self._set_responsive_tag_map_collapsed(
            event.size().height() < 600)
        self.inline_volume_slider.setVisible(event.size().width() >= 1024)

    def load(self, path: Path, frame_rate: float = 30.0) -> None:
        self.video_widget.discard_unpainted_frames()
        self.error_label.hide()
        self._reset_shuttle()
        self._reset_initial_stopped_seek_wake()
        self._last_presented_source_position_ms = None
        self._pending_step_frame = None
        self._last_time_text = ""
        self._last_presented_selection_epoch = None
        self._next_presented_frame_is_hard_seek = True
        self._awaiting_initial_frame = True
        if frame_rate > 0:
            self.frame_duration_ms = 1000 / frame_rate
        # Presentation only: lets the timeline draw frame ticks when zoomed
        # right in. Frame stepping keeps using self.frame_duration_ms.
        self.slider.set_frame_duration_ms(self.frame_duration_ms)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.in_point_ms = None
        self.out_point_ms = None
        self._update_marks_label()

    def swap_source(self, path: Path) -> None:
        """Hot-swap the media file, preserving position and play state.

        Used when the scrub-optimized proxy finishes: same timeline, lighter
        file. In/out marks are timestamps, so they carry over untouched.
        """
        self.video_widget.discard_unpainted_frames()
        position = self.player.position()
        was_playing = \
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self._reset_shuttle()
        self._reset_initial_stopped_seek_wake()
        self._last_presented_source_position_ms = None
        self._pending_step_frame = None
        self._last_time_text = ""
        self._last_presented_selection_epoch = None
        self._next_presented_frame_is_hard_seek = True

        def _restore(status) -> None:
            if status == QMediaPlayer.MediaStatus.LoadedMedia:
                self.player.mediaStatusChanged.disconnect(_restore)
                self._set_media_position(position, hard_seek=True)
                if was_playing:
                    self.player.play()

        self.player.mediaStatusChanged.connect(_restore)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        if not was_playing:
            # Paint the restored frame even while paused.
            self.player.pause()

    def unload(self) -> None:
        self.video_widget.discard_unpainted_frames()
        self._reset_shuttle()
        self._reset_initial_stopped_seek_wake()
        self._last_presented_source_position_ms = None
        self._pending_step_frame = None
        self._last_time_text = ""
        self._last_presented_selection_epoch = None
        self._next_presented_frame_is_hard_seek = True
        self.player.stop()
        self.player.setSource(QUrl())
        # Never leave background jobs frozen when playback goes away.
        self._transport_busy = False
        background_service.resume_all()

    # ---------- transport ----------

    # ---------- clip playback (review mode) ----------

    def set_clip_range(self, start_ms: int, end_ms: int, loop: bool) -> None:
        """Update clip playback bounds without seeking or starting playback."""
        self._reset_shuttle()
        self._range_start = max(0, start_ms)
        self._range_end = max(0, end_ms)
        self._range_loop = loop
        self._range_finished = False

    def play_clip_range(self, start_ms: int, end_ms: int, loop: bool) -> None:
        """Play just this clip: seek to its in-point, stop at its out-point."""
        self.set_clip_range(start_ms, end_ms, loop)
        self._set_media_position(self._range_start, hard_seek=True)
        self.player.play()

    def replay_range(self) -> None:
        if self._range_end > self._range_start:
            self.play_clip_range(self._range_start, self._range_end,
                                 self._range_loop)

    def set_range_loop(self, loop: bool) -> None:
        self._range_loop = loop
        if loop:
            self._range_finished = False

    def clear_clip_range(self) -> None:
        self._range_start = self._range_end = 0
        self._range_finished = False

    def _enforce_clip_range(self, pos: int) -> None:
        """Stop (or loop) at the clip's out-point. Runs on the position hook,
        so it stays to two integer compares in the common case."""
        if self._range_finished or self._range_end <= 0 or pos < self._range_end:
            return
        # Only while actually playing - otherwise scrubbing past the out-point
        # would keep yanking the playhead back.
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        if self._range_loop:
            self._set_media_position(self._range_start, hard_seek=True)
        else:
            # Stop review once. Explicit playback can then continue, while
            # keeping the bounds available for Replay and the loop control.
            self._range_finished = True
            self.shuttle_stop()

    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.shuttle_stop()
        else:
            self._prepare_forward_playback()
            self._reset_shuttle()
            self.player.play()

    def _prepare_forward_playback(self) -> None:
        if self._shuttle_dir == -1:
            self.shuttle_stop()
        if not self._range_loop and self._range_end > 0 \
                and self.player.position() >= self._range_end:
            self._range_finished = True
        self._user_transport_supersedes_initial_frame(resume_direction=1)

    def stop(self) -> None:
        self._user_transport_supersedes_initial_frame()
        self._reset_shuttle()
        self._next_presented_frame_is_hard_seek = True
        self.player.stop()

    # ---------- JKL shuttle ----------

    def shuttle_forward(self) -> None:
        """L: play forward; repeated presses step up 1x → 2x → 4x → 8x."""
        with PerfTimer("jkl_response"):
            self._prepare_forward_playback()
            if self._shuttle_dir == 1:
                self._shuttle_idx = min(self._shuttle_idx + 1, len(SHUTTLE_SPEEDS) - 1)
            else:
                self._shuttle_dir = 1
                self._shuttle_idx = 0
            # Leaving reverse: stop its watchdog and frame-paced seek loop.
            self._reverse_watchdog.stop()
            self._connect_frame_hook(False)
            speed = SHUTTLE_SPEEDS[self._shuttle_idx]
            # Sped-up audio above 2x is just crackle - mute it for clean scanning.
            self._set_transport_mute(speed > 2.0)
            self.player.setPlaybackRate(speed)
            self.player.play()
            self.shuttle_label.setText(f"› {speed:g}x")
            self._show_shuttle_rate(speed)

    def shuttle_reverse(self) -> None:
        """J: shuttle backward; repeated presses step up 1x → 2x → 4x → 8x.

        Media backends can't decode backwards; reverse is a paced seek loop
        that respects decoder speed (see __init__ comment).
        """
        with PerfTimer("jkl_response"):
            if self._shuttle_dir == 1 or self.player.playbackState() == \
                    QMediaPlayer.PlaybackState.PlayingState:
                self.shuttle_stop()
            self._user_transport_supersedes_initial_frame(resume_direction=-1)
            if self._shuttle_dir == -1:
                self._shuttle_idx = min(self._shuttle_idx + 1, len(SHUTTLE_SPEEDS) - 1)
            else:
                self._shuttle_dir = -1
                self._shuttle_idx = 0
            self.player.pause()
            self._set_transport_mute(True)  # seek-loop audio is only clicks/pops
            self._connect_frame_hook(True)
            self._reverse_clock.restart()
            self._reverse_watchdog.start(REVERSE_WATCHDOG_MS)
            speed = SHUTTLE_SPEEDS[self._shuttle_idx]
            self.shuttle_label.setText(f"‹ {speed:g}x")
            self._show_shuttle_rate(-speed)
            self._reverse_step()

    def shuttle_stop(self) -> None:
        """K: stop shuttling and pause."""
        with PerfTimer("jkl_response"):
            self._user_transport_supersedes_initial_frame()
            shown = self.displayed_position_ms()
            held = shown is not None and self.video_widget.hold_current_frame()
            self._reset_shuttle()
            self.player.pause()
            if held and shown is not None:
                # Seek inside the frame actually painted, not to a pending
                # reverse request or a leading fast-forward backend position.
                self._set_media_position(
                    shown + 1, hard_seek=False, release_initial_hold=False)

    def _shuttle_active(self) -> bool:
        return self._shuttle_dir != 0

    def _toggle_range_loop(self, checked: bool) -> None:
        self.set_range_loop(bool(checked))

    def _show_shuttle_rate(self, rate: float) -> None:
        """Mirror authoritative J/K/L telemetry onto the attached deck."""
        deck = self._control_center
        if deck is not None:
            deck.set_shuttle_rate(rate)

    def _reset_shuttle(self) -> None:
        self._shuttle_dir = 0
        self._shuttle_idx = 0
        self._show_shuttle_rate(0.0)
        self._reverse_watchdog.stop()
        self._connect_frame_hook(False)
        self._set_transport_mute(False)
        self.player.setPlaybackRate(self.speed_combo.currentData() or 1.0)
        self.shuttle_label.setText("")

    def _mark_transport_busy(self) -> None:
        """Any playback/seek activity pauses background work immediately.

        Called from the position hook, so it runs per frame - the flag keeps
        the hot path down to a bool check and a timer restart.
        """
        if not self._transport_busy:
            self._transport_busy = True
            background_service.set_transport_active(True)
        self._transport_idle_timer.start()

    def _transport_went_idle(self) -> None:
        self._transport_busy = False
        background_service.set_transport_active(False)

    def _set_transport_mute(self, muted: bool) -> None:
        if muted != self._transport_muted:
            self._transport_muted = muted
            self.audio.setMuted(self._user_muted or muted)

    def _connect_frame_hook(self, connect: bool) -> None:
        """Listen to frame delivery only while reverse shuttling (zero cost
        during normal playback)."""
        sink = self.video_widget.videoSink()
        if sink is None or connect == self._frame_hook_connected:
            return
        if connect:
            sink.videoFrameChanged.connect(self._reverse_frame_arrived)
        else:
            try:
                sink.videoFrameChanged.disconnect(self._reverse_frame_arrived)
            except (RuntimeError, TypeError):
                pass
        self._frame_hook_connected = connect

    def _reverse_frame_arrived(self, _frame) -> None:
        """Decode completed; request another frame only when its time is due."""
        if self._shuttle_dir == -1:
            self._reverse_step()

    def _reverse_step(self) -> None:
        if self._shuttle_dir != -1:
            return
        elapsed = self._reverse_clock.elapsed()
        speed = SHUTTLE_SPEEDS[self._shuttle_idx]
        remaining = self.frame_duration_ms / speed - elapsed
        if remaining > 0:
            # Retain the elapsed budget until a frame is due; restarting it on
            # an early callback would make rate depend on decoder throughput.
            self._reverse_watchdog.start(math.ceil(remaining))
            return
        self._reverse_clock.restart()
        self._reverse_watchdog.start(REVERSE_WATCHDOG_MS)
        # Clamp the elapsed window so a hitch can't produce a huge jump.
        elapsed = min(elapsed, REVERSE_WATCHDOG_MS)
        step = max(round(self.frame_duration_ms), round(speed * elapsed))
        new_pos = self.player.position() - step
        if new_pos <= 0:
            self._set_media_position(0, hard_seek=False)
            self._reset_shuttle()
        else:
            # Reverse JKL is one continuous displayed trajectory. Its backend
            # uses seeks only because QMediaPlayer cannot decode in reverse.
            self._set_media_position(new_pos, hard_seek=False)

    # ---------- seeking / stepping ----------

    def jump_backward(self) -> None:
        self._reset_shuttle()
        self.seek_relative(-int(self.settings.jump_backward_seconds * 1000))

    def jump_forward(self) -> None:
        self._reset_shuttle()
        self.seek_relative(int(self.settings.jump_forward_seconds * 1000))

    def step_or_jump(self, direction: int) -> None:
        """Arrow-key behavior: exact frame step when paused, jump when playing."""
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState \
                and not self._shuttle_active():
            if direction < 0:
                self.jump_backward()
            else:
                self.jump_forward()
        else:
            self._step_frames(direction)

    def frame_step_backward(self) -> None:
        self._step_frames(-1)

    def frame_step_forward(self) -> None:
        self._step_frames(1)

    def _step_frames(self, frames: int) -> None:
        if not frames:
            return
        self._reset_shuttle()
        self.player.pause()
        frame_ms = self.frame_duration_ms
        base = self._pending_step_frame
        shown = self.displayed_position_ms()
        if base is None:
            # The backend position can lag the picture after K. An explicit
            # seek still in flight owns the anchor until its frame is shown.
            position = shown
            if position is None or self._next_presented_frame_is_hard_seek:
                position = self.player.position()
            base = round(position / frame_ms)
        last = max(0, math.ceil(self.player.duration() / frame_ms) - 1)
        target_frame = max(0, min(last, base + frames))
        if target_frame == base and self._pending_step_frame is None \
                and shown is not None \
                and round(shown / frame_ms) == target_frame:
            return
        # Seek strictly inside the desired frame: even an exact integer
        # boundary can decode the previous frame. Count pending frames instead
        # of rounding each delta, including when decode is coalesced.
        target_ms = min(self.player.duration(), int(target_frame * frame_ms) + 1)
        self._set_media_position(
            target_ms, hard_seek=True, step_frame=target_frame)

    def _scrub_started(self) -> None:
        """Pause while dragging the slider so every seek paints a frame."""
        self._user_transport_supersedes_initial_frame()
        self._reset_shuttle()
        self._was_playing_before_scrub = \
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.player.pause()
        self._last_scrub_seek_ms = None
        self._scrub_preview_clock.invalidate()

    def _scrub_moved(self, position_ms: int) -> None:
        """Preview a drag at a bounded rate while the visual stays immediate."""
        if self._scrub_preview_clock.isValid() and \
                self._scrub_preview_clock.elapsed() < \
                SCRUB_PREVIEW_INTERVAL_MS:
            return
        self._set_media_position(position_ms, hard_seek=True)
        self._last_scrub_seek_ms = position_ms
        self._scrub_preview_clock.restart()

    def _scrub_finished(self) -> None:
        # The final pointer position may have been skipped by the preview
        # throttle. Seek it exactly once before optionally resuming playback.
        final_ms = self.slider.value()
        if final_ms != self._last_scrub_seek_ms:
            self._set_media_position(final_ms, hard_seek=True)
        self._last_scrub_seek_ms = final_ms
        self._scrub_preview_clock.invalidate()
        if self._was_playing_before_scrub:
            self.player.play()

    def _wheel_seek(self, notches: int) -> None:
        self._reset_shuttle()
        amount = self.settings.jump_forward_seconds if notches > 0 \
            else self.settings.jump_backward_seconds
        self.seek_relative(int(notches * amount * 1000))

    def _wheel_frame(self, notches: int) -> None:
        self._step_frames(notches)

    def _jog_frames_requested(self, frames: int) -> None:
        """Route jog input through the same frame-step authority as arrows."""
        self._step_frames(frames)

    def _wheel_zoom(self, notches: int, anchor_ms: int) -> None:
        """Zoom the source viewport around the time beneath the pointer."""
        if notches == 0:
            return
        step = 1 if notches > 0 else -1
        level = max(
            self.timeline_zoom_slider.minimum(),
            min(
                self.timeline_zoom_slider.maximum(),
                self.timeline_zoom_slider.value() + step,
            ),
        )
        if level == self.timeline_zoom_slider.value():
            return
        self._set_timeline_zoom_level(level, anchor_ms)

    def seek_relative(self, delta_ms: int) -> None:
        target = max(0, min(self.player.duration(), self.player.position() + delta_ms))
        self._set_media_position(target, hard_seek=True)

    def seek_to(self, ms: int) -> None:
        self._set_media_position(max(0, ms), hard_seek=True)

    def _set_media_position(
            self, ms: int, *, hard_seek: bool,
            release_initial_hold: bool = True,
            step_frame: int | None = None) -> None:
        """Issue one backend position request with presentation provenance.

        Every production ``setPosition`` call routes through here. Reverse JKL
        explicitly passes ``hard_seek=False`` because those decoder-paced
        requests form one continuous trajectory; all user seek/step/scrub and
        clip-loop requests pass ``True``. The flag is consumed only when a new
        valid-PTS image is actually painted.
        """
        if not isinstance(hard_seek, bool):
            raise TypeError("hard_seek must be a bool")
        target = int(ms)
        self._pending_step_frame = step_frame
        if release_initial_hold and (
                self._initial_stopped_seek_waking
                or self.video_widget.frame_hold_active()):
            # This wrapper is the sole backend position path, so reaching it
            # after the internal wake has begun is an actual user/transport
            # seek.  It owns the next image and must release the initial hold.
            self._reset_initial_stopped_seek_wake()
        if hard_seek:
            self.video_widget.clear_resume_guard()
            if self._last_presented_source_position_ms == target:
                # The requested image is already the one on screen. Treat it
                # as settled for the current selection instead of waiting for
                # a decoder callback that some backends never send for a
                # same-position seek.
                self._next_presented_frame_is_hard_seek = False
                self._last_presented_selection_epoch = self._selection_epoch
            else:
                self._next_presented_frame_is_hard_seek = True
        self.player.setPosition(target)
        if hard_seek and self._awaiting_initial_frame:
            # Keep only the newest selection made while the source is loading.
            # Once media is ready the short timer gives backends that can
            # decode while stopped a chance to answer without any wake.
            self._initial_stopped_seek_target_ms = target
            presented = self._last_presented_source_position_ms
            tolerance = self._initial_frame_tolerance_ms()
            if presented is not None \
                    and target <= presented <= target + tolerance:
                self._reset_initial_stopped_seek_wake()
            else:
                self._initial_stopped_seek_wake_timer.start()

    def _initial_stopped_seek_media_status_changed(self, status) -> None:
        """Retry the one-shot first-frame wake once asynchronous load is ready."""
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._reset_initial_stopped_seek_wake()
            return
        if status in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia) \
                and self._awaiting_initial_frame \
                and not self._initial_stopped_seek_waking \
                and self._initial_stopped_seek_target_ms is not None:
            self._initial_stopped_seek_wake_timer.start()

    def _wake_initial_stopped_seek(self) -> None:
        """Prime the decoder until the requested native frame is secured."""
        target = self._initial_stopped_seek_target_ms
        if not self._awaiting_initial_frame or target is None:
            return
        if self.player.playbackState() == \
                QMediaPlayer.PlaybackState.PlayingState:
            # A real user playback command will deliver the first frame. Do
            # not turn that command back into a pause behind their back.
            self._reset_initial_stopped_seek_wake()
            return
        if self.player.mediaStatus() not in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia):
            return

        # A seek issued while LoadingMedia can update QMediaPlayer.position()
        # without becoming the decoder's start point.  Re-issue the same
        # request now that media is loaded, still through the sole position
        # wrapper, and do it before marking this as internal playback.
        self._set_media_position(target, hard_seek=True)
        # The accepted frame is chosen on native PTS, not QMediaPlayer's
        # leading position property.  StepVideoWidget freezes it before the
        # Windows backend can drain later queued frames after pause().
        self._initial_stopped_seek_wake_timer.stop()
        self._initial_stopped_seek_waking = True
        self.video_widget.arm_frame_hold(
            target, self._initial_frame_tolerance_ms())
        self.audio.setMuted(True)
        try:
            self._initial_stopped_seek_hold_timer.start()
            self.player.play()
        except Exception:
            self._reset_initial_stopped_seek_wake()
            raise

    def _initial_stopped_seek_frame_held(self, position_ms: int) -> None:
        """Settle the internal wake without releasing its secured image."""
        target = self._initial_stopped_seek_target_ms
        if not self._initial_stopped_seek_waking or target is None:
            return
        tolerance = self._initial_frame_tolerance_ms()
        if not target <= int(position_ms) <= target + tolerance:
            return

        self._initial_stopped_seek_wake_timer.stop()
        self._initial_stopped_seek_hold_timer.stop()
        self._awaiting_initial_frame = False
        self._initial_stopped_seek_waking = False
        self._initial_stopped_seek_target_ms = None
        self.player.pause()
        # pause() can still let Windows advance the position property while it
        # drains decoded frames.  Reassert the exact requested position through
        # the authoritative wrapper while keeping the secured image frozen;
        # any floor frame decoded by this correction is deliberately ignored.
        self._set_media_position(
            target, hard_seek=True, release_initial_hold=False)
        self.audio.setMuted(self._user_muted or self._transport_muted)

    def _initial_stopped_seek_frame_missed(self, _position_ms: int) -> None:
        """Stand down safely if the backend skips beyond one source frame."""
        if not self._initial_stopped_seek_waking:
            return
        self.player.pause()
        self._reset_initial_stopped_seek_wake()

    def _initial_stopped_seek_hold_timed_out(self) -> None:
        """Never leave an unsuccessful internal decoder wake running/muted."""
        if not self._initial_stopped_seek_waking:
            return
        self.player.pause()
        self._reset_initial_stopped_seek_wake()

    def _initial_frame_tolerance_ms(self) -> int:
        return max(1, math.ceil(self.frame_duration_ms))

    def _user_transport_supersedes_initial_frame(self, resume_direction: int = 0) -> None:
        """Release pending/held initial state before a real user command."""
        self._pending_step_frame = None
        if self._awaiting_initial_frame \
                or self._initial_stopped_seek_waking \
                or self.video_widget.frame_hold_active():
            self._reset_initial_stopped_seek_wake(resume_direction)
        elif not resume_direction:
            self.video_widget.clear_resume_guard()

    def _reset_initial_stopped_seek_wake(self, resume_direction: int = 0) -> None:
        self._initial_stopped_seek_wake_timer.stop()
        self._initial_stopped_seek_hold_timer.stop()
        self._awaiting_initial_frame = False
        self._initial_stopped_seek_waking = False
        self._initial_stopped_seek_target_ms = None
        self.video_widget.release_frame_hold(resume_direction)
        self.audio.setMuted(self._user_muted or self._transport_muted)

    def _source_frame_presented(self, source_position_ms: int) -> None:
        """Publish the PTS belonging to the image StepVideoWidget painted."""
        if source_position_ms < 0:
            # QVideoFrame may lack timing metadata for a malformed/backend-only
            # surface. QMediaPlayer.position() is not a safe substitute: it can
            # already name a later requested frame, especially in reverse.
            return
        position = int(source_position_ms)
        initial_target = self._initial_stopped_seek_target_ms
        if self._awaiting_initial_frame and initial_target is not None \
                and initial_target <= position <= \
                initial_target + self._initial_frame_tolerance_ms():
            self._reset_initial_stopped_seek_wake()
        hard_seek = self._next_presented_frame_is_hard_seek
        self._next_presented_frame_is_hard_seek = False
        self._last_presented_source_position_ms = position
        if self._pending_step_frame == round(position / self.frame_duration_ms):
            self._pending_step_frame = None
        self._update_position_readout(position)
        self._last_presented_selection_epoch = self._selection_epoch
        self.source_frame_presented.emit(position, hard_seek)

    def displayed_position_ms(self) -> int | None:
        """PTS of the last valid source frame actually painted, if any."""
        return self._last_presented_source_position_ms

    def recording_anchor_ms(self) -> int | None:
        """Return a selected-clip PTS only after its visual is settled."""
        position = self._last_presented_source_position_ms
        if position is None or self._next_presented_frame_is_hard_seek:
            return None
        if self._last_presented_selection_epoch != self._selection_epoch:
            return None
        if self._selected_clip_range_ms is None:
            return None
        start_ms, end_ms = self._selected_clip_range_ms
        if not start_ms <= position < end_ms:
            return None
        return position

    def position_ms(self) -> int:
        return self.player.position()

    # ---------- in/out ----------

    def set_in_point(self) -> None:
        self.in_point_ms = self.player.position()
        if self.out_point_ms is not None and self.out_point_ms <= self.in_point_ms:
            self.out_point_ms = None
        self._update_marks_label()
        self.in_point_set.emit(self.in_point_ms)

    def set_out_point(self) -> None:
        self.out_point_ms = self.player.position()
        if self.in_point_ms is not None and self.out_point_ms <= self.in_point_ms:
            self.in_point_ms = None
        self._update_marks_label()
        self.out_point_set.emit(self.out_point_ms)

    def clear_marks(self) -> None:
        self.in_point_ms = None
        self.out_point_ms = None
        self._update_marks_label()

    def request_add_clip(self) -> None:
        if self.in_point_ms is None or self.out_point_ms is None:
            self.error_label.setText(
                "Set both an in-point (I) and an out-point (O) first, then press A.")
            self.error_label.show()
            return
        self.error_label.hide()
        self.add_clip_requested.emit(self.in_point_ms, self.out_point_ms,
                                     self.name_edit.text().strip())

    def clear_name(self) -> None:
        self.name_edit.clear()

    def focus_name(self) -> None:
        """Focus the name field, or keep the keyboard on playback without it.

        With the field out of the transport row, focusing it would send the
        next keystrokes to a hidden widget - or worse, leak them into the
        single-key shortcuts. Naming now happens in the inspector.
        """
        if not self.name_edit.isVisible():
            self.focus_transport()
            return
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def focus_transport(self) -> None:
        """Hand the keyboard back to playback (so J/K/L/Space work again)."""
        self.video_widget.setFocus()

    def _update_filename_preview(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.filename_preview.clear()
            self.filename_preview.hide()
            return
        base = filename_service.sanitize_filename_base(
            name, self.settings.separator_style)
        self.filename_preview.setText(f"Output:  {base}.mp4")
        self.filename_preview.show()

    def set_clip_blocks(self, blocks: list[TimelineBlock | tuple]) -> None:
        """Clip ranges and semantic labels to draw on the timeline."""
        self.slider.set_blocks(blocks)
        self._sync_selected_clip_range()
        if self.timeline_overview is not None:
            self.timeline_overview.set_blocks(list(self.slider.blocks()))
        self._sync_timeline_viewport_controls()

    def set_attribute_clips(self, clips: list) -> None:
        """Feed the grid the clips themselves.

        Blocks carry only what the timeline paints; the grid reads
        details, notes and lineage, so it needs the real objects.
        """
        self.attribute_grid.set_clips(list(clips))

    def set_coverage_segments(
            self, segments: list[TimelineCoverage | dict | tuple]) -> None:
        """Show the latest persisted Coverage Guardian ledger."""
        self.slider.set_coverage_segments(segments)

    def set_ignored_fragment_summary(
            self, count: int, duration_ms: int) -> None:
        count = max(0, int(count))
        duration_ms = max(0, int(duration_ms))
        self.timeline_fragments_button.setText(
            f"Fragments {count}" if count else "Fragments")
        self.timeline_fragments_button.setToolTip(
            f"Show {count} preserved unclassified source fragment"
            f"{'s' if count != 1 else ''} "
            f"({duration_ms / 1000:.1f}s). They appear automatically "
            "when the timeline is zoomed in.")
        self.timeline_fragments_button.setAccessibleName(
            f"Show {count} ignored timeline fragments")
        self.timeline_fragments_button.setVisible(bool(count))

    def _timeline_fragments_toggled(self, visible: bool) -> None:
        self.slider.set_show_ignored_fragments(visible)
        if self.settings.timeline_show_ignored_fragments != bool(visible):
            self.settings.timeline_show_ignored_fragments = bool(visible)
            self.settings.save()

    def set_selected_clip_id(self, clip_id: str | None) -> None:
        """Highlight one timeline block without rebuilding its clip data."""
        changed = clip_id != self._selected_timeline_clip_id
        if changed:
            self._selection_epoch += 1
        self._selected_timeline_clip_id = clip_id
        self.slider.set_selected_clip_id(clip_id)
        self._sync_selected_clip_range()
        if self.timeline_overview is not None:
            self.timeline_overview.set_selected_clip_id(clip_id)
        self.attribute_grid.set_selected_clip_id(clip_id)
        if self.timeline_variant == DUAL and changed and clip_id:
            selected = self.slider.selected_block_range()
            if selected is not None:
                visible_start, visible_end = self.slider.visible_range()
                source_duration = self.slider.viewport.source_duration_ms
                visible_duration = self.slider.viewport.visible_duration_ms
                selected_is_outside = (
                    selected[0] < visible_start or selected[1] > visible_end)
                selected_duration = selected[1] - selected[0]
                if visible_duration >= source_duration \
                        or selected_duration >= visible_duration:
                    self.slider.fit_range(
                        *selected,
                        context_before_ms=DUAL_DETAIL_CONTEXT_MS,
                        context_after_ms=DUAL_DETAIL_CONTEXT_MS,
                    )
                elif selected_is_outside:
                    # Previous/Next should move the existing editing window,
                    # not silently reset the zoom the analyst chose. Center
                    # the next play in the current-width source viewport.
                    selected_midpoint = (selected[0] + selected[1]) // 2
                    self.slider.set_visible_start(
                        selected_midpoint - visible_duration // 2)
        self._sync_timeline_viewport_controls()

    def _sync_selected_clip_range(self) -> None:
        """Pin the recording boundary to the selected timeline interval."""
        selected = self.slider.selected_block_range() \
            if self._selected_timeline_clip_id else None
        self._selected_clip_range_ms = (
            (int(selected[0]), int(selected[1]))
            if selected is not None else None
        )

    def set_predicted_snap_state(
            self, state: str, prediction: dict | None = None) -> None:
        """Keep the timeline action discoverable and honest about its state."""
        prediction = prediction or {}
        if state == "finding":
            text = "Finding..."
            tooltip = "Analyzing the first camera angle for its snap"
            enabled = False
        elif state == "confirmed":
            text = "Go to Snap"
            tooltip = (
                f"Confirmed snap: {format_ms(int(prediction['source_ms']), show_millis=True)}"
                " | G jumps here"
            )
            enabled = True
        elif state == "ready":
            source_ms = int(prediction.get("source_ms", 0))
            confidence = float(prediction.get("confidence", 0.0))
            eligible = bool(prediction.get("eligible", False))
            text = "Go to Snap"
            quality = "Predicted snap" if eligible else \
                "Low-confidence snap estimate"
            tooltip = (
                f"{quality}: {format_ms(source_ms, show_millis=True)} "
                f"({confidence:.0%}) | G jumps here"
            )
            enabled = True
        elif state == "missing":
            text = "Find Snap"
            tooltip = (
                "Analyze the selected play's first camera angle and jump to "
                "the predicted snap (G)"
            )
            enabled = True
        else:
            text = "Snap"
            tooltip = "Select one play to use predicted snap navigation"
            enabled = False
        self.predicted_snap_button.setText(text)
        self.predicted_snap_button.setToolTip(tooltip)
        self.predicted_snap_button.setAccessibleName(tooltip)
        self.predicted_snap_button.setEnabled(enabled)

    def set_timeline_key(
            self, mode: str,
            entries: tuple[tuple[str, str, object], ...]) -> None:
        """Replace the popup contents with the active project's color key."""
        # clear() and nothing else. Detaching the actions and deferring
        # their deletion instead was measured at 0 clean runs in 10: the
        # action outlives the menu's own cleanup and its default widget
        # gets destroyed twice. Synchronous is correct here.
        self.timeline_key_menu.clear()
        self.timeline_legend_items = {}
        mode_label = TIMELINE_COLOR_MODE_LABELS.get(mode, "Play Type")

        header = QLabel(f"COLOR BY {mode_label.upper()}")
        header.setProperty("role", "legendTitle")
        header.setContentsMargins(10, 6, 10, 4)
        header_action = QWidgetAction(self.timeline_key_menu)
        header_action.setDefaultWidget(header)
        self.timeline_key_menu.addAction(header_action)

        # Mode switching lives here now instead of in a permanent combo box.
        # These delegate to the combo, so the signal path is unchanged.
        for value, text in TIMELINE_COLOR_MODES:
            action = self.timeline_key_menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(value == mode)
            action.triggered.connect(
                lambda _checked=False, target=value:
                self._select_timeline_color_mode(target))
        self.timeline_key_menu.addSeparator()

        for key, text, colour in entries:
            # Parented to the menu on purpose. Dropping the parent so
            # setDefaultWidget is sole owner reads correct in C++ and is
            # wrong in PySide: with no parent and no Python reference the
            # wrapper is collected before ownership transfers, and the
            # action then frees it again. Measured, twice.
            item = QWidget(self.timeline_key_menu)
            item.setProperty("role", "legendItem")
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(10, 3, 12, 3)
            item_layout.setSpacing(7)
            swatch = QFrame(item)
            swatch.setObjectName(
                f"TimelineLegendSwatch_{key or 'unlabelled'}")
            swatch.setAccessibleName(f"{text} timeline color")
            swatch.setFixedSize(10, 10)
            swatch.setStyleSheet(
                f"background-color: {colour.name()}; "
                "border: 1px solid rgba(255, 255, 255, 45); "
                "border-radius: 2px;")
            label = QLabel(text, item)
            label.setProperty("role", "legendItem")
            item.setToolTip(f"{text} clips on the timeline")
            item_layout.addWidget(swatch)
            item_layout.addWidget(label, 1)
            action = QWidgetAction(self.timeline_key_menu)
            action.setDefaultWidget(item)
            self.timeline_key_menu.addAction(action)
            self.timeline_legend_items[key] = label

        # Shape cues live below the colours and deliberately stay out of
        # timeline_legend_items: they are not part of the colour key, and the
        # timeline must never rely on colour alone to say these two things.
        self.timeline_key_menu.addSeparator()
        # Words, not glyphs: block-drawing characters are not in the bundled
        # display font and render as empty boxes.
        shape_cues = [
            "Bright left cap  =  detected play",
            "Thin amber notch  =  plays overlap here",
        ]
        if self.timeline_variant == "compact":
            shape_cues.extend([
                "Bright amber gap  =  check first possible miss",
                "Amber source gap  =  possible missed footage",
                "Muted amber gap  =  low signal, still review",
                "Dark source gap  =  verified black separator",
            ])
        for text in shape_cues:
            shape = QLabel(text, self.timeline_key_menu)
            shape.setProperty("role", "legendItem")
            shape.setContentsMargins(10, 3, 12, 3)
            shape_action = QWidgetAction(self.timeline_key_menu)
            shape_action.setDefaultWidget(shape)
            self.timeline_key_menu.addAction(shape_action)

        self.timeline_key_button.setAccessibleName(
            f"Timeline color key for {mode_label}")
        self.timeline_key_button.setToolTip(
            f"Show the {mode_label.lower()} color key")

    def _select_timeline_color_mode(self, mode: str) -> None:
        """Route a Key-popup choice through the existing combo signal path."""
        index = self.timeline_color_combo.findData(mode)
        if index >= 0:
            self.timeline_color_combo.setCurrentIndex(index)

    def _timeline_color_mode_selected(self) -> None:
        mode = self.timeline_color_combo.currentData()
        if mode:
            self.timeline_color_mode_changed.emit(str(mode))

    def set_period_markers(self, markers_ms: list[int]) -> None:
        """Project-level Q2/Q3/Q4/overtime boundaries for the timeline rail."""
        self.slider.set_period_markers(markers_ms)

    def _timeline_zoom_changed(self, level: int) -> None:
        self._set_timeline_zoom_level(level, self.position_ms())

    def _set_timeline_zoom_level(
            self, level: int, anchor_ms: int) -> None:
        """Apply one discrete zoom level around a real source timestamp."""
        level = max(
            self.timeline_zoom_slider.minimum(),
            min(self.timeline_zoom_slider.maximum(), int(level)),
        )
        self.timeline_zoom_slider.blockSignals(True)
        self.timeline_zoom_slider.setValue(level)
        self.timeline_zoom_slider.blockSignals(False)
        factor = TIMELINE_ZOOM_FACTORS[max(
            0, min(len(TIMELINE_ZOOM_FACTORS) - 1, level))]
        self.slider.set_zoom_factor(factor, anchor_ms)
        self._sync_timeline_viewport_controls()

    def _timeline_visible_range_changed(
            self, _start_ms: int, _end_ms: int) -> None:
        self._sync_timeline_viewport_controls()

    def _timeline_scroll_changed(self, start_ms: int) -> None:
        self.slider.set_visible_start(start_ms)
        self._sync_timeline_viewport_controls()

    def _overview_visible_start_requested(self, start_ms: int) -> None:
        self.slider.set_visible_start(start_ms)
        self._sync_timeline_viewport_controls()

    def _timeline_follow_changed(self, enabled: bool) -> None:
        self.slider.set_follow_playhead(enabled)
        self._sync_timeline_viewport_controls()

    def fit_timeline_play(self) -> None:
        selected = self.slider.selected_block_range()
        if selected is None:
            self._sync_timeline_viewport_controls()
            return
        self.slider.fit_range(
            *selected,
            context_before_ms=FIT_PLAY_CONTEXT_MS,
            context_after_ms=FIT_PLAY_CONTEXT_MS,
        )
        self._sync_timeline_viewport_controls()

    def _coverage_activated(
            self, start_ms: int, end_ms: int, kind: str) -> None:
        """Open an amber source gap into an inspectable editing window."""
        if kind != "possible_missed" or end_ms <= start_ms:
            return
        duration = end_ms - start_ms
        context = max(5_000, min(15_000, duration * 2))
        self.focus_source_range(
            start_ms, end_ms, context_ms=context, seek_center=True)

    def focus_source_range(
            self, start_ms: int, end_ms: int, *,
            context_ms: int = FIT_PLAY_CONTEXT_MS,
            seek_center: bool = True) -> None:
        """Expose an arbitrary source interval without inventing a clip."""
        if end_ms <= start_ms:
            return
        self.slider.fit_range(
            start_ms,
            end_ms,
            context_before_ms=max(0, int(context_ms)),
            context_after_ms=max(0, int(context_ms)),
        )
        if seek_center:
            self.seek_to(start_ms + (end_ms - start_ms) // 2)
        self._sync_timeline_viewport_controls()

    def pan_timeline_left(self) -> None:
        self.slider.pan_viewport_fraction(-TIMELINE_PAN_FRACTION)
        self._sync_timeline_viewport_controls()

    def pan_timeline_right(self) -> None:
        self.slider.pan_viewport_fraction(TIMELINE_PAN_FRACTION)
        self._sync_timeline_viewport_controls()

    def _sync_timeline_viewport_controls(self) -> None:
        viewport = self.slider.viewport
        source_start, source_end = viewport.source_range()
        visible_start, visible_end = viewport.visible_range()
        source_duration = viewport.source_duration_ms
        visible_duration = viewport.visible_duration_ms
        zoom = viewport.zoom_factor
        closest_level = min(
            range(len(TIMELINE_ZOOM_FACTORS)),
            key=lambda index: abs(TIMELINE_ZOOM_FACTORS[index] - zoom),
        )
        self.timeline_zoom_slider.blockSignals(True)
        self.timeline_zoom_slider.setValue(closest_level)
        self.timeline_zoom_slider.blockSignals(False)

        if abs(zoom - round(zoom)) < 0.05:
            zoom_text = f"{round(zoom)}×"
        else:
            zoom_text = f"{zoom:.1f}×"
        self.timeline_zoom_label.setText(zoom_text)
        self.timeline_range_label.setText(
            f"{format_ms(visible_start)} – {format_ms(visible_end)}")

        has_source = source_duration > 0
        full_game = not has_source or visible_duration >= source_duration
        self.timeline_zoom_out.setEnabled(has_source and not full_game)
        self.timeline_zoom_in.setEnabled(
            has_source
            and closest_level < len(TIMELINE_ZOOM_FACTORS) - 1
            and visible_duration > viewport.minimum_visible_duration_ms
        )
        self.timeline_fit_game.setEnabled(has_source and not full_game)
        self.timeline_fit_play.setEnabled(
            has_source and self.slider.selected_block_range() is not None)
        self.timeline_pan_left.setEnabled(
            has_source and visible_start > source_start)
        self.timeline_pan_right.setEnabled(
            has_source and visible_end < source_end)

        self.timeline_follow_playhead.blockSignals(True)
        self.timeline_follow_playhead.setChecked(
            viewport.follow_playhead_enabled)
        self.timeline_follow_playhead.blockSignals(False)

        max_start = max(source_start, source_end - visible_duration)
        self.timeline_viewport_scroll.blockSignals(True)
        self.timeline_viewport_scroll.setRange(source_start, max_start)
        self.timeline_viewport_scroll.setPageStep(max(1, visible_duration))
        self.timeline_viewport_scroll.setSingleStep(
            max(1, visible_duration // 10))
        self.timeline_viewport_scroll.setValue(visible_start)
        self.timeline_viewport_scroll.setEnabled(has_source and not full_game)
        self.timeline_viewport_scroll.blockSignals(False)
        self.timeline_viewport_scroll.setVisible(has_source and not full_game)
        if self.timeline_overview is not None:
            self.timeline_overview.set_visible_range(
                visible_start, visible_end)

    # ---------- Tag Map and persistent Telestration rail ----------

    TOOLS = (("Arrow", "arrow"), ("Line", "line"), ("Circle", "circle"))

    def _build_telestration_rail(self) -> QWidget:
        """Build the only drawing control surface, beside the film."""
        rail = QFrame(self)
        rail.setObjectName("TelestrationRail")
        rail.setProperty("telestrationRail", "true")
        rail.setFrameShape(QFrame.Shape.NoFrame)
        rail.setAccessibleName("Telestration tools")
        rail.setAccessibleDescription("No marks")
        rail.setFixedWidth(30)
        rail.setStyleSheet(TELESTRATION_RAIL_QSS)
        rail.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        column = QVBoxLayout(rail)
        # The stylesheet frame contributes one physical pixel. These layout
        # margins keep the visible insets at L/R=3 and T/B=4.
        column.setContentsMargins(2, 3, 2, 3)
        column.setSpacing(0)

        tool_column = QVBoxLayout()
        tool_column.setContentsMargins(0, 0, 0, 0)
        tool_column.setSpacing(3)

        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)

        select_button = QToolButton(rail)
        select_button.setObjectName("TelestrationSelect")
        select_button.setText("")
        select_button.setIcon(
            telestration_state_icon("telestration_select", 16))
        select_button.setIconSize(QSize(16, 16))
        select_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly)
        select_button.setProperty("deckKey", "true")
        select_button.setProperty("deckTool", "true")
        select_button.setCheckable(True)
        select_button.setChecked(True)
        select_button.setFixedSize(24, 24)
        select_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        select_button.setToolTip("Select film playback; stop drawing")
        select_button.setAccessibleName("Select film; stop drawing")
        self.tool_group.addButton(select_button, 0)
        tool_column.addWidget(select_button)

        self.tool_group.idClicked.connect(self._tool_picked)

        # One key for every shape. A 30px rail cannot hold 27 buttons,
        # and the three it used to hold were an arbitrary subset.
        self.shape_library_button = QToolButton(rail)
        self.shape_library_button.setObjectName("TelestrationLibrary")
        self.shape_library_button.setText("")
        self.shape_library_button.setIcon(
            shape_state_icon(DEFAULT_SHAPE_TOOL, 16))
        self.shape_library_button.setIconSize(QSize(16, 16))
        self.shape_library_button.setProperty("deckKey", "true")
        self.shape_library_button.setProperty("deckTool", "true")
        self.shape_library_button.setCheckable(True)
        self.shape_library_button.setFixedSize(24, 24)
        self.shape_library_button.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus)
        self.shape_library_button.setToolTip("All shapes and inks")
        self.shape_library_button.setAccessibleName(
            "Shape and ink library")
        self.shape_library_button.clicked.connect(
            self._open_shape_library)
        self.tool_group.addButton(self.shape_library_button, 1)
        tool_column.addWidget(self.shape_library_button)
        column.addLayout(tool_column)

        column.addSpacing(6)
        # One swatch showing the ink in use, opening the palette.
        # Three fixed swatches cannot represent twelve inks.
        self.ink_group = QButtonGroup(self)
        self.ink_group.setExclusive(True)
        ink_column = QVBoxLayout()
        ink_column.setContentsMargins(0, 0, 0, 0)
        ink_column.setSpacing(3)
        self.ink_button = QToolButton(rail)
        self.ink_button.setObjectName("TelestrationInk")
        self.ink_button.setText("")
        self.ink_button.setProperty("deckSwatch", "true")
        self.ink_button.ensurePolished()
        self.ink_button.setFixedSize(18, 18)
        self.ink_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ink_button.clicked.connect(self._open_ink_palette)
        ink_column.addWidget(
            self.ink_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self._sync_ink_button(DEFAULT_INK)
        column.addLayout(ink_column)

        column.addStretch(1)
        utility_column = QVBoxLayout()
        utility_column.setContentsMargins(0, 0, 0, 0)
        utility_column.setSpacing(3)
        for object_name, glyph, tip, slot in (
                ("TelestrationUndo", "undo",
                 "Undo the last mark", self._undo_mark),
                ("TelestrationClear", "telestration_trash",
                 "Clear every mark", self._clear_marks)):
            button = QToolButton(rail)
            button.setObjectName(object_name)
            button.setText("")
            button.setIcon(deck_icons.state_icon(glyph, 16))
            button.setIconSize(QSize(16, 16))
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setProperty("deckKey", "true")
            button.setFixedSize(24, 24)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.clicked.connect(slot)
            utility_column.addWidget(button)
        column.addLayout(utility_column)

        self.mark_count_label = QLabel("No marks", rail)
        self.mark_count_label.setObjectName("TelestrationMarkCount")
        self.mark_count_label.setProperty("role", "subtle")
        self.mark_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mark_count_label.hide()
        # Nothing is selected at construction, so the rail starts greyed and
        # selection turns it on via set_telestration_enabled.
        rail.setEnabled(False)
        rail.setToolTip("Select a play to draw on it")
        return rail

    def _sync_ink_button(self, name: str) -> None:
        """Show the ink actually in use on the rail's one swatch."""
        button = getattr(self, "ink_button", None)
        if button is None:
            return
        colour = INK.get(name, INK[DEFAULT_INK])
        button.setIcon(deck_icons.load("circle", colour, 10))
        button.setIconSize(QSize(10, 10))
        button.setProperty("inkColor", name)
        button.setToolTip(f"{name.title()} ink - click for the palette")
        button.setAccessibleName(f"{name} ink")

    def _tool_picked(self, index: int) -> None:
        """Select (0) disarms drawing; the library key (1) opens it."""
        if index == 0:
            self.video_widget.set_tool(None)
            library = getattr(self, "shape_library_button", None)
            if library is not None:
                library.setChecked(False)

    def _ink_picked(self, index: int) -> None:
        """Retained for callers that drive ink by legacy index."""
        names = ("gold", "cyan", "red")
        if 0 <= index < len(names):
            self._ink_library_picked(names[index])
    def _open_ink_palette(self) -> None:
        """Just the colours. The swatch and the library key are different
        controls, so they must not open the same panel.
        """
        palette = getattr(self, "ink_palette", None)
        if palette is None:
            palette = ShapePicker(self, inks_only=True)
            palette.ink_chosen.connect(self._ink_library_picked)
            self.ink_palette = palette
        palette.set_ink(self.video_widget.ink())
        palette.popup_at(self.ink_button)

    def _open_shape_library(self) -> None:
        """Show the full shape and ink library beside the rail."""
        picker = getattr(self, "shape_picker", None)
        if picker is None:
            picker = ShapePicker(self)
            picker.shape_chosen.connect(self._shape_library_picked)
            picker.ink_chosen.connect(self._ink_library_picked)
            self.shape_picker = picker
        picker.set_ink(self.video_widget.ink())
        current = self.video_widget.tool()
        if current:
            picker.set_shape(current)
        picker.popup_at(self.shape_library_button)

    def _shape_library_picked(self, name: str) -> None:
        """Arm a library shape, and release the three everyday keys.

        The rail's own buttons stay exclusive among themselves, so the armed
        tool would otherwise appear to be both an arrow and a block.
        """
        group = self.tool_group
        checked = group.checkedButton()
        if checked is not None:
            group.setExclusive(False)
            checked.setChecked(False)
            group.setExclusive(True)
        self.shape_library_button.setChecked(True)
        self.shape_library_button.setIcon(shape_state_icon(name, 16))
        self.video_widget.set_tool(name)

    def _ink_library_picked(self, name: str) -> None:
        self.video_widget.set_ink(name)
        self._sync_ink_button(name)
    def _undo_mark(self) -> None:
        self.video_widget.undo_mark()
        self._refresh_mark_count()

    def _clear_marks(self) -> None:
        self.video_widget.clear_marks()
        self._refresh_mark_count()

    def _surface_marks_changed(self) -> None:
        """Relay only edits emitted by the film surface to the window owner."""
        self._refresh_mark_count()
        self.telestration_marks_changed.emit()

    def set_telestration_marks(self, marks) -> None:
        """Paint a clip's stored marks without reporting a user edit."""
        self.video_widget.set_marks(marks)
        self._refresh_mark_count()

    def telestration_marks(self):
        """Return a detached snapshot of the marks currently on the film."""
        return self.video_widget.marks()

    def _refresh_mark_count(self) -> None:
        label = getattr(self, "mark_count_label", None)
        if label is None:
            return
        count = len(self.video_widget.marks())
        text = "No marks" if not count \
            else f"{count} mark{'s' if count != 1 else ''}"
        label.setText(text)
        rail = getattr(self, "telestration_rail", None)
        if rail is not None:
            rail.setAccessibleDescription(text)

    def set_telestration_enabled(self, enabled: bool) -> None:
        """Arm or grey the drawing rail.

        A stroke belongs to exactly one play, so with nothing selected the
        workflow rejects it: _telestration_marks_edited sees no owner,
        reloads the empty surface and never dirties the session. Greying
        the rail says that up front. The surface itself stays interactive -
        an unowned drag must still draw and emit so the reject path runs;
        a surface that swallows the gesture without the signal would leave
        the stroke silently attached to whatever play is selected next.
        """
        enabled = bool(enabled)
        rail = getattr(self, "telestration_rail", None)
        if rail is None:
            return
        rail.setEnabled(enabled)
        rail.setToolTip(
            "" if enabled else "Select a play to draw on it")

    def tag_map_collapsed(self) -> bool:
        return bool(getattr(self, "_tag_map_collapsed", False))

    def _tag_map_effectively_collapsed(self) -> bool:
        return self.tag_map_collapsed() or bool(getattr(
            self, "_responsive_tag_map_collapsed", False))

    def _sync_tag_map_presentation(self) -> None:
        collapsed = self._tag_map_effectively_collapsed()
        responsive = bool(getattr(
            self, "_responsive_tag_map_collapsed", False))
        self.attribute_grid.setVisible(not collapsed)
        self.quick_tag_slot.setVisible(
            not collapsed and bool(self._quick_tags_mounted))

        button = self.tag_map_collapse_button
        if button.isChecked() != collapsed:
            button.blockSignals(True)
            button.setChecked(collapsed)
            button.blockSignals(False)
        # The button stays operable under the responsive fold: a visible
        # EXPAND that does nothing when clicked reads as broken, and the
        # manual fold must be able to win over the height heuristic.
        button.setEnabled(True)
        button.setText("EXPAND v" if collapsed else "COLLAPSE ^")
        if responsive:
            compact_hint = (
                "Tag Map is compact while the window is short; increase "
                "the window height to restore it")
            button.setAccessibleName("Tag Map compact at this window height")
            button.setAccessibleDescription(compact_hint)
            button.setToolTip(compact_hint)
        else:
            button.setAccessibleName(
                "Expand Tag Map" if collapsed else "Collapse Tag Map")
            button.setAccessibleDescription("")
            button.setToolTip("Collapse or expand the complete Tag Map")

    def _set_responsive_tag_map_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if bool(getattr(
                self, "_responsive_tag_map_collapsed", False)) == collapsed:
            return
        self._responsive_tag_map_collapsed = collapsed
        self._sync_tag_map_presentation()

    def set_tag_map_collapsed(
            self, collapsed: bool, *, persist: bool = True) -> None:
        """Fold the complete Tag Map without changing drawing state."""
        collapsed = bool(collapsed)
        self._tag_map_collapsed = collapsed
        # An explicit expand is a user intent that beats the short-window
        # fold; leave it in place and the next manual COLLAPSE stays folded
        # forever even after the window grows. Clearing it here makes the
        # manual choice authoritative until the window shrinks again.
        if not collapsed:
            self._responsive_tag_map_collapsed = False
        self._sync_tag_map_presentation()

        if persist and getattr(
                self.settings, "workspace_tag_map_collapsed", False
        ) != collapsed:
            self.settings.workspace_tag_map_collapsed = collapsed
            self.settings.save()

    def toggle_tag_map_collapsed(self) -> None:
        self.set_tag_map_collapsed(not self.tag_map_collapsed())

    def _timeline_snapping_toggled(self, enabled: bool) -> None:
        self.slider.set_snapping_enabled(enabled)
        self.timeline_snap_button.setText(
            "Snap On" if enabled else "Snap Off")
        state = "enabled" if enabled else "disabled"
        self.timeline_snap_button.setAccessibleName(
            f"Timeline snapping {state}")
        self.timeline_snap_button.setToolTip(
            f"Timeline snapping is {state} | S toggles | "
            "hold Alt to bypass")

    def toggle_timeline_snapping(self) -> None:
        self.timeline_snap_button.toggle()

    def zoom_timeline_in(self) -> None:
        self.timeline_zoom_slider.setValue(
            min(self.timeline_zoom_slider.maximum(),
                self.timeline_zoom_slider.value() + 1))

    def zoom_timeline_out(self) -> None:
        self.timeline_zoom_slider.setValue(
            max(self.timeline_zoom_slider.minimum(),
                self.timeline_zoom_slider.value() - 1))

    def reset_timeline_zoom(self) -> None:
        if self.timeline_zoom_slider.value() == 0:
            self.slider.reset_zoom()
        else:
            self.timeline_zoom_slider.setValue(0)
        self._sync_timeline_viewport_controls()

    def _update_marks_label(self) -> None:
        self.slider.set_marks(self.in_point_ms, self.out_point_ms)
        deck = self._control_center
        if deck is not None:
            deck.set_marks(self.in_point_ms, self.out_point_ms)

    # ---------- internal ----------

    def _speed_changed(self) -> None:
        self.player.setPlaybackRate(self.speed_combo.currentData())

    def _toggle_mute(self) -> None:
        self._user_muted = not self._user_muted
        self.audio.setMuted(self._user_muted or self._transport_muted)
        self.mute_btn.setText("Unmute" if self._user_muted else "Mute")
        icon_path = (
            VOLUME_MUTED_ICON
            if self._user_muted
            else VOLUME_ICON
        )
        self.volume_btn.setProperty("iconAsset", icon_path.name)
        self.volume_btn.setIcon(QIcon(str(icon_path)))

    def _volume_changed(self, value: int) -> None:
        self.audio.setVolume(value / 100)
        for slider in (
                getattr(self, "volume_slider", None),
                getattr(self, "inline_volume_slider", None)):
            if slider is None or slider.value() == value:
                continue
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)

    def _position_changed(self, pos: int) -> None:
        # Fires for playback, seeks, shuttle and scrubbing alike - one hook
        # covering every way the transport can be in use.
        self._mark_transport_busy()
        self._enforce_clip_range(pos)
        if not self.slider.isSliderDown():
            self.slider.setValue(pos)
        if self.timeline_overview is not None:
            self.timeline_overview.setValue(pos)
        # Position events still drive timeline/range behavior. The readout
        # follows completed paints, including the last frame delivered after K.

    def _update_position_readout(self, pos: int) -> None:
        text = self._jog_timecode(pos)
        if text != self._last_time_text:  # skip repaint when display is unchanged
            self._last_time_text = text
            deck = self._control_center
            if deck is not None:
                try:
                    deck.set_position(
                        text,
                        round(pos / max(0.001, self.frame_duration_ms)),
                    )
                except RuntimeError:
                    # positionChanged can still arrive while Qt tears down
                    # sibling dock widgets; the readout is not worth a crash.
                    pass

    def _jog_timecode(self, pos: int) -> str:
        return format_jog_timecode(pos, self.frame_duration_ms)

    def _duration_changed(self, duration: int) -> None:
        self.slider.setRange(0, duration)
        if self.timeline_overview is not None:
            self.timeline_overview.setRange(0, duration)
        self.timeline_zoom_slider.blockSignals(True)
        self.timeline_zoom_slider.setValue(0)
        self.timeline_zoom_slider.blockSignals(False)
        self._timeline_zoom_changed(0)
        if self.timeline_variant == DUAL \
                and self.slider.selected_block_range() is not None:
            selected = self.slider.selected_block_range()
            assert selected is not None
            self.slider.fit_range(
                *selected,
                context_before_ms=DUAL_DETAIL_CONTEXT_MS,
                context_after_ms=DUAL_DETAIL_CONTEXT_MS,
            )
            self._sync_timeline_viewport_controls()
        self.total_label.setText(format_ms(duration))

    def _state_changed(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if playing and not self._initial_stopped_seek_waking:
            self._pending_step_frame = None
        if playing and not self._initial_stopped_seek_waking \
                and (self._awaiting_initial_frame
                     or self.video_widget.frame_hold_active()):
            # Real user playback supersedes the load-scoped paused-frame wake.
            # The internal wake marks itself before play(), so only an
            # external playback command can reach this branch.
            self._reset_initial_stopped_seek_wake()
        icon = (
            QStyle.StandardPixmap.SP_MediaPause
            if playing
            else QStyle.StandardPixmap.SP_MediaPlay
        )
        self._set_play_glyph(icon, QPoint(0 if playing else 1, 0))

    def _on_error(self, _error, error_string: str) -> None:
        self._pending_step_frame = None
        self._reset_shuttle()
        self._reset_initial_stopped_seek_wake()
        if error_string:
            self.error_label.setText(
                f"Preview problem: {error_string}\n"
                "Export still works - preview uses Windows codecs, which may not "
                "support this format.")
            self.error_label.show()
