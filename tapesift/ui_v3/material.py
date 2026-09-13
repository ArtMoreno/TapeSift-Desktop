"""Shared broadcast-slate surface, using the standard generated grain asset."""

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap


@lru_cache(maxsize=1)
def _texture() -> QPixmap:
    return QPixmap(str(Path(__file__).parents[1] / "resources/branding/broadcast-slate-texture.png"))


def paint_slate(widget, *, header=False) -> None:
    painter = QPainter(widget)
    gradient = QLinearGradient(0, 0, 0, widget.height())
    gradient.setColorAt(0, QColor("#1c2024" if header else "#15181b"))
    gradient.setColorAt(.24, QColor("#14171a" if header else "#0f1113"))
    gradient.setColorAt(1, QColor("#0f1113" if header else "#0a0b0c"))
    painter.fillRect(widget.rect(), gradient)
    painter.setOpacity(.12)
    painter.drawTiledPixmap(widget.rect(), _texture())
    painter.setOpacity(1)
    if header:
        painter.setPen(QColor("#2d3237"))
        painter.drawLine(0, 0, widget.width(), 0)
        painter.setPen(QColor("#1f2327"))
        painter.drawLine(0, widget.height()-1, widget.width(), widget.height()-1)
    painter.end()
