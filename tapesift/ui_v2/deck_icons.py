"""Deck icon set: one SVG per shape, tinted at load.

The transport keys were Qt's standard pixmaps (``SP_MediaPlay`` and
friends). Those are drawn by whatever style is active, at whatever weight
that style chose, which is why they never matched the tool glyphs beside
them and read as placeholder art.

These are drawn on one 24px grid with matched optical weight: 2.8px rounded
strokes for editing and telestration shapes, and bold solid fills for the
transport marks, so both treatments sit at the same visual weight side by
side.

Every path uses ``currentColor``. Qt does not resolve that, so ``load()``
substitutes the colour before handing the document to the renderer - one
file per shape rather than one per state.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons" / "deck"

#: Rendered at 2x and scaled down, so the strokes stay clean on a HiDPI
#: display without shipping a second set of files.
SUPERSAMPLE = 2

REST = "#9AA694"
ACTIVE = "#E8A33D"
BRIGHT = "#E6EBE0"


@lru_cache(maxsize=256)
def _document(name: str) -> str:
    path = ICON_DIR / f"{name}.svg"
    if not path.is_file():
        raise FileNotFoundError(f"deck icon missing: {path}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=512)
def load(name: str, colour: str = REST, size: int = 16) -> QIcon:
    """A deck icon in one colour.

    Cached because the deck rebuilds icons on every state change, and
    re-parsing the same handful of documents for each repaint is wasted
    work on a surface that has to stay out of the render loop's way.
    """
    document = _document(name).replace("currentColor", colour)
    renderer = QSvgRenderer(bytearray(document, encoding="utf-8"))

    pixmap = QPixmap(size * SUPERSAMPLE, size * SUPERSAMPLE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, pixmap.width(), pixmap.height()))
    painter.end()

    scaled = pixmap.scaled(
        QSize(size, size),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation)
    return QIcon(scaled)


def state_icon(name: str, size: int = 16) -> QIcon:
    """One icon carrying its own rest / active / disabled colours.

    Qt picks the mode itself, so a checked or disabled control changes
    colour without the caller tracking state or swapping icons.
    """
    icon = QIcon()
    icon.addPixmap(load(name, REST, size).pixmap(size, size),
                   QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(load(name, BRIGHT, size).pixmap(size, size),
                   QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(load(name, ACTIVE, size).pixmap(size, size),
                   QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(load(name, "#4E584A", size).pixmap(size, size),
                   QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


def available() -> list[str]:
    return sorted(path.stem for path in ICON_DIR.glob("*.svg"))
