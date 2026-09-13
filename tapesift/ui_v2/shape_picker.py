"""The telestration shape and ink picker.

A flyout rather than a permanent rail: twenty-seven shapes and twelve inks
would take more width beside the film than the film could spare, and the
rail's job is to stay out of the way of the play.

Icons live in ``resources/icons/telestration`` and are drawn in
``currentColor``, so one file serves every ink.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tapesift.models.telestration import INK, MarkKind

SHAPE_ICON_DIR = (
    Path(__file__).resolve().parent.parent / "resources" / "icons"
    / "telestration")

SUPERSAMPLE = 2
REST = "#D2D9DF"
BRIGHT = "#FFFFFF"
SELECTED = "#39E07A"

#: Grouped the way an analyst reaches for them, not alphabetically.
SHAPE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("COACH'S CHALK", (
        MarkKind.ROUTE_ARROW.value, MarkKind.ROUTE_CURVE.value,
        MarkKind.ROUTE_COMEBACK.value, MarkKind.MOTION_DASHED.value,
        MarkKind.BLOCK_TEE.value, MarkKind.DOUBLE_TEAM.value,
        MarkKind.PULL_TRAP.value, MarkKind.HANDOFF.value,
        MarkKind.OPTION_PITCH.value, MarkKind.COVERAGE_MAN.value,
        MarkKind.ZONE_BUBBLE.value, MarkKind.PURSUIT_ANGLE.value)),
    ("PLAYERS", (
        MarkKind.PLAYER_O.value, MarkKind.PLAYER_X.value,
        MarkKind.PLAYER_RING.value, MarkKind.PLAYER_TRIANGLE.value,
        MarkKind.NUMBER_MARKER.value)),
    ("MARKUP", (
        MarkKind.LINE_SOLID.value, MarkKind.LINE_DASHED.value,
        MarkKind.ARROW_DOUBLE.value, MarkKind.RECTANGLE.value,
        MarkKind.ZONE_BOX.value, MarkKind.ELLIPSE_SHAPE.value,
        MarkKind.SPOTLIGHT.value, MarkKind.FREEHAND_SHAPE.value,
        MarkKind.MEASURE.value, MarkKind.TEXT_LABEL.value)),
)

#: Ink order: the three original film inks first, then neon, then the rest.
INK_ORDER = ("gold", "cyan", "red", "neon", "white", "black",
             "orange", "yellow", "green", "blue", "purple", "pink")

PICKER_QSS = """
QWidget#ShapePicker {
    background: #16191C;
    border: 1px solid #333A41;
    border-radius: 8px;
}
QLabel[pickerHeading="true"] {
    color: #6E7781;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1.1px;
}
QToolButton[pickerShape="true"] {
    background: #1B1F23;
    border: 1px solid #262C32;
    border-radius: 6px;
}
QToolButton[pickerShape="true"]:hover {
    background: #232830;
    border-color: #3A424A;
}
QToolButton[pickerShape="true"]:checked {
    background: #16341F;
    border-color: #2F7A45;
}
QToolButton[pickerSwatch="true"] {
    border: 2px solid #262C32;
    border-radius: 11px;
}
QToolButton[pickerSwatch="true"]:hover { border-color: #6E7781; }
QToolButton[pickerSwatch="true"]:checked { border-color: #E6EDF3; }
"""


@lru_cache(maxsize=64)
def _document(name: str) -> str:
    path = SHAPE_ICON_DIR / f"{name}.svg"
    if not path.is_file():
        raise FileNotFoundError(f"telestration icon missing: {path}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=512)
def shape_icon(name: str, colour: str = REST, size: int = 26) -> QIcon:
    """One shape icon in one colour, supersampled for a clean edge."""
    # The art is drawn at 1.5px for the canvas, where strokes are 30px+
    # wide. At a 20px icon that lands near one physical pixel and simply
    # disappears against a dark button, so display weight is thickened
    # here rather than coarsening the shapes people actually draw.
    document = (_document(name)
                .replace("currentColor", colour)
                .replace('stroke-width="1.5"', 'stroke-width="2.2"'))
    renderer = QSvgRenderer(bytearray(document, encoding="utf-8"))
    pixmap = QPixmap(size * SUPERSAMPLE, size * SUPERSAMPLE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation))


def shape_state_icon(name: str, size: int = 26) -> QIcon:
    """Rail/picker icon with rest, hover and selected states."""
    icon = QIcon()
    icon.addPixmap(shape_icon(name, REST, size).pixmap(size, size),
                   QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(shape_icon(name, BRIGHT, size).pixmap(size, size),
                   QIcon.Mode.Active, QIcon.State.Off)
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active):
        icon.addPixmap(shape_icon(name, SELECTED, size).pixmap(size, size),
                       mode, QIcon.State.On)
    return icon


def _swatch_icon(colour: str, size: int = 16) -> QIcon:
    pixmap = QPixmap(size * SUPERSAMPLE, size * SUPERSAMPLE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    painter.drawEllipse(pixmap.rect().adjusted(2, 2, -2, -2))
    painter.end()
    return QIcon(pixmap.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation))


class ShapePicker(QWidget):
    """Grid of every shape, plus the ink row, as one dismissible flyout."""

    shape_chosen = Signal(str)
    ink_chosen = Signal(str)

    COLUMNS = 6

    def __init__(self, parent: QWidget | None = None, *,
                 inks_only: bool = False) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("ShapePicker")
        self.setStyleSheet(PICKER_QSS)
        column = QVBoxLayout(self)
        column.setContentsMargins(12, 10, 12, 12)
        column.setSpacing(8)

        self.shape_group = QButtonGroup(self)
        self.shape_group.setExclusive(True)
        self.shape_buttons: dict[str, QToolButton] = {}

        self.inks_only = inks_only
        for title, names in (() if inks_only else SHAPE_GROUPS):
            heading = QLabel(title, self)
            heading.setProperty("pickerHeading", "true")
            column.addWidget(heading)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(4)
            for index, name in enumerate(names):
                button = QToolButton(self)
                button.setObjectName(f"Shape_{name}")
                button.setProperty("pickerShape", "true")
                button.setCheckable(True)
                button.setFixedSize(40, 40)
                button.setIconSize(QSize(26, 26))
                button.setIcon(shape_state_icon(name))
                label = name.replace("_shape", "").replace("_", " ")
                button.setToolTip(label)
                button.setAccessibleName(f"{label} tool")
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                button.clicked.connect(
                    lambda _checked=False, key=name: self._pick_shape(key))
                grid.addWidget(button, index // self.COLUMNS,
                               index % self.COLUMNS)
                self.shape_group.addButton(button)
                self.shape_buttons[name] = button
            column.addLayout(grid)

        heading = QLabel("INK" if not inks_only else "INK COLOUR", self)
        heading.setProperty("pickerHeading", "true")
        column.addWidget(heading)
        ink_grid = QGridLayout()
        ink_grid.setContentsMargins(0, 0, 0, 0)
        ink_grid.setSpacing(4)
        self.ink_group = QButtonGroup(self)
        self.ink_group.setExclusive(True)
        self.ink_buttons: dict[str, QToolButton] = {}
        for index, name in enumerate(INK_ORDER):
            if name not in INK:
                continue
            button = QToolButton(self)
            button.setObjectName(f"Ink_{name}")
            button.setProperty("pickerSwatch", "true")
            button.setCheckable(True)
            button.setFixedSize(22, 22)
            button.setIconSize(QSize(16, 16))
            # Painted by the sheet: a coloured icon inside a QToolButton
            # is drawn through the button's own palette and came out a
            # uniform dull olive for every ink.
            button.setStyleSheet(
                f"background: {INK[name]}; border-radius: 11px;")
            button.setToolTip(name)
            button.setAccessibleName(f"{name} ink")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(
                lambda _checked=False, key=name: self._pick_ink(key))
            ink_grid.addWidget(button, index // self.COLUMNS,
                               index % self.COLUMNS)
            self.ink_group.addButton(button)
            self.ink_buttons[name] = button
        column.addLayout(ink_grid)

    def _pick_shape(self, name: str) -> None:
        self.set_shape(name)
        self.shape_chosen.emit(name)
        self.hide()

    def _pick_ink(self, name: str) -> None:
        self.set_ink(name)
        self.ink_chosen.emit(name)

    def set_shape(self, name: str) -> None:
        """Reflect the armed tool without emitting another request."""
        for key, button in self.shape_buttons.items():
            button.setChecked(key == name)

    def set_ink(self, name: str) -> None:
        for key, button in self.ink_buttons.items():
            button.setChecked(key == name)

    def popup_at(self, anchor: QWidget) -> None:
        """Show beside the rail, kept on screen."""
        self.adjustSize()
        corner = anchor.mapToGlobal(anchor.rect().topRight())
        screen = anchor.screen()
        x = corner.x() + 6
        y = corner.y()
        if screen is not None:
            available = screen.availableGeometry()
            x = min(x, available.right() - self.width() - 4)
            y = min(max(available.top() + 4, y),
                    available.bottom() - self.height() - 4)
        self.move(x, y)
        self.show()
