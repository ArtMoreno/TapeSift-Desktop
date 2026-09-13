"""Bundled typefaces for the locked Shell V3 Review surface."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase


SANS_FAMILY = "IBM Plex Sans"
# IBM's Windows TTFs expose the weight in the registered family name.
MONO_FAMILY = "IBM Plex Mono Medium"

_FONT_FILES = (
    "IBMPlexSans-Regular.ttf",
    "IBMPlexSans-Medium.ttf",
    "IBMPlexSans-SemiBold.ttf",
    "IBMPlexSans-Bold.ttf",
    "IBMPlexMono-Medium.ttf",
    "IBMPlexMono-SemiBold.ttf",
)
_loaded = False
_families: tuple[str, ...] = ()


def load_v3_fonts() -> tuple[str, ...]:
    """Register the locked Review fonts once on the GUI thread."""
    global _loaded, _families
    if _loaded:
        return _families
    font_dir = Path(__file__).resolve().parent.parent / "resources" / "fonts"
    families: list[str] = []
    for name in _FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(font_dir / name))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    _families = tuple(dict.fromkeys(families))
    _loaded = True
    return _families
