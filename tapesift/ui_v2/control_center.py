"""Window-owned TapeSift control center.

The control center is presentation and input only.  ``VideoPlayer`` remains
the sole media/transport authority; the window constructs this widget and
explicitly attaches it to that authority.  Keeping construction here prevents
a player column from secretly owning a window-wide dock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_core.transport_pill import TransportPill
from tapesift.ui_v2 import deck_icons
from tapesift.ui_v2.shuttle_wheel import ProfessionalJogWheel

if TYPE_CHECKING:
    from tapesift.ui_core.video_player import VideoPlayer


def _console_control_style(radius: int) -> str:
    """Return the keycap treatment shared by deck and viewport controls."""
    return f"""
QToolButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #303234, stop:0.55 #242628, stop:1 #181A1C);
    border: 1px solid #44474A;
    border-radius: {radius}px;
    padding: 0px;
}}
QToolButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3A3D40, stop:0.55 #2D3032, stop:1 #212326);
    border-color: #62666A;
}}
QToolButton:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #141618, stop:1 #202326);
    border-color: #39e07a;
}}
QToolButton:disabled {{
    color: #9d998b;
    background: #1f201c;
    border-color: #303336;
}}
"""


MEDIA_CONTROL_STYLE = _console_control_style(0)
CIRCULAR_CONTROL_STYLE = _console_control_style(16)


# Telestration's persistent film-side rail deliberately reuses the quiet
# key/swatch treatment.  Exporting this small shared sheet keeps that rail
# styled without making VideoPlayer own any part of the six-zone deck.
DECK_KEY_QSS = """
QToolButton[deckKey="true"] {
    background: #1f201c;
    border: 1px solid #333C2D;
    border-radius: 2px;
    color: #78846F;
    padding: 0px;
}
QToolButton[deckKey="true"]:hover { background: #1B2118; border-color: #46523C; }
QToolButton[deckKey="true"]:checked {
    background: rgba(232,163,61,0.16);
    border-color: #E8A33D;
    color: #E8A33D;
}
QToolButton[deckTool="true"] {
    color: #AAB4A2;
    font-family: "Consolas","Cascadia Mono";
    font-size: 9px;
    padding: 0px 5px;
}
QToolButton[deckSeg="mid"] { border-radius: 0px; border-left: none; }
QToolButton[deckSeg="first"] { border-top-right-radius: 0px; border-bottom-right-radius: 0px; }
QToolButton[deckSeg="last"] { border-top-left-radius: 0px;
                              border-bottom-left-radius: 0px;
                              border-left: none; }
QToolButton[deckSwatch="true"] {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 2px;
    font-size: 12px;
    padding: 0px;
    min-width: 24px; max-width: 24px;
    min-height: 24px; max-height: 24px;
}
QToolButton[deckSwatch="true"][inkColor="gold"] { color: #F4C451; }
QToolButton[deckSwatch="true"][inkColor="cyan"] { color: #56C7DC; }
QToolButton[deckSwatch="true"][inkColor="red"] { color: #F06A63; }
QToolButton[deckSwatch="true"]:hover { background: #1B2118; }
QToolButton[deckSwatch="true"]:checked {
    background: #20261D;
    border-color: #687264;
}
"""


CONTROL_CENTER_QSS = DECK_KEY_QSS + """
QWidget#TransportPill {
    background-color: #1a1b18;
    border: 1px solid #202222;
    border-radius: 0px;
}
QWidget#ControlCenterDeckSurface,
QWidget#TransportPositionZone,
QWidget#TransportPositionBody,
QWidget#TransportCluster,
QWidget#TransportWheelAnchor,
QWidget#TransportDialZone,
QWidget#TransportDialBody,
QWidget#TransportMarksGroup,
QWidget#TransportVoiceoverZone,
QWidget#TransportExportZone {
    background: transparent;
    border: none;
}
QFrame#TransportZoneRule {
    background-color: #3b3b32;
    border: none;
    max-width: 1px;
}
QFrame#TransportRowRule {
    background-color: #262729;
    border: none;
    max-height: 1px;
}
QWidget#ControlCenterTopRow,
QWidget#ControlCenterBottomRow,
QWidget#TransportReservedBay {
    background: transparent;
    border: none;
}
QWidget#TransportWheelHost {
    background: transparent;
    border: none;
}
QWidget#TransportCluster QToolButton[deckKey="true"] {
    background: #1A1C1E;
    border-color: #36393C;
    border-radius: 0px;
    color: #858B8F;
}
QWidget#TransportCluster QToolButton[deckKey="true"]:hover {
    background: #24272A;
    border-color: #4A4E52;
}
QWidget#TransportCluster QToolButton[deckKey="true"]:pressed {
    background: #111315;
    border-color: #E8A33D;
}
QWidget#TransportCluster QToolButton[deckKey="true"]:checked {
    background: rgba(232,163,61,0.16);
    border-color: #E8A33D;
    color: #E8A33D;
}
QWidget#TransportCluster QToolButton[deckKey="true"]:disabled {
    background: #151719;
    border-color: #2B2E30;
    color: #676C70;
}
QWidget#TransportPositionZone QLabel { background: transparent; }
QLabel[deckZoneLabel="true"] {
    background: transparent;
    color: #9D998B;
    font-family: "Consolas","Cascadia Mono";
    font-size: 10px;
    font-weight: 600;
}
QPushButton[deckVoiceRecord="true"][recording="true"] {
    background: #2A1111;
    border-color: #E14D4D;
    color: #F06A6A;
}
QPushButton[deckExportAction="true"] {
    background: #173322;
    border-color: #2F7D4C;
    color: #8FD6A8;
}
QWidget#DeckFieldGroup {
    background-color: #0D110C;
    border: 1px solid #333C2D;
    border-radius: 2px;
}
QWidget#DeckField { background: transparent; border: none; }
QWidget#DeckField[cell="mid"],
QWidget#DeckField[cell="last"] { border-left: 1px solid #232A1F; }
QWidget#DeckField QLabel[mark="true"] { color: #E8DFC6; }
QPushButton[transport="true"] {
    background: #1f201c;
    border: 1px solid #333C2D;
    border-radius: 2px;
    color: #c9c3b3;
    font-family: "Consolas","Cascadia Mono";
    font-size: 10px;
    padding: 0px;
}
QPushButton[transport="true"]:hover { background: #1B2118; border-color: #46523C; }
QPushButton[transport="true"]:disabled { color: #4E584A; border-color: #262D22; }
QPushButton[transport="true"]:focus {
    outline: 1px solid #565345;
    outline-offset: 2px;
}
QPushButton[transport="true"]:checked {
    background: #173322;
    border-color: #2F7D4C;
    color: #8FD6A8;
}
QPushButton#AddClipButton, QPushButton[transport="true"][commit="true"] {
    border-color: #2F7D4C; color: #8FD6A8;
}
QWidget#DeckField QLabel { background: transparent; }
QWidget#DeckField QLabel[role="caption"] {
    color: #9D998B;
    font-family: "Consolas","Cascadia Mono";
    font-size: 9px;
    letter-spacing: 2px;
}
"""


class ShuttleMeter(QWidget):
    """Signed J/K/L scale and explicit PAUSED/REV/FWD state readout."""

    SCALE_VALUES = (-8.0, -4.0, -2.0, -1.0, 0.0,
                    1.0, 2.0, 4.0, 8.0)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ShuttleMeter")
        self.setMinimumSize(250, 86)
        self.setMaximumHeight(86)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Signed shuttle speed and J K L state")
        self._rate = 0.0
        self.setAccessibleDescription(self._state_text())

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float) -> None:
        rate = float(rate or 0.0)
        if abs(rate - self._rate) < 0.001:
            return
        self._rate = rate
        self.setAccessibleDescription(self._state_text())
        self.update()

    def _state_text(self) -> str:
        if abs(self._rate) <= 0.001:
            return "PAUSED 0×"
        return f"{'FWD' if self._rate > 0 else 'REV'} {abs(self._rate):g}×"

    @staticmethod
    def _mono(size: float, *, bold: bool = False) -> QFont:
        font = QFont(["Consolas", "Cascadia Mono", "Courier New"])
        font.setPointSizeF(size)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setBold(bold)
        return font

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        left = 12.0
        right = max(left + 1.0, self.width() - 12.0)
        axis_y = 27.0
        step = (right - left) / (len(self.SCALE_VALUES) - 1)
        positions = [left + index * step
                     for index in range(len(self.SCALE_VALUES))]

        painter.setFont(self._mono(6.4))
        painter.setPen(QColor("#A8AAA7"))
        for value, x in zip(self.SCALE_VALUES, positions):
            label = "0" if value == 0 else f"{value:g}×"
            painter.drawText(
                QRectF(x - 18.0, 0.0, 36.0, 15.0),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

        painter.setPen(QPen(QColor("#777B79"), 1.0))
        painter.drawLine(int(left), int(axis_y), int(right), int(axis_y))
        for index, x in enumerate(positions):
            length = 13.0 if index in (0, 4, 8) else 9.0
            painter.drawLine(
                int(x), int(axis_y - length / 2.0),
                int(x), int(axis_y + length / 2.0))

        nearest = min(
            range(len(self.SCALE_VALUES)),
            key=lambda index: abs(self.SCALE_VALUES[index] - self._rate),
        )
        cursor_x = positions[nearest]
        painter.setPen(QPen(QColor("#F0A63C"), 3.0))
        painter.drawLine(
            int(cursor_x), int(axis_y - 14.0),
            int(cursor_x), int(axis_y + 14.0))

        active = abs(self._rate) > 0.001
        painter.setFont(self._mono(8.0, bold=True))
        painter.setPen(QColor("#F0A63C" if active else "#43CDE8"))
        painter.drawText(
            QRectF(0.0, 36.0, self.width(), 20.0),
            Qt.AlignmentFlag.AlignCenter,
            self._state_text(),
        )

        painter.setFont(self._mono(6.2))
        painter.setPen(QColor("#B6B8B3"))
        thirds = self.width() / 3.0
        for index, text in enumerate((
                "J  REVERSE", "K  PAUSE", "L  FORWARD")):
            painter.drawText(
                QRectF(index * thirds, 64.0, thirds, 16.0),
                Qt.AlignmentFlag.AlignCenter,
                text,
            )


class VoiceoverWaveform(QWidget):
    """Compact real-input meter and selected-take waveform surface."""

    BAR_COUNT = 14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VoiceoverWaveform")
        self.setMinimumSize(92, 30)
        self.setMaximumHeight(30)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Voiceover input level and take waveform")
        self._waveform: tuple[float, ...] = ()
        self._peak = 0.0
        self._rms = 0.0
        self._live = False

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def rms(self) -> float:
        return self._rms

    @property
    def waveform(self) -> tuple[float, ...]:
        return self._waveform

    def sizeHint(self) -> QSize:
        # The prior 27 px bar plus its 2 px top/bottom layout margins exposed
        # this hint even though the outer placeholder was fixed at 92x30.
        # Matching it exactly protects the deck/dial height contract.
        return QSize(77, 31)

    def minimumSizeHint(self) -> QSize:
        return QSize(77, 31)

    def set_levels(self, peak: float, rms: float) -> None:
        self._peak = max(0.0, min(1.0, float(peak)))
        self._rms = max(0.0, min(1.0, float(rms)))
        self._live = True
        self.update()

    def set_waveform(self, waveform: tuple[float, ...]) -> None:
        self._waveform = tuple(
            max(0.0, min(1.0, float(value))) for value in waveform)
        self._peak = 0.0
        self._rms = 0.0
        self._live = False
        self.update()

    def _display_values(self) -> tuple[float, ...]:
        if self._live:
            # The envelope breathes with RMS while alternating bars expose the
            # current peak. It is deliberately state-derived, not decorative.
            shape = (0.48, 0.72, 0.58, 0.90, 0.64, 1.00, 0.78)
            return tuple(min(
                1.0,
                self._rms * shape[index % len(shape)]
                + self._peak * (0.35 if index % 3 == 1 else 0.12),
            ) for index in range(self.BAR_COUNT))
        if not self._waveform:
            return (0.06,) * self.BAR_COUNT
        values: list[float] = []
        count = len(self._waveform)
        for index in range(self.BAR_COUNT):
            start = index * count // self.BAR_COUNT
            stop = max(start + 1, (index + 1) * count // self.BAR_COUNT)
            values.append(max(self._waveform[start:min(stop, count)]))
        return tuple(values)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        values = self._display_values()
        gap = 2.0
        bar_width = max(
            2.0,
            (self.width() - gap * (len(values) - 1)) / len(values),
        )
        colour = QColor("#58C780" if self._live else "#4E9D68")
        quiet = QColor("#253429")
        for index, value in enumerate(values):
            height = max(2.0, round(value * (self.height() - 4)))
            left = index * (bar_width + gap)
            top = (self.height() - height) / 2.0
            painter.fillRect(
                QRectF(left, top, bar_width, height),
                colour if value > 0.08 else quiet,
            )


class ControlCenterDeck(TransportPill):
    """Six-zone control center constructed and owned by a window."""

    DECK_HEIGHT = 197
    TOP_ROW_HEIGHT = 135
    BOTTOM_ROW_HEIGHT = 61
    POSITION_CONTENT_WIDTH = 146
    TARGET_TRANSPORT_CONTENT_WIDTH = 415
    TARGET_WHEEL_CONTENT_WIDTH = 242
    TARGET_SHUTTLE_CONTENT_WIDTH = 383
    COMPACT_TRANSPORT_CONTENT_WIDTH = 290
    COMPACT_WHEEL_CONTENT_WIDTH = 242
    COMPACT_SHUTTLE_CONTENT_WIDTH = 268
    TARGET_LAYOUT_WIDTH = 1683
    COMPACT_LAYOUT_WIDTH = 1260

    # Feature owners connect here.  These requests intentionally never route
    # through VideoPlayer: recording and export are window workflows, not
    # media-transport behavior.
    voiceover_record_requested = Signal()
    voiceover_clear_requested = Signal()
    export_style_requested = Signal(str)
    export_format_requested = Signal()
    export_requested = Signal()

    COMPATIBILITY_ALIASES = (
        "current_label",
        "position_detail",
        "shuttle_meter",
        "play_btn",
        "jog_ring",
        "step_back_btn",
        "step_fwd_btn",
        "loop_btn",
        "in_button",
        "out_button",
        "add_clip_btn",
        "in_value",
        "out_value",
        "len_value",
        "position_zone",
        "dial_zone",
        "marks_group",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        outer_row = QHBoxLayout()
        outer_row.setContentsMargins(0, 0, 0, 0)
        outer_row.setSpacing(0)
        super().__init__(parent, outer_row)
        self._player: VideoPlayer | None = None
        self._row = outer_row

        # TransportPill remains the window-owned host, but this standard deck
        # is a flat edge-to-edge console rather than an elevated rounded card.
        # Keep the exception local: no shared theme or host behavior changes.
        shell = self.layout()
        if shell is not None:
            shell.setContentsMargins(0, 0, 0, 0)
            shell.setSpacing(0)
        self.setGraphicsEffect(None)

        self._deck_surface = QWidget(self)
        self._deck_surface.setObjectName("ControlCenterDeckSurface")
        deck_stack = QVBoxLayout(self._deck_surface)
        deck_stack.setContentsMargins(0, 0, 0, 0)
        deck_stack.setSpacing(0)
        self._deck_stack = deck_stack

        self._top_row_host = QWidget(self._deck_surface)
        self._top_row_host.setObjectName("ControlCenterTopRow")
        self._top_row_host.setFixedHeight(self.TOP_ROW_HEIGHT)
        self._top_row = QHBoxLayout(self._top_row_host)
        self._top_row.setContentsMargins(0, 0, 0, 0)
        self._top_row.setSpacing(0)
        deck_stack.addWidget(self._top_row_host)

        self._row_divider = QFrame(self._deck_surface)
        self._row_divider.setObjectName("TransportRowRule")
        self._row_divider.setFrameShape(QFrame.Shape.NoFrame)
        self._row_divider.setFixedHeight(1)
        deck_stack.addWidget(self._row_divider)

        self._bottom_row_host = QWidget(self._deck_surface)
        self._bottom_row_host.setObjectName("ControlCenterBottomRow")
        self._bottom_row_host.setFixedHeight(self.BOTTOM_ROW_HEIGHT)
        self._bottom_row = QHBoxLayout(self._bottom_row_host)
        self._bottom_row.setContentsMargins(0, 0, 0, 0)
        self._bottom_row.setSpacing(0)
        deck_stack.addWidget(self._bottom_row_host)

        outer_row.addWidget(self._deck_surface, 1)
        self._build_zones()
        self.setStyleSheet(CONTROL_CENTER_QSS)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # MainWindowV2 derives the native bottom dock height from this widget.
        # Pin the standard two-row contract so no child hint can inflate it.
        self.setMinimumHeight(self.sizeHint().height())
        self.resized.connect(self._update_responsive_state)
        self._update_responsive_state(0)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(hint.width(), self.DECK_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(self.COMPACT_LAYOUT_WIDTH, self.DECK_HEIGHT)

    @property
    def bound_player(self) -> VideoPlayer | None:
        return self._player

    def bind_player(self, player: VideoPlayer) -> None:
        """Connect this presentation tree to one media authority exactly once."""
        if self._player is player:
            return
        if self._player is not None:
            raise RuntimeError("ControlCenterDeck is already bound to a player")
        self._player = player
        self.skip_backward_btn.clicked.connect(player.jump_backward)
        self.rewind_btn.clicked.connect(player.shuttle_reverse)
        self.play_btn.clicked.connect(player.toggle_play)
        self.fast_forward_btn.clicked.connect(player.shuttle_forward)
        self.skip_forward_btn.clicked.connect(player.jump_forward)
        self.jog_ring.framesRequested.connect(player._jog_frames_requested)
        self.step_back_btn.clicked.connect(player.frame_step_backward)
        self.step_fwd_btn.clicked.connect(player.frame_step_forward)
        self.loop_btn.toggled.connect(player.set_range_loop)
        self.in_button.clicked.connect(player.set_in_point)
        self.out_button.clicked.connect(player.set_out_point)
        self.add_clip_btn.clicked.connect(player.request_add_clip)

    def set_position(self, timecode: str, frame_number: int) -> None:
        self.current_label.setText(timecode)
        frame_text = f"{int(frame_number):,}"
        self.jog_ring.set_timecode(timecode)
        self.jog_ring.set_frame_number(frame_text)
        self.position_detail.setText(f"F {frame_text}")

    def set_shuttle_rate(self, rate: float) -> None:
        self.jog_ring.set_shuttle_rate(rate)
        self.shuttle_meter.set_rate(rate)

    def set_marks(self, in_ms: int | None, out_ms: int | None) -> None:
        self.in_value.setText(
            format_ms(in_ms, show_millis=True) if in_ms is not None else "-")
        self.out_value.setText(
            format_ms(out_ms, show_millis=True) if out_ms is not None else "-")
        ready = in_ms is not None and out_ms is not None
        self.len_value.setText(
            f"{(out_ms - in_ms) / 1000:.1f}s" if ready else "-")
        self.add_clip_btn.setEnabled(ready)

    def set_playing(self, playing: bool) -> None:
        self.play_btn.setIcon(
            deck_icons.state_icon(
                "console_pause" if playing else "console_play", 40))
        self.play_btn.setIconSize(QSize(40, 40))
        action = "Pause" if playing else "Play"
        self.play_btn.setAccessibleName(f"{action} playback (Space)")
        self.play_btn.setToolTip(f"{action} playback (Space)")
        self.play_btn.setProperty("playing", "true" if playing else "false")
        self.play_btn.style().unpolish(self.play_btn)
        self.play_btn.style().polish(self.play_btn)

    def _zone_label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setProperty("deckZoneLabel", "true")
        label.setFixedHeight(14)
        return label

    def _button(self, text: str, tooltip: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _media_button(
            self, icon_name: str, accessible_name: str,
            object_name: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName(object_name)
        button.setProperty("mediaControl", "true")
        button.setStyleSheet(MEDIA_CONTROL_STYLE)
        button.setIcon(deck_icons.state_icon(icon_name, 40))
        button.setIconSize(QSize(40, 40))
        button.setFixedSize(44, 44)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAccessibleName(accessible_name)
        button.setToolTip(accessible_name)
        return button

    @staticmethod
    def _plate_state_icon(
            icon_name: str, size: int, active: str = "#E8A33D") -> QIcon:
        icon = QIcon()
        icon.addPixmap(
            deck_icons.load(icon_name, deck_icons.REST, size).pixmap(size, size),
            QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(
            deck_icons.load(icon_name, deck_icons.BRIGHT, size).pixmap(
                size, size),
            QIcon.Mode.Active, QIcon.State.Off)
        icon.addPixmap(
            deck_icons.load(icon_name, active, size).pixmap(size, size),
            QIcon.Mode.Normal, QIcon.State.On)
        icon.addPixmap(
            deck_icons.load(icon_name, "#4E584A", size).pixmap(size, size),
            QIcon.Mode.Disabled, QIcon.State.Off)
        return icon

    def _rule(self, row: QHBoxLayout, height: int) -> QFrame:
        line = QFrame(self)
        line.setObjectName("TransportZoneRule")
        line.setFrameShape(QFrame.Shape.NoFrame)
        line.setFixedWidth(1)
        line.setFixedHeight(height)
        row.addWidget(line, 0, Qt.AlignmentFlag.AlignVCenter)
        return line

    @staticmethod
    def _hug(widget: QWidget) -> QWidget:
        widget.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        return widget

    def _build_zones(self) -> None:
        # 1 POSITION
        self.position_zone = QWidget(self)
        self.position_zone.setObjectName("TransportPositionZone")
        self.position_zone.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        position_layout = QVBoxLayout(self.position_zone)
        position_layout.setContentsMargins(4, 8, 14, 4)
        position_layout.setSpacing(3)
        position_layout.addWidget(self._zone_label("POSITION"))
        position_body = QWidget(self.position_zone)
        position_body.setObjectName("TransportPositionBody")
        position_box = QHBoxLayout(position_body)
        position_box.setContentsMargins(0, 0, 0, 0)
        position_box.setSpacing(8)
        self.current_label = QLabel("00:00:00:00", self.position_zone)
        self.current_label.setFixedWidth(124)
        self.current_label.setProperty("mono", "true")
        self.current_label.setObjectName("TransportTimecode")
        self.current_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        timecode_font = QFont(["Consolas", "Cascadia Mono", "Courier New"])
        timecode_font.setPointSizeF(13.0)
        timecode_font.setStyleHint(QFont.StyleHint.Monospace)
        self.current_label.setFont(timecode_font)
        self.position_detail = QLabel("-", self.position_zone)
        self.position_detail.setObjectName("TransportPositionDetail")
        self.position_detail.setProperty("role", "subtle")
        position_column = QVBoxLayout()
        position_column.setContentsMargins(0, 0, 0, 0)
        position_column.setSpacing(5)
        position_column.addWidget(self.current_label)
        position_column.addWidget(self.position_detail)
        position_column.addStretch(1)
        position_box.addLayout(position_column)
        position_layout.addWidget(position_body)
        self._top_row.addWidget(self.position_zone, 0)
        self._rule(self._top_row, self.TOP_ROW_HEIGHT)

        # 2 TRANSPORT
        self.transport_zone = QWidget(self)
        self.transport_zone.setObjectName("TransportCluster")
        self.transport_zone.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        transport_layout = QVBoxLayout(self.transport_zone)
        transport_layout.setContentsMargins(8, 8, 8, 4)
        transport_layout.setSpacing(4)
        transport_layout.addWidget(self._zone_label("TRANSPORT"))
        self.skip_backward_btn = self._media_button(
            "console_jump_back", "Jump backward (Shift+Left)",
            "TransportSkipBackward")
        self.rewind_btn = self._media_button(
            "console_shuttle_back", "Shuttle backward (J)",
            "TransportRewind")
        self.play_btn = self._media_button(
            "console_play", "Play playback (Space)",
            "TransportPlayPause")
        self.fast_forward_btn = self._media_button(
            "console_shuttle_fwd", "Shuttle forward (L)",
            "TransportFastForward")
        self.skip_forward_btn = self._media_button(
            "console_jump_fwd", "Jump forward (Shift+Right)",
            "TransportSkipForward")
        keys_grid = QGridLayout()
        keys_grid.setContentsMargins(0, 0, 0, 0)
        keys_grid.setHorizontalSpacing(0)
        keys_grid.setVerticalSpacing(5)
        self._transport_keys_row = keys_grid
        self._primary_media_buttons = (
            self.skip_backward_btn,
            self.rewind_btn,
            self.play_btn,
            self.fast_forward_btn,
            self.skip_forward_btn,
        )
        for column, control in enumerate(self._primary_media_buttons):
            keys_grid.setColumnStretch(column, 1)
            keys_grid.addWidget(
                control, 0, column,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.step_back_btn = QToolButton(self.transport_zone)
        self.step_back_btn.setObjectName("TransportFrameBackward")
        self.step_back_btn.setIcon(
            self._plate_state_icon("console_frame_back", 26))
        self.step_back_btn.setToolTip("Step back one frame (Left)")
        self.step_fwd_btn = QToolButton(self.transport_zone)
        self.step_fwd_btn.setObjectName("TransportFrameForward")
        self.step_fwd_btn.setIcon(
            self._plate_state_icon("console_frame_fwd", 26))
        self.step_fwd_btn.setToolTip("Step forward one frame (Right)")
        self.loop_btn = QToolButton(self.transport_zone)
        self.loop_btn.setObjectName("TransportLoop")
        self.loop_btn.setIcon(
            self._plate_state_icon("console_loop", 26, "#55D17B"))
        self.loop_btn.setToolTip("Loop the marked range")
        self.loop_btn.setCheckable(True)
        secondary_buttons = (
            self.step_back_btn, self.loop_btn, self.step_fwd_btn)
        for column, button in zip((1, 2, 3), secondary_buttons):
            button.setIconSize(QSize(26, 26))
            button.setProperty("deckKey", "true")
            button.setFixedSize(30, 30)
            button.setAccessibleName(button.toolTip())
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            keys_grid.addWidget(
                button, 1, column,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        transport_layout.addLayout(keys_grid)
        transport_layout.addStretch(1)
        self._top_row.addWidget(self.transport_zone, 0)
        self._rule(self._top_row, self.TOP_ROW_HEIGHT)

        # 3 FULL-HEIGHT WHEEL BAY. The row layouts reserve the same column,
        # while this one host spans both rows and masks the horizontal rule.
        self._wheel_anchor_top = QWidget(self)
        self._wheel_anchor_top.setObjectName("TransportWheelAnchor")
        self._wheel_anchor_top.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._top_row.addWidget(self._wheel_anchor_top)
        self._rule(self._top_row, self.TOP_ROW_HEIGHT)

        self.wheel_host = QWidget(self._deck_surface)
        self.wheel_host.setObjectName("TransportWheelHost")
        self.wheel_host.setFixedSize(
            self.TARGET_WHEEL_CONTENT_WIDTH, self.DECK_HEIGHT)
        wheel_layout = QVBoxLayout(self.wheel_host)
        wheel_layout.setContentsMargins(0, 0, 0, 0)
        wheel_layout.setSpacing(0)
        self.jog_ring = ProfessionalJogWheel(parent=self.wheel_host)
        wheel_layout.addWidget(
            self.jog_ring, 0,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.wheel_host.raise_()

        # 4 SHUTTLE TELEMETRY
        self.dial_zone = QWidget(self)
        self.dial_zone.setObjectName("TransportDialZone")
        self.dial_zone.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        dial_layout = QVBoxLayout(self.dial_zone)
        dial_layout.setContentsMargins(8, 8, 10, 2)
        dial_layout.setSpacing(3)
        dial_layout.addWidget(self._zone_label("SHUTTLE"))
        dial_body = QWidget(self.dial_zone)
        dial_body.setObjectName("TransportDialBody")
        dial_box = QHBoxLayout(dial_body)
        dial_box.setContentsMargins(0, 0, 0, 0)
        dial_box.setSpacing(0)
        self._dial_box = dial_box
        self.shuttle_meter = ShuttleMeter(self.dial_zone)
        dial_box.addWidget(
            self.shuttle_meter, 1, Qt.AlignmentFlag.AlignVCenter)
        dial_layout.addWidget(dial_body)
        self._top_row.addWidget(self.dial_zone, 0)
        self._rule(self._top_row, self.TOP_ROW_HEIGHT)

        # 5 CLIP
        self.marks_group = QWidget(self)
        self.marks_group.setObjectName("TransportMarksGroup")
        marks_column = QVBoxLayout(self.marks_group)
        marks_column.setContentsMargins(8, 8, 8, 4)
        marks_column.setSpacing(3)
        marks_column.addWidget(self._zone_label("MARK CLIP"))
        readout_row = QHBoxLayout()
        readout_row.setContentsMargins(0, 0, 0, 0)
        readout_row.setSpacing(10)
        marks_controls = QHBoxLayout()
        marks_controls.setContentsMargins(0, 0, 0, 0)
        marks_controls.setSpacing(5)
        marks_column.addLayout(readout_row)
        marks_column.addLayout(marks_controls)
        self.in_button = self._button("I   In", "Set the clip in point (I)")
        self.in_button.setProperty("transport", "true")
        self.in_button.setFixedSize(84, 32)
        marks_controls.addWidget(self.in_button)
        marks_controls.addStretch(1)
        self.out_button = self._button("O   Out", "Set the clip out point (O)")
        self.out_button.setProperty("transport", "true")
        self.out_button.setFixedSize(84, 32)
        marks_controls.addWidget(self.out_button)
        marks_controls.addStretch(1)
        self.add_clip_btn = self._button(
            "+ Clip", "Create a clip from the marked in/out points (A)")
        self.add_clip_btn.setProperty("transport", "true")
        self.add_clip_btn.setProperty("commit", "true")
        self.add_clip_btn.setFixedSize(68, 32)
        self.add_clip_btn.setEnabled(False)
        marks_controls.addWidget(self.add_clip_btn)
        self.in_value = self._mono_value("-")
        self.out_value = self._mono_value("-")
        self.len_value = self._mono_value("-")
        self.clip_fields = QWidget(self)
        self.clip_fields.setObjectName("DeckFieldGroup")
        field_group = QHBoxLayout(self.clip_fields)
        field_group.setContentsMargins(0, 0, 0, 0)
        field_group.setSpacing(0)
        cells = (("IN", self.in_value, 74, True),
                 ("OUT", self.out_value, 74, True),
                 ("LEN", self.len_value, 46, False))
        for index, (caption, value, width, is_mark) in enumerate(cells):
            cell = QWidget(self)
            cell.setObjectName("DeckField")
            cell.setProperty("cell", "mid" if index else "first")
            if index == len(cells) - 1:
                cell.setProperty("cell", "last")
            column = QVBoxLayout(cell)
            column.setContentsMargins(9, 3, 9, 3)
            column.setSpacing(0)
            tag = QLabel(caption, cell)
            tag.setProperty("role", "caption")
            value.setMinimumWidth(width)
            value.setProperty("mark", "true" if is_mark else "false")
            value.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            column.addWidget(tag)
            column.addWidget(value)
            field_group.addWidget(cell)
        readout_row.addWidget(
            self.clip_fields, 0, Qt.AlignmentFlag.AlignVCenter)
        readout_row.addStretch(1)
        self.marks_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._top_row.addWidget(
            self.marks_group, 1, Qt.AlignmentFlag.AlignVCenter)

        # 5 VOICEOVER
        self.voiceover_zone = QWidget(self)
        self.voiceover_zone.setObjectName("TransportVoiceoverZone")
        self.voiceover_zone.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        voice_column = QVBoxLayout(self.voiceover_zone)
        voice_column.setContentsMargins(12, 6, 12, 2)
        voice_column.setSpacing(4)
        voice_column.addWidget(self._zone_label("VOICEOVER"))
        voice_top = QHBoxLayout()
        self._voiceover_top_row = voice_top
        voice_top.setContentsMargins(0, 0, 0, 0)
        voice_top.setSpacing(8)
        self.voiceover_record_button = QPushButton("●  REC", self)
        self.voiceover_record_button.setObjectName("VoiceoverRecord")
        self.voiceover_record_button.setProperty("transport", "true")
        self.voiceover_record_button.setProperty("deckVoiceRecord", "true")
        self.voiceover_record_button.setProperty("recording", "false")
        self.voiceover_record_button.setFixedSize(72, 30)
        self.voiceover_record_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.voiceover_record_button.clicked.connect(
            lambda _checked=False: self.voiceover_record_requested.emit())
        voice_top.addWidget(self.voiceover_record_button)
        self.voiceover_waveform = VoiceoverWaveform(self.voiceover_zone)
        voice_top.addWidget(self.voiceover_waveform)
        voice_bottom = QHBoxLayout()
        voice_bottom.setContentsMargins(0, 0, 0, 0)
        voice_bottom.setSpacing(6)
        self.voiceover_take_label = QLabel("TAKE 1  ·  --:--", self)
        self.voiceover_take_label.setProperty("role", "subtle")
        voice_bottom.addWidget(self.voiceover_take_label)
        self.voice_clear_button = QPushButton("CLEAR", self)
        self.voice_clear_button.setProperty("transport", "true")
        self.voice_clear_button.setFixedSize(52, 26)
        self.voice_clear_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.voice_clear_button.clicked.connect(
            lambda _checked=False: self.voiceover_clear_requested.emit())
        self.voice_clear_button.setEnabled(False)
        voice_bottom.addWidget(self.voice_clear_button)
        voice_content = QHBoxLayout()
        voice_content.setContentsMargins(0, 0, 0, 0)
        voice_content.setSpacing(12)
        voice_content.addLayout(voice_top, 3)
        voice_content.addLayout(voice_bottom, 2)
        voice_content.addStretch(1)
        voice_column.addLayout(voice_content)
        voice_column.addStretch(1)
        self.voiceover_zone.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._bottom_row.addWidget(
            self.voiceover_zone, 0, Qt.AlignmentFlag.AlignVCenter)
        self._rule(self._bottom_row, self.BOTTOM_ROW_HEIGHT)

        # The standard lower console keeps a quiet bay between capture and
        # export. It is presentation-only and collapses before either workflow
        # loses the space its existing controls require.
        self.reserved_bay = QWidget(self)
        self.reserved_bay.setObjectName("TransportReservedBay")
        self.reserved_bay.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._bottom_row.addWidget(self.reserved_bay)
        self._rule(self._bottom_row, self.BOTTOM_ROW_HEIGHT)

        # 6 EXPORT. These are requests into the authoritative ExportPanel;
        # the deck never owns a second queue or renderer.
        self.deck_export_zone = QWidget(self)
        self.deck_export_zone.setObjectName("TransportExportZone")
        self.deck_export_zone.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        export_column = QVBoxLayout(self.deck_export_zone)
        export_column.setContentsMargins(12, 6, 4, 2)
        export_column.setSpacing(4)
        export_column.addWidget(self._zone_label("EXPORT"))
        export_top = QHBoxLayout()
        self._export_top_row = export_top
        export_top.setContentsMargins(0, 0, 0, 0)
        export_top.setSpacing(5)
        self.deck_export_style_buttons: list[QPushButton] = []
        self.deck_export_style_group = QButtonGroup(self)
        self.deck_export_style_group.setExclusive(True)
        for text, width, checked in (("SIGNATURE", 84, False),
                                     ("CLEAN", 58, True),
                                     ("VERTICAL", 76, False)):
            button = QPushButton(text, self)
            button.setObjectName(f"DeckExportStyle{text.title()}")
            button.setProperty("transport", "true")
            button.setCheckable(True)
            button.setChecked(checked)
            button.setAccessibleName(f"{text.title()} export style")
            button.setAccessibleDescription(
                "Select this presentation style and open Export Package")
            button.setFixedSize(width, 28)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(
                lambda _checked=False, style=text:
                self._request_export_style(style))
            export_top.addWidget(button)
            self.deck_export_style_group.addButton(button)
            self.deck_export_style_buttons.append(button)
        export_top.addStretch(1)
        export_bottom = QHBoxLayout()
        self._export_bottom_row = export_bottom
        export_bottom.setContentsMargins(0, 0, 0, 0)
        export_bottom.setSpacing(6)
        self.deck_format_button = QPushButton("16:9  ·  1080p", self)
        self.deck_format_button.setProperty("transport", "true")
        self.deck_format_button.setFixedSize(112, 28)
        self.deck_format_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.deck_format_button.clicked.connect(
            lambda _checked=False: self.export_format_requested.emit())
        export_bottom.addWidget(self.deck_format_button)
        self.deck_export_button = QPushButton("EXPORT", self)
        self.deck_export_button.setObjectName("DeckExportAction")
        self.deck_export_button.setProperty("transport", "true")
        self.deck_export_button.setProperty("deckExportAction", "true")
        self.deck_export_button.setFixedSize(78, 28)
        self.deck_export_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.deck_export_button.clicked.connect(
            lambda _checked=False: self.export_requested.emit())
        export_bottom.addWidget(self.deck_export_button)
        export_bottom.addStretch(1)
        export_content = QHBoxLayout()
        export_content.setContentsMargins(0, 0, 0, 0)
        export_content.setSpacing(14)
        export_content.addLayout(export_top)
        export_content.addLayout(export_bottom)
        export_content.addStretch(1)
        export_column.addLayout(export_content)
        export_column.addStretch(1)
        self._bottom_row.addWidget(self.deck_export_zone, 1)
        self.set_export_style("clean")

    def _request_export_style(self, style: str) -> None:
        self.set_export_style(style)
        self.export_style_requested.emit(style)

    def set_export_style(self, style: str) -> None:
        """Mirror ExportPanel selection without emitting another request."""

        normalized = str(style).strip().lower()
        labels = {
            "clean": "SOURCE  ·  PRESET",
            "signature": "16:9  ·  1920×1080",
            "vertical": "9:16  ·  1080×1920",
        }
        if normalized not in labels:
            raise ValueError(f"Unknown export style: {style!r}")
        for button in self.deck_export_style_buttons:
            button.setChecked(button.text().lower() == normalized)
        self.deck_format_button.setText(labels[normalized])
        self.deck_format_button.setToolTip(
            "Open Export Package to review the separate encoding choice")

    def _mono_value(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setProperty("mono", "true")
        label.setMinimumWidth(58)
        label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return label

    @staticmethod
    def _voiceover_duration_text(duration_ms: int | None) -> str:
        if duration_ms is None:
            return "--:--"
        total_seconds = max(0, int(duration_ms)) // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def set_voiceover_available(self, enabled: bool, tooltip: str) -> None:
        self.voiceover_record_button.setEnabled(bool(enabled))
        self.voiceover_record_button.setToolTip(tooltip)
        self.voiceover_record_button.setAccessibleName(tooltip)

    def set_voiceover_recording(
            self, recording: bool, *, busy: bool = False) -> None:
        self.voiceover_record_button.setText(
            "■  STOP" if recording else "●  REC")
        self.voiceover_record_button.setProperty(
            "recording", "true" if recording else "false")
        self.voiceover_record_button.style().unpolish(
            self.voiceover_record_button)
        self.voiceover_record_button.style().polish(
            self.voiceover_record_button)
        if busy:
            self.voiceover_record_button.setEnabled(False)

    def set_voiceover_levels(self, peak: float, rms: float) -> None:
        self.voiceover_waveform.set_levels(peak, rms)

    def set_voiceover_take(
            self, *, take_number: int | None, duration_ms: int | None,
            waveform: tuple[float, ...] | None,
            clear_enabled: bool) -> None:
        take = "-" if take_number is None else str(max(1, int(take_number)))
        self.voiceover_take_label.setText(
            f"TAKE {take}  ·  {self._voiceover_duration_text(duration_ms)}")
        if waveform is not None:
            self.voiceover_waveform.set_waveform(tuple(waveform))
        self.voice_clear_button.setEnabled(bool(clear_enabled))

    def _update_responsive_state(self, width: int) -> None:
        layout_width = max(self.COMPACT_LAYOUT_WIDTH, int(width or 0))
        self.voiceover_waveform.show()
        for button in self.deck_export_style_buttons:
            button.show()

        # At 1683 px the selected layout lands on exact 147 / 563 / 806 / 1190
        # zone starts. At compact widths the three generous console columns
        # contract together, leaving Mark Clip at least 310 px wide.
        fraction = max(0.0, min(
            1.0,
            (layout_width - self.COMPACT_LAYOUT_WIDTH)
            / (self.TARGET_LAYOUT_WIDTH - self.COMPACT_LAYOUT_WIDTH),
        ))
        transport_width = round(
            self.COMPACT_TRANSPORT_CONTENT_WIDTH
            + fraction * (
                self.TARGET_TRANSPORT_CONTENT_WIDTH
                - self.COMPACT_TRANSPORT_CONTENT_WIDTH))
        wheel_width = self.TARGET_WHEEL_CONTENT_WIDTH
        shuttle_width = round(
            self.COMPACT_SHUTTLE_CONTENT_WIDTH
            + fraction * (
                self.TARGET_SHUTTLE_CONTENT_WIDTH
                - self.COMPACT_SHUTTLE_CONTENT_WIDTH))
        self.position_zone.setFixedWidth(self.POSITION_CONTENT_WIDTH)
        self.transport_zone.setFixedWidth(transport_width)
        self._wheel_anchor_top.setFixedWidth(wheel_width)
        self.dial_zone.setFixedWidth(shuttle_width)

        # Voiceover spans Position + its rule + Transport. Both row layouts
        # therefore reserve the same wheel column, and the single interactive
        # wheel can safely sit above those empty anchors without collisions.
        voiceover_width = (
            self.POSITION_CONTENT_WIDTH + 1 + transport_width)
        wheel_x = voiceover_width + 1
        self.voiceover_zone.setFixedWidth(voiceover_width)
        self.reserved_bay.setFixedWidth(wheel_width)
        self.wheel_host.setFixedSize(wheel_width, self.DECK_HEIGHT)
        self.wheel_host.move(wheel_x, 0)
        self.jog_ring.set_bay_width(wheel_width)
        self.wheel_host.raise_()
        self._export_top_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._export_bottom_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._voiceover_top_row.invalidate()
        self._export_top_row.invalidate()
        self._export_bottom_row.invalidate()
        self._top_row.invalidate()
        self._bottom_row.invalidate()
        self.wheel_host.updateGeometry()
        self.voiceover_zone.updateGeometry()
        self.deck_export_zone.updateGeometry()
        self.marks_group.updateGeometry()
