"""Full-game navigator paired with the editable TapeSift timeline."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QWidget

from tapesift.ui_core.timeline import (
    C_BG,
    C_BLOCK,
    C_BLOCK_SEL,
    C_CONFLICT,
    C_PLAYHEAD,
    PLAY_COLORS,
    TimelineBlock,
    assign_lanes,
)
from tapesift.ui_v2.tokens import COLORS


OVERVIEW_HEIGHT = 46
TRACK_TOP = 15
TRACK_HEIGHT = 12
# The strip below the track carries the wedge that ties this row's window to
# the detail timeline underneath it.
WEDGE_HEIGHT = 10


class TimelineOverview(QWidget):
    """A single-row map of the source with a draggable detail viewport."""

    seekRequested = Signal(int)
    visibleStartRequested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(OVERVIEW_HEIGHT)
        self.setMouseTracking(True)
        self.setAccessibleName("Full game timeline overview")
        self.setToolTip(
            "Full game overview | click to seek | drag the window to pan")
        self._minimum = 0
        self._maximum = 0
        self._value = 0
        self._visible_start = 0
        self._visible_end = 0
        self._blocks: list[TimelineBlock] = []
        self._conflicts: tuple[tuple[int, int], ...] = ()
        self._dragging_viewport = False
        self._drag_offset_ms = 0

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum = int(minimum)
        self._maximum = max(self._minimum, int(maximum))
        self._visible_start = self._minimum
        self._visible_end = self._maximum
        self._value = max(self._minimum, min(self._maximum, self._value))
        self.update()

    def setValue(self, value: int) -> None:
        normalized = max(self._minimum, min(self._maximum, int(value)))
        if normalized != self._value:
            self._value = normalized
            self.update()

    def set_visible_range(self, start_ms: int, end_ms: int) -> None:
        start = max(self._minimum, min(self._maximum, int(start_ms)))
        end = max(start, min(self._maximum, int(end_ms)))
        if (start, end) != (self._visible_start, self._visible_end):
            self._visible_start, self._visible_end = start, end
            self.update()

    def set_blocks(self, blocks: list[TimelineBlock]) -> None:
        normalized = [replace(block, lane=0) for block in blocks]
        _lanes, conflicts = assign_lanes(normalized)
        if normalized != self._blocks or conflicts != self._conflicts:
            self._blocks = normalized
            self._conflicts = conflicts
            self.update()

    def set_selected_clip_id(self, clip_id: str | None) -> None:
        normalized = [
            replace(block, selected=bool(clip_id and block.clip_id == clip_id))
            for block in self._blocks
        ]
        if normalized != self._blocks:
            self._blocks = normalized
            self.update()

    def _span(self) -> int:
        return max(1, self._maximum - self._minimum)

    def _x_for(self, value: int) -> int:
        fraction = (int(value) - self._minimum) / self._span()
        return round(max(0.0, min(1.0, fraction)) * max(1, self.width() - 1))

    def _ms_for(self, x: float) -> int:
        fraction = max(0.0, min(1.0, float(x) / max(1, self.width() - 1)))
        return round(self._minimum + fraction * self._span())

    def _viewport_rect(self) -> QRect:
        left = self._x_for(self._visible_start)
        right = self._x_for(self._visible_end)
        return QRect(left, TRACK_TOP - 3, max(4, right - left), TRACK_HEIGHT + 6)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), C_BG)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["overview_track"]))
        painter.drawRect(0, TRACK_TOP, self.width(), TRACK_HEIGHT)

        ordered = sorted(
            self._blocks,
            key=lambda block: (block.selected, block.start_ms, block.end_ms),
        )
        previous_x2 = -10
        for block in ordered:
            x1 = self._x_for(block.start_ms)
            x2 = self._x_for(block.end_ms)
            width = max(2, x2 - x1)
            colour = QColor(block.colour) if block.colour else \
                PLAY_COLORS.get(block.kind, C_BLOCK)
            if not colour.isValid():
                colour = C_BLOCK
            painter.setBrush(colour)
            painter.drawRect(x1, TRACK_TOP, width, TRACK_HEIGHT)
            if x1 - previous_x2 <= 1 and previous_x2 >= 0:
                painter.setBrush(C_BG)
                painter.drawRect(max(0, x1 - 1), TRACK_TOP, 1, TRACK_HEIGHT)
            previous_x2 = max(previous_x2, x1 + width)
            if block.selected:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(C_BLOCK_SEL, 1))
                painter.drawRect(x1, TRACK_TOP, width, TRACK_HEIGHT - 1)
                painter.setPen(Qt.PenStyle.NoPen)

        painter.setBrush(C_CONFLICT)
        for start, end in self._conflicts:
            x1, x2 = self._x_for(start), self._x_for(end)
            painter.drawRect(x1, TRACK_TOP + TRACK_HEIGHT + 1,
                             max(2, x2 - x1), 2)

        full_game = self._visible_start <= self._minimum \
            and self._visible_end >= self._maximum
        if not full_game:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_window(painter)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        playhead_x = self._x_for(self._value)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(C_PLAYHEAD)
        painter.drawRect(playhead_x - 1, 2, 2, TRACK_TOP + TRACK_HEIGHT + 2)

        font = painter.font()
        font.setPointSize(7)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(COLORS["overview_label"]))
        painter.drawText(
            2, 0, max(1, self.width() - 4), 12,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "FULL GAME",
        )
        if not full_game:
            painter.setPen(QColor(COLORS["overview_label_dim"]))
            painter.drawText(
                2, 0, max(1, self.width() - 6), 12,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._window_caption(),
            )
        painter.end()

    def _window_caption(self) -> str:
        shown = max(0, self._visible_end - self._visible_start) / 60_000
        total = self._span() / 60_000
        return f"SHOWING {shown:.0f} OF {total:.0f} MIN"

    def _paint_window(self, painter: QPainter) -> None:
        """Dim what is off screen, bracket what is, and tie it to the row below.

        The old treatment was a faint tinted rectangle over the track, which
        read as a smudge rather than as a window onto the detail timeline.
        """
        left = self._x_for(self._visible_start)
        right = self._x_for(self._visible_end)
        top = TRACK_TOP - 3
        height = TRACK_HEIGHT + 6

        painter.setPen(Qt.PenStyle.NoPen)
        dim = QColor(COLORS["overview_dim"])
        dim.setAlpha(118)
        painter.setBrush(dim)
        painter.drawRect(0, top, max(0, left), height)
        painter.drawRect(right, top, max(0, self.width() - right), height)

        window = QColor(COLORS["overview_window"])
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(window, 1.6))
        painter.drawRect(QRect(left, top, max(4, right - left), height))

        # Grab handles: the window has always been draggable and nothing on
        # screen said so.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(window)
        for edge in (left, right):
            painter.drawRect(edge - 1, top - 2, 3, height + 4)

        # The wedge fans from the window's edges out to the full width of the
        # detail timeline directly below, which is what that row is showing.
        wedge_top = OVERVIEW_HEIGHT - WEDGE_HEIGHT
        wedge_bottom = self.height()
        wedge = QColor(COLORS["overview_window"])
        wedge.setAlpha(30)
        painter.setBrush(wedge)
        painter.drawPolygon(QPolygon([
            QPoint(left, wedge_top),
            QPoint(right, wedge_top),
            QPoint(self.width(), wedge_bottom),
            QPoint(0, wedge_bottom),
        ]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        edge = QColor(COLORS["overview_window"])
        edge.setAlpha(120)
        painter.setPen(QPen(edge, 1))
        painter.drawLine(left, wedge_top, 0, wedge_bottom)
        painter.drawLine(right, wedge_top, self.width(), wedge_bottom)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        clicked_ms = self._ms_for(event.position().x())
        viewport = self._viewport_rect()
        full_game = self._visible_start <= self._minimum \
            and self._visible_end >= self._maximum
        if not full_game and viewport.adjusted(-4, -3, 4, 3).contains(
                event.position().toPoint()):
            self._dragging_viewport = True
            self._drag_offset_ms = clicked_ms - self._visible_start
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self.seekRequested.emit(clicked_ms)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging_viewport:
            duration = self._visible_end - self._visible_start
            start = self._ms_for(event.position().x()) - self._drag_offset_ms
            start = max(
                self._minimum,
                min(self._maximum - duration, start),
            )
            self.visibleStartRequested.emit(start)
            return
        if self._viewport_rect().adjusted(-4, -3, 4, 3).contains(
                event.position().toPoint()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton \
                and self._dragging_viewport:
            self._dragging_viewport = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
