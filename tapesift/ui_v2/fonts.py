"""Bundled display fonts used by the V2 visual system."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase


DISPLAY_FONT_FAMILY = "Rajdhani"


def load_v2_fonts() -> list[str]:
    font_dir = Path(__file__).resolve().parent.parent / "resources" / "fonts"
    families: list[str] = []
    for name in ("Rajdhani-SemiBold.ttf", "Rajdhani-Bold.ttf"):
        font_id = QFontDatabase.addApplicationFont(str(font_dir / name))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families
