"""Build fixed transparent UI icons from Windows' Fluent icon library."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter,
)


ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "tapesift" / "resources" / "icons"
CANVAS_SIZE = 128
GLYPH_PIXEL_SIZE = 96
FLUENT_GLYPHS = {
    "tapesift-search.png": 0xE721,
    "tapesift-settings.png": 0xE713,
    "tapesift-video.png": 0xE714,
    "tapesift-close.png": 0xE8BB,
    "tapesift-volume.png": 0xE994,
    "tapesift-volume-muted.png": 0xE74F,
}


def render_fluent_glyph(glyph: int, family: str) -> QImage:
    image = QImage(
        CANVAS_SIZE,
        CANVAS_SIZE,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.transparent)

    font = QFont(family)
    font.setPixelSize(GLYPH_PIXEL_SIZE)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setFont(font)
    painter.setPen(QColor("#f4f4ef"))
    painter.drawText(
        QRect(0, 0, CANVAS_SIZE, CANVAS_SIZE),
        Qt.AlignmentFlag.AlignCenter,
        chr(glyph),
    )
    painter.end()
    return image


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    families = set(QFontDatabase.families())
    family = (
        "Segoe Fluent Icons"
        if "Segoe Fluent Icons" in families
        else "Segoe MDL2 Assets"
    )
    if family not in families:
        raise RuntimeError("Windows Fluent icon font is not installed")

    ICONS.mkdir(parents=True, exist_ok=True)
    for name, glyph in FLUENT_GLYPHS.items():
        output = ICONS / name
        if not render_fluent_glyph(glyph, family).save(str(output), "PNG"):
            raise OSError(f"Could not write {output}")
        print(output.relative_to(ROOT))
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
