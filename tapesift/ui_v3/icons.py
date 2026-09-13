"""Paths and icons from the locked official Fluent V3 asset set."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

# Resolved once. icon_path ran Path.resolve() per call, and the ledger asks for
# an icon per row on every rebuild, so a 60-clip refresh spent 120 filesystem
# round trips deciding where a directory it had already found lived.
_ICON_DIR = (
    Path(__file__).resolve().parent.parent / "resources" / "icons" / "v3"
)


def icon_path(name: str) -> Path:
    return _ICON_DIR / name


@lru_cache(maxsize=16)
def brand_pixmap(name: str, height: int) -> QPixmap:
    """Render the supplied white artwork without its black image backing."""
    resources = _ICON_DIR.parent.parent
    path = resources / ("branding" if name.endswith(".jpg") else "icons") / name
    source = QImage(str(path))
    if source.isNull():
        return QPixmap()
    # The JPEG's luminance is its original ink coverage, including antialiasing.
    if not source.hasAlphaChannel():
        coverage = source.convertToFormat(QImage.Format.Format_Grayscale8)
        source = source.convertToFormat(QImage.Format.Format_ARGB32)
        source.fill(Qt.GlobalColor.white)
        source.setAlphaChannel(coverage)
    return QPixmap.fromImage(source).scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)


# The asset set is locked and these are pure functions of their arguments, so
# the results are cached for the life of the process. Roughly 20 distinct
# glyphs exist, so the cache is bounded by the asset set, not by use. QIcon is
# implicitly shared and widgets take their own reference, so handing the same
# instance to several widgets is the ordinary Qt pattern.
@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    return QIcon(str(icon_path(name)))


@lru_cache(maxsize=None)
def tinted_icon(
        name: str, color: str = "#dce5de", size: int = 20) -> QIcon:
    """Tint an official Fluent glyph without changing its source geometry."""

    source = icon(name).pixmap(QSize(size, size))
    canvas = QPixmap(source.size())
    canvas.setDevicePixelRatio(source.devicePixelRatio())
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(canvas.rect(), QColor(color))
    painter.end()
    return QIcon(canvas)
