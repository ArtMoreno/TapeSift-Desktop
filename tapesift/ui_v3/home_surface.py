"""Cinematic Home artwork; project controls remain real Qt widgets."""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QFrame

from tapesift.ui_v3.icons import brand_pixmap


BRANDING = Path(__file__).resolve().parent.parent / "resources" / "branding"


def paint_cover(painter, pixmap, rect):
    if pixmap.isNull() or rect.isEmpty():
        return
    scale = max(rect.width() / pixmap.width(), rect.height() / pixmap.height())
    width, height = rect.width() / scale, rect.height() / scale
    source = QRectF((pixmap.width() - width) / 2, (pixmap.height() - height) / 2, width, height)
    painter.drawPixmap(QRectF(rect), pixmap, source)


class EmptyFilmRoom(QFrame):
    """Use the standard raster field with the supplied, unmodified brand ink."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.field = QPixmap(str(BRANDING / "home-field.png"))
        self.logo = brand_pixmap("ifi-horizontal-approved.jpg", 120)
        self.setObjectName("V3EmptyFilmRoom")
        self.setAcceptDrops(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#11110f"))
        # Artwork and midfield branding share normalized coordinates so resizing
        # cannot detach the logo from the fifty-yard line.
        painter.drawPixmap(self.rect(), self.field)
        painter.save()
        painter.translate(self.width() * .73, self.height() * .49)
        painter.rotate(12)
        painter.shear(-.15, 0)
        painter.setOpacity(.14)
        width = self.width() * .205
        height = width * self.logo.height() / max(1, self.logo.width())
        painter.drawPixmap(QRectF(-width / 2, -height / 2, width, height), self.logo, QRectF(self.logo.rect()))
        painter.restore()
        if self.property("dragover") == "true":
            painter.setPen(QColor("#b9a46c"))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))


def paint_film_hero(widget, source):
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.fillRect(widget.rect(), QColor("#11110f"))
    paint_cover(painter, source, widget.rect())
    if not source.isNull():
        scrim = QLinearGradient(0, 0, 0, widget.height())
        scrim.setColorAt(0, QColor(0, 0, 0, 25))
        scrim.setColorAt(.45, QColor(0, 0, 0, 8))
        scrim.setColorAt(1, QColor(8, 8, 7, 240))
        painter.fillRect(widget.rect(), scrim)
