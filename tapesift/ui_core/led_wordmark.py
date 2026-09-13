"""LED scoreboard wordmark: renders text as a green dot-matrix pixmap.

Draws the classic stadium-scoreboard look - bright green LED dots on a dark
tile, with the unlit dot grid faintly visible - matching the TapeSift brand.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

# 5x7 dot-matrix font (1 = lit). Full A-Z, so any screen can carry a
# dot-matrix heading rather than only the wordmark itself.
_FONT: dict[str, list[str]] = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10101", "10011", "10001", "10001"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    " ": ["00000"] * 7,
}

LED_ON = QColor("#39e15a")
LED_ON_CORE = QColor("#8dff9f")
LED_OFF = QColor("#1a2a1d")
BACKGROUND = QColor(0, 0, 0, 0)  # transparent - sits on the app background

# Silver scheme: the black-and-silver identity, dots without the glow.
# The unlit dots sit barely above the background - visible enough to keep
# the dot-matrix character, faint enough that the letters read as solid
# shapes rather than something that failed to light up.
SILVER_ON = QColor("#d6d6e0")
SILVER_CORE = QColor("#ffffff")
SILVER_OFF = QColor("#1e1e22")


def wordmark_pixmap(text: str = "TAPESIFT", dot: int = 5,
                    gap: int = 2, letter_gap: int = 2,
                    scheme: str = "green") -> QPixmap:
    """Render `text` (A–Z subset in _FONT) as an LED dot-matrix pixmap.

    scheme: "green" (stadium LED, glow) or "silver" (brand identity, flat).
    """
    if scheme == "silver":
        # Tighter grid: lit dots nearly touch, so strokes read continuously.
        return _render(text, dot, max(1, gap - 1), letter_gap,
                       SILVER_ON, SILVER_CORE, SILVER_OFF, glow=False,
                       bleed=1)
    return _render(text, dot, gap, letter_gap, LED_ON, LED_ON_CORE, LED_OFF,
                   glow=True)


def _render(text: str, dot: int, gap: int, letter_gap: int, on: QColor,
            core: QColor, off: QColor, glow: bool, bleed: int = 0) -> QPixmap:
    text = text.upper()
    cell = dot + gap
    letter_w = 5 * cell + letter_gap * cell
    width = len(text) * letter_w
    height = 7 * cell
    pixmap = QPixmap(width, height)
    pixmap.fill(BACKGROUND)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)

    for index, char in enumerate(text):
        pattern = _FONT.get(char, _FONT[" "])
        origin_x = index * letter_w
        for row, bits in enumerate(pattern):
            for col, bit in enumerate(bits):
                x = origin_x + col * cell
                y = row * cell
                rect = QRectF(x, y, dot, dot)
                if bit == "1":
                    if glow:
                        halo = QColor(on)
                        halo.setAlpha(60)
                        painter.setBrush(halo)
                        painter.drawEllipse(rect.adjusted(-2, -2, 2, 2))
                    painter.setBrush(on)
                    # bleed grows lit dots so adjacent ones merge into a stroke
                    painter.drawRoundedRect(
                        rect.adjusted(-bleed, -bleed, bleed, bleed), 1.5, 1.5)
                    painter.setBrush(core)
                    painter.drawRoundedRect(
                        rect.adjusted(dot * 0.28, dot * 0.28,
                                      -dot * 0.28, -dot * 0.28), 1, 1)
                else:
                    painter.setBrush(off)
                    painter.drawRoundedRect(rect, 1.5, 1.5)
    painter.end()
    return pixmap
