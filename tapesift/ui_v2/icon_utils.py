"""Small helpers for presenting Qt's native icon library consistently."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QWidget


def tinted_standard_icon(
        widget: QWidget,
        standard_pixmap: QStyle.StandardPixmap,
        *,
        colour: str = "#f4f4ef",
        size: QSize = QSize(20, 20),
        glyph_size: QSize | None = None,
        optical_offset: QPoint = QPoint(0, 0),
) -> QIcon:
    """Return a recoloured icon sourced from Qt's platform icon library.

    Windows' native style supplies the right transport/window-control shapes,
    but some of its dark-mode pixmaps are almost black. Recolouring the native
    alpha mask keeps the genuine platform icon while making it legible on the
    TapeSift transport.
    """
    source = widget.style().standardIcon(standard_pixmap).pixmap(size)
    if source.isNull():
        return QIcon()

    # Windows' native style may return (for example) a 27px pixmap tagged
    # with DPR 1.5 for an 18px logical request, even when the application
    # window itself is on a DPR 1.0 screen. From this point on we work in
    # device-independent pixels, so remove that source-only DPR metadata and
    # explicitly resample onto the requested logical canvas.
    source.setDevicePixelRatio(1.0)
    glyph = source.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    if glyph_size is not None:
        image = source.toImage()
        opaque = [
            (x, y)
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        ]
        if opaque:
            left = min(x for x, _y in opaque)
            right = max(x for x, _y in opaque)
            top = min(y for _x, y in opaque)
            bottom = max(y for _x, y in opaque)
            bounds = QRect(
                left,
                top,
                right - left + 1,
                bottom - top + 1,
            )
            glyph = source.copy(bounds).scaled(
                glyph_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    glyph.setDevicePixelRatio(1.0)

    tinted = QPixmap(glyph.size())
    tinted.fill(QColor(Qt.GlobalColor.transparent))
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, glyph)
    painter.setCompositionMode(
        QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor(colour))
    painter.end()

    rendered = QPixmap(size)
    rendered.fill(QColor(Qt.GlobalColor.transparent))
    x = ((size.width() - tinted.width()) // 2) + optical_offset.x()
    y = ((size.height() - tinted.height()) // 2) + optical_offset.y()
    painter = QPainter(rendered)
    painter.drawPixmap(x, y, tinted)
    painter.end()
    return QIcon(rendered)


def magnifier_icon(
        operation: str,
        *,
        colour: str = "#f4f4ef",
        size: QSize = QSize(18, 18),
) -> QIcon:
    """Draw an unmistakable zoom-in or zoom-out symbol.

    Qt does not expose a portable magnifier standard pixmap. Drawing this
    tiny vector at runtime keeps the timeline control sharp at every Windows
    scale factor and avoids falling back to ambiguous bare +/- text.
    """
    rendered = QPixmap(size)
    rendered.fill(QColor(Qt.GlobalColor.transparent))
    painter = QPainter(rendered)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(colour))
    pen.setWidthF(1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    lens = QRectF(2.0, 2.0, 10.5, 10.5)
    painter.drawEllipse(lens)
    painter.drawLine(QPoint(11, 11), QPoint(16, 16))
    painter.drawLine(QPoint(5, 7), QPoint(10, 7))
    if operation == "in":
        painter.drawLine(QPoint(7, 5), QPoint(7, 10))
    painter.end()
    return QIcon(rendered)
